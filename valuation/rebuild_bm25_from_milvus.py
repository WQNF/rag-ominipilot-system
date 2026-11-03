# -*- coding: utf-8 -*-
"""从 Milvus 直接重建单字段 BM25 索引。

为什么需要它
------------
`init_milvus.py` 在每个分支结束时都会删除 `bm25_index.pkl`（强制下次重建），
所以 Milvus 重建完之后这个文件已经不存在了——`rebuild_bm25.py` 假设它存在，
会直接 FileNotFoundError。这个脚本改为从 Milvus 读全部干净正文再建索引。

用法：
    python valuation/rebuild_bm25_from_milvus.py
    python valuation/rebuild_bm25_from_milvus.py --out bm25_index.pkl
"""
import os
import sys
import time
import pickle
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rank_bm25 import BM25Okapi          # noqa: E402
from tools import bm25_tokenizer as T    # noqa: E402
from tools.retriever import get_retriever  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="bm25_index.pkl")
    ap.add_argument("--limit", type=int, default=0, help="只取前 N 条（自测用）")
    a = ap.parse_args()

    T.init(dict_path="bm25_domain_dict.txt")

    print("[1/3] 从 Milvus 读取全部文档…", flush=True)
    t0 = time.time()
    docs = get_retriever().get_all_documents()
    if a.limit:
        docs = docs[: a.limit]
    ids = [d["id"] for d in docs]
    contents = [d["content"] for d in docs]
    metas = [d["metadata"] for d in docs]
    print(f"      {len(docs)} 条，用时 {time.time() - t0:.0f}s", flush=True)

    # 顺带检查是否还有残留的 Source 前缀
    legacy = sum(1 for c in contents[:5000] if str(c).lstrip().startswith("[Source:"))
    print(f"      抽样 5000 条中仍带 [Source:] 前缀的：{legacy}", flush=True)

    print("[2/3] 切词…", flush=True)
    t0 = time.time()
    corpus = []
    for i, c in enumerate(contents, 1):
        corpus.append(T.tokenize(c))
        if i % 20000 == 0:
            print(f"      已切 {i}/{len(contents)}，{time.time() - t0:.0f}s", flush=True)
    avgdl = sum(len(d) for d in corpus) / max(len(corpus), 1)
    print(f"      切词完成，{time.time() - t0:.0f}s；avgdl={avgdl:.2f}", flush=True)

    print("[3/3] 建 BM25Okapi 并写盘…", flush=True)
    t0 = time.time()
    bm = BM25Okapi(corpus)
    sig = T.signature()
    tmp = a.out + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump((bm, ids, contents, metas, sig), f)
    os.replace(tmp, a.out)
    print(f"      完成，{time.time() - t0:.0f}s；词表 {len(bm.idf)}；"
          f"写入 {a.out}（{os.path.getsize(a.out)/1e6:.0f} MB）", flush=True)
    print(f"      signature={sig}", flush=True)


if __name__ == "__main__":
    main()
