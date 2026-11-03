# -*- coding: utf-8 -*-
"""只跑检索的评测：不调用 LLM，直接量 Recall@{1,5,10} 与 MRR@10。

为什么要单独做
--------------
`evaluate_all.py` 里的检索指标是**在 Planner 定位之后**算的：
题库 95% 的题引用了文档名，Planner 直接把范围锁到 1–3 份文档，
于是"金标准文档"几乎总是排第 1 —— 这就是为什么 MRR@10 恰好等于 Recall@5。
那组数字不能用来评价"检索能力"，只能用来评价"定位 + 靶向检索"。

这个脚本量的是**全库检索**：不加 file_name 过滤，向量 + BM25F 双路召回 →
RRF 融合 → rerank，全程不花钱（只有 embedding 与 rerank 的 API）。

用法：
    python valuation/eval_retrieval_only.py                  # 300 题，完整链路
    python valuation/eval_retrieval_only.py --no-rerank      # 看 rerank 的增量
    python valuation/eval_retrieval_only.py --q 100          # 只跑前 100 题
"""
import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                    # noqa: E402
from tools.embedder import embed_query                # noqa: E402
from tools.retriever import get_retriever             # noqa: E402
from tools.bm25_retriever import get_bm25_retriever   # noqa: E402
from tools.reranker import call_siliconflow_rerank    # noqa: E402
from agents.rag import reciprocal_rank_fusion         # noqa: E402
from tools.context_format import parse_header
from langchain_core.documents import Document         # noqa: E402


def gold_in(name, gold):
    if not gold or not name:
        return False
    return gold in name or name in gold


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", type=int, default=300)
    ap.add_argument("--topk", type=int, default=24, help="进入来源列表的片段数，默认与生产一致(24)")
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--tag", default="", help="结果文件后缀，便于留档")
    a = ap.parse_args()

    qs = json.load(open("data/test_dataset/all.py", encoding="utf-8"))[: a.q]
    vec = get_retriever()
    bm = get_bm25_retriever()

    hits = {1: 0, 5: 0, 10: 0}
    rr_sum = 0.0
    ranks = []
    t0 = time.time()

    for i, q in enumerate(qs, 1):
        gold = str(q.get("source_document", ""))
        query = q["question"] + " " + " ".join(str(v) for v in q.get("options", {}).values())

        emb = embed_query(q["question"])
        vdocs = vec.retrieve(emb, top_k=80, score_threshold=-1.0, nprobe=128)
        bres = bm.search(q["question"], top_k=80)

        # RRF 只吃顺序：把两路结果转成 [(key, score)]，key 用片段文本
        def uniq(seq):
            seen, out = set(), []
            for x in seq:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            return out

        v_keys = uniq([d.page_content for d in vdocs])
        b_keys = uniq([c for _, c, _ in bres])
        vec_pairs = [(k, 0.0) for k in v_keys]
        bm_pairs = [(k, 0.0) for k in b_keys]

        fused = reciprocal_rank_fusion([vec_pairs, bm_pairs], k=60)

        cand = {}
        for d in vdocs:
            cand.setdefault(d.page_content, d)
        # BM25 命中的片段在向量结果里可能不存在，必须自己构造 Document，
        # 否则 RRF 融合后这些片段会被过滤掉（第一版只统计到了"向量路"的原因）。
        for _, c, _ in bres:
            if c not in cand:
                meta = parse_header(c)
                cand[c] = Document(page_content=c, metadata=meta)

        ordered = [cand[k] for k in fused if cand.get(k) is not None][:120]

        if not a.no_rerank and ordered:
            texts = [d.page_content for d in ordered]
            reranked = call_siliconflow_rerank(query=query, documents=texts, top_n=a.topk)
            ordered = [ordered[r["index"]] for r in reranked if "index" in r] or ordered[: a.topk]
        else:
            ordered = ordered[: a.topk]

        names = [str(d.metadata.get("file_name", "")) for d in ordered]
        rank = 0
        for r, n in enumerate(names, 1):
            if gold_in(n, gold):
                rank = r
                break
        ranks.append(rank)
        for k in hits:
            hits[k] += 0 < rank <= k
        rr_sum += 1.0 / rank if 0 < rank <= 10 else 0.0

        if i % 25 == 0:
            eta = (time.time() - t0) / i * (len(qs) - i)
            print(f"  {i}/{len(qs)}  R@1={hits[1]} R@5={hits[5]} ETA={eta:.0f}s", flush=True)

    n = len(qs)
    out = {
        "questions": n,
        "rerank": not a.no_rerank,
        "recall@1": round(hits[1] / n, 4),
        "recall@5": round(hits[5] / n, 4),
        "recall@10": round(hits[10] / n, 4),
        "mrr@10": round(rr_sum / n, 4),
        "miss": sum(1 for r in ranks if r == 0),
        "elapsed_s": round(time.time() - t0),
    }
    print("\n=== 全库检索评测（无 LLM）===")
    print(json.dumps(out, ensure_ascii=False, indent=2))

    rep = os.path.join("valuation", "reports")
    os.makedirs(rep, exist_ok=True)
    fn = os.path.join(rep, f"retrieval_only{a.tag}.json")
    json.dump(out, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"写入 {fn}")


if __name__ == "__main__":
    main()
