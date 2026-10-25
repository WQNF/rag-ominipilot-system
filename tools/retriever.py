"""
统一检索模块：支持 Milvus 向量库检索，支持元数据字段存储与过滤
"""
import os
import json
from typing import List, Optional, Tuple, Dict, Any
from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType, utility
from langchain_core.documents import Document


class BaseRetriever:
    def search(self, query_embedding: List[float], top_k: int = 5, expr: Optional[str] = None) -> List[Tuple[Document, float]]:
        raise NotImplementedError
    
    def get_all_documents(self) -> List[Dict[str, Any]]:
        """返回所有文档，格式：[{'id': doc_id, 'content': text, 'metadata': {...}}]"""
        raise NotImplementedError


class MilvusRetriever(BaseRetriever):
    def __init__(
        self,
        host: str = None,
        port: int = 19530,
        collection_name: str = "knowledge_base",
        dim: int = 1024,
    ):
        self.host = host if host is not None else os.getenv("MILVUS_HOST", "localhost")
        self.port = port
        self.collection_name = collection_name
        self.dim = dim
        
        connections.connect(host=self.host, port=self.port)
        self.collection = self._get_or_create_collection()
    
    def _get_or_create_collection(self) -> Collection:
        if utility.has_collection(self.collection_name):
            self.collection = Collection(self.collection_name)
            self.collection.load()
            print(f"[Milvus] 集合 '{self.collection_name}' 已存在，追加模式")
            return self.collection
        
        # 主键 ID
        id_field = FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True)
        # 向量字段
        embedding_field = FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.dim)
        # 文本内容
        text_field = FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535)
        
        # ========== 元数据字段（增大长度） ==========
        file_name_field = FieldSchema(name="file_name", dtype=DataType.VARCHAR, max_length=4096)
        file_type_field = FieldSchema(name="file_type", dtype=DataType.VARCHAR, max_length=64)
        chapter_field = FieldSchema(name="chapter", dtype=DataType.VARCHAR, max_length=4096)
        section_field = FieldSchema(name="section", dtype=DataType.VARCHAR, max_length=512)
        authority_field = FieldSchema(name="authority", dtype=DataType.VARCHAR, max_length=4096)
        publish_date_field = FieldSchema(name="publish_date", dtype=DataType.VARCHAR, max_length=256)
        effective_date_field = FieldSchema(name="effective_date", dtype=DataType.VARCHAR, max_length=256)
        doc_number_field = FieldSchema(name="doc_number", dtype=DataType.VARCHAR, max_length=512)
        doc_id_field = FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=256)
        indicator_field = FieldSchema(name="indicator", dtype=DataType.VARCHAR, max_length=4096)
        sheet_name_field = FieldSchema(name="sheet_name", dtype=DataType.VARCHAR, max_length=512)
        time_dimension_field = FieldSchema(name="time_dimension", dtype=DataType.VARCHAR, max_length=256)
        tags_field = FieldSchema(name="tags", dtype=DataType.VARCHAR, max_length=4096)
        normative_strength_field = FieldSchema(name="normative_strength", dtype=DataType.VARCHAR, max_length=256)
                
        schema = CollectionSchema(
            fields=[
                id_field, embedding_field, text_field,
                file_name_field, file_type_field, chapter_field, section_field,
                authority_field, publish_date_field, effective_date_field, doc_number_field,
                doc_id_field,
                indicator_field, sheet_name_field, time_dimension_field,
                tags_field, normative_strength_field
            ],
            description="OmniPilot 知识库（支持元数据过滤）"
        )
        
        self.collection = Collection(self.collection_name, schema)
        
        # 创建索引
        index_params = {
            "metric_type": "IP",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128}
        }
        self.collection.create_index("embedding", index_params)
        self.collection.load()
        print(f"[Milvus] 新建集合 '{self.collection_name}' 完成")
        return self.collection

    def delete_by_file(self, file_name: str):
        """按 file_name 删除旧数据（用于重新分块前清理）"""
        try:
            count_expr = f'file_name like "%{file_name}%"'
            before = self.collection.query(expr=count_expr, output_fields=["id"])
            print(f"[Milvus] 删除前匹配到 {len(before)} 条记录 (file_name like '%{file_name}%')")
            if len(before) > 0:
                ids = [item["id"] for item in before]
                result = self.collection.delete(expr=f"id in {ids}")
                self.collection.flush()
                print(f"[Milvus] delete 返回: {result}")
                self.collection.load()
                after = self.collection.query(expr=count_expr, output_fields=["id"])
                print(f"[Milvus] 删除后剩余 {len(after)} 条记录")
            else:
                print(f"[Milvus] 无匹配记录，跳过删除")
        except Exception as e:
            print(f"[Milvus] 删除旧数据失败: {e}")

    def insert(self, texts: List[str], embeddings: List[List[float]], metadatas: List[Dict[str, Any]]):
        """
        批量插入文本、向量和元数据（逐行插入，处理 None 值）
        """
        for i, (text, emb, meta) in enumerate(zip(texts, embeddings, metadatas)):
            # 辅助函数：将 None 转为空字符串，其他类型转为字符串
            def safe_str(value):
                if value is None:
                    return ""
                return str(value)
            
            # 处理 tags：可能是列表，需转为 JSON 字符串
            tags_value = meta.get("tags")
            if tags_value is None:
                tags_str = "[]"
            elif isinstance(tags_value, list):
                tags_str = json.dumps(tags_value)
            else:
                tags_str = safe_str(tags_value)
            
            file_name_val = safe_str(meta.get("file_name"))
            chapter_val = safe_str(meta.get("chapter"))
            indicator_val = safe_str(meta.get("indicator"))
            authority_val = safe_str(meta.get("authority"))
            
            # 诊断：检查字段长度是否超限
            _max_1024_fields = {
                "file_name": (file_name_val, 1024),
                "chapter": (chapter_val, 1024),
                "authority": (authority_val, 1024),
                "indicator": (indicator_val, 1024),
            }
            for _fname, (_fval, _flimit) in _max_1024_fields.items():
                if len(_fval) > _flimit:
                    print(f"[WARN] 第 {i} 个 chunk 字段 '{_fname}' 长度 {len(_fval)} > {_flimit}")
                    print(f"        file_name='{file_name_val}', {_fname}='{_fval[:200]}...'")
            
            entity = {
                "text": safe_str(text),
                "embedding": emb,
                "file_name": file_name_val,
                "file_type": safe_str(meta.get("file_type")),
                "chapter": chapter_val,
                "section": safe_str(meta.get("section")),
                "authority": authority_val,
                "publish_date": safe_str(meta.get("publish_date")),
                "effective_date": safe_str(meta.get("effective_date")),
                "doc_number": safe_str(meta.get("doc_number")),
                "doc_id": safe_str(meta.get("doc_id")),
                "indicator": indicator_val,
                "sheet_name": safe_str(meta.get("sheet_name")),
                "time_dimension": safe_str(meta.get("time_dimension")),
                "tags": tags_str,
                "normative_strength": safe_str(meta.get("normative_strength"))
            }
            self.collection.insert([entity])
        self.collection.flush()
        print(f"✅ 成功插入 {len(texts)} 条记录")
    
    def search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        score_threshold: Optional[float] = None,
        expr: Optional[str] = None,
        nprobe: int = 128
    ) -> List[Tuple[Document, float]]:
        """
        检索向量库，支持元数据过滤表达式 expr
        nprobe: 搜索的簇数，128=全量（Excel），10=精炼（Word/PDF）
        """
        self.collection.load()
        search_params = {"metric_type": "IP", "params": {"nprobe": nprobe}}
        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            expr=expr,   # 关键：支持过滤
            output_fields=["text", "file_name", "chapter", "section", "authority", 
                           "publish_date", "indicator", "sheet_name", "time_dimension",
                           "tags", "normative_strength", "doc_id"]
        )
        
        docs_with_scores = []
        if results and results[0]:
            for hit in results[0]:
                score = hit.score
                if score_threshold is not None and score < score_threshold:
                    continue
                # 重建元数据
                metadata = {
                    "file_name": hit.entity.get("file_name", ""),
                    "chapter": hit.entity.get("chapter", ""),
                    "section": hit.entity.get("section", ""),
                    "authority": hit.entity.get("authority", ""),
                    "publish_date": hit.entity.get("publish_date", ""),
                    "indicator": hit.entity.get("indicator", ""),
                    "sheet_name": hit.entity.get("sheet_name", ""),
                    "time_dimension": hit.entity.get("time_dimension", ""),
                    "tags": json.loads(hit.entity.get("tags", "[]")),
                    "normative_strength": hit.entity.get("normative_strength", ""),
                    "doc_id": hit.entity.get("doc_id", "")
                }
                doc = Document(page_content=hit.entity.get("text", ""), metadata=metadata)
                docs_with_scores.append((doc, score))
        
        return docs_with_scores
    
    def retrieve(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        score_threshold: Optional[float] = None,
        expr: Optional[str] = None,
        nprobe: int = 128
    ) -> List[Document]:
        results = self.search(query_embedding, top_k, score_threshold, expr, nprobe=nprobe)
        return [doc for doc, _ in results]
    
    def get_all_documents(self) -> List[Dict[str, Any]]:
        """
        获取集合中所有文档，供 BM25 构建索引使用
        使用游标分页（基于自增主键 id > last_id），彻底绕过 Milvus 的 offset+limit ≤ 16384 限制
        """
        self.collection.load()
        batch_size = 8000
        all_docs = []
        total = self.collection.num_entities
        last_id = 0  # INT64 auto_id 从 0 开始
        print(f"[Milvus] 集合共 {total} 条记录，开始游标分页加载...")
        while True:
            expr = "" if last_id == 0 else f"id > {last_id}"
            results = self.collection.query(
                expr=expr,
                output_fields=["id", "text", "file_name", "chapter", "section", "authority",
                               "publish_date", "indicator", "sheet_name", "time_dimension",
                               "tags", "normative_strength", "doc_id"],
                limit=batch_size
            )
            if not results:
                break
            for res in results:
                metadata = {
                    "file_name": res.get("file_name", ""),
                    "chapter": res.get("chapter", ""),
                    "section": res.get("section", ""),
                    "authority": res.get("authority", ""),
                    "publish_date": res.get("publish_date", ""),
                    "indicator": res.get("indicator", ""),
                    "sheet_name": res.get("sheet_name", ""),
                    "time_dimension": res.get("time_dimension", ""),
                    "tags": json.loads(res.get("tags", "[]")),
                    "normative_strength": res.get("normative_strength", "")
                }
                all_docs.append({
                    "id": res["id"],
                    "content": res["text"],
                    "metadata": metadata
                })
            last_id = max(r["id"] for r in results)
            print(f"  [Milvus] 已加载 {len(all_docs)}/{total} 篇文档...")
        return all_docs


# ========== 工厂函数 ==========
def get_retriever(backend: Optional[str] = None, **kwargs) -> BaseRetriever:
    if backend is None:
        backend = os.getenv("RETRIEVER_BACKEND", "milvus")
    if backend.lower() == "milvus":
        return MilvusRetriever(**kwargs)
    else:
        raise ValueError(f"不支持的检索后端: {backend}")