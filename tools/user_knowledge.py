import os
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from dotenv import load_dotenv
from langchain_core.documents import Document
from psycopg2.extras import RealDictCursor
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

from tools.chunker_doc_01 import parse_doc_to_chunks
from tools.chunker_docx_01 import parse_docx_to_chunks_advanced
from tools.chunker_excel import parse_excel_to_chunks
from tools.chunker_pdf import parse_pdf_advanced
from tools.embedder import embed_texts


load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "user_knowledge"
USER_COLLECTION_NAME = "user_knowledge_base"

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "user": os.getenv("POSTGRES_USER", "omnipilot"),
    "password": os.getenv("POSTGRES_PASSWORD", "omnipilot123"),
    "database": os.getenv("POSTGRES_DB", "omnipilot"),
}

CREATE_USER_DOCUMENTS_SQL = """
CREATE TABLE IF NOT EXISTS user_documents (
    id VARCHAR(64) PRIMARY KEY,
    username VARCHAR(64) NOT NULL REFERENCES app_users(username) ON DELETE CASCADE,
    file_name VARCHAR(512) NOT NULL,
    stored_name VARCHAR(512) NOT NULL,
    file_type VARCHAR(32) NOT NULL,
    file_path TEXT NOT NULL,
    file_size BIGINT NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'ready',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(username, file_name)
);

CREATE INDEX IF NOT EXISTS idx_user_documents_username ON user_documents(username);
"""


def _get_connection():
    return psycopg2.connect(**DB_CONFIG)


def ensure_user_documents_table() -> None:
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_USER_DOCUMENTS_SQL)


def _safe_name(name: str) -> str:
    clean = Path(name or "").name.strip()
    if not clean:
        raise ValueError("文件名不能为空")
    return clean


def _safe_username(username: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in username)


def _expr_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _file_type(filename: str) -> Optional[str]:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".doc", ".docx"):
        return "word"
    if ext in (".xls", ".xlsx"):
        return "excel"
    return None


def user_file_dir(username: str, file_type: str) -> Path:
    safe_user = _safe_username(username)
    path = USER_KNOWLEDGE_DIR / safe_user / file_type
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_root_dir(username: str) -> Path:
    return USER_KNOWLEDGE_DIR / _safe_username(username)


class UserKnowledgeRetriever:
    def __init__(
        self,
        host: str = None,
        port: int = 19530,
        collection_name: str = USER_COLLECTION_NAME,
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
            collection = Collection(self.collection_name)
            collection.load()
            return collection

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.dim),
            FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="document_id", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="file_name", dtype=DataType.VARCHAR, max_length=4096),
            FieldSchema(name="file_type", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="section", dtype=DataType.VARCHAR, max_length=1024),
            FieldSchema(name="page", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="sheet_name", dtype=DataType.VARCHAR, max_length=512),
        ]
        schema = CollectionSchema(fields=fields, description="OmniPilot 用户个人知识库")
        collection = Collection(self.collection_name, schema)
        collection.create_index(
            "embedding",
            {"metric_type": "IP", "index_type": "IVF_FLAT", "params": {"nlist": 128}},
        )
        collection.load()
        return collection

    def insert(self, texts: List[str], embeddings: List[List[float]], metadatas: List[Dict[str, Any]]) -> None:
        for text, emb, meta in zip(texts, embeddings, metadatas):
            entity = {
                "text": str(text or ""),
                "embedding": emb,
                "user_id": str(meta.get("user_id") or ""),
                "document_id": str(meta.get("document_id") or ""),
                "file_name": str(meta.get("file_name") or ""),
                "file_type": str(meta.get("file_type") or ""),
                "section": str(meta.get("section") or meta.get("chapter") or ""),
                "page": str(meta.get("page") or ""),
                "sheet_name": str(meta.get("sheet_name") or ""),
            }
            self.collection.insert([entity])
        self.collection.flush()
        self.collection.load()

    def delete_document(self, username: str, document_id: str) -> None:
        expr = f'user_id == "{_expr_value(username)}" and document_id == "{_expr_value(document_id)}"'
        self.collection.delete(expr=expr)
        self.collection.flush()
        self.collection.load()

    def delete_user(self, username: str) -> None:
        expr = f'user_id == "{_expr_value(username)}"'
        self.collection.delete(expr=expr)
        self.collection.flush()
        self.collection.load()

    def search(
        self,
        username: str,
        query_embedding: List[float],
        top_k: int = 20,
        file_names: Optional[List[str]] = None,
    ) -> List[Tuple[Document, float]]:
        self.collection.load()
        expr = f'user_id == "{_expr_value(username)}"'
        if file_names:
            name_expr = " or ".join(
                f'file_name == "{_expr_value(Path(name).name)}"' for name in file_names
            )
            expr = f"{expr} and ({name_expr})"

        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param={"metric_type": "IP", "params": {"nprobe": 64}},
            limit=top_k,
            expr=expr,
            output_fields=["text", "document_id", "file_name", "file_type", "section", "page", "sheet_name"],
        )

        docs: List[Tuple[Document, float]] = []
        if not results or not results[0]:
            return docs
        for hit in results[0]:
            metadata = {
                "document_id": hit.entity.get("document_id", ""),
                "file_name": hit.entity.get("file_name", ""),
                "file_type": hit.entity.get("file_type", ""),
                "section": hit.entity.get("section", ""),
                "page": hit.entity.get("page", ""),
                "sheet_name": hit.entity.get("sheet_name", ""),
                "knowledge_scope": "user",
            }
            docs.append((Document(page_content=hit.entity.get("text", ""), metadata=metadata), hit.score))
        return docs


def _parse_file(filepath: Path, document_id: str, username: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    ext = filepath.suffix.lower()
    file_type = _file_type(filepath.name)
    if not file_type:
        raise ValueError("仅支持 PDF、Word、Excel 文件")

    if ext == ".pdf":
        chunks = parse_pdf_advanced(str(filepath), chunk_size=800)
    elif ext == ".docx":
        chunks = parse_docx_to_chunks_advanced(str(filepath), chunk_size=800)
    elif ext == ".doc":
        chunks = parse_doc_to_chunks(str(filepath), chunk_size=800)
    elif ext in (".xls", ".xlsx"):
        chunks = parse_excel_to_chunks(str(filepath))
    else:
        chunks = []

    texts: List[str] = []
    metadatas: List[Dict[str, Any]] = []
    for chunk in chunks:
        meta = dict(chunk.get("metadata") or {})
        meta["user_id"] = username
        meta["document_id"] = document_id
        meta["file_name"] = filepath.name
        meta["file_type"] = file_type

        prefix_parts = [f"[Source: {filepath.name}]"]
        if meta.get("page"):
            prefix_parts.append(f"Page: {meta['page']}")
        if meta.get("section"):
            prefix_parts.append(f"Clause: {meta['section']}")
        if meta.get("sheet_name"):
            prefix_parts.append(f"Sheet: {meta['sheet_name']}")
        prefix = " | ".join(prefix_parts) + "\n"
        texts.append(prefix + str(chunk.get("content") or ""))
        metadatas.append(meta)
    return texts, metadatas


def _row_to_doc(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["file_name"],
        "stored_name": row["stored_name"],
        "type": row["file_type"],
        "path": row["file_path"],
        "size": int(row.get("file_size") or 0),
        "modified": row["updated_at"].timestamp() if row.get("updated_at") else 0,
        "chunk_count": int(row.get("chunk_count") or 0),
        "status": row.get("status") or "ready",
        "error_message": row.get("error_message") or "",
    }


def list_user_documents(username: str) -> Dict[str, List[Dict[str, Any]]]:
    ensure_user_documents_table()
    grouped: Dict[str, List[Dict[str, Any]]] = {"pdf": [], "word": [], "excel": []}
    with _get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, file_name, stored_name, file_type, file_path, file_size,
                       chunk_count, status, error_message, created_at, updated_at
                FROM user_documents
                WHERE username = %s
                ORDER BY updated_at DESC, file_name ASC
                """,
                (username,),
            )
            for row in cur.fetchall():
                doc = _row_to_doc(row)
                grouped.setdefault(doc["type"], []).append(doc)
    return grouped


def get_user_document(username: str, document_id: str) -> Optional[Dict[str, Any]]:
    ensure_user_documents_table()
    with _get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, file_name, stored_name, file_type, file_path, file_size,
                       chunk_count, status, error_message, created_at, updated_at
                FROM user_documents
                WHERE username = %s AND id = %s
                """,
                (username, document_id),
            )
            row = cur.fetchone()
            return _row_to_doc(row) if row else None


def delete_user_document(username: str, document_id: str) -> None:
    ensure_user_documents_table()
    doc = get_user_document(username, document_id)
    if not doc:
        return
    UserKnowledgeRetriever().delete_document(username, document_id)
    file_path = Path(doc["path"])
    if file_path.exists() and str(file_path.resolve()).startswith(str(USER_KNOWLEDGE_DIR.resolve())):
        file_path.unlink()
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_documents WHERE username = %s AND id = %s", (username, document_id))


def _delete_existing_by_filename(username: str, file_name: str) -> None:
    ensure_user_documents_table()
    with _get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM user_documents WHERE username = %s AND file_name = %s",
                (username, file_name),
            )
            row = cur.fetchone()
    if row:
        delete_user_document(username, row["id"])


def ingest_user_file(username: str, source_path: Path, original_name: str) -> Dict[str, Any]:
    ensure_user_documents_table()
    original_name = _safe_name(original_name)
    file_type = _file_type(original_name)
    if not file_type:
        raise ValueError("仅支持 PDF、Word、Excel 文件")

    _delete_existing_by_filename(username, original_name)

    document_id = uuid.uuid4().hex
    target_dir = user_file_dir(username, file_type)
    stored_name = original_name
    target_path = target_dir / stored_name
    if source_path.resolve() != target_path.resolve():
        shutil.copy2(source_path, target_path)

    texts, metadatas = _parse_file(target_path, document_id, username)
    if not texts:
        raise ValueError("文件未解析出任何知识单元")

    embeddings = embed_texts(texts)
    UserKnowledgeRetriever().insert(texts, embeddings, metadatas)

    with _get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO user_documents (
                    id, username, file_name, stored_name, file_type, file_path,
                    file_size, chunk_count, status, error_message
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ready', '')
                RETURNING id, file_name, stored_name, file_type, file_path, file_size,
                          chunk_count, status, error_message, created_at, updated_at
                """,
                (
                    document_id,
                    username,
                    original_name,
                    stored_name,
                    file_type,
                    str(target_path),
                    target_path.stat().st_size,
                    len(texts),
                ),
            )
            return _row_to_doc(cur.fetchone())


def delete_all_user_knowledge(username: str) -> None:
    ensure_user_documents_table()
    UserKnowledgeRetriever().delete_user(username)

    root = user_root_dir(username)
    if root.exists() and str(root.resolve()).startswith(str(USER_KNOWLEDGE_DIR.resolve())):
        shutil.rmtree(root)

    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_documents WHERE username = %s", (username,))


def search_user_knowledge(
    username: Optional[str],
    query_embedding: List[float],
    top_k: int = 20,
    file_names: Optional[List[str]] = None,
) -> List[Tuple[Document, float]]:
    if not username:
        return []
    try:
        return UserKnowledgeRetriever().search(username, query_embedding, top_k=top_k, file_names=file_names)
    except Exception as exc:
        print(f"[UserKB] 个人知识库检索跳过: {exc}")
        return []