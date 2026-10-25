#!/usr/bin/env python3
"""
消融实验 B1 / B2（含 Planner 的 target_docs 过滤）
用法: python valuation/ablation_test.py --mode B1 --end 10

B1: 向量-only          Planner → 向量(过滤) → 答题
B2: 向量+BM25+RRF       Planner → 向量+BM25(过滤)→RRF/merge → 答题
B3: 全链路(现有)        Planner → 向量+BM25+用户知识库+Rerank+自评
"""
import os, sys, re, time, json, argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)
from dotenv import load_dotenv; load_dotenv()
from langchain_core.messages import HumanMessage

TEST_FILE = os.path.join(PROJECT_ROOT, "data", "test_dataset", "全部测试题目.py")
REPORT_DIR = os.path.join(PROJECT_ROOT, "valuation", "reports")
TOKEN_PRICE_INPUT = 0.55; TOKEN_PRICE_OUTPUT = 2.19

# ============================================================
# RRF (from agents/rag.py)
# ============================================================
def reciprocal_rank_fusion(results_list, k=60):
    fusion_scores = {}
    for results in results_list:
        for rank, (doc_id, _) in enumerate(results):
            fusion_scores[doc_id] = fusion_scores.get(doc_id, 0) + 1 / (k + rank + 1)
    return [doc_id for doc_id, _ in sorted(fusion_scores.items(), key=lambda x: x[1], reverse=True)]

# ============================================================
# 工具函数
# ============================================================
def load_questions(fp): return eval(open(fp, "r", encoding="utf-8").read())

def classify_doc_type(sd):
    ext = Path(sd).suffix.lower()
    if ext in (".doc", ".docx"): return "Word"
    if ext == ".pdf": return "PDF"
    if ext in (".xls", ".xlsx"): return "Excel"
    return "Other"

def extract_answer_letter(reply):
    if "无正确选项" in reply: return "X"
    for p in [r'\*\*[^》]*?答案\*\*\s*[:：]\s*\*?([A-D])\*?',
              r'答案[:：]\s*\*?([A-D])\*?',
              r'[正确]确答案[是为：]\s*\*?([A-D])\*?',
              r'最终答案[:：]\s*\*?([A-D])\*?',
              r'选择\s*([A-D])\s*[。，]', r'选\s*([A-D])\s*$']:
        m = re.search(p, reply, re.MULTILINE)
        if m: return m.group(1)
    return None

def normalize_filename(name):
    name = Path(name).stem.strip().lower()
    return re.sub(r'^附件\d+[：:]?\s*', '', name)

def check_doc_match(gold_source, retrieved_files, top_k):
    gn = normalize_filename(gold_source)
    gf = Path(gold_source).name.strip().lower()
    for rank, fn in enumerate(retrieved_files[:top_k], start=1):
        fn2 = normalize_filename(fn)
        if gn == fn2 or gn in fn2 or fn2 in gn: return True, rank
        if gf == fn or gf in fn: return True, rank
    return False, 0

def extract_sources(final_docs):
    sources, file_names = [], []
    for text in final_docs:
        sm = re.search(r'$$Source:\s*([^$$]+)\]', text)
        fn = sm.group(1) if sm else "未知文件"
        cm = re.search(r'Chapter:\s*([^|\n]+)', text)
        clm = re.search(r'Clause:\s*([^|\n]+)', text)
        section = (clm.group(1).strip() if clm else (cm.group(1).strip() if cm else ""))
        sources.append({"file_name": fn, "section": section, "full_text": text})
        if fn not in file_names: file_names.append(fn)
    return sources, file_names

# ============================================================
# LLM 追踪
# ============================================================
_q_llm_calls = []; _q_first_token = None; _orig_invoke = None
def _install_tracker():
    global _orig_invoke
    from core.llm import llm
    _orig_invoke = llm.invoke
    def _tracked(*a, **kw):
        global _q_first_token
        t0 = time.perf_counter()
        r = _orig_invoke(*a, **kw)
        dt = time.perf_counter() - t0
        tu = r.response_metadata.get("token_usage", {})
        _q_llm_calls.append({"elapsed": dt, "input_tokens": tu.get("prompt_tokens", 0),
                             "output_tokens": tu.get("completion_tokens", 0)})
        if _q_first_token is None: _q_first_token = dt
        return r
    object.__setattr__(llm, 'invoke', _tracked)

def _reset_q():
    global _q_llm_calls, _q_first_token
    _q_llm_calls = []; _q_first_token = None

def _uninstall_tracker():
    global _orig_invoke
    if _orig_invoke:
        from core.llm import llm
        object.__setattr__(llm, 'invoke', _orig_invoke)
        _orig_invoke = None

# ============================================================
# 答题 prompt (from agents/summarize.py _answer_mcq — 无自评)
# ============================================================
def answer_mcq(question, context, sources_info):
    from core.llm import llm
    prompt = f"""你是一名金融监管考试选择题解答专家。请根据提供的检索信息，回答以下选择题。

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
    return llm.invoke(prompt).content

# ============================================================
# 检索 — B1: 向量-only（复用 rag_agent 架构，剥离 BM25/用户知识库/rerank）
# ============================================================
def retrieve_B1(search_query, target_docs, user_msg, parsed_question, user_id="eval_user"):
    from tools.retriever import get_retriever
    from tools.embedder import embed_query

    retriever = get_retriever()
    query_embedding = embed_query(search_query)
    multi_fact = parsed_question.get("_multi_fact", False)
    top_n = 24 if multi_fact else 20

    if target_docs:
        # === 有 target_docs：逐文档过滤检索（同 rag_agent 主体逻辑） ===
        all_cands = []
        for doc_name in target_docs:
            expr = f'file_name like "%{doc_name}%"'
            docs = retriever.retrieve(query_embedding, top_k=80, score_threshold=-1.0, expr=expr)
            all_cands.extend([d.page_content for d in docs])
            print(f"  [B1] 文档「{doc_name[:30]}」→ 向量 {len(docs)} 个")

        # 去重
        seen = set(); unique = []
        for c in all_cands:
            key = c[:200]
            if key not in seen: seen.add(key); unique.append(c)

        final_docs = unique[:top_n]
        print(f"  [B1] 去重后 {len(unique)} → top {len(final_docs)}")
    else:
        # === 无 target_docs：全局向量检索 ===
        docs = retriever.retrieve(query_embedding, top_k=top_n, score_threshold=-1.0)
        final_docs = [d.page_content for d in docs]
        print(f"  [B1] 全局向量 {len(final_docs)} 个")

    sources, file_names = extract_sources(final_docs)
    context = "\n\n".join(final_docs)
    sources_info = "；".join([f"{s['file_name']} {s['section']}" for s in sources])
    return context, sources_info, sources, file_names

# ============================================================
# 检索 — B2: 向量+BM25+RRF（复用 rag_agent 架构，剥离 用户知识库/rerank）
# ============================================================
def retrieve_B2(search_query, target_docs, user_msg, parsed_question, user_id="eval_user"):
    from tools.retriever import get_retriever
    from tools.embedder import embed_query
    from tools.bm25_retriever import get_bm25_retriever

    retriever = get_retriever()
    bm25_retriever = get_bm25_retriever()
    query_embedding = embed_query(search_query)
    multi_fact = parsed_question.get("_multi_fact", False)
    top_n = 24 if multi_fact else 20

    if target_docs:
        # === 有 target_docs：逐文档 向量+BM25 → merge → dedup (同 rag_agent) ===
        all_cands = []
        for doc_name in target_docs:
            expr = f'file_name like "%{doc_name}%"'
            vec_docs = retriever.retrieve(query_embedding, top_k=80, score_threshold=-1.0, expr=expr)
            bm25_raw = bm25_retriever.search(query=search_query, top_k=80, filter_expr=expr)

            doc_map = {}
            for idx, doc in enumerate(vec_docs):
                doc_map[f"vec_{doc_name}_{idx}"] = doc.page_content
            for doc_id, content, _ in bm25_raw:
                if doc_id not in doc_map: doc_map[doc_id] = content

            all_cands.extend(doc_map.values())
            print(f"  [B2] 文档「{doc_name[:30]}」→ 向量{len(vec_docs)}+BM25{len(bm25_raw)} → 合并{len(doc_map)}")

        seen = set(); unique = []
        for c in all_cands:
            key = c[:200]
            if key not in seen: seen.add(key); unique.append(c)
        final_docs = unique[:top_n]
        print(f"  [B2] 去重 {len(all_cands)}→{len(unique)} → top {len(final_docs)}")
    else:
        # === 无 target_docs：全局 向量+BM25 → RRF (同 rag_agent 无 target 路径) ===
        vec_docs = retriever.retrieve(query_embedding, top_k=100, score_threshold=-1.0)
        vec_results, doc_map = [], {}
        for idx, doc in enumerate(vec_docs):
            did = f"vec_{idx}"
            vec_results.append((did, doc.metadata.get("score", 0.0)))
            doc_map[did] = doc.page_content

        bm25_raw = bm25_retriever.search(query=search_query, top_k=100)
        bm25_results = []
        for doc_id, content, score in bm25_raw:
            bm25_results.append((doc_id, score))
            if doc_id not in doc_map: doc_map[doc_id] = content

        fused_ids = reciprocal_rank_fusion([vec_results, bm25_results], k=60)
        final_docs = [doc_map[did] for did in fused_ids[:top_n] if did in doc_map]
        print(f"  [B2] 全局 向量{len(vec_docs)}+BM25{len(bm25_raw)} → RRF top {len(final_docs)}")

    sources, file_names = extract_sources(final_docs)
    context = "\n\n".join(final_docs)
    sources_info = "；".join([f"{s['file_name']} {s['section']}" for s in sources])
    return context, sources_info, sources, file_names

# ============================================================
# 评测引擎
# ============================================================
def evaluate_ablation(questions, mode, start_idx=0, end_idx=None, verbose=True):
    from agents.planner import planner_node

    if end_idx is None: end_idx = len(questions)
    questions = questions[start_idx:end_idx]
    total = len(questions)

    _install_tracker()
    results = []
    stats = {"total":0, "correct":0, "wrong":0, "parse_failed":0, "api_error":0,
             "recall_hit":0, "recall_miss":0, "retrieve_ok_wrong":0, "mrr_sum":0.0,
             "doc_type_stats": defaultdict(lambda: {"total":0, "correct":0}),
             "diff_stats": defaultdict(lambda: {"total":0, "correct":0})}
    e2e_times, ft_times = [], []
    all_in, all_out = 0, 0
    t_wall = time.time()

    for i, q in enumerate(questions):
        qid = q.get("id","?"); diff = q.get("difficulty","unknown")
        expected = q.get("answer",""); source_doc = q.get("source_document","")
        doc_type = classify_doc_type(source_doc)

        q_text = q.get("question",""); options = q.get("options",{})
        parts = [q_text]
        for lt in ["A","B","C","D"]:
            if lt in options: parts.append(f"{lt}. {options[lt]}")
        full_q = "".join(parts)

        _reset_q()
        try:
            t0 = time.perf_counter()

            # 1. Planner
            planner_state = {
                "messages": [HumanMessage(content=full_q)],
                "original_query": full_q,
                "plan": [], "step_count": 0,
                "user_id": "eval_user", "department": "general",
            }
            planner_result = planner_node(planner_state)
            target_docs = list(planner_result.get("target_documents", []) or [])
            parsed_question = planner_result.get("parsed_question", {})
            parsed_question["_multi_fact"] = planner_result.get("multi_fact", False)
            search_query = parsed_question.get("stem", full_q)

            # 2. 检索 (B1 或 B2)
            if mode == "B1":
                context, sources_info, sources, file_names = retrieve_B1(
                    search_query, target_docs, full_q, parsed_question)
            else:
                context, sources_info, sources, file_names = retrieve_B2(
                    search_query, target_docs, full_q, parsed_question)

            # 3. 答题
            answer = answer_mcq(full_q, context, sources_info)
            e2e_time = time.perf_counter() - t0

        except Exception as e:
            stats["api_error"] += 1
            results.append({"id":qid,"status":"api_error","expected":expected,
                            "actual":str(e)[:100],"correct":False,
                            "difficulty":diff,"doc_type":doc_type,
                            "source_document":source_doc,"e2e_time":0})
            if verbose: print(f"  [{i+1}/{total}] {qid} ❌ 异常: {e}")
            continue

        actual = extract_answer_letter(answer)
        pf = actual is None
        actual_l = actual if actual else "?"
        is_correct = (actual_l == expected)

        rh, gr = check_doc_match(source_doc, file_names, 5)
        _, mr = check_doc_match(source_doc, file_names, 10)
        mrv = 1.0 / mr if mr > 0 else 0.0

        q_in = sum(c["input_tokens"] for c in _q_llm_calls)
        q_out = sum(c["output_tokens"] for c in _q_llm_calls)
        q_ft = _q_first_token or 0

        stats["total"] += 1; all_in += q_in; all_out += q_out
        e2e_times.append(e2e_time)
        if q_ft > 0: ft_times.append(q_ft)
        if is_correct: stats["correct"] += 1
        else: stats["wrong"] += 1
        if pf: stats["parse_failed"] += 1
        if rh:
            stats["recall_hit"] += 1
            if not is_correct: stats["retrieve_ok_wrong"] += 1
        else: stats["recall_miss"] += 1
        stats["mrr_sum"] += mrv
        stats["doc_type_stats"][doc_type]["total"] += 1
        if is_correct: stats["doc_type_stats"][doc_type]["correct"] += 1
        stats["diff_stats"][diff]["total"] += 1
        if is_correct: stats["diff_stats"][diff]["correct"] += 1

        results.append({
            "id":qid,"status":"correct" if is_correct else "wrong",
            "expected":expected,"actual":actual_l,"correct":is_correct,
            "difficulty":diff,"doc_type":doc_type,"source_document":source_doc,
            "recall_hit":rh,"gold_rank_top5":gr,"mrr_rank":mr,"mrr_value":round(mrv,4),
            "e2e_time":round(e2e_time,3),"first_token_time":round(q_ft,3),
            "input_tokens":q_in,"output_tokens":q_out,
            "retrieved_files":file_names[:10],"answer_preview":answer[:200],
        })

        if verbose:
            et = time.time()-t_wall; avg=et/(i+1); eta=avg*(total-i-1)
            print(f"  [{i+1}/{total}] {qid} {'✅' if is_correct else '❌'} "
                  f"期望={expected} 实际={actual_l} "
                  f"R@5={'✓' if rh else '✗'} | {e2e_time:.1f}s | ETA={eta:.0f}s")

    _uninstall_tracker()
    return {"mode":mode,"results":results,"stats":dict(stats),
            "e2e_times":e2e_times,"first_token_times":ft_times,
            "all_input_tokens":all_in,"all_output_tokens":all_out,
            "elapsed_seconds":time.time()-t_wall,"timestamp":datetime.now().isoformat()}

# ============================================================
# 统计 & 主函数
# ============================================================
def percentile(vals, p):
    if not vals: return 0
    k = (len(vals)-1)*p/100; f=int(k); c=k-f
    return vals[f]+c*(vals[f+1]-vals[f]) if f+1<len(vals) else vals[f]

def calc_rate(n,d): return round(n/d*100,2) if d else 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["B1","B2"], required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    os.makedirs(REPORT_DIR, exist_ok=True)
    label = {"B1":"向量-only","B2":"向量+BM25+RRF"}[args.mode]
    print(f"{'='*60}\n🧪 消融实验 {args.mode}: {label}\n{'='*60}")

    qs = load_questions(TEST_FILE)
    si = max(0, args.start); ei = min(len(qs), args.end) if args.end else len(qs)
    print(f"📌 Q[{si+1}..{ei}] → {ei-si} 题\n")

    ed = evaluate_ablation(qs, args.mode, si, ei, verbose=not args.quiet)
    s = ed["stats"]; e2e=sorted(ed["e2e_times"]); ft=sorted(ed["first_token_times"])
    T=s["total"]; C=s["correct"]; ai=ed["all_input_tokens"]; ao=ed["all_output_tokens"]
    cost = ai/1e6*TOKEN_PRICE_INPUT + ao/1e6*TOKEN_PRICE_OUTPUT

    jp = os.path.join(REPORT_DIR, f"ablation_{args.mode}_result.json")
    slim = {k:v for k,v in ed.items() if k!="results"}
    slim["results"] = [{k:v for k,v in r.items() if k!="reply"} for r in ed["results"]]
    json.dump(slim, open(jp,"w",encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"📊 {args.mode} ({label}) 报告")
    print(f"{'='*60}")
    print(f"  准确率:        {C}/{T} = {calc_rate(C,T)}%")
    print(f"  Recall@5:      {s['recall_hit']}/{T} = {calc_rate(s['recall_hit'],T)}%")
    print(f"  MRR@10:        {round(s['mrr_sum']/T,4) if T else 0}")
    print(f"  检索失败:      {s['recall_miss']}")
    print(f"  检索OK但答错:  {s['retrieve_ok_wrong']}")
    print(f"  解析失败:      {s['parse_failed']}")
    print(f"  P50/P95 E2E:   {percentile(e2e,50):.1f}s / {percentile(e2e,95):.1f}s")
    print(f"  首token P50/95:{percentile(ft,50):.1f}s / {percentile(ft,95):.1f}s")
    for d in ["easy","medium","hard"]:
        dt=s["diff_stats"].get(d,{"total":0,"correct":0})
        print(f"  {d}: {dt['correct']}/{dt['total']} = {calc_rate(dt['correct'],dt['total'])}%")
    for dt in ["Word","PDF","Excel"]:
        d=s["doc_type_stats"].get(dt,{"total":0,"correct":0})
        print(f"  {dt}: {d['correct']}/{d['total']} = {calc_rate(d['correct'],d['total'])}%")
    print(f"  Token: {ai:,}in + {ao:,}out | 成本 ¥{cost:.4f} | 单题 ¥{cost/T:.6f}")
    print(f"\n💾 JSON: {jp}")

if __name__ == "__main__": main()