#!/usr/bin/env python3
"""
300 题全流程评测脚本
用法: python valuation/test_300.py [--start N] [--end N] [--quiet]

输出指标：
  1. 官方 300 题准确率
  2. 分难度准确率 (easy/medium/hard)
  3. 分文档类型准确率 (Word/PDF/Excel)
  4. Recall@5
  5. MRR@10
  6. 检索失败题数 / 检索成功但选错题数
  7. 解析失败题数
  8. P50/P95 端到端延迟
  9. 首 token P50/P95 延迟
  10. 单题成本
"""

import os
import sys
import re
import time
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# ============================================================

# 路径 & 环境

# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage

# ============================================================

# 配置

# ============================================================
TEST_FILE = os.path.join(PROJECT_ROOT, "data", "test_dataset", "全部测试题目.py")
REPORT_DIR = os.path.join(PROJECT_ROOT, "valuation", "reports")

# DeepSeek 单价 (RMB/1M tokens) — 根据实际模型调整
TOKEN_PRICE_INPUT = 0.55   # ¥/百万 input tokens  (deepseek-v4-flash: ¥0.55)
TOKEN_PRICE_OUTPUT = 2.19  # ¥/百万 output tokens (deepseek-v4-flash: ¥2.19)

# ============================================================

# 工具函数

# ============================================================
def load_questions(filepath: str) -> List[Dict]:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    questions = eval(content)
    return questions

def classify_doc_type(source_doc: str) -> str:
    ext = Path(source_doc).suffix.lower()
    if ext in (".doc", ".docx"):
        return "Word"
    elif ext == ".pdf":
        return "PDF"
    elif ext in (".xls", ".xlsx"):
        return "Excel"
    return "Other"

def extract_answer_letter(reply: str) -> Optional[str]:
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

def normalize_filename(name: str) -> str:
    """去除扩展名、路径、多余空格，用于文件名模糊匹配"""
    name = Path(name).stem
    name = name.strip().lower()
    # 去掉 "附件X：" 前缀
    name = re.sub(r'^附件\d+[：:]?\s*', '', name)
    return name

def check_doc_match(gold_source: str, retrieved_files: List[str], top_k: int) -> Tuple[bool, int]:
    """
    检查 gold source_document 是否出现在 retrieved_files 的前 top_k 中
    返回 (是否匹配, 排名，1-indexed，未找到返回 0)
    """
    gold_norm = normalize_filename(gold_source)
    # 也准备带扩展名的版本
    gold_full = Path(gold_source).name.strip().lower()
    
    for rank, fname in enumerate(retrieved_files[:top_k], start=1):
        f_norm = normalize_filename(fname)
        # 精确匹配或包含匹配
        if gold_norm == f_norm or gold_norm in f_norm or f_norm in gold_norm:
            return True, rank
        if gold_full == fname or gold_full in fname:
            return True, rank
    return False, 0

# ============================================================

# LLM 追踪（Monkey-patch）

# ============================================================
_per_question_llm_calls = []        # 每题重置
_per_question_first_token_time = None

_original_llm_invoke = None

def _install_llm_tracker():
    global _original_llm_invoke
    from core.llm import llm
    _original_llm_invoke = llm.invoke

    def _tracked_invoke(*args, **kwargs):
        global _per_question_first_token_time
        start = time.perf_counter()
        result = _original_llm_invoke(*args, **kwargs)
        elapsed = time.perf_counter() - start
        tu = result.response_metadata.get("token_usage", {})
        _per_question_llm_calls.append({
            "elapsed": elapsed,
            "input_tokens": tu.get("prompt_tokens", 0),
            "output_tokens": tu.get("completion_tokens", 0),
        })
        if _per_question_first_token_time is None:
            _per_question_first_token_time = elapsed
        return result

    object.__setattr__(llm, 'invoke', _tracked_invoke)


def _reset_per_question():
    global _per_question_llm_calls, _per_question_first_token_time
    _per_question_llm_calls = []
    _per_question_first_token_time = None


def _uninstall_llm_tracker():
    global _original_llm_invoke
    if _original_llm_invoke:
        from core.llm import llm
        object.__setattr__(llm, 'invoke', _original_llm_invoke)
        _original_llm_invoke = None

# ============================================================

# 评测引擎

# ============================================================
def evaluate_all(questions: List[Dict], start_idx: int = 0, end_idx: int = None,
                 verbose: bool = True) -> Dict:
    from core.graph import graph

    if end_idx is None:
        end_idx = len(questions)
    questions = questions[start_idx:end_idx]
    total = len(questions)

    _install_llm_tracker()

    results = []
    # 聚合统计
    stats = {
        "total": 0, "correct": 0, "wrong": 0,
        "parse_failed": 0, "api_error": 0,
        "recall_hit": 0,       # gold doc 在 top5
        "recall_miss": 0,      # gold doc 不在 top5
        "retrieve_ok_wrong": 0,  # gold doc 在 top5 但答案错
        "mrr_sum": 0.0,
        "doc_type_stats": defaultdict(lambda: {"total": 0, "correct": 0}),
        "diff_stats": defaultdict(lambda: {"total": 0, "correct": 0}),
    }
    e2e_times = []
    first_token_times = []
    all_input_tokens = 0
    all_output_tokens = 0

    start_wall = time.time()

    for i, q in enumerate(questions):
        qid = q.get("id", "?")
        difficulty = q.get("difficulty", "unknown")
        expected = q.get("answer", "")
        source_doc = q.get("source_document", "")
        doc_type = classify_doc_type(source_doc)

        # 构建问题
        q_text = q.get("question", "")
        options = q.get("options", {})
        parts = [q_text]
        for letter in ["A", "B", "C", "D"]:
            if letter in options:
                parts.append(f"{letter}. {options[letter]}")
        prompt = "".join(parts)

        _reset_per_question()

        # 调用 graph
        try:
            t0 = time.perf_counter()
            result = graph.invoke({
                "messages": [HumanMessage(content=prompt)],
                "original_query": prompt,
                "next_agent": "planner",
                "task_completed": False,
                "visited_agents": [],
                "step_count": 0,
                "plan": [],
                "user_id": "eval_user",
                "department": "general",
            })
            e2e_time = time.perf_counter() - t0
            reply = result["messages"][-1].content
        except Exception as e:
            stats["api_error"] += 1
            results.append({
                "id": qid, "status": "api_error", "expected": expected,
                "actual": str(e)[:100], "correct": False,
                "difficulty": difficulty, "doc_type": doc_type,
                "source_document": source_doc, "e2e_time": 0,
            })
            if verbose:
                print(f"  [{i+1}/{total}] {qid} ❌ 异常: {e}")
            continue

        # 提取答案
        actual = extract_answer_letter(reply)
        parse_failed = actual is None
        actual_letter = actual if actual else "?"

        is_correct = (actual_letter == expected)

        # 检索分析
        sources = result.get("sources", [])
        retrieved_files = [s.get("file_name", "") for s in sources]
        recall_hit, gold_rank = check_doc_match(source_doc, retrieved_files, top_k=5)
        mrr_hit, mrr_rank = check_doc_match(source_doc, retrieved_files, top_k=10)
        mrr_value = 1.0 / mrr_rank if mrr_rank > 0 else 0.0

        # Token 统计
        q_input_tokens = sum(c["input_tokens"] for c in _per_question_llm_calls)
        q_output_tokens = sum(c["output_tokens"] for c in _per_question_llm_calls)
        q_first_token = _per_question_first_token_time or 0
        llm_calls_count = len(_per_question_llm_calls)

        # 更新统计
        stats["total"] += 1
        all_input_tokens += q_input_tokens
        all_output_tokens += q_output_tokens
        e2e_times.append(e2e_time)
        if q_first_token > 0:
            first_token_times.append(q_first_token)

        if is_correct:
            stats["correct"] += 1
        else:
            stats["wrong"] += 1

        if parse_failed:
            stats["parse_failed"] += 1

        if recall_hit:
            stats["recall_hit"] += 1
            if not is_correct:
                stats["retrieve_ok_wrong"] += 1
        else:
            stats["recall_miss"] += 1

        stats["mrr_sum"] += mrr_value

        stats["doc_type_stats"][doc_type]["total"] += 1
        if is_correct:
            stats["doc_type_stats"][doc_type]["correct"] += 1

        stats["diff_stats"][difficulty]["total"] += 1
        if is_correct:
            stats["diff_stats"][difficulty]["correct"] += 1

        results.append({
            "id": qid,
            "status": "correct" if is_correct else "wrong",
            "expected": expected,
            "actual": actual_letter,
            "correct": is_correct,
            "difficulty": difficulty,
            "doc_type": doc_type,
            "source_document": source_doc,
            "recall_hit": recall_hit,
            "gold_rank_top5": gold_rank,
            "mrr_rank": mrr_rank,
            "mrr_value": round(mrr_value, 4),
            "e2e_time": round(e2e_time, 3),
            "first_token_time": round(q_first_token, 3),
            "input_tokens": q_input_tokens,
            "output_tokens": q_output_tokens,
            "llm_calls": llm_calls_count,
            "retrieved_files": retrieved_files[:10],
            "reply_preview": reply[:200],
        })

        # 进度
        if verbose:
            elapsed = time.time() - start_wall
            avg_t = elapsed / (i + 1)
            eta = avg_t * (total - i - 1)
            icon = "✅" if is_correct else "❌"
            rc = "✓" if recall_hit else "✗"
            print(f"  [{i+1}/{total}] {qid} {icon} "
                  f"期望={expected} 实际={actual_letter} "
                  f"Recall@5={rc} | {e2e_time:.1f}s | ETA={eta:.0f}s")

    _uninstall_llm_tracker()

    elapsed = time.time() - start_wall
    return {
        "results": results,
        "stats": dict(stats),
        "e2e_times": e2e_times,
        "first_token_times": first_token_times,
        "all_input_tokens": all_input_tokens,
        "all_output_tokens": all_output_tokens,
        "elapsed_seconds": elapsed,
        "timestamp": datetime.now().isoformat(),
    }

# ============================================================

# 统计计算

# ============================================================
def percentile(sorted_values: List[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p / 100
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_values):
        return sorted_values[f] + c * (sorted_values[f + 1] - sorted_values[f])
    return sorted_values[f]

def calc_rate(num: int, den: int) -> float:
    return round(num / den * 100, 2) if den > 0 else 0.0

# ============================================================

# 报告生成

# ============================================================
def generate_report(eval_data: Dict) -> str:
    stats = eval_data["stats"]
    e2e_times = sorted(eval_data["e2e_times"])
    ft_times = sorted(eval_data["first_token_times"])
    total = stats["total"]
    correct = stats["correct"]
    wrong = stats["wrong"]
    parse_failed = stats["parse_failed"]
    api_error = stats["api_error"]
    recall_hit = stats["recall_hit"]
    recall_miss = stats["recall_miss"]
    retrieve_ok_wrong = stats["retrieve_ok_wrong"]
    mrr_sum = stats["mrr_sum"]
    all_input = eval_data["all_input_tokens"]
    all_output = eval_data["all_output_tokens"]

    # 成本
    cost_input = all_input / 1_000_000 * TOKEN_PRICE_INPUT
    cost_output = all_output / 1_000_000 * TOKEN_PRICE_OUTPUT
    cost_total = cost_input + cost_output
    cost_per_q = cost_total / total if total > 0 else 0

    report = f"""# 🧪 300 题全流程评测报告

> 生成时间: {eval_data['timestamp']}

> 总耗时: {eval_data['elapsed_seconds']:.1f}s ({eval_data['elapsed_seconds']/60:.1f}min)

> 评测题目数: {total}

---

## 📊 核心指标

| 指标 | 数值 |
|------|------|
| **官方 300 题准确率** | {correct}/{total} = **{calc_rate(correct, total)}%** |
| **解析失败题数** | {parse_failed} |
| **API 错误题数** | {api_error} |

---

## 🎯 分难度准确率

| 难度 | 总数 | 答对 | 准确率 |
|------|:----:|:----:|:------:|
"""

    for diff in ["easy", "medium", "hard"]:
        d = stats["diff_stats"].get(diff, {"total": 0, "correct": 0})
        icon = "✅" if calc_rate(d["correct"], d["total"]) >= 80 else "⚠️"
        report += f"| {diff} | {d['total']} | {d['correct']} | **{calc_rate(d['correct'], d['total'])}%** {icon} |\n"

    report += f"""
---

## 📄 分文档类型准确率

| 类型 | 总数 | 答对 | 准确率 |
|------|:----:|:----:|:------:|
"""

    for dtype in ["Word", "PDF", "Excel"]:
        d = stats["doc_type_stats"].get(dtype, {"total": 0, "correct": 0})
        icon = "✅" if calc_rate(d["correct"], d["total"]) >= 80 else "⚠️"
        report += f"| {dtype} | {d['total']} | {d['correct']} | **{calc_rate(d['correct'], d['total'])}%** {icon} |\n"

    report += f"""
---

## 🔍 检索指标

| 指标 | 数值 |
|------|------|
| **Recall@5** | {recall_hit}/{total} = **{calc_rate(recall_hit, total)}%** |
| **MRR@10** | **{round(mrr_sum / total, 4) if total > 0 else 0}** |
| **检索失败题数** (gold ∉ top5) | {recall_miss} |
| **检索成功但选错题数** (gold ∈ top5, answer ✗) | {retrieve_ok_wrong} |
| **解析失败题数** (无 A/B/C/D) | {parse_failed} |

---

## ⏱️ 延迟指标

| 指标 | 数值 |
|------|------|
| **P50 端到端延迟** | {percentile(e2e_times, 50):.2f}s |
| **P95 端到端延迟** | {percentile(e2e_times, 95):.2f}s |
| **首 token P50** | {percentile(ft_times, 50):.2f}s |
| **首 token P95** | {percentile(ft_times, 95):.2f}s |
| **平均端到端** | {sum(e2e_times)/len(e2e_times) if e2e_times else 0:.2f}s |

---

## 💰 成本分析

| 指标 | 数值 |
|------|------|
| 总 Input Tokens | {all_input:,} |
| 总 Output Tokens | {all_output:,} |
| Input 成本 (¥{TOKEN_PRICE_INPUT}/M) | ¥{cost_input:.4f} |
| Output 成本 (¥{TOKEN_PRICE_OUTPUT}/M) | ¥{cost_output:.4f} |
| **总成本** | **¥{cost_total:.4f}** |
| **单题成本** | **¥{cost_per_q:.6f}** |
| Token 单价模型 | Input ¥{TOKEN_PRICE_INPUT}/M · Output ¥{TOKEN_PRICE_OUTPUT}/M |

---

## ❌ 错误题目详情 (前 50 题)

| ID | 期望 | 实际 | 难度 | 类型 | Recall@5 | 来源文档 |
|----|:----:|:----:|:----:|:----:|:--------:|----------|
"""

    errors = [r for r in eval_data["results"] if not r["correct"]]
    for e in errors[:50]:
        rc_icon = "✓" if e.get("recall_hit") else "✗"
        report += f"| {e['id']} | {e['expected']} | {e['actual']} | {e['difficulty']} | {e['doc_type']} | {rc_icon} | {e['source_document'][:35]} |\n"

    if len(errors) > 50:
        report += f"\n> ... 共 {len(errors)} 题错误，仅展示前 50 题\n"

    report += "\n---\n\n*报告由 test_300.py 自动生成*\n"
    return report

# ============================================================

# 主入口

# ============================================================
def main():
    parser = argparse.ArgumentParser(description="300 题全流程评测")
    parser.add_argument("--start", type=int, default=0, help="起始题号 (0-indexed)")
    parser.add_argument("--end", type=int, default=None, help="结束题号 (exclusive)")
    parser.add_argument("--quiet", action="store_true", help="静默模式 (仅最终报告)")
    args = parser.parse_args()

    os.makedirs(REPORT_DIR, exist_ok=True)

    # 1. 加载
    print("=" * 60)
    print("📂 加载测试集...")
    questions = load_questions(TEST_FILE)
    print(f"✅ 加载完成: {len(questions)} 题")
    start_idx = max(0, args.start)
    end_idx = min(len(questions), args.end) if args.end else len(questions)
    print(f"📌 评测范围: Q[{start_idx+1}..{end_idx}] → {end_idx - start_idx} 题")

    # 2. 评测
    print(f"\n{'=' * 60}")
    print(f"🚀 开始评测 ({end_idx - start_idx} 题)")
    print(f"{'=' * 60}")
    eval_data = evaluate_all(questions, start_idx, end_idx, verbose=not args.quiet)

    # 3. 保存 JSON
    json_path = os.path.join(REPORT_DIR, "test_300_result.json")
    # 精简版 JSON (去掉 reply 全文本，只保留 preview)
    slim_results = []
    for r in eval_data["results"]:
        slim_results.append({k: v for k, v in r.items() if k != "reply"})
    save_data = {
        **{k: v for k, v in eval_data.items() if k != "results"},
        "results": slim_results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 JSON 结果: {json_path}")

    # 4. 生成报告
    report = generate_report(eval_data)
    md_path = os.path.join(REPORT_DIR, "test_300_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"📝 Markdown 报告: {md_path}")

    # 5. 打印摘要
    stats = eval_data["stats"]
    e2e = sorted(eval_data["e2e_times"])
    ft = sorted(eval_data["first_token_times"])
    total = stats["total"]
    all_in = eval_data["all_input_tokens"]
    all_out = eval_data["all_output_tokens"]
    cost = all_in / 1_000_000 * TOKEN_PRICE_INPUT + all_out / 1_000_000 * TOKEN_PRICE_OUTPUT

    print(f"\n{'=' * 60}")
    print("📊 评测摘要")
    print(f"{'=' * 60}")
    print(f"  准确率:        {stats['correct']}/{total} = {calc_rate(stats['correct'], total)}%")
    print(f"  Recall@5:      {stats['recall_hit']}/{total} = {calc_rate(stats['recall_hit'], total)}%")
    print(f"  MRR@10:        {round(stats['mrr_sum']/total, 4) if total > 0 else 0}")
    print(f"  检索失败:      {stats['recall_miss']} 题")
    print(f"  检索OK但答错:  {stats['retrieve_ok_wrong']} 题")
    print(f"  解析失败:      {stats['parse_failed']} 题")
    print(f"  P50/P95 端到端: {percentile(e2e, 50):.1f}s / {percentile(e2e, 95):.1f}s")
    print(f"  首 token P50/P95: {percentile(ft, 50):.1f}s / {percentile(ft, 95):.1f}s")
    print(f"  总 Token:      {all_in:,} in + {all_out:,} out")
    print(f"  总成本:        ¥{cost:.4f}")
    print(f"  单题成本:      ¥{cost/total:.6f}" if total > 0 else "")
    print(f"  总耗时:        {eval_data['elapsed_seconds']:.0f}s")

if __name__ == "__main__":
    main()