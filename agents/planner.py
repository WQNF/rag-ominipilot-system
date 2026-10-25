# agents/planner.py
import re
import json
from difflib import SequenceMatcher
from langchain_core.messages import HumanMessage
from core.llm import llm
from core.metrics import record_llm_usage
from config.prompts import PLANNER_PROMPT, QUESTION_PARSER_PROMPT
from config.documents import (
    ALL_DOC_NAMES,
    get_all_doc_names,
    get_display_name_map,
    _normalize_name,
)

def _safe_parse_json_array(raw: str) -> list | None:
    """
    鲁棒解析 LLM 返回的 JSON 数组。
    处理：markdown 代码块、缺失逗号、多余逗号、前后多余文本。
    """
    if not raw or not raw.strip():
        return None
    # 1. 直接解析
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
    except Exception:
        pass
    # 2. 提取 ```json 代码块
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(1))
            if isinstance(result, list):
                return result
        except Exception:
            pass
    # 3. 提取第一个 [ ... ] 数组
    m = re.search(r'\[.*?\]', raw, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            pass
        # 3b. 修复缺失逗号后重试
        fixed = re.sub(r',(\s*[}\]])', r'\1', candidate)  # 末尾多余逗号
        fixed = re.sub(r'"\s*\n\s*"', r'",\n"', fixed)  # 对象属性间缺失逗号
        fixed = re.sub(r'}\s*\n\s*{', r'},\n{', fixed)    # 数组元素间缺失逗号
        if fixed != candidate:
            try:
                return json.loads(fixed)
            except Exception:
                pass
    return None


def _safe_parse_json_dict(raw: str) -> dict | None:
    """鲁棒解析 LLM 返回的 JSON 对象"""
    if not raw or not raw.strip():
        return None
    # 1. 直接解析
    try:
        result = json.loads(raw)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    # 2. 提取 ```json 代码块
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(1))
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    # 3. 提取第一个 { ... } 对象
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            pass
        # 3b. 修复常见 JSON 错误后重试
        fixed = re.sub(r',(\s*[}\]])', r'\1', candidate)
        fixed = re.sub(r'"\s*\n\s*"', r'",\n"', fixed)
        if fixed != candidate:
            try:
                return json.loads(fixed)
            except Exception:
                pass
    return None


def _extract_options_from_text(text: str) -> dict:
    """从原始文本中用正则提取选项 A/B/C/D，支持标准格式和紧凑格式"""
    options = {}
    # 先尝试紧凑格式：a-1107.71b-1007.01c-906.31d1007.01（无换行/分隔符）
    compact = re.findall(r'([a-dA-D])\s*(-?\d+\.?\d*)', text)
    if compact:
        for letter, value in compact:
            options[letter.upper()] = value.strip()
        return options
    # 标准格式：每行一个选项，如 "A. -1107.71"
    for letter in ["A", "B", "C", "D"]:
        m = re.search(rf'(?:^|\n)\s*{letter}\s*[\.\、\)]\s*(.+?)(?=\n\s*[A-D]\s*[\.\、\)]|\Z)', text, re.DOTALL)
        if m:
            options[letter] = m.group(1).strip()
    return options


def _extract_stem(text: str, options: dict) -> str:
    """从含选项的文本中提取纯题干"""
    stem = text
    for letter in ["D", "C", "B", "A"]:
        if letter in options:
            val = options[letter]
            # 移除选项行
            idx = stem.rfind(f"{letter}.")
            if idx == -1:
                idx = stem.rfind(f"{letter}、")
            if idx == -1:
                idx = stem.rfind(f"{letter})")
            if idx != -1:
                stem = stem[:idx].strip()
    return stem


def rewrite_query(user_query: str) -> str:
    return user_query

def identify_documents_by_regex(query: str) -> list:
    """
    基于正则表达式匹配文档名称（书名号、表格名、关键词）
    返回匹配到的文档名列表（最多一个，优先采用最明确的匹配）
    """
    matched = set()
    
    # 1. 匹配所有《...》中的内容（文档名称）
    book_title_matches = re.findall(r'《([^》]+)》', query)
    for title in book_title_matches:
        for doc in ALL_DOC_NAMES:
            if title in doc:
                matched.add(doc)
    
    # 2. 匹配所有“XXX表中”或“XXX统计表中”的XXX部分（表格名称）
    table_phrase_matches = re.findall(r'([\u4e00-\u9fa5a-zA-Z0-9（）]+)表[中]?', query)
    for table_name in table_phrase_matches:
        full_table_name = table_name + "表"
        for doc in ALL_DOC_NAMES:
            if full_table_name in doc or table_name in doc:
                matched.add(doc)

    return list(matched)

def identify_documents_by_fuzzy(query: str, top_n: int = 5) -> list:
    """
    基于模糊匹配识别查询中最可能相关的文档。
    无需将 500+ 文档名全部塞入 LLM 上下文。

    策略：
    1. 从查询中提取可能的文档名称线索（书名号、表名、关键词）
    2. 对每条线索，用 difflib 在所有文档名中做模糊匹配
    3. 聚合得分，返回 Top-N
    """
    # 获取完整的文档列表和名称映射
    all_docs = get_all_doc_names()
    name_map = get_display_name_map()

    # === 第一步：从查询中提取文档名线索 ===
    hints = set()

    # 1a. 书名号内的内容，如《银行函证工作操作指引》
    book_titles = re.findall(r'《([^》]+)》', query)
    for t in book_titles:
        hints.add(t.strip())

    # 1b. "XXX表" 或 "XXX表中" 模式
    table_matches = re.findall(r'([\u4e00-\u9fa5a-zA-Z0-9（）]+)表[中]?', query)
    for t in table_matches:
        hints.add(t.strip() + "表")
        hints.add(t.strip())

    # 1c. 查询本身作为整体线索（分段提取）
    # 去掉常见的疑问词后作为线索
    query_clean = re.sub(r"[的什么哪些如何怎样是哪些请问]", " ", query)
    # 按标点/空格分段
    segments = re.split(r"[，。！？、；：\s]", query_clean)
    for seg in segments:
        seg = seg.strip()
        if len(seg) >= 4:  # 至少4个中文字符才作为线索
            hints.add(seg)

    # 如果没有任何线索，尝试从查询中提取关键词作为拼音节
    if not hints:
        # 取查询中最长的2~4字片段
        words = re.findall(r"[\u4e00-\u9fa5]{2,}", query)
        for w in words:
            if len(w) >= 2:
                hints.add(w)

    print(f"[Planner] 文档匹配线索: {hints}")

    # === 第二步：对每条线索计算模糊匹配得分 ===
    # score_map: { 文档名: 总得分 }
    score_map = {}

    # 所有待匹配的目标文本（文档名 + 干净名称）
    candidates = {}
    for doc in all_docs:
        clean = _normalize_name(doc)
        candidates[doc] = clean

    for hint in hints:
        if not hint:
            continue
        for doc, clean in candidates.items():
            # 计算 hint 与原始文件名的相似度
            score_raw = SequenceMatcher(None, hint.lower(), doc.lower()).ratio()
            # 计算 hint 与干净名称的相似度
            score_clean = SequenceMatcher(None, hint.lower(), clean.lower()).ratio()
            # 子串匹配加分（hint 包含在文档名中或反之）
            bonus = 0.0
            if hint.lower() in doc.lower() or doc.lower() in hint.lower():
                bonus = 0.3
            if hint.lower() in clean.lower() or clean.lower() in hint.lower():
                bonus = max(bonus, 0.4)

            final_score = max(score_raw, score_clean) + bonus
            if final_score > 0.3:  # 阈值过滤
                score_map[doc] = score_map.get(doc, 0) + final_score

    # === 第三步：排序返回 Top-N ===
    ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
    result = [doc for doc, score in ranked[:top_n]]

    print(f"[Planner] 模糊匹配结果 ({len(ranked)} 个候选): {result}")
    return result

def parse_question(user_query: str) -> dict:
    """
    使用 LLM 解析用户问题，提取题干、选项和题目类型。
    返回：{"type": "mcq_single"|"mcq_multiple"|"qa", "stem": "...", "options": {"A": "..."}}
    """
    prompt = QUESTION_PARSER_PROMPT.format(user_query=user_query)
    response = llm.invoke(prompt)
    record_llm_usage(response, agent="question_parser")
    try:
        content = response.content.strip()
        # 尝试多种 JSON 提取方式
        result = _safe_parse_json_dict(content)
        if result is None:
            raise ValueError("无法解析 LLM 返回的 JSON")
        # 确保关键字段存在
        if "type" not in result:
            result["type"] = "qa"
        if "stem" not in result:
            result["stem"] = user_query
        if "options" not in result:
            result["options"] = {}
        # LLM 可能误判为 qa，但原文实际有选项 → 正则强制纠正
        if result["type"] == "qa" or not result.get("options"):
            regex_options = _extract_options_from_text(user_query)
            if regex_options:
                result["type"] = "mcq_single"
                result["options"] = regex_options
                result["stem"] = _extract_stem(user_query, regex_options)
        return result
    except Exception as e:
        print(f"[Planner] 问题解析失败: {e}")
        # 正则兜底：从原始问题中提取选项
        options = _extract_options_from_text(user_query)
        if options:
            stem = _extract_stem(user_query, options)
            return {"type": "mcq_single", "stem": stem, "options": options}
        return {"type": "qa", "stem": user_query, "options": {}}


def _format_options_for_prompt(options: dict) -> str:
    """将选项字典格式化为提示词中的文本"""
    if not options:
        return ""
    lines = ["选项："]
    for letter in ["A", "B", "C", "D"]:
        if letter in options:
            lines.append(f"- {letter}. {options[letter]}")
    return "\n".join(lines)

def _detect_multi_fact(question: str, parsed_question: dict) -> bool:
    """检测是否为多事实选择题"""
    options = parsed_question.get("options", {})
    if len(options) < 3:
        return False
    patterns = ["两项表述均属于", "两项均属于", "两项表述", "两组表述"]
    for p in patterns:
        if p in question:
            print("[Planner] 检测到多事实选择题")
            return True
    prompt = "判断以下问题是否为多事实选择题。多事实选择题定义：每个选项中包含两个独立陈述。问题：" + question + " 输出true或false。"
    try:
        resp = llm.invoke(prompt)
        return resp.content.strip().lower() == "true"
    except:
        return False

def _split_option_into_claims(option_text: str) -> tuple:
    """将选项拆分为两个独立陈述"""
    for sep in ["; "]:
        parts = option_text.split(sep)
        non_empty = [p.strip() for p in parts if p.strip()]
        if len(non_empty) >= 2:
            print(f"  [拆分] 用分隔符拆分为两个陈述")
            return non_empty[0], non_empty[1]
    prompt = '将以下选项拆分为两个独立陈述，输出JSON格式{"c1":"陈述1","c2":"陈述2"}。选项：' + option_text + " 只输出JSON。"
    try:
        resp = llm.invoke(prompt)
        content = resp.content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        result = json.loads(content)
        return result.get("c1", option_text), result.get("c2", "")
    except Exception as e:
        print(f"[Planner] 拆分失败: {e}")
        return option_text, ""

def planner_node(state: dict) -> dict:
    print("\n" + "="*60)
    print("  [Planner] 步骤 1/4 — 任务规划")
    print("="*60)
    
    # 1. 获取原始用户查询
    user_query = ""
    if state.get("messages"):
        last_message = state["messages"][-1]
        if isinstance(last_message, HumanMessage):
            user_query = last_message.content
        else:
            user_query = str(last_message)
    if not user_query and "original_query" in state:
        user_query = state["original_query"]
    if not user_query:
        user_query = "请提供帮助"
    
    # 2. 问题标准化改写（已禁用）
    rewritten_query = rewrite_query(user_query)  # 当前直接返回原始查询
    print(f"[Planner] 原始问题: {user_query}")
    
    # 3. 用 LLM 解析问题（提前，获取 stem 用于《》提取）
    print("\n[Planner] → LLM 解析题干/选项/题型...")
    parsed_question = parse_question(user_query)
    print(f"  → 题型: {parsed_question['type']}")
    print(f"  → 题干: {parsed_question['stem']}")
    if parsed_question.get("options"):
        print(f"  → 选项 ({len(parsed_question['options'])} 个):")
        for k, v in parsed_question['options'].items():
            print(f"       {k}. {v}")
    
    # 4. 识别目标文档
    # ---- 优先策略：只从题干 stem 提取《》，避免选项中《》污染 ----
    stem = parsed_question.get("stem", user_query)
    book_title_matches = re.findall(r'《([^》]+)》', stem)
    explicit_matches = set()
    for title in book_title_matches:
        for doc in ALL_DOC_NAMES:
            if title in doc:
                explicit_matches.add(doc)

    if explicit_matches:
        explicit_matches = {d for d in explicit_matches if not d.startswith("~$")}
        target_docs = list(explicit_matches)
        print(f"[Planner] 题干《》精确匹配 ({len(target_docs)} 个): {target_docs}")
    elif book_title_matches:
        # 精确匹配失败 → 宽松匹配：去掉标题末尾括号内容（如（2023年版）→ 去掉（...）后缀）
        # 原因：文件名可能被截断，如 "（2023年.pdf" vs "（2023年版）"
        for title in book_title_matches:
            idx = max(title.rfind('（'), title.rfind('('))
            loose_title = title[:idx].strip() if idx > 0 else title
            if loose_title and loose_title != title:
                for doc in ALL_DOC_NAMES:
                    if loose_title in doc:
                        explicit_matches.add(doc)
        if explicit_matches:
            explicit_matches = {d for d in explicit_matches if not d.startswith("~$")}
            target_docs = list(explicit_matches)
            print(f"[Planner] 题干《》宽松匹配 ({len(target_docs)} 个): {target_docs}")
        else:
            # 宽松匹配也失败 → 用标题单独做模糊匹配，排除选项干扰
            target_docs_set = set()
            for title in book_title_matches:
                fuzzy_for_title = identify_documents_by_fuzzy(title, top_n=3)
                target_docs_set.update(fuzzy_for_title)
            target_docs = list(target_docs_set)
            print(f"[Planner] 题干《》模糊匹配 ({len(target_docs)} 个): {target_docs}")
    else:
        # 精确匹配失败，兜底：模糊匹配（仍用 user_query，保持 Word/Excel 行为不变）
        target_docs_set = set()
        fuzzy_matches = identify_documents_by_fuzzy(user_query, top_n=5)
        target_docs_set.update(fuzzy_matches)
        regex_matches = identify_documents_by_regex(user_query)
        target_docs_set.update(regex_matches)
        if not target_docs_set:
            fuzzy_matches_2 = identify_documents_by_fuzzy(user_query, top_n=10)
            target_docs_set.update(fuzzy_matches_2)
        target_docs = list(target_docs_set)
        print(f"[Planner] 模糊匹配识别 ({len(target_docs)} 个): {target_docs}")

    # 5. 检测多事实选择题并拆分选项
    is_multi_fact = _detect_multi_fact(user_query, parsed_question)
    option_claim_map = {}

    if is_multi_fact and parsed_question.get("options"):
        print("\n[Planner] → 多事实模式：拆分选项 + 去重...")
        options = parsed_question["options"]
        
        # 1. 先拆分所有选项的 claims
        raw_claims = {}  # opt_key -> (claim_1, claim_2)
        for opt_key in ["A", "B", "C", "D"]:
            if opt_key not in options:
                continue
            raw_claims[opt_key] = _split_option_into_claims(options[opt_key])
        
        # 2. 去重：相同文本只保留一个，记录被哪些选项共享
        unique_claims = {}  # claim_text -> {"option_keys": [...], "claim_idx": 1/2}
        for opt_key, (c1, c2) in raw_claims.items():
            for idx, claim_text in [(1, c1), (2, c2)]:
                if claim_text not in unique_claims:
                    unique_claims[claim_text] = {"option_keys": [], "claim_idx": idx}
                unique_claims[claim_text]["option_keys"].append(opt_key)
        
        # 3. 构建去重后的 plan 和 option_claim_map
        plan = []
        option_claim_map = {}
        task_id = 1
        for claim_text, info in unique_claims.items():
            opt_label = "".join(info["option_keys"])  # 如 "ABCD"、"A"、"B"
            plan.append({
                "id": task_id,
                "description": claim_text,
                "expert": "rag_searcher",
                "options": opt_label,
                "claim_idx": info["claim_idx"]
            })
            # 为每个选项记录其 claim 文本
            for opt_key in info["option_keys"]:
                if opt_key not in option_claim_map:
                    option_claim_map[opt_key] = {}
                claim_key = f"claim_{info['claim_idx']}"
                option_claim_map[opt_key][claim_key] = claim_text
            task_id += 1
        
        original_count = len(raw_claims) * 2
        print(f"[Planner] 多事实模式：去重后 {len(plan)} 个子任务（原 {original_count} 个，减少 {original_count - len(plan)} 个）")

    else:
        print("\n[Planner] → 生成检索计划...")
        options_text = _format_options_for_prompt(parsed_question.get("options", {}))
        prompt = PLANNER_PROMPT.format(
            user_query=user_query,
            question_type=parsed_question.get("type", "qa"),
            stem=parsed_question.get("stem", user_query),
            options_text=options_text
        )
        response = llm.invoke(prompt)
        record_llm_usage(response, agent="planner")
        try:
            plan = _safe_parse_json_array(response.content)
            if not plan:
                raise ValueError("无法解析 LLM 返回的 JSON 数组")
        except Exception as e:
            print(f"[Planner] 解析失败，使用默认计划: {e}")
            plan = [{"id": 1, "description": "检索相关信息", "expert": "rag_searcher"}]
    
    print(f"[Planner] 生成计划 ({len(plan)} 个任务):")
    for task in plan:
        print(f"  └─ [{task['id']}] {task['description']}")

    # 6. 构建状态更新
    state_update = {
        "plan": plan,
        "next_agent": "dispatcher",
        "step_count": state.get("step_count", 0) + 1,
        "department": state.get("department", ""),
        "user_id": state.get("user_id", ""),
        "target_documents": target_docs,
        "rewritten_query": rewritten_query,
        "parsed_question": parsed_question,
        "multi_fact": is_multi_fact,
        "option_claim_map": option_claim_map,
    }
    
    experts_needed = set()
    for item in plan:
        if isinstance(item, dict) and "expert" in item:
            experts_needed.add(item["expert"])
    if "data_analyst" not in experts_needed:
        state_update["sql_result"] = ""
    if "rag_searcher" not in experts_needed:
        state_update["context_docs"] = []
    
    print("\n" + "="*60)
    print("  [Planner] 完成 → 下一步: dispatcher (路由到各专家节点)")
    print("="*60)
    return state_update