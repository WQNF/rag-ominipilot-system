#!/usr/bin/env python3
"""
自动评测脚本：遍历测试集，调用 RAG 系统 API，生成评测报告。
赛题要求五项指标：
  1. 制度事实类问题准确率
  2. 表格取数类问题准确率
  3. 证据引用命中率
  4. 关键数字/日期/机构名称/文号错误率
  5. 资料库外问题拒答/澄清率

用法: python valuation/evaluate.py [--api-url http://localhost:8000]
"""

import os
import sys
import json
import re
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import requests
from langchain_core.messages import HumanMessage

# ============================================================
# 配置
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = os.path.join(PROJECT_ROOT, "data", "test_dataset")
OUTSIDE_TEST_DIR = os.path.join(PROJECT_ROOT, "data", "test_outside_dataset")
REPORT_DIR = os.path.join(PROJECT_ROOT, "valuation", "reports")
DEFAULT_API_URL = "http://localhost:8000"

# 直接调用 LangGraph 时使用；在 main() 中初始化，避免 API 可用时出现 NameError
_graph = None

# 测试集文件 → 分类（顺序重要：excel 在 word_add_excel 之前，确保 table 分类优先）
DATASET_FILES = [
    ("word_all.py", "regulation"),
    ("word_easy.py", "regulation"),
    ("word_middle.py", "regulation"),
    ("word_difficult.py", "regulation"),
    ("pdf_all.py", "regulation"),
    ("pdf_easy.py", "regulation"),
    ("pdf_middle.py", "regulation"),
    ("pdf_difficult.py", "regulation"),
    ("excel_all.py", "table"),
    ("excel_easy.py", "table"),
    ("excel_middle.py", "table"),
    ("excel_difficult.py", "table"),
    ("word_add_excel.py", "cross_file"),
]

# 库外测试集（存放在 test_outside_dataset 目录）
OUTSIDE_DATASET_FILES = [
    ("拒答测试集.py", "out_of_kb"),
]

# 拒答关键词
REFUSAL_KEYWORDS = [
    "无法回答", "没有找到", "未找到", "无相关", "资料不足",
    "无法提供", "暂无", "不能确定", "依据不足", "不在资料库",
    "无法判断", "缺乏依据", "暂无相关信息", "根据现有资料库",
]


# ============================================================
# 工具函数
# ============================================================
def load_all_questions(test_dir: str) -> List[Dict]:
    """加载所有测试集文件，去重合并

    去重规则:
    - 如果题目 ID 在 "all.py" 中已经存在，分分类文件中重复的跳过
    - 如果只过滤 category=table，但是 all.py 中没有 table 题，需要从 excel_*.py 加载
    """
    all_questions = []
    seen_ids = set()
    
    # 先加载 all.py，让它优先（all.py 包含所有题）
    fname_all = "all.py"
    fpath = os.path.join(test_dir, fname_all)
    if os.path.exists(fpath):
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        try:
            questions = eval(content)
            added = 0
            for q in questions:
                qid = q.get("id", "")
                if qid not in seen_ids:
                    seen_ids.add(qid)
                    q["_category"] = "regulation"
                    q["_source_file"] = fname_all
                    all_questions.append(q)
                    added += 1
            print(f"  ✅ {fname_all}: {added} 题 (分类: regulation)")
        except Exception as e:
            print(f"  ❌ 解析失败: {fname_all} — {e}")

    # 再加载分分类文件，如果 ID 不存在才添加
    for fname, category in DATASET_FILES:
        if fname == "all.py":
            continue
        fpath = os.path.join(test_dir, fname)
        if not os.path.exists(fpath):
            print(f"  ⚠️ 文件不存在，跳过: {fname}")
            continue

        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        try:
            questions = eval(content)
        except Exception:
            try:
                questions = json.loads(content)
            except Exception as e:
                print(f"  ❌ 解析失败: {fname} — {e}")
                continue

        added = 0
        for q in questions:
            qid = q.get("id", "")
            if qid in seen_ids:
                # 修正分类：只从默认 regulation 升级到具体分类，不覆盖已设置的具体分类
                for q_exist in all_questions:
                    if q_exist.get("id") == qid:
                        if q_exist["_category"] == "regulation" and category != "regulation":
                            q_exist["_category"] = category
                        break
                continue
            seen_ids.add(qid)
            q["_category"] = category
            q["_source_file"] = fname
            all_questions.append(q)
            added += 1
        if added > 0:
            print(f"  ✅ {fname}: {added} 题 (分类: {category})")
        else:
            print(f"  ⚠️ {fname}: 0 题 (全部去重)")

    return all_questions


def extract_answer_letter(reply: str) -> Optional[str]:
    """从 AI 回答中提取答案选项字母 — 与 test_word_Excel_pdf.py 的 extract_answer_from_text 一致"""
    if "无正确选项" in reply:
        return "X"
    patterns = [
        r'\*\*[^》]*?答案\*\*\s*[:：]\s*\*?([A-D])\*?',
        r'答案[:：]\s*\*?([A-D])\*?',
        r'[正确]确答案[是为：]\s*\*?([A-D])\*?',
        r'最终答案[:：]\s*\*?([A-D])\*?',
        r'选择\s*([A-D])\s*[。，]',
        r'选\s*([A-D])\s*$',
    ]
    for pattern in patterns:
        match = re.search(pattern, reply, re.MULTILINE)
        if match:
            return match.group(1)
    return None

def extract_numbers(text: str) -> List[str]:
    """提取文本中的关键数字（含小数点、千分位）"""
    numbers = re.findall(r"\d[\d,]*\.?\d*", text)
    return numbers


def extract_dates(text: str) -> List[str]:
    """提取文本中的日期"""
    dates = re.findall(r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月|\d{4}-\d{2}-\d{2}", text)
    return dates


def check_evidence_citation(reply: str, source_doc: str) -> bool:
    """检查回复中是否引用了正确的源文档

    分层匹配：
    1. 精确匹配：完整文件名、stem、去扩展名
    2. 核心名匹配：去掉"附件X："、年份、季度等修饰
    3. 关键词匹配：至少2个核心中文关键词同时出现
    """
    doc_name = Path(source_doc).stem

    # 第1层：精确匹配（保留原逻辑）
    patterns = [
        source_doc,
        doc_name,
        source_doc.replace(".docx", "").replace(".doc", "").replace(".pdf", "").replace(".xlsx", "").replace(".xls", ""),
    ]
    for pat in patterns:
        if pat and len(pat) > 3 and pat in reply:
            return True

    # 第2层：核心名匹配（去掉"附件X："、年份、季度等修饰）
    core = re.sub(r'^附件\d+[：:]\s*', '', doc_name)
    core = re.sub(r'[（(]\d{4}年?[）)]', '', core)
    core = re.sub(r'[（(](季度|月度|年度)[）)]', '', core)
    core = core.strip()
    core_no_date = re.sub(r'^\d{4}年(\d{1,2}月|[一二三四]季度)?', '', core).strip()

    core_patterns = list(dict.fromkeys([c for c in [core, core_no_date] if c and len(c) > 3]))
    for pat in core_patterns:
        if pat in reply:
            return True

    # 第3层：关键词片段匹配
    keywords = re.findall(r'[\u4e00-\u9fff]{2,}', core_no_date or core)
    keywords = sorted(set(keywords), key=len, reverse=True)
    if not keywords:
        return False

    matched = sum(1 for kw in keywords if kw in reply)
    if len(keywords) >= 2 and matched >= 2:
        return True
    if len(keywords) == 1 and matched >= 1:
        return True

    return False


def check_refusal(reply: str) -> bool:
    """检查回复是否为拒答"""
    for kw in REFUSAL_KEYWORDS:
        if kw in reply:
            return True
    return False


def check_key_numbers(reply: str, correct_option_text: str) -> Tuple[int, int]:
    """
    检查回复中关键数字的准确性。
    返回 (匹配数, 总数)
    """
    correct_nums = set(extract_numbers(correct_option_text))
    if not correct_nums:
        return 0, 0
    reply_nums = set(extract_numbers(reply))
    matched = correct_nums & reply_nums
    return len(matched), len(correct_nums)


def format_question_prompt(question: Dict) -> str:
    """构建完整问题文本 — 与 test_word_Excel_pdf.py 的 build_full_query 严格一致"""
    q_text = question.get("question", "")
    options = question.get("options", {})
    if not options:
        return q_text
    parts = [q_text]
    for letter in ["A", "B", "C", "D"]:
        if letter in options:
            parts.append(f"{letter.lower()}{options[letter]}")
    return "".join(parts)


# ============================================================
# 评测引擎
# ============================================================
def evaluate(api_url: str, questions: List[Dict], verbose: bool = True) -> Dict:
    """
    逐题评测，返回完整评测结果
    """
    results = []
    categories = defaultdict(lambda: {"total": 0, "correct": 0, "refusal": 0})
    stats = {
        "total": 0,
        "correct": 0,
        "wrong": 0,
        "refusal": 0,
        "evidence_cited": 0,
        "evidence_missed": 0,
        "number_total": 0,
        "number_matched": 0,
        "api_error": 0,
        "parse_failed": 0,
    }
    difficulties = defaultdict(lambda: {"total": 0, "correct": 0})

    total = len(questions)
    start_time = time.time()

    for i, q in enumerate(questions):
        qid = q.get("id", "?")
        difficulty = q.get("difficulty", "unknown")
        category = q.get("_category", "unknown")
        expected = q.get("answer", "")
        source_doc = q.get("source_document", "")

        prompt = format_question_prompt(q)

               # 直接调用 graph（与 test_word_Excel_pdf.py 一致，无 HTTP 超时风险）
        try:
            initial_state = {
                "messages": [HumanMessage(content=prompt)],
                "original_query": prompt,
                "next_agent": "planner",
                "task_completed": False,
                "visited_agents": [],
                "step_count": 0,
                "plan": [],
                "user_id": "eval_user",
                "department": "general",
            }
            graph = _graph
            if graph is None:
                if PROJECT_ROOT not in sys.path:
                    sys.path.insert(0, PROJECT_ROOT)
                from core.graph import graph as graph_obj
                graph = graph_obj
                globals()["_graph"] = graph
            result = graph.invoke(initial_state)
            reply = result["messages"][-1].content
        except Exception as e:
            stats["api_error"] += 1
            results.append({"id": qid, "status": "api_error", "expected": expected,
                            "actual": str(e)[:100], "correct": False, "difficulty": difficulty,
                            "category": category, "source_document": source_doc})
            if verbose:
                print(f"  [{i+1}/{total}] {qid} ❌ 连接失败: {e}")
            continue

        # 提取答案
        actual = extract_answer_letter(reply)
        if actual is None:
            stats["parse_failed"] += 1
            actual_letter = "?"
        else:
            actual_letter = actual

        is_correct = (actual_letter == expected)
        is_refusal = check_refusal(reply)
        # 库外题：answer="X"，正确条件是拒答或返回"无正确选项"
        if expected == "X":
            is_correct = is_refusal or (actual_letter == "X")
        is_evidence = check_evidence_citation(reply, source_doc)

        # 关键数字检查
        correct_option_text = q.get("options", {}).get(expected, "")
        num_matched, num_total = check_key_numbers(reply, correct_option_text)

        # 更新统计
        stats["total"] += 1
        categories[category]["total"] += 1
        difficulties[difficulty]["total"] += 1

        if is_correct:
            stats["correct"] += 1
            categories[category]["correct"] += 1
            difficulties[difficulty]["correct"] += 1
        else:
            stats["wrong"] += 1

        if is_refusal:
            stats["refusal"] += 1
            categories[category]["refusal"] += 1

        if is_evidence:
            stats["evidence_cited"] += 1
        else:
            stats["evidence_missed"] += 1

        stats["number_total"] += num_total
        stats["number_matched"] += num_matched

        result = {
            "id": qid,
            "status": "correct" if is_correct else "wrong",
            "expected": expected,
            "actual": actual_letter,
            "correct": is_correct,
            "difficulty": difficulty,
            "category": category,
            "source_document": source_doc,
            "evidence_cited": is_evidence,
            "is_refusal": is_refusal,
            "number_matched": num_matched,
            "number_total": num_total,
            "reply_preview": reply[:200],
            "reply": reply,
        }
        results.append(result)

        # 进度输出
        if verbose:
            elapsed = time.time() - start_time
            avg_time = elapsed / (i + 1)
            eta = avg_time * (total - i - 1)
            icon = "✅" if is_correct else "❌"
            print(f"  [{i+1}/{total}] {qid} {icon} "
                  f"期望={expected} 实际={actual_letter} "
                  f"| 证据={'✓' if is_evidence else '✗'} "
                  f"| 耗时={avg_time:.1f}s | ETA={eta:.0f}s")

    elapsed = time.time() - start_time
    return {
        "results": results,
        "stats": dict(stats),
        "categories": {k: dict(v) for k, v in categories.items()},
        "difficulties": {k: dict(v) for k, v in difficulties.items()},
        "elapsed_seconds": elapsed,
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================
# 报告生成
# ============================================================
def calc_rate(correct: int, total: int) -> float:
    return round(correct / total * 100, 2) if total > 0 else 0.0


def generate_report(eval_result: Dict) -> str:
    """生成 Markdown 评测报告"""
    stats = eval_result["stats"]
    cats = eval_result["categories"]
    diffs = eval_result["difficulties"]

    total = stats["total"]
    correct = stats["correct"]
    wrong = stats["wrong"]
    refusal = stats["refusal"]
    api_error = stats["api_error"]
    parse_failed = stats["parse_failed"]
    evidence_cited = stats["evidence_cited"]
    evidence_missed = stats["evidence_missed"]
    num_matched = stats["number_matched"]
    num_total = stats["number_total"]

    reg_total = cats.get("regulation", {}).get("total", 0)
    reg_correct = cats.get("regulation", {}).get("correct", 0)
    table_total = cats.get("table", {}).get("total", 0)
    table_correct = cats.get("table", {}).get("correct", 0)
    cross_total = cats.get("cross_file", {}).get("total", 0)
    cross_correct = cats.get("cross_file", {}).get("correct", 0)
    out_total = cats.get("out_of_kb", {}).get("total", 0)
    out_refusal = cats.get("out_of_kb", {}).get("refusal", 0)
    out_correct = cats.get("out_of_kb", {}).get("correct", 0)

    report = f"""# RAG 系统评测报告

> 生成时间: {eval_result['timestamp']}
> 总耗时: {eval_result['elapsed_seconds']:.1f} 秒
> 评测题目数: {total}

---

## 📊 赛题五项指标

| 指标 | 公式 | 数值 | 目标值 | 达标 |
|------|------|------|:------:|:----:|
| 制度事实类问题准确率 | {reg_correct}/{reg_total} | **{calc_rate(reg_correct, reg_total)}%** | ≥85% | {"✅" if calc_rate(reg_correct, reg_total) >= 85 else "❌"} |
| 表格取数类问题准确率 | {table_correct}/{table_total} | **{calc_rate(table_correct, table_total)}%** | ≥80% | {"✅" if calc_rate(table_correct, table_total) >= 80 else "❌"} |
| 证据引用命中率 | {evidence_cited}/{evidence_cited + evidence_missed} | **{calc_rate(evidence_cited, evidence_cited + evidence_missed)}%** | ≥90% | {"✅" if calc_rate(evidence_cited, evidence_cited + evidence_missed) >= 90 else "❌"} |
| 关键数字/日期/机构/文号错误率 | {num_total - num_matched}/{num_total} | **{calc_rate(num_total - num_matched, num_total)}%** | ≤5% | {"✅" if calc_rate(num_total - num_matched, num_total) <= 5 else "❌"} |
| 拒答/澄清率 (资料库外) | {out_refusal + out_correct}/{out_total} | **{calc_rate(out_refusal + out_correct, out_total)}%** | ≥80% | {"✅" if calc_rate(out_refusal + out_correct, out_total) >= 80 else "❌"} |

---

## 📈 总体统计

| 指标 | 数值 |
|------|------|
| 总题数 | {total} |
| 正确数 | {correct} |
| 错误数 | {wrong} |
| API 错误 | {api_error} |
| 解析失败 | {parse_failed} |
| **整体准确率** | **{calc_rate(correct, total)}%** |
| 拒答数 | {refusal} |
| 证据引用率 | {calc_rate(evidence_cited, evidence_cited + evidence_missed)}% |
| 数字匹配率 | {calc_rate(num_matched, num_total)}% |

---

## 📂 按分类统计

| 分类 | 总数 | 正确 | 准确率 | 拒答 |
|------|:----:|:----:|:------:|:----:|
| 制度事实 (regulation) | {reg_total} | {reg_correct} | {calc_rate(reg_correct, reg_total)}% | {cats.get('regulation', {}).get('refusal', 0)} |
| 表格取数 (table) | {table_total} | {table_correct} | {calc_rate(table_correct, table_total)}% | {cats.get('table', {}).get('refusal', 0)} |
| 跨文件 (cross_file) | {cross_total} | {cross_correct} | {calc_rate(cross_correct, cross_total)}% | {cats.get('cross_file', {}).get('refusal', 0)} |
| 资料库外 (out_of_kb) | {out_total} | {out_correct} | {calc_rate(out_correct, out_total)}% | {out_refusal} |

---

## 🎯 按难度统计

| 难度 | 总数 | 正确 | 准确率 |
|------|:----:|:----:|:------:|
"""

    for diff in ["easy", "medium", "hard"]:
        d = diffs.get(diff, {"total": 0, "correct": 0})
        if d["total"] > 0:
            report += f"| {diff} | {d['total']} | {d['correct']} | {calc_rate(d['correct'], d['total'])}% |\n"

    report += "\n---\n\n## ❌ 错误题目详情\n\n"
    errors = [r for r in eval_result["results"] if not r["correct"]]
    if errors:
        report += "| ID | 期望 | 实际 | 难度 | 分类 | 来源文档 | 证据 |\n"
        report += "|----|:----:|:----:|:----:|------|------|:----:|\n"
        for e in errors[:50]:
            evidence = "✓" if e.get("evidence_cited") else "✗"
            report += f"| {e['id']} | {e['expected']} | {e['actual']} | {e['difficulty']} | {e['category']} | {e['source_document'][:30]} | {evidence} |\n"
        if len(errors) > 50:
            report += f"\n> ... 共 {len(errors)} 题错误，仅展示前 50 题\n"

    report += "\n---\n\n*报告由 evaluate.py 自动生成*\n"
    return report


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="RAG 系统自动评测")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="API 服务地址")
    parser.add_argument("--sample", type=int, default=0, help="随机抽样 N 题（0=全部）")
    parser.add_argument("--category", default="all", choices=["all", "regulation", "table", "cross_file", "out_of_kb"],
                        help="仅评测指定分类")
    parser.add_argument("--direct", action="store_true", help="直接调用 LangGraph，不使用 API")
    parser.add_argument("--quiet", action="store_true", help="静默模式")
    args = parser.parse_args()

    os.makedirs(REPORT_DIR, exist_ok=True)

    # 1. 加载测试集
    print("=" * 60)
    print("📂 加载测试集...")
    print("=" * 60)
    questions = load_all_questions(TEST_DIR)

    # 加载库外测试集
    if args.category in ("all", "out_of_kb"):
        print("\n📂 加载库外测试集...")
        outside_questions = []
        for fname, category in OUTSIDE_DATASET_FILES:
            fpath = os.path.join(OUTSIDE_TEST_DIR, fname)
            if not os.path.exists(fpath):
                print(f"  ⚠️ 文件不存在，跳过: {fname}")
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            try:
                qs = eval(content)
                for q in qs:
                    q["_category"] = category
                    q["_source_file"] = fname
                outside_questions.extend(qs)
                print(f"  ✅ {fname}: {len(qs)} 题 (分类: {category})")
            except Exception as e:
                print(f"  ❌ 解析失败: {fname} — {e}")
        if args.category == "out_of_kb":
            questions = outside_questions
        else:
            questions.extend(outside_questions)
    print(f"\n📊 总计: {len(questions)} 题（去重后）")

    # 调试：打印分类分布
    cat_dist = defaultdict(int)
    for q in questions:
        cat_dist[q["_category"]] += 1
    print(f"📂 分类分布: {dict(cat_dist)}")
    if cat_dist.get("table", 0) == 0:
        print("🔍 前5题分类:", [(q["id"], q["_category"]) for q in questions[:5]])
        print("🔍 后5题分类:", [(q["id"], q["_category"]) for q in questions[-5:]])

    # 2. 过滤分类
    if args.category != "all":
        questions = [q for q in questions if q["_category"] == args.category]
        print(f"🔍 过滤后 ({args.category}): {len(questions)} 题")

    # 3. 抽样
    if args.sample > 0 and args.sample < len(questions):
        import random
        random.seed(42)
        questions = random.sample(questions, args.sample)
        print(f"🎲 随机抽样: {len(questions)} 题")

    # 4. 检查 API 是否可用
    if args.direct:
        print("\n🔧 使用直接 graph 调用模式")
        args.api_url = None
    else:
        print(f"\n🔗 检查 API: {args.api_url}")
        try:
            r = requests.get(f"{args.api_url}/docs", timeout=5)
        except Exception:
            print("  ⚠️ API 不可用，将直接调用 graph.invoke()")
            args.api_url = None

        if args.api_url:
            if r.status_code == 200:
                print("  ✅ API 连接正常")
            else:
                print(f"  ⚠️ API 返回状态码 {r.status_code}，将直接调用 graph")
                args.api_url = None

    if not args.api_url:
        print("  🔧 使用直接 graph 调用模式")
        sys.path.insert(0, PROJECT_ROOT)
        from core.graph import graph as app_graph
        global _graph
        _graph = app_graph

    # 5. 开始评测
    print(f"\n{'=' * 60}")
    print(f"🚀 开始评测 ({len(questions)} 题)")
    print(f"{'=' * 60}")
    eval_result = evaluate(args.api_url, questions, verbose=not args.quiet)

    # 6. 保存 JSON 结果
    json_path = os.path.join(REPORT_DIR, "evaluation_result.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(eval_result, f, ensure_ascii=False, indent=2)
    print(f"\n💾 JSON 结果已保存: {json_path}")

    # 7. 生成 Markdown 报告
    report = generate_report(eval_result)
    md_path = os.path.join(REPORT_DIR, "evaluation_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"📝 Markdown 报告已保存: {md_path}")

    # 8. 打印摘要
    stats = eval_result["stats"]
    cats = eval_result["categories"]
    print(f"\n{'=' * 60}")
    print("📊 评测摘要")
    print(f"{'=' * 60}")
    print(f"  总题数: {stats['total']}")
    print(f"  正确: {stats['correct']} | 错误: {stats['wrong']} | API错误: {stats['api_error']}")
    print(f"  整体准确率: {calc_rate(stats['correct'], stats['total'])}%")
    print(f"  制度事实准确率: {calc_rate(cats.get('regulation', {}).get('correct', 0), cats.get('regulation', {}).get('total', 1))}%")
    print(f"  表格取数准确率: {calc_rate(cats.get('table', {}).get('correct', 0), cats.get('table', {}).get('total', 1))}%")
    print(f"  证据引用率: {calc_rate(stats['evidence_cited'], stats['evidence_cited'] + stats['evidence_missed'])}%")
    print(f"  数字匹配率: {calc_rate(stats['number_matched'], stats['number_total'])}%")
    print(f"  总耗时: {eval_result['elapsed_seconds']:.1f}s")


if __name__ == "__main__":
    main()