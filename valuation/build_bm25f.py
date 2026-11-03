# -*- coding: utf-8 -*-
"""构建 BM25F 双字段索引（标题/元数据 + 正文），并做对比评测。

用法：
    python valuation/build_bm25f.py                 # 构建 + 300 题评测
    python valuation/build_bm25f.py --q 120         # 只跑 120 题
    python valuation/build_bm25f.py --grid          # 额外做 w_title × b_title 小网格
    python valuation/build_bm25f.py --no-save       # 只评估不写盘
    python valuation/build_bm25f.py --with-baselines # 额外重算 A/B 两路（慢）

产物：bm25_index_f.pkl（签名含 mode=bm25f-v1、字段权重、词典 hash）
"""
import os
import re
import sys
import json
import time
import pickle
import argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                       # noqa: E402
from tools import bm25_tokenizer as T    # noqa: E402
from tools.bm25f import BM25F            # noqa: E402

RE_TOKEN = re.compile(r"[\u4e00-\u9fa5a-zA-Z0-9]+")
PREFIX_RE = re.compile(r"\[Source:[^\]]*\]")
FIELD_RE = re.compile(r"\|\s*(Chapter|Clause|Section|File):[^|]*", re.I)
tok_regex = lambda s: RE_TOKEN.findall((s or "").lower())

TITLE_FIELDS = ("file_name", "chapter", "indicator", "authority")


def load_pkl(path):
    with open(path, "rb") as f:
        p = pickle.load(f)
    if len(p) == 5:
        return p
    bm, ids, contents, meta = p
    return bm, ids, contents, meta, {"version": "regex-v0", "backend": "regex"}


def title_text(meta):
    parts = [str(meta.get(k)) for k in TITLE_FIELDS if meta.get(k)]
    parts += [str(t) for t in (meta.get("tags") or [])]
    fn = re.sub(r"^\d+_", "", str(meta.get("file_name", "")))
    fn = re.sub(r"\.(docx?|pdf|xlsx?|xls)$", "", fn, flags=re.I)
    parts.append(fn)
    return " ".join(parts)


def body_text(content):
    s = PREFIX_RE.sub(" ", content or "")
    return FIELD_RE.sub(" ", s)


class FastSingle:
    """把 rank_bm25 的状态转成倒排打分器（评测用，公式一致）。"""

    def __init__(self, bm):
        self.idf, self.k1, self.b = bm.idf, bm.k1, bm.b
        self.doc_len = np.asarray(bm.doc_len, dtype=float)
        self.avgdl = bm.avgdl
        self.K = self.k1 * (1 - self.b + self.b * self.doc_len / self.avgdl)
        post = defaultdict(lambda: ([], []))
        for i, fr in enumerate(bm.doc_freqs):
            for t, f in fr.items():
                post[t][0].append(i)
                post[t][1].append(float(f))
        self.post = {t: (np.asarray(d, np.int32), np.asarray(f, float))
                     for t, (d, f) in post.items()}

    def get_scores(self, toks):
        s = np.zeros(len(self.doc_len))
        for t in toks:
            p = self.post.get(t)
            idf = self.idf.get(t, 0.0)
            if p is None or not idf:
                continue
            d, f = p
            s[d] += idf * f * (self.k1 + 1) / (f + self.K[d])
        return s


def evaluate(scorer_fn, tok, qs, names, ks=(1, 5, 10)):
    hits = {k: 0 for k in ks}
    for q in qs:
        gold = str(q.get("source_document", ""))
        scores = scorer_fn(tok(q["question"]))
        top = np.argsort(np.asarray(scores))[::-1][:max(ks)]
        rank = 0
        for r, i in enumerate(top, 1):
            n = names[int(i)]
            if gold and (gold in n or n in gold):
                rank = r
                break
        for k in ks:
            hits[k] += 0 < rank <= k
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", type=int, default=300)
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--with-baselines", action="store_true")
    ap.add_argument("--old", default="bm25_index.pkl.bak")
    ap.add_argument("--cur", default="bm25_index.pkl")
    ap.add_argument("--out", default="bm25_index_f.pkl")
    a = ap.parse_args()

    T.init(dict_path="bm25_domain_dict.txt")
    _, ids, contents, meta, cur_sig = load_pkl(a.cur)
    names = [str(m.get("file_name", "")) for m in meta]
    qs = json.load(open(os.path.join("data", "test_dataset", "all.py"), encoding="utf-8"))[:a.q]
    print(f"文档 {len(contents)}；题 {len(qs)}；当前单字段签名 {cur_sig.get('version')}", flush=True)

    def pairs():
        for m, c in zip(meta, contents):
            yield T.tokenize(title_text(m)), T.tokenize(body_text(c))

    t0 = time.time()
    f = BM25F.from_pairs(pairs(), verbose=True)
    print(f"  建索引总耗时 {time.time() - t0:.0f}s", flush=True)

    sig = {"version": "bm25f-v1", "backend": "jieba",
           "dict_hash": T.signature()["dict_hash"],
           "w_title": f.w_title, "w_body": f.w_body,
           "b_title": f.b_title, "b_body": f.b_body, "k1": f.k1}

    r_old = {1: 258, 5: 271, 10: 276}          # 2026-11-03 实测（旧正则单字段）
    r_cur = {1: 231, 5: 280, 10: 280}          # 同批次（jieba 单字段）

    if a.with_baselines:
        t0 = time.time()
        r_old = evaluate(FastSingle(load_pkl(a.old)[0]).get_scores, tok_regex, qs, names)
        print(f"  A 旧·正则单字段 {r_old} ({time.time()-t0:.0f}s)", flush=True)
        t0 = time.time()
        r_cur = evaluate(FastSingle(load_pkl(a.cur)[0]).get_scores, T.tokenize, qs, names)
        print(f"  B jieba·单字段 {r_cur} ({time.time()-t0:.0f}s)", flush=True)

    t0 = time.time()
    r_f = evaluate(f.get_scores, T.tokenize, qs, names)
    print(f"\n三路对比（文档级命中，n={len(qs)}）")
    print(f"  A 旧·正则单字段 : R@1 {r_old[1]:3d} R@5 {r_old[5]:3d} R@10 {r_old[10]:3d}")
    print(f"  B jieba·单字段  : R@1 {r_cur[1]:3d} R@5 {r_cur[5]:3d} R@10 {r_cur[10]:3d}")
    print(f"  C BM25F 双字段  : R@1 {r_f[1]:3d} R@5 {r_f[5]:3d} R@10 {r_f[10]:3d}  ({time.time()-t0:.0f}s)")

    if a.grid:
        print("\n权重小网格（只重算打分，不重建倒排）")
        for w in (1.0, 3.0, 6.0):
            for bt in (0.1, 0.3, 0.75):
                r = evaluate(lambda toks, w=w, bt=bt: f.get_scores(toks, w_title=w, b_title=bt),
                             T.tokenize, qs, names)
                print(f"  w_title={w:<4} b_title={bt:<5} → R@1 {r[1]:3d} R@5 {r[5]:3d} R@10 {r[10]:3d}")

    if a.no_save:
        print("\n--no-save：未写盘")
        return

    tmp = a.out + ".tmp"
    with open(tmp, "wb") as fp:
        pickle.dump((f, ids, contents, meta, sig), fp)
    os.replace(tmp, a.out)
    print(f"\n已写入 {a.out}（{os.path.getsize(a.out)/1e6:.0f} MB）；签名 {sig}")


if __name__ == "__main__":
    main()
