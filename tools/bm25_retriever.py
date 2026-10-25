# tools/bm25_retriever.py
import os
import re
import pickle
from typing import List, Tuple, Optional, Dict, Any
from rank_bm25 import BM25Okapi
from tools.retriever import get_retriever

class BM25Retriever:
    def __init__(self, rebuild: bool = False):
        self.bm25 = None
        self.doc_ids = []
        self.doc_contents = []
        self.metadata_list = []
        self.index_path = "bm25_index.pkl"
        
        if not rebuild and os.path.exists(self.index_path):
            self._load()
        else:
            self._build()
            self._save()
    
    def _tokenize(self, text: str) -> List[str]:
        """简单分词，可替换为 jieba"""
        # 保留中英文、数字
        tokens = re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]+', text)
        return tokens
    
    def _build(self):
        print("[BM25] 正在从 Milvus 加载所有文档，构建 BM25 索引...")
        retriever = get_retriever()
        all_docs = retriever.get_all_documents()
        
        self.doc_ids = [doc["id"] for doc in all_docs]
        self.doc_contents = [doc["content"] for doc in all_docs]
        self.metadata_list = [doc["metadata"] for doc in all_docs]
        
        tokenized_corpus = [self._tokenize(doc) for doc in self.doc_contents]
        self.bm25 = BM25Okapi(tokenized_corpus)
        print(f"[BM25] 索引构建完成，共 {len(self.doc_contents)} 篇文档")
    
    def search(self, query: str, top_k: int = 20, filter_expr: Optional[str] = None) -> List[Tuple[str, str, float]]:
        """
        返回：[(doc_id, content, score), ...]
        """
        tokenized_query = self._tokenize(query)
        scores = self.bm25.get_scores(tokenized_query)
        
        if filter_expr:
            indices = self._apply_filter(filter_expr)
        else:
            indices = list(range(len(scores)))
        
        results = [(self.doc_ids[i], self.doc_contents[i], scores[i]) for i in indices]
        results.sort(key=lambda x: x[2], reverse=True)
        return results[:top_k]
    
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
                # 可根据需要扩展更多操作符
            if match:
                indices.append(i)
        return indices
    
    def _save(self):
        with open(self.index_path, 'wb') as f:
            pickle.dump((self.bm25, self.doc_ids, self.doc_contents, self.metadata_list), f)
    
    def _load(self):
        with open(self.index_path, 'rb') as f:
            self.bm25, self.doc_ids, self.doc_contents, self.metadata_list = pickle.load(f)
        print(f"[BM25] 索引加载完成，共 {len(self.doc_contents)} 篇文档")

# 全局单例
_bm25_instance = None
def get_bm25_retriever(rebuild: bool = False) -> BM25Retriever:
    global _bm25_instance
    if _bm25_instance is None or rebuild:
        _bm25_instance = BM25Retriever(rebuild=rebuild)
    return _bm25_instance