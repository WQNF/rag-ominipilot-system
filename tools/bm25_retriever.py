# tools/bm25_retriever.py
"""BM25 检索器（jieba 领域分词版）。

相对旧版的改动
--------------
1. 分词统一走 tools.bm25_tokenizer（jieba + 领域词典 + 条款号通道），
   索引侧与查询侧共用同一个 tokenizer。
2. 索引文件里记录 tokenizer 签名（版本 / backend / 词典 hash）。
   加载时若签名不一致，**停用 BM25 这一路并打印重建指引**，
   而不是用错位的 token 给出一个看起来正常、实际有毒的排序。
3. 构建索引时先用 metadata 自动生成并落盘领域词典（bm25_domain_dict.txt）。

回滚方式
--------
BM25_TOKENIZER=regex 环境变量即可退回旧的正则切分（需重建索引）。
"""
import os
import re
import pickle

import numpy as np
from typing import List, Tuple, Optional, Dict, Any

from rank_bm25 import BM25Okapi

from tools.retriever import get_retriever
from tools import bm25_tokenizer as T

INDEX_PATH = os.getenv("BM25_INDEX_PATH", "bm25_index.pkl")


INDEX_PATH = os.getenv("BM25_INDEX_PATH", "")


def _default_index_path():
    """优先用 BM25F 索引（如果构建过），否则退回单字段索引。"""
    for name in ("bm25_index_f.pkl", "bm25_index.pkl"):
        if os.path.exists(name):
            return name
    return "bm25_index.pkl"


class BM25Retriever:
    def __init__(self, rebuild: bool = False):
        self.bm25 = None
        self.doc_ids: List[Any] = []
        self.doc_contents: List[str] = []
        self.metadata_list: List[Dict[str, Any]] = []
        self.index_path = INDEX_PATH or _default_index_path()
        self.signature: Optional[Dict[str, Any]] = None
        self.disabled = False

        if not rebuild and os.path.exists(self.index_path):
            self._load()
        else:
            self._build()
            self._save()

    # ---------- 分词 ----------

    def _tokenize(self, text: str) -> List[str]:
        return T.tokenize(text)

    # ---------- 构建 ----------

    def _build(self):
        print("[BM25] 正在从 Milvus 加载所有文档，构建 BM25 索引...")
        retriever = get_retriever()
        all_docs = retriever.get_all_documents()

        self.doc_ids = [doc["id"] for doc in all_docs]
        self.doc_contents = [doc["content"] for doc in all_docs]
        self.metadata_list = [doc["metadata"] for doc in all_docs]

        # 先用 metadata 生成/加载领域词典，再切词——顺序不能反
        T.init(metadata=self.metadata_list, rebuild_dict=not os.path.exists(T.DICT_PATH))

        tokenized_corpus = [self._tokenize(doc) for doc in self.doc_contents]
        self.bm25 = BM25Okapi(tokenized_corpus)
        self.signature = T.signature()
        avgdl = sum(len(d) for d in tokenized_corpus) / max(len(tokenized_corpus), 1)
        print(
            f"[BM25] 索引构建完成：{len(self.doc_contents)} 篇，"
            f"avgdl={avgdl:.2f} token/篇，tokenizer={self.signature}"
        )

    # ---------- 检索 ----------

    def search(self, query: str, top_k: int = 20,
               filter_expr: Optional[str] = None) -> List[Tuple[str, str, float]]:
        """返回：[(doc_id, content, score), ...]

        只用 argpartition 取 top-k，不把 11 万个候选全部物化成 (id, content, score) 元组，
        单次检索从 ~100ms 降到 ~10ms 量级。
        """
        if self.disabled or self.bm25 is None:
            return []

        tokenized_query = self._tokenize(query)
        scores = np.asarray(self.bm25.get_scores(tokenized_query), dtype=np.float64)

        if filter_expr:
            idx = np.asarray(self._apply_filter(filter_expr), dtype=np.int64)
            if idx.size == 0:
                return []
            k = min(top_k, idx.size)
            top = idx[np.argsort(scores[idx])[::-1][:k]]
        else:
            k = min(top_k, scores.size)
            top = np.argpartition(scores, -k)[-k:]
            top = top[np.argsort(scores[top])[::-1]]

        from tools.context_format import ensure_header
        return [(self.doc_ids[int(i)],
                 ensure_header(self.doc_contents[int(i)], self.metadata_list[int(i)]),
                 float(scores[int(i)]))
                for i in top]

    def _apply_filter(self, expr: str) -> List[int]:
        """解析简单的过滤表达式，返回符合条件的数据索引"""
        indices = []
        conditions = expr.split(' and ')
        for i, meta in enumerate(self.metadata_list):
            match = True
            for cond in conditions:
                cond = cond.strip()
                if 'like' in cond:
                    # 格式：file_name like "%xxx%"
                    field = cond.split(' like')[0].strip()
                    m = re.search(r'like "%([^%]+)%"', cond)
                    if m:
                        keyword = m.group(1)
                        if keyword not in str(meta.get(field, '')):
                            match = False
                            break
                elif '==' in cond:
                    field, value = [x.strip().strip('"') for x in cond.split('==')]
                    if str(meta.get(field, '')) != value:
                        match = False
                        break
                elif '>=' in cond:
                    # 简单支持日期比较
                    field, value = [x.strip() for x in cond.split('>=')]
                    if meta.get(field, '') < value.strip('"'):
                        match = False
                        break
            if match:
                indices.append(i)
        return indices

    # ---------- 持久化 ----------

    def _save(self):
        with open(self.index_path, 'wb') as f:
            pickle.dump(
                (self.bm25, self.doc_ids, self.doc_contents,
                 self.metadata_list, self.signature),
                f,
            )

    def _load(self):
        with open(self.index_path, 'rb') as f:
            payload = pickle.load(f)

        if len(payload) == 5:
            self.bm25, self.doc_ids, self.doc_contents, self.metadata_list, self.signature = payload
        else:                                    # 旧的 4 元组（正则分词时代）
            self.bm25, self.doc_ids, self.doc_contents, self.metadata_list = payload
            self.signature = {"version": "regex-v0", "backend": "regex", "dict_hash": "none"}

        # 先按当前配置初始化分词器，再比对签名
        T.init()
        current = T.signature()

        mode = "bm25f" if str(self.signature.get("version", "")).startswith("bm25f") else "single"

        # 正则切分是稳定的，只要两侧都是 regex 就视为兼容（允许用环境变量回滚）
        if self.signature.get("backend") == "regex" == current.get("backend"):
            same = True
        else:
            same = (self.signature.get("backend") == current.get("backend")
                    and self.signature.get("dict_hash") == current.get("dict_hash")
                    and (mode == "bm25f" or self.signature.get("version") == current.get("version")))

        if same:
            print(f"[BM25] 索引加载完成（mode={mode}）：{len(self.doc_contents)} 篇，tokenizer={current}")
            return

        self.disabled = True
        print(
            "[BM25] ⚠ 索引与当前 tokenizer 不一致，已停用 BM25 这一路。\n"
            f"        索引: {self.signature}\n"
            f"        当前: {current}\n"
            "        单字段索引重建： python valuation/rebuild_bm25.py\n"
            "        BM25F 索引重建： python valuation/build_bm25f.py\n"
            "        （或用 BM25_TOKENIZER=regex 退回旧行为）"
        )


# 全局单例
_bm25_instance = None


def get_bm25_retriever(rebuild: bool = False) -> BM25Retriever:
    global _bm25_instance
    if _bm25_instance is None or rebuild:
        _bm25_instance = BM25Retriever(rebuild=rebuild)
    return _bm25_instance
