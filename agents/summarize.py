# agents/summarize.py
import json
import re
from langchain_core.messages import AIMessage, HumanMessage
from core.llm import llm
from core.metrics import record_llm_usage


def _check_document_mismatch(query: str, context: str) -> bool:
    """检查题目提到的文档名是否存在于检索结果中（宽松匹配）"""
    doc_names = re.findall(r'《([^》]+)》', query)
    if not doc_names:
        return False
    context_lower = context.lower()
    for doc_name in doc_names:
        name_lower = doc_name.lower()
        # 1) 精确匹配
        if name_lower in context_lower:
            continue
        # 2) 去扩展名匹配
        name_no_ext = name_lower.rsplit('.', 1)[0] if '.' in name_lower else name_lower
        if name_no_ext in context_lower:
            continue
        # 3) 去版本后缀匹配（如"（2023年版）"、"（2023年）"）
        name_core = re.sub(r'[（(]\d{4}年.*?[）)]', '', name_lower).strip()
        if name_core and len(name_core) > 5 and name_core in context_lower:
            continue
        return True
    return False

def extract_sources_from_docs(docs: list) -> list:
    """从文档片段中提取来源信息（复用之前实现）"""
    sources = []
    for text in docs:
        src_match = re.search(r'\[Source:\s*([^\]]+)\]', text)
        file_name = src_match.group(1) if src_match else "未知文件"
        chap_match = re.search(r'Chapter:\s*([^|\n]+)', text)
        clause_match = re.search(r'Clause:\s*([^|\n]+)', text)
        section = clause_match.group(1).strip() if clause_match else (chap_match.group(1).strip() if chap_match else "")
        preview = text
        sources.append({
            "file_name": file_name,
            "section": section,
            "preview": preview,
            "full_text": text,
            "score": 0.0
        })
    return sources

def _is_multiple_choice(query: str) -> bool:
    """判断用户问题是否为选择题（单选/多选）"""
    # 选项模式：A. / A、/ A) 开头，后有中文内容
    option_pat = re.search(r'[A-Da-d][\.\、\)]\s*[\u4e00-\u9fa5]', query)
    if option_pat:
        return True
    # 关键词检测
    mcq_keywords = ["以下选项", "以下说法", "以下哪些", "以下哪项",
                    "正确的是", "错误的是", "正确的有", "错误的有",
                    "属于", "不属于", "哪一个", "哪个", "选择",
                    "多选题", "单选题","哪项"]
    for kw in mcq_keywords:
        if kw in query:
            return True
    return False

def _answer_mcq(question: str, context: str, sources_info: str) -> str:
    """专门回答选择题"""
    mcq_prompt = f"""你是一名金融监管考试选择题解答专家。请根据提供的检索信息，回答以下选择题。

检索信息：
{context}

题目：{question}

请按以下步骤分析并输出：
1. 逐一分析每个选项，判断正误，引用检索信息作为依据
2. 给出最终答案

输出格式：
**答案**：[正确选项字母]

**逐项分析：**
- [正确/错误] **A.** [分析及依据]
- [正确/错误] **B.** [分析及依据]
- [正确/错误] **C.** [分析及依据]
- [正确/错误] **D.** [分析及依据]

**依据来源：** {sources_info}

直接输出答案："""
    resp = llm.invoke(mcq_prompt)
    return resp.content

def _evaluate_mcq(question: str, context: str, answer: str) -> dict:
    """评估选择题答案质量"""
    eval_prompt = f"""你是一个严格的选择题答案评估专家。请评估以下选择题答案的质量。

题目：{question}

检索信息：{context}

生成的答案：{answer}

评估标准：
1. 是否逐项分析了每个选项？
2. 每个选项的分析是否正确引用了检索信息作为依据？
3. 最终答案是否正确？注意：检索信息中每个数据带有 Type 标签。当问"数值最高"时：只比较同一 Type 下的数值，严禁跨 Type 比较。如果选项涉及多个 Type，选择 premium 类型下最高的。⚠️ 对于"从X到Y的数值变化"：变化量 = Y − X（后减前）。⚠️ 季度表术语："年-季度"指"一季度"，"季度-季度"指"四季度"。⚠️ 银行业特殊术语："年"=银行业金融机构合计，"季度"=商业银行合计，"年-季度"=银行业金融机构一季度，"季度-季度"=商业银行四季度。⚠️ 贷款类季度表："本年累计/截至当期"=期初（数值为0），变化量=0−一季度值=−一季度值。⚠️ "与材料内容一致"题：若答案为"无正确选项"，必须确认所有选项在检索信息中确实完全无依据，不可仅因未逐字匹配就判错
4. 格式是否符合要求（应包含【答案】和【逐项分析】两部分，每个选项逐一分析）？

请输出 JSON 格式的评估结果：
{{"pass": true/false, "correct_option": "", "issues": ["问题1"]}}

只输出 JSON，不要有其他内容。"""
    resp = llm.invoke(eval_prompt)
    try:
        content = resp.content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        return json.loads(content)
    except Exception as e:
        print(f"[选择题评估] JSON解析失败: {e}")
        return {"pass": False, "correct_option": "", "issues": ["评估器解析失败"]}


def _refine_mcq(question: str, context: str, prev_answer: str, issues: list) -> str:
    """修正选择题答案"""
    issues_text = "\n".join(f"- {issue}" for issue in issues)
    refine_prompt = f"""你是一名金融监管考试选择题解答专家。请根据以下反馈修正你的选择题答案。

题目：{question}

检索信息：{context}

你之前的答案：{prev_answer}

评估发现的问题：
{issues_text}

修正要求：
- 严格基于检索信息重新判断每个选项
- 如果检索信息中没有任何条款支持或否定某个选项，则判定该选项为错误
- 必须引用检索信息中的具体条款编号（如"第X条"）作为依据
- 确保逐项分析所有选项（A/B/C/D）
- 每个分析必须引用检索信息中的具体内容
- 注意：检索信息中每个数据带有 Type 标签。当问"数值最高"时：只比较同一 Type 下的数值，严禁跨 Type 比较。如果选项涉及多个 Type，选择 premium 类型下最高的
- ⚠️ 对于"从X到Y的数值变化"：变化量 = Y − X（后减前），必须按此方向计算，注意正负号
- ⚠️ 季度表术语：如果题目提到"年-季度"但检索信息中只有"一季度/二季度/三季度/四季度"，"年-季度"指"一季度"，"季度-季度"指"四季度"
- ⚠️ 银行业特殊术语：对于银行业总资产/总负债表，"年"=银行业金融机构合计，"季度"=商业银行合计。"年-季度"=银行业金融机构一季度，"季度-季度"=商业银行四季度。变化量=商业银行四季度−银行业金融机构合计一季度
- ⚠️ 贷款类季度表："本年累计/截至当期"=期初（数值为0），变化量=0−一季度值=−一季度值。若计算值不匹配选项，尝试反向计算或检查−X/−Y是否匹配
- ⚠️ "与材料内容一致"题：如果答案是"无正确选项"，必须确认所有选项在检索信息中确实完全无依据，不可仅因未逐字匹配就判错。仔细检查每个选项的主题是否与检索信息的章节匹配
- 输出格式必须是可读的 markdown 文本（不要输出 JSON），格式如下：

**答案**：[正确选项字母]

**逐项分析：**
- [正确/错误] **A.** [分析及依据]
- [正确/错误] **B.** [分析及依据]
- [正确/错误] **C.** [分析及依据]
- [正确/错误] **D.** [分析及依据]

直接输出修正后的答案："""
    resp = llm.invoke(refine_prompt)
    return resp.content

def _evaluate_answer(question: str, context: str, answer: str) -> dict:
    """评估答案质量，返回 {'pass': bool, 'issues': list[str]}"""
    eval_prompt = f"""你是一个严格的质量评估专家。请评估以下答案是否满足用户问题的要求。

用户问题：{question}

检索信息：{context}

生成的答案：{answer}

评估标准：
1. 完整性：答案是否回答了用户问题中的所有子问题？（没有任何遗漏）
2. 数值使用：如果用户问题中提供了具体数值并要求计算（如比例、差值），答案是否进行了计算并给出了结果？（不得以"检索信息未提供"为由拒绝）
3. 格式：答案是否严格遵守"答案来源：...\n答案内容：..."的格式？每个答案块是否清晰？
4. 无冗余：答案中是否没有评价用户问题（如"该陈述正确"、"用户表述合理"等）？是否没有添加无关的前言后语？
5. 信息不足说明：如果检索信息确实无法回答某个子问题，答案是否明确说明了"根据现有资料无法完全回答XX"？

请输出一个 JSON 对象，格式如下：
{{
    "pass": true/false,
    "issues": ["问题1", "问题2", ...]
}}
如果完全通过，issues 为空数组。
只输出 JSON，不要有其他内容。"""
    resp = llm.invoke(eval_prompt)
    try:
        # 提取JSON（可能包含markdown代码块）
        content = resp.content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        result = json.loads(content)
        return result
    except Exception as e:
        print(f"[评估] JSON解析失败: {e}")
        return {"pass": False, "issues": ["评估器解析失败，请检查答案"]}

def _refine_answer(question: str, context: str, prev_answer: str, issues: list) -> str:
    """根据评估问题修正答案"""
    issues_text = "\n".join(f"- {issue}" for issue in issues)
    refine_prompt = f"""你是一名金融监管问答助手。请根据以下反馈修正你的答案。

用户问题：{question}

检索信息：{context}

你之前的答案：{prev_answer}

评估发现的问题：
{issues_text}

修正要求：
- 确保修正后的答案完整回答所有子问题。
- 如果用户问题提供了数值，必须进行计算，不得拒绝。
- 严格遵守"答案来源：...\n答案内容：..."格式。
- 不要评价用户问题，不要添加无关语句。
- 如果检索信息确实无法回答某个子问题，请明确写出"根据现有资料无法完全回答XX"。

直接输出修正后的答案："""
    resp = llm.invoke(refine_prompt)
    return resp.content

def summarize_agent(state: dict) -> dict:
    print("\n" + "="*60)
    print("  [总结Agent] 步骤 4/4 — 答案整合")
    print("="*60)
    
    user_query = state.get("original_query", "")
    if not user_query and state.get("messages"):
        last_message = state["messages"][-1]
        if isinstance(last_message, HumanMessage):
            user_query = last_message.content
        else:
            user_query = str(last_message)
    if not user_query:
        user_query = "请提供帮助"
    
    # 获取检索片段
    final_docs = state.get("final_context_docs", state.get("context_docs", []))
    retrieval_insufficient = state.get("retrieval_insufficient", False)
    missing_docs = state.get("missing_docs", [])

    # 从 state 读取 planner 解析好的问题结构
    parsed_question = state.get("parsed_question", {})
    question_type = parsed_question.get("type", "qa")
    question_stem = parsed_question.get("stem", user_query)
    question_options = parsed_question.get("options", {})
    
    print(f"[总结Agent] 问题类型: {question_type}")
    print(f"[总结Agent] 检索片段数: {len(final_docs)}")
    
    if not final_docs:
        print("[总结Agent] ⚠ 未检索到任何文档，终止生成")
        print("\n" + "="*60)
        print("  [总结Agent] 完成（无检索结果 → 拒答）")
        print("="*60)
        refusal_msg = """**答案**：无正确选项

**逐项分析：**
- [错误] **A.** 检索信息中无任何相关依据，无法判断正确性。
- [错误] **B.** 检索信息中无任何相关依据，无法判断正确性。
- [错误] **C.** 检索信息中无任何相关依据，无法判断正确性。
- [错误] **D.** 检索信息中无任何相关依据，无法判断正确性。

**依据来源：** 无

⚠️ 根据现有资料库，无法回答此问题。本题涉及的内容不在当前知识库范围内。"""
        return {
            "messages": [AIMessage(content=refusal_msg)],
            "visited_agents": ["summarize"],
            "step_count": state.get("step_count", 0) + 1,
            "sources": []
        }
    
    context = "\n\n".join(final_docs)
    sources = state.get("sources", []) or extract_sources_from_docs(final_docs)
    sources_info = "；".join([f"{s['file_name']} {s['section']}" for s in sources if s.get('section')]) or "、".join([s['file_name'] for s in sources])

    if question_type in ("mcq_single", "mcq_multiple"):
        multi_fact = state.get("multi_fact", False)
        option_claim_map = state.get("option_claim_map", {})

        if multi_fact and option_claim_map:
            print("[总结Agent] → 多事实选择题模式")
            claims_lines = []
            for opt_key in ["A", "B", "C", "D"]:
                if opt_key in option_claim_map:
                    c = option_claim_map[opt_key]
                    claims_lines.append(f"选项{opt_key}包含两个表述：")
                    claims_lines.append(f"  · 表述1：{c.get('claim_1', '')}")
                    claims_lines.append(f"  · 表述2：{c.get('claim_2', '')}")
                    claims_lines.append("")
            claims_text = "\n".join(claims_lines)

            mcq_prompt = f"""你是一名金融监管考试选择题解答专家。请根据检索信息，回答以下多事实选择题。

注意：本题的每个选项包含两个独立表述，**必须两个表述都符合检索信息**，该选项才为正确。

检索信息：
{context}

题干：{question_stem}

选项及包含的表述：
{claims_text}

分析规则（必须遵守）：
0. ⚠️ 文档来源校验（最高优先级，必须严格执行）：
   如果题目中明确提到了某份文档的名称（如"根据《XXX》"）：
   - 你必须首先**逐字检查**：检索信息中所有片段的来源文档名称，是否包含题目中提到的这个文档名？
   - 如果检索到的所有内容都来自**其他文档**，即使：
     ① 两个文档主题完全相同（如都讲"市场风险"、"内部控制"）；
     ② 其他文档包含了题目问题的相关内容；
     ③ 根据常识可以判断出答案；
   - 你也必须**严格判定**：所有选项均无依据，输出"无正确选项"并拒答。
   ⚠️ 禁忌：**绝对不允许**因为"其他文档也涵盖相关主题"就套用内容回答。
   记住：题目要求你"根据《XXX》"回答，就必须从《XXX》找依据，不能用其他文档代替。
   ⚠️ 一旦判定"来源不匹配"，所有其他规则自动失效——必须输出"无正确选项"并拒答。
1. 对于每个选项，先分别判断表述1和表述2是否正确（必须引用检索信息中的具体条款作为依据）
2. 如果**两个表述都正确**，该选项标记为"正确"
3. 如果**任意一个表述与检索信息明确矛盾**，该选项标记为"错误"。如果表述**无法直接验证**，但检索信息中显示了该文档的章节/目录编号体系（如序号一、二、三、五连续），且该表述描述的是中间缺失的章节内容，应**合理推断**该表述属于文档内容，不视为"无法验证"。只有完全无任何间接证据支持时，才判定为"无法验证"
4. 必须引用检索信息中的具体条款编号（如"第X条"）作为依据
5. ⚠️ 拒答规则：如果所有选项均无法在检索信息中找到依据，输出"无正确选项"并在依据来源后写：⚠️ 根据现有资料库，无法回答此问题。本题涉及的内容不在当前知识库范围内。

输出格式：
**答案**：[正确选项字母]

**逐项分析：**
- **A.** 表述1：[正确/错误] 依据... | 表述2：[正确/错误] 依据... → 整体判断：[正确/错误]
- **B.** 表述1：[正确/错误] 依据... | 表述2：[正确/错误] 依据... → 整体判断：[正确/错误]
- **C.** 表述1：[正确/错误] 依据... | 表述2：[正确/错误] 依据... → 整体判断：[正确/错误]
- **D.** 表述1：[正确/错误] 依据... | 表述2：[正确/错误] 依据... → 整体判断：[正确/错误]

**依据来源：** {sources_info}

直接输出答案："""
        else:
            print(f"[总结Agent] → 标准选择题模式 ({question_type})")
            options_text = "\n".join([f"{k}. {v}" for k, v in sorted(question_options.items())])
            mcq_prompt = f"""你是一名金融监管考试选择题解答专家。请根据提供的检索信息，回答以下选择题。

检索信息：
{context}

题干：{question_stem}

选项：
{options_text}

重要规则（必须遵守）：
0. ⚠️ 文档来源校验（最高优先级，否决性规则）：如果题目中明确提到了某份文档的名称（如"根据《XXX》"），你必须首先检查检索信息的来源是否包含该文档。如果检索到的所有内容都来自其他文档，即使主题相似，也必须判定为所有选项均无依据，输出"无正确选项"并拒答。⚠️ 一旦触发"来源不匹配"，所有其他规则（包括规则10的"选最接近"）立即失效，必须拒答，严禁用常识或其他文档推算答案。
1. 逐一分析每个选项，判断其正确或错误
2. 如果检索信息中没有任何条款、数据或原文支持或否定某个选项，则判定该选项为错误
3. 对于每个判断，必须引用检索信息中的具体条款编号（如"第X条"）、数据或原文作为依据
4. 如果多个选项都有检索信息支持，选择唯一正确的一个；如果所有选项均无依据支持，则输出"无正确选项"
5. 检索信息中每个数据都带有 Type 标签（premium/asset/sum_insured/claim）。当题目问"数值最高"时：只比较同一 Type 下的数值，严禁跨 Type 比较。如果选项涉及多个 Type，选择 premium 类型下最高的（经营情况表/保费收入表的核心指标是 premium 保费收入类）
6. ⚠️ 计算题规则：如果题目是"从X到Y的数值变化"，变化量 = Y的数值 − X的数值（后减前）。例如："从合计到健康险的变化" = 健康险 − 合计；"从一季度到四季度的变化" = 四季度 − 一季度
7. ⚠️ 季度表术语：如果题目提到"年-季度"但检索信息中只有"一季度/二季度/三季度/四季度"，"年-季度"指"一季度"（年度第一个季度），不是"四季度"
8. ⚠️ 季度表术语：如果题目提到"季度-季度"，且前面有"年-季度"，"季度-季度"指"四季度"（年度最后一个季度），不是其他季度
9. ⚠️ 银行业特殊术语：对于《银行业总资产、总负债》类表格，"年"指"银行业金融机构合计"（顶层汇总），"季度"指"商业银行合计"（子层级）。"年-季度"=银行业金融机构合计一季度，"季度-季度"=商业银行合计四季度。变化量=商业银行合计四季度−银行业金融机构合计一季度
10. ⚠️ 数值计算匹配规则：如果计算出的数值与所有选项都不完全一致，你必须选择差值最小的那个选项（即最接近的选项），并输出该选项字母。绝对不允许输出"无正确选项"。计算差值时取绝对值 |计算值 − 选项值|，选最小的。
11. ⚠️ 贷款类季度表术语："本年累计/截至当期"指期初（数值为0），不是四季度累计值。变化量 = 0 − 对应机构一季度值 = −（一季度值）。
12. ⚠️ 计算题兜底：如果按规则6计算的变化量不匹配任何选项，尝试以下备选方案（按顺序）：① 反向计算 X − Y；② 检查 −X 或 −Y 是否匹配选项（当Y=0或X=0时）；③ 检查 X 或 Y 本身是否匹配选项。取第一个匹配到选项的方案。
13. ⚠️ "与材料内容一致"类题目：当题目问"以下哪一项与材料内容一致"时，必须逐一检查每个选项的主题是否在检索信息中出现。如果选项主题（如"函证公示"）与检索信息章节（如"银行函证工作办理说明"）匹配，你必须仔细阅读该章节的完整内容，寻找支持或否定该选项的依据。严禁因为未找到逐字完全匹配的句子就判定"无依据"。检索信息的章节标题和内容主题匹配即可作为判断依据。
            14. ⚠️ 资料库外问题拒答规则：在逐项分析完所有选项后，你必须做一个总体判断：检索信息是否包含至少一个选项的明确依据？
                - 如果所有选项都无法在检索信息中找到任何依据，你必须输出答案"无正确选项"，并在依据来源后另起一行写：⚠️ 根据现有资料库，无法回答此问题。本题涉及的内容不在当前知识库范围内。
                - 如果至少有一个选项能在检索信息中找到依据，则正常输出答案。
                - 宁可明确拒答，也不要猜测或编造答案。
                - ⚠️ 特别注意：如果你已经按照规则0判断检索来源不匹配题目中提到的文档，则必须拒答，所有其他规则（包括规则10的近似匹配）全部失效，不要因为主题相似就套用其他文档的内容，不要用常识或其他数据推算答案。

输出格式：
**答案**：[正确选项字母或无正确选项]

**逐项分析：**
- [正确/错误] **A.** [分析及依据，必须引用具体条款编号]
- [正确/错误] **B.** [分析及依据，必须引用具体条款编号]
- [正确/错误] **C.** [分析及依据，必须引用具体条款编号]
- [正确/错误] **D.** [分析及依据，必须引用具体条款编号]

**依据来源：** {sources_info}

直接输出答案："""
         # 按需重试：提取答案字母，若为None则重新生成（最多3次）
        def _extract_letter(text: str) -> str | None:
            m = re.search(r'\*\*答案\*\*\s*[:：]\s*\*?([A-D])\*?', text, re.MULTILINE)
            return m.group(1) if m else None

        current_answer = llm.invoke(mcq_prompt).content
        answer_letter = _extract_letter(current_answer)

        is_ookb = _check_document_mismatch(user_query, context)
        if is_ookb and answer_letter is None and "无正确选项" in current_answer:
            print(f"[总结Agent] 检测到库外题，直接接受拒答")
            answer_letter = "X"

        max_retries = 2
        for retry in range(max_retries):
            if answer_letter is not None:
                break
            print(f"[总结Agent] 未能提取答案字母，重新生成 (第{retry+2}/3次)...")
            current_answer = llm.invoke(mcq_prompt).content
            answer_letter = _extract_letter(current_answer)

        if answer_letter:
            print(f"[总结Agent] 答案={answer_letter}")
        else:
            print(f"[总结Agent] ⚠ 3次均未能提取有效答案字母")

        final_pass = False
        for attempt in range(2):
            eval_result = _evaluate_mcq(user_query, context, current_answer)
            if eval_result.get("pass"):
                print(f"[总结Agent] 选择题第{attempt+1}次评估 \u2713 通过")
                final_pass = True
                break
            print(f"[总结Agent] 选择题第{attempt+1}次评估 \u2717 未通过: {eval_result.get('issues')}")
            # 检测是否因检索信息不足而失败
            issues_text = " ".join(eval_result.get("issues", []))
            if any(kw in issues_text for kw in ["检索信息不足以", "检索信息中未提供", "无直接依据", "无直接证据", "无法依据检索信息"]):
                print(f"[总结Agent] \u26a0 检索信息不足以确认正确答案，停止修正")
                final_pass = False
                break
             # 修正前记住原始字母
            orig_letter = _extract_letter(current_answer)
            current_answer = _refine_mcq(user_query, context, current_answer, eval_result.get("issues", []))

            # ==== 关键修复：修正后检查是否丢失了有效字母 ====
            refined_letter = _extract_letter(current_answer)
            if refined_letter is None and orig_letter is not None:
                print(f"[总结Agent] ⚠ 修正后答案丢失有效字母（原={orig_letter}），评估可能误判，从头重试生成...")
                current_answer = llm.invoke(mcq_prompt).content
                answer_letter = _extract_letter(current_answer)
                if answer_letter:
                    print(f"[总结Agent] 重试生成答案={answer_letter}")
                    continue  # 重新进入评估循环
                else:
                    print(f"[总结Agent] 重试仍无有效字母，接受当前输出")
                    break

        if not final_pass:
            issues_text = " ".join(eval_result.get("issues", []))
            insufficient_kw = ["检索信息不足以", "检索信息中未提供", "无直接依据", "无直接证据", "无法依据检索信息"]
            if any(kw in issues_text for kw in insufficient_kw):
                final_answer = f"""⚠️ **根据当前检索信息，无法完全确认正确答案。**

检索信息中缺少判断本题所需的完整条款内容，以下答案可能不准确。

{current_answer}

**建议：** 请对照《消费金融公司管理办法》原文核对相关条款。"""
            else:
                final_answer = current_answer
        else:
            final_answer = current_answer
        print(f"[总结Agent] 选择题最终答案: {final_answer}")
    else:
        print("[总结Agent] → 普通问答模式")
        missing_info = ""
        if retrieval_insufficient and missing_docs:
            missing_info = f"\n\n注意：以下文档未能成功检索到相关信息：{', '.join(missing_docs)}。请诚实说明无法回答这些文档相关的问题。"
        initial_prompt = f"""你是一名金融监管问答助手。请根据以下检索信息回答用户问题。

检索信息：
{context}{missing_info}

用户问题：{user_query}

要求：
- 在回答前，你必须先判断：检索信息中是否包含回答本题所需的核心事实或依据？
- ⚠️ 文档来源校验：如果题目中明确提到了某份文档的名称（如"根据《XXX》"），你必须先检查检索信息的来源文件是否包含该文档。如果检索到的所有内容都来自其他文档，即使主题相似，也必须拒答，输出：⚠️ 根据现有资料库，无法回答此问题。本题涉及的内容不在当前知识库范围内。
- 如果检索信息不包含任何与本题相关的依据，你必须输出：⚠️ 根据现有资料库，无法回答此问题。本题涉及的内容不在当前知识库范围内。宁可明确拒答，也不要猜测或编造答案。
- 如果检索信息包含相关依据，则正常回答，答案必须基于检索信息，不得编造。
- 如果用户问题中包含数值并要求计算，必须进行计算并给出结果。
- 如果检索信息中缺少回答某个子问题所需的内容，请在答案中明确说明"根据现有资料无法完全回答XX"。
- 每个答案块格式：
  答案来源：[文件名] 第X条/第X章
  答案内容：[具体答案]
- 禁止评价用户问题，禁止添加"正确"、"合理"等词语。

直接输出答案："""
        current_answer = llm.invoke(initial_prompt).content
        for attempt in range(2):
            eval_result = _evaluate_answer(user_query, context, current_answer)
            if eval_result.get("pass"):
                print(f"[总结Agent] 第{attempt+1}次评估 ✓ 通过")
                break
            print(f"[总结Agent] 第{attempt+1}次评估 ✗ 未通过: {eval_result.get('issues')}")
            current_answer = _refine_answer(user_query, context, current_answer, eval_result.get("issues", []))
        final_answer = current_answer
        print(f"[总结Agent] 答案已生成 ({len(final_answer)} 字)")

    # ---- 后处理：如果答案是JSON格式，转换为可读的markdown ----
    try:
        parsed = json.loads(final_answer.strip())
        if isinstance(parsed, dict) and "answer" in parsed and "analysis" in parsed:
            analysis_lines = []
            for opt_key in sorted(parsed["analysis"].keys()):
                opt_text = parsed["analysis"][opt_key]
                is_correct = opt_key == parsed["answer"]
                label = "正确" if is_correct else "错误"
                analysis_lines.append(f"- [{label}] **{opt_key}.** {opt_text}")
            formatted = f"**答案**：{parsed['answer']}\n\n**逐项分析：**\n" + "\n".join(analysis_lines)
            final_answer = formatted
            print(f"[总结Agent] JSON答案已转换为markdown格式")
    except (json.JSONDecodeError, Exception):
        pass  # 不是JSON，保持原样

    print("\n" + "="*60)
    print("  [总结Agent] 完成 → 最终答案已生成")
    print("="*60)

    # 后置守卫：OOKB 文档不匹配强制拒答，不影响原有正确流程
    # 条件：Planner 未匹配到任何文档 + 检索结果中找不到题目文档名
    target_docs = state.get("target_documents", [])
    if not target_docs:
        context_full = "\n\n".join(final_docs)
        if _check_document_mismatch(user_query, context_full):
            print(f"[总结Agent] 后置守卫：检测到库外题，强制拒答")
            refusal_msg = """**答案**：无正确选项

**逐项分析：**
- [错误] **A.** 检索信息中无目标文档相关依据，无法判断正确性。
- [错误] **B.** 检索信息中无目标文档相关依据，无法判断正确性。
- [错误] **C.** 检索信息中无目标文档相关依据，无法判断正确性。
- [错误] **D.** 检索信息中无目标文档相关依据，无法判断正确性。

**依据来源：** {sources_info}

⚠️ 根据现有资料库，无法回答此问题。本题涉及的内容不在当前知识库范围内。"""
            return {
                "messages": [AIMessage(content=refusal_msg)],
                "visited_agents": ["summarize"],
                "step_count": state.get("step_count", 0) + 1,
                "sources": sources
            }

    return {
        "messages": [AIMessage(content=final_answer)],
        "visited_agents": ["summarize"],
        "step_count": state.get("step_count", 0) + 1,
        "sources": sources
    }