# -*- coding: utf-8 -*-
"""BM25F：双字段（标题/元数据 + 正文）加权打分。

为什么要有它
------------
旧实现把整段 chunk 正文（含 `[Source: 文件名]` 前缀）当成一个字段，
于是"标题"这个强信号在正文里被算了一次；而题目里 95% 引用文档名，
导致 BM25 实际退化成"标题精确匹配器"，@1 很高但 @5/@10 一般。

BM25F 的做法：把字段拆开，各自做长度归一化、乘字段权重合并成加权词频，
**最后只饱和一次**（这是 BM25F 与"两个 BM25 分数相加"的本质区别）。

    tf̃(t,D) = Σ_f  w_f · tf_f(t,D) / (1 − b_f + b_f · len_f(D)/avglen_f)
    score   = Σ_t  IDF(t) · tf̃·(k1+1) / (k1 + tf̃)

    IDF(t)  = ln(1 + (N − df_union(t) + 0.5) / (df_union(t) + 0.5))

实现要点
--------
- 流式构建：逐个文档切词、写倒排、丢语料，内存只与倒排表成正比；
- 倒排用 array('i')/array('f') 紧凑存储（11 万块约百 MB 量级）；
- 参数（w/b/k1）可在打分时覆盖，便于做权重网格而不重建索引。
"""
import math
import numpy as np
from array import array
from collections import defaultdict

MODE = "bm25f-v1"


class BM25F:
    def __init__(self, k1=1.5, b_title=0.3, b_body=0.75, w_title=3.0, w_body=1.0,
                 verbose=True):
        self.k1, self.b_title, self.b_body = k1, b_title, b_body
        self.w_title, self.w_body = w_title, w_body
        self.mode = MODE
        self.N = 0
        self.len_title = None
        self.len_body = None
        self.avg_title = 1.0
        self.avg_body = 1.0
        self.post_title = {}
        self.post_body = {}
        self.idf = {}
        self.verbose = verbose

    # ---------- 构建 ----------

    @classmethod
    def from_pairs(cls, pairs, **kw):
        """pairs: 可迭代的 (title_tokens, body_tokens)；流式，不保留语料。"""
        self = cls(**kw)
        df_union = defaultdict(int)
        post_t = defaultdict(lambda: (array("i"), array("f")))
        post_b = defaultdict(lambda: (array("i"), array("f")))
        len_t, len_b = array("i"), array("i")

        n = 0
        for i, (tt, bt) in enumerate(pairs):
            n = i + 1
            len_t.append(len(tt))
            len_b.append(len(bt))
            terms = set()
            if tt:
                freq = {}
                for w in tt:
                    freq[w] = freq.get(w, 0) + 1
                for w, f in freq.items():
                    ids, tfs = post_t[w]
                    ids.append(i)
                    tfs.append(float(f))
                    terms.add(w)
            if bt:
                freq = {}
                for w in bt:
                    freq[w] = freq.get(w, 0) + 1
                for w, f in freq.items():
                    ids, tfs = post_b[w]
                    ids.append(i)
                    tfs.append(float(f))
                    terms.add(w)
            for w in terms:
                df_union[w] += 1
            if n % 20000 == 0 and self.verbose:
                print(f"  [BM25F] 已建 {n} 篇，词表 {len(df_union)}", flush=True)

        self.N = n
        self.len_title = np.frombuffer(len_t, dtype=np.int32) if n else np.zeros(0, np.int32)
        self.len_body = np.frombuffer(len_b, dtype=np.int32) if n else np.zeros(0, np.int32)
        self.avg_title = float(self.len_title.mean()) if n else 1.0
        self.avg_body = float(self.len_body.mean()) if n else 1.0
        self.post_title = {w: (np.frombuffer(ids, dtype=np.int32), np.frombuffer(tfs, dtype=np.float32))
                           for w, (ids, tfs) in post_t.items()}
        self.post_body = {w: (np.frombuffer(ids, dtype=np.int32), np.frombuffer(tfs, dtype=np.float32))
                          for w, (ids, tfs) in post_b.items()}
        self.idf = {w: math.log(1 + (n - df + 0.5) / (df + 0.5)) for w, df in df_union.items()}
        if self.verbose:
            print(f"[BM25F] N={n} | vocab={len(self.idf)} | "
                  f"avg_title={self.avg_title:.1f} avg_body={self.avg_body:.1f} | "
                  f"w_title={self.w_title} w_body={self.w_body} "
                  f"b_title={self.b_title} b_body={self.b_body} k1={self.k1}")
        return self

    # ---------- 打分 ----------

    def _K(self, lens, avg, b):
        return 1.0 - b + b * lens.astype(np.float64) / max(avg, 1e-9)

    @property
    def avgdl(self):
        return self.avg_body

    def get_scores(self, query_tokens, w_title=None, b_title=None, k1=None):
        w_t = self.w_title if w_title is None else w_title
        b_t = self.b_title if b_title is None else b_title
        k = self.k1 if k1 is None else k1
        K_t = self._K(self.len_title, self.avg_title, b_t)
        K_b = self._K(self.len_body, self.avg_body, self.b_body)

        scores = np.zeros(self.N, dtype=np.float64)
        for t in set(query_tokens or ()):
            idf = self.idf.get(t)
            if not idf:
                continue
            tfn = np.zeros(self.N, dtype=np.float64)
            pt = self.post_title.get(t)
            if pt is not None:
                idx, tf = pt
                tfn[idx] += w_t * tf / K_t[idx]
            pb = self.post_body.get(t)
            if pb is not None:
                idx, tf = pb
                tfn[idx] += self.w_body * tf / K_b[idx]
            mask = tfn > 0
            if mask.any():
                v = tfn[mask]
                scores[mask] += idf * v * (k + 1) / (k + v)
        return scores

    def top_k(self, query_tokens, k=10, **params):
        s = self.get_scores(query_tokens, **params)
        idx = np.argpartition(s, -k)[-k:]
        return idx[np.argsort(s[idx])[::-1]]
