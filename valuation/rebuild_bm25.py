# -*- coding: utf-8 -*-
"""重建 BM25 索引（jieba + 领域词典）。

做什么
------
1. 读现有 bm25_index.pkl（旧的 4 元组或新的 5 元组都支持），取出 doc_ids / 内容 / metadata；
2. 用 metadata 自动生成领域词典并落盘（bm25_domain_dict.txt）；
3. 用新 tokenizer 重新切词，重建 BM25Okapi，写入签名（版本/backend/词典 hash）；
4. 可选：在测试集上对比「旧正则分词」与「新 jieba 分词」的 BM25-only 文档级命中率。

用法
----
    pip install jieba
    python valuation/rebuild_bm25.py                    # 只重建，不做对比
    python valuation/rebuild_bm25.py --eval-q 80        # 重建并对比前 80 题（约 2-5 分钟）
    python valuation/rebuild_bm25.py --dry-run          # 只统计，不写文件

注意
----
- 重建会先把旧文件备份为 bm25_index.pkl.bak，可用 --no-backup 关闭；
- 全量重建后，服务侧重启即可自动加载（签名一致才启用）；
- 对比数字是「文档级」命中（gold 文件名是否出现在 top-k 的来源里），不是片段级。
"""
import os
import re
import sys
import json
import time
import shutil
import pickle
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rank_bm25 import BM25Okapi          # noqa: E402
from tools import bm25_tokenizer as T    # noqa: E402

RE_TOKEN = re.compile(r"[\u4e00-\u9fa5a-zA-Z0-9]+")


def load_old(path):
    with open(path, "rb") as f:
        payload = pickle.load(f)
    if len(payload) == 5:
        bm25, ids, contents, meta, sig = payload
    else:
        bm25, ids, contents, meta = payload
        sig = {"version": "regex-v0", "backend": "regex", "dict_hash": "none"}
    return bm25, ids, contents, meta, sig


def tok_regex(text):
    return RE_TOKEN.findall((text or "").lower())


def tokenize_corpus(contents, tokenize, label):
    t0 = time.time()
    out = []
    for i, c in enumerate(contents, 1):
        out.append(tokenize(c))
        if i % 20000 == 0:
            print(f"  [{label}] 已切 {i}/{len(contents)} 篇，用时 {time.time() - t0:.0f}s")
    print(f"  [{label}] 切词完成，用时 {time.time() - t0:.0f}s")
    return out


def doc_recall(bm25, tokenize, questions, names, ks=(1, 5, 10), progress=True):
    hits = {k: 0 for k in ks}
    t0 = time.time()
    for i, q in enumerate(questions, 1):
        gold = str(q.get("source_document", ""))
        scores = bm25.get_scores(tokenize(q["question"]))
        top = sorted(range(len(scores)), key=lambda j: scores[j], reverse=True)[:max(ks)]
        rank = 0
        for r, idx in enumerate(top, 1):
            n = names[idx]
            if gold and (gold in n or n in gold):
                rank = r
                break
        for k in ks:
            if 0 < rank <= k:
                hits[k] += 1
        if progress and i % 20 == 0:
            print(f"    评测 {i}/{len(questions)}，用时 {time.time() - t0:.0f}s")
    n = max(len(questions), 1)
    return {k: hits[k] for k in ks}, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkl", default="bm25_index.pkl")
    ap.add_argument("--questions", default=os.path.join("data", "test_dataset", "all.py"))
    ap.add_argument("--eval-q", type=int, default=0, help="对比前 N 题；0 表示跳过对比")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--allow-regex", action="store_true",
                    help="jieba 不可用时也继续（会重建出正则分词的索引，仅用于自测）")
    a = ap.parse_args()

    print(f"读取旧索引：{a.pkl}")
    old_bm25, ids, contents, meta, old_sig = load_old(a.pkl)
    print(f"  文档数 {len(contents)}；旧签名 {old_sig}")
    old_avgdl = getattr(old_bm25, "avgdl", 0.0)
    old_vocab = len(getattr(old_bm25, "idf", {}) or {})
    print(f"  旧 avgdl={old_avgdl:.2f} token/篇，词表 {old_vocab}")

    print("生成领域词典 …")
    words, stats = T.build_domain_dict(meta)
    detail = "、".join(f"{k}:{v}" for k, v in sorted(stats.items()))
    print(f"  词典 {len(words)} 词（{detail}）")
    if not a.dry_run:
        T.save_dict(words)
        print(f"  已写入 {T.DICT_PATH}")

    T.init(metadata=meta, rebuild_dict=True)
    new_sig = T.signature()
    print(f"  新签名 {new_sig}")

    if new_sig["backend"] != "jieba" and not a.allow_regex:
        print("\n[中止] 当前后端不是 jieba（未安装或被 BM25_TOKENIZER=regex 覆盖）。")
        print("       请先 `pip install jieba`，且确认环境变量 BM25_TOKENIZER 未设为 regex。")
        print("       若确实想用正则切分重建，加 --allow-regex。")
        sys.exit(2)

    t0 = time.time()
    new_corpus = tokenize_corpus(contents, T.tokenize, new_sig["backend"])
    new_avgdl = sum(len(d) for d in new_corpus) / max(len(new_corpus), 1)
    print(f"  新 avgdl={new_avgdl:.2f} token/篇（旧 {old_avgdl:.2f}），切词耗时 {time.time() - t0:.0f}s")

    names = [str(m.get("file_name", "")) for m in meta]

    if a.eval_q > 0 and os.path.exists(a.questions):
        questions = json.load(open(a.questions, encoding="utf-8"))[:a.eval_q]
        print(f"\n对比实验：{len(questions)} 题（BM25-only，文档级）")
        print("  评测旧索引（正则分词）…")
        old_hits, n = doc_recall(old_bm25, tok_regex, questions, names)
        print("  旧：", {f"R@{k}": f"{v}/{n}" for k, v in old_hits.items()})
        del old_bm25

        print("  构建新索引（jieba）…")
        new_bm25 = BM25Okapi(new_corpus)
        print("  评测新索引 …")
        new_hits, n2 = doc_recall(new_bm25, T.tokenize, questions, names)
        print("  新：", {f"R@{k}": f"{v}/{n2}" for k, v in new_hits.items()})
        print("\n  对比表：")
        for k in old_hits:
            print(f"    Recall@{k}: {old_hits[k]}/{n} → {new_hits[k]}/{n2}")
    else:
        new_bm25 = BM25Okapi(new_corpus)

    if a.dry_run:
        print("\n--dry-run：未写入任何文件。")
        return

    if os.path.exists(a.pkl) and not a.no_backup:
        bak = a.pkl + ".bak"
        shutil.copy2(a.pkl, bak)
        print(f"\n旧索引已备份到 {bak}")

    tmp = a.pkl + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump((new_bm25, ids, contents, meta, new_sig), f)
    os.replace(tmp, a.pkl)
    print(f"新索引已写入 {a.pkl}")
    print("重启后端服务即可加载（签名一致才会启用 BM25）。")


if __name__ == "__main__":
    main()
