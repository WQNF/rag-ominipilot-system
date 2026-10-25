import os
import sys
import re
import json
from pathlib import Path
from dotenv import load_dotenv


# 将项目根目录添加到 sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


from tools.retriever import MilvusRetriever
from tools.embedder import embed_texts
from pymilvus import utility, connections
from tools.chunker_docx_01 import parse_docx_to_chunks_advanced
from tools.chunker_doc_01 import parse_doc_to_chunks
from tools.chunker_word_397 import parse_bank_letter_guideline
from tools.chunker_word_417 import parse_disclosure_docx
from tools.chunker_pdf import parse_pdf_advanced      # ← 新增
from tools.chunker_pdf_460 import parse_pdf_460       # ← 460 号文档专用
from tools.chunker_pdf_467 import parse_pdf_467       # ← 467 号文档专用
from tools.chunker_excel import parse_excel_to_chunks  # ← 通用 Excel 解析
from tools.chunker_excel_130 import parse_excel_130_to_chunks  # ← 130 号文档专用
from tools.chunker_excel_128_131_132 import parse_asset_liability_table_to_chunks  # ← 128/131/132 专用
from tools.chunker_excel_33_34_35_57_58_59_90_91_92 import parse_operating_table_to_chunks  # ← 33-35/57-59/90-92 专用
from tools.manifest_loader import load_manifest  # ← 元数据加载


load_dotenv()


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_knowledge_base(doc_directory: str = None, file_list: list = None, progress_callback=None) -> dict:
    """Word 知识库 — 追加模式，不删除已有 PDF/Excel 数据"""
    files_to_process = []

    if file_list is not None:
        files_to_process = [Path(f) for f in file_list if Path(f).suffix.lower() in (".docx", ".doc")]
    elif doc_directory is not None:
        doc_dir = Path(doc_directory)
        if not doc_dir.exists():
            return {"status": "error", "message": f"目录不存在: {doc_directory}", "chunks_count": 0}
        for f in doc_dir.iterdir():
            if f.is_file() and f.suffix.lower() in (".docx", ".doc"):
                files_to_process.append(f)
    else:
        for sub_dir in ["docx", "doc"]:
            scan_dir = Path(project_root) / "data" / "word" / sub_dir
            if scan_dir.exists():
                for f in scan_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in (".docx", ".doc"):
                        files_to_process.append(f)

    files_to_process.sort(key=lambda x: x.name)

    if not files_to_process:
        return {"status": "error", "message": "未找到任何 .docx 或 .doc 文件", "chunks_count": 0}

    try:
        connections.connect(host=os.getenv("MILVUS_HOST", "localhost"), port=int(os.getenv("MILVUS_PORT", "19530")))
    except Exception as e:
        return {"status": "error", "message": f"连接 Milvus 失败: {e}", "chunks_count": 0}

    retriever = MilvusRetriever()

    manifest = load_manifest()

    all_texts = []
    all_metadatas = []

    for i, filepath in enumerate(files_to_process):
        filename = filepath.name
        if progress_callback:
            progress_callback(i + 1, len(files_to_process), filename)
        ext = filepath.suffix.lower()
        doc_meta = manifest.get(filename, {})

        try:
            if ext == ".docx":
                if "397" in filename:
                    chunks = parse_bank_letter_guideline(str(filepath), chunk_size=800)
                    print(f"✅ [特殊解析器] 解析 {filename} -> {len(chunks)} 个知识块")
                elif "417" in filename:
                    chunks = parse_disclosure_docx(str(filepath))
                    print(f"✅ [417解析器] 解析 {filename} -> {len(chunks)} 个知识块")
                else:
                    chunks = parse_docx_to_chunks_advanced(str(filepath), chunk_size=800, doc_meta=doc_meta)
                    print(f"✅ 解析 {filename} -> {len(chunks)} 个知识块")
            elif ext == ".doc":
                chunks = parse_doc_to_chunks(str(filepath), chunk_size=800, doc_meta=doc_meta)
                print(f"✅ 解析 {filename} -> {len(chunks)} 个知识块")
            else:
                continue
        except Exception as e:
            print(f"❌ 解析 {filename} 失败: {e}")
            continue

        for chunk in chunks:
            meta = chunk["metadata"]
            if doc_meta:
                for key in ("authority", "publish_date", "doc_number", "doc_id"):
                    if not meta.get(key) and doc_meta.get(key):
                        meta[key] = doc_meta[key]
            prefix_parts = [f"[Source: {meta.get('file_name', 'unknown')}]"]
            if meta.get("chapter"):
                prefix_parts.append(f"Chapter: {meta['chapter']}")
            if meta.get("section"):
                prefix_parts.append(f"Clause: {meta['section']}")
            if meta.get("sheet_name"):
                prefix_parts.append(f"Sheet: {meta['sheet_name']}")
            if meta.get("indicator"):
                prefix_parts.append(f"Indicator: {meta['indicator']}")
            prefix = " | ".join(prefix_parts) + "\n"
            full_content = prefix + chunk["content"]
            all_texts.append(full_content)
            all_metadatas.append(meta)

    if not all_texts:
        return {"status": "error", "message": "未生成任何知识单元", "chunks_count": 0}

    print(f"\n📊 共生成 {len(all_texts)} 个 Word 知识单元")
    print("开始生成向量...")
    print("[Milvus] 清理旧 Word 数据...")
    for filepath in files_to_process:
        retriever.delete_by_file(filepath.name)
    all_embeddings = embed_texts(all_texts)

    retriever.insert(all_texts, all_embeddings, all_metadatas)

    bm25_pickle = os.path.join(project_root, "bm25_index.pkl")
    if os.path.exists(bm25_pickle):
        os.remove(bm25_pickle)
        print(f"[BM25] 旧索引缓存已删除，下次 RAG 调用时自动重建")

    msg = f"Word 知识库追加完成！新增 {len(all_texts)} 个块（未影响已有 PDF 数据）。"
    print(f"🎉 {msg}")
    return {"status": "success", "message": msg, "chunks_count": len(all_texts)}



def build_pdf_knowledge_base(pdf_directory: str = None, skip_confirm: bool = False, file_list: list = None, progress_callback=None) -> dict:
    """
    PDF 知识库 — 追加模式，不删除已有 Word 数据
    """
    if file_list is not None:
        files_to_process = sorted(
            [Path(f) for f in file_list if Path(f).suffix.lower() == ".pdf"],
            key=lambda x: x.name
        )
    else:
        if pdf_directory is None:
            pdf_directory = Path(project_root) / "data" / "pdf"
        pdf_dir = Path(pdf_directory)
        if not pdf_dir.exists():
            return {"status": "error", "message": f"目录不存在: {pdf_directory}", "chunks_count": 0}
        files_to_process = sorted(
            [f for f in pdf_dir.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"],
            key=lambda x: x.name
        )

    if not files_to_process:
        return {"status": "error", "message": "未找到 PDF 文件", "chunks_count": 0}

    try:
        connections.connect(host=os.getenv("MILVUS_HOST", "localhost"), port=int(os.getenv("MILVUS_PORT", "19530")))
    except Exception as e:
        return {"status": "error", "message": f"连接 Milvus 失败: {e}", "chunks_count": 0}

    retriever = MilvusRetriever()

    manifest = load_manifest()

    all_texts = []
    all_metadatas = []

    pdf_ocr_images = _env_flag("PDF_OCR_IMAGES", True)
    pdf_ocr_full_page = _env_flag("PDF_OCR_FULL_PAGE", True)
    pdf_ocr_lang = os.getenv("PDF_OCR_LANG", "chi_sim+eng")

    for i, filepath in enumerate(files_to_process):
        filename = filepath.name
        if progress_callback:
            progress_callback(i + 1, len(files_to_process), filename)
        doc_meta = manifest.get(filename, {})
        try:
            # 460 号文档使用专用解析器（解决多列排版问题）
            if "460" in filename:
                chunks = parse_pdf_460(
                    str(filepath),
                    chunk_size=800,
                    ocr_images=pdf_ocr_images,
                    ocr_full_page=pdf_ocr_full_page,
                    ocr_lang=pdf_ocr_lang,
                )
                print(f"✅ [460专用] 解析 {filename} -> {len(chunks)} 个知识块")
            # 467 号文档使用专用解析器（解决公司名单截断问题）
            elif "467" in filename:
                chunks = parse_pdf_467(
                    str(filepath),
                    chunk_size=800,
                )
                print(f"✅ [467专用] 解析 {filename} -> {len(chunks)} 个知识块")
            else:
                chunks = parse_pdf_advanced(
                    str(filepath),
                    chunk_size=800,
                    ocr_images=pdf_ocr_images,
                    ocr_full_page=pdf_ocr_full_page,
                    ocr_lang=pdf_ocr_lang,
                    doc_meta=doc_meta,
                )
                print(f"✅ 解析 {filename} -> {len(chunks)} 个知识块")
        except Exception as e:
            print(f"❌ 解析 {filename} 失败: {e}")
            continue

        for chunk in chunks:
            meta = chunk["metadata"]
            if doc_meta:
                for key in ("authority", "publish_date", "doc_number", "doc_id"):
                    if not meta.get(key) and doc_meta.get(key):
                        meta[key] = doc_meta[key]
            prefix_parts = [f"[Source: {meta.get('file_name', 'unknown')}]"]
            if meta.get("chapter"):
                prefix_parts.append(f"Chapter: {meta['chapter']}")
            if meta.get("section"):
                prefix_parts.append(f"Clause: {meta['section']}")
            if meta.get("page"):
                prefix_parts.append(f"Page: {meta['page']}")
            prefix = " | ".join(prefix_parts) + "\n"
            full_content = prefix + chunk["content"]
            all_texts.append(full_content)
            all_metadatas.append(meta)

    if not all_texts:
        return {"status": "error", "message": "未生成任何知识单元", "chunks_count": 0}

    print(f"\n📊 共生成 {len(all_texts)} 个 PDF 知识单元")
    print("开始生成向量...")
    # 清理旧 PDF 数据（避免新旧 chunk 共存）
    print("[Milvus] 清理旧 PDF 数据...")
    for filepath in files_to_process:
        retriever.delete_by_file(filepath.name)
    all_embeddings = embed_texts(all_texts)

    retriever.insert(all_texts, all_embeddings, all_metadatas)

    bm25_pickle = os.path.join(project_root, "bm25_index.pkl")
    if os.path.exists(bm25_pickle):
        os.remove(bm25_pickle)
        print(f"[BM25] 旧索引缓存已删除，下次 RAG 调用时自动重建（含 Word + PDF）")

    msg = f"PDF 知识库追加完成！新增 {len(all_texts)} 个块（未影响已有 Word 数据）。"
    print(f"🎉 {msg}")
    return {"status": "success", "message": msg, "chunks_count": len(all_texts)}


def build_excel_knowledge_base(excel_directory: str = None, skip_confirm: bool = False, file_list: list = None, progress_callback=None) -> dict:
    """
    Excel 知识库 — 追加模式，不删除已有 Word/PDF 数据
    """
    if file_list is not None:
        files_to_process = sorted(
            [Path(f) for f in file_list if Path(f).suffix.lower() in (".xls", ".xlsx")],
            key=lambda x: x.name
        )
    else:
        if excel_directory is None:
            excel_directory = Path(project_root) / "data" / "excel"
        excel_dir = Path(excel_directory)
        if not excel_dir.exists():
            return {"status": "error", "message": f"目录不存在: {excel_directory}", "chunks_count": 0}
        files_to_process = sorted(
            [f for f in excel_dir.iterdir() if f.is_file() and f.suffix.lower() in (".xls", ".xlsx")],
            key=lambda x: x.name
        )

    if not files_to_process:
        return {"status": "error", "message": "未找到 Excel 文件", "chunks_count": 0}

    try:
        connections.connect(host=os.getenv("MILVUS_HOST", "localhost"), port=int(os.getenv("MILVUS_PORT", "19530")))
    except Exception as e:
        return {"status": "error", "message": f"连接 Milvus 失败: {e}", "chunks_count": 0}

    retriever = MilvusRetriever()

    manifest = load_manifest()

    all_texts = []
    all_metadatas = []

    for i, filepath in enumerate(files_to_process):
        filename = filepath.name
        if progress_callback:
            progress_callback(i + 1, len(files_to_process), filename)
        doc_meta = manifest.get(filename, {})
        try:
            # 提取文件名中的数字编号，用于匹配专用 chunker
            match_num = re.match(r"(\d+)", filename)
            doc_num = match_num.group(1) if match_num else ""

            # 130 号文档：商业银行主要指标分机构类情况表（季度维度）
            if doc_num == "130":
                chunks = parse_excel_130_to_chunks(str(filepath), doc_meta=doc_meta)
                print(f"✅ [130专用] 解析 {filename} -> {len(chunks)} 个知识块")
            # 128/131/132 号文档：资产负债类表格
            elif doc_num in ("128", "131", "132"):
                chunks = parse_asset_liability_table_to_chunks(str(filepath), doc_meta=doc_meta)
                print(f"✅ [{doc_num}专用] 解析 {filename} -> {len(chunks)} 个知识块")
            # 33-35/57-59/90-92 号文档：经营情况类表格
            elif doc_num in ("33", "34", "35", "57", "58", "59", "90", "91", "92"):
                chunks = parse_operating_table_to_chunks(str(filepath), doc_meta=doc_meta)
                print(f"✅ [{doc_num}专用] 解析 {filename} -> {len(chunks)} 个知识块")
            else:
                chunks = parse_excel_to_chunks(str(filepath), doc_meta=doc_meta)
                print(f"✅ 解析 {filename} -> {len(chunks)} 个知识块")
        except Exception as e:
            print(f"❌ 解析 {filename} 失败: {e}")
            continue

        for chunk in chunks:
            meta = chunk["metadata"]
            if doc_meta:
                for key in ("authority", "publish_date", "doc_number", "doc_id"):
                    if not meta.get(key) and doc_meta.get(key):
                        meta[key] = doc_meta[key]
            prefix_parts = [f"[Source: {meta.get('file_name', 'unknown')}]"]
            if meta.get("sheet_name"):
                prefix_parts.append(f"Sheet: {meta['sheet_name']}")
            if meta.get("indicator"):
                prefix_parts.append(f"Indicator: {meta['indicator']}")
            if meta.get("time_dimension"):
                prefix_parts.append(f"Period: {meta['time_dimension']}")
            prefix = " | ".join(prefix_parts) + "\n"
            full_content = prefix + chunk["content"]
            all_texts.append(full_content)
            all_metadatas.append(meta)

    if not all_texts:
        return {"status": "error", "message": "未生成任何知识单元", "chunks_count": 0}

    print(f"\n📊 共生成 {len(all_texts)} 个 Excel 知识单元")
    print("开始生成向量...")
    # 清理旧 Excel 数据（避免新旧 chunk 共存）
    print("[Milvus] 清理旧 Excel 数据...")
    for filepath in files_to_process:
        retriever.delete_by_file(filepath.name)
    all_embeddings = embed_texts(all_texts)

    retriever.insert(all_texts, all_embeddings, all_metadatas)

    bm25_pickle = os.path.join(project_root, "bm25_index.pkl")
    if os.path.exists(bm25_pickle):
        os.remove(bm25_pickle)
        print(f"[BM25] 旧索引缓存已删除，下次 RAG 调用时自动重建（含 Word + PDF + Excel）")

    msg = f"Excel 知识库追加完成！新增 {len(all_texts)} 个块（未影响已有 Word/PDF 数据）。"
    print(f"🎉 {msg}")
    return {"status": "success", "message": msg, "chunks_count": len(all_texts)}


def init_knowledge_base():
    """命令行入口 — Word 文档（追加模式，保留 PDF 数据）"""
    result = build_knowledge_base()
    if result["status"] == "success":
        print(result["message"])
    else:
        print(f"❌ {result['message']}")


def init_pdf():
    """命令行入口 — PDF 文档（追加模式）"""
    result = build_pdf_knowledge_base()
    if result["status"] == "success":
        print(result["message"])
    else:
        print(f"❌ {result['message']}")


def init_excel():
    """命令行入口 — Excel 文档（追加模式）"""
    result = build_excel_knowledge_base()
    if result["status"] == "success":
        print(result["message"])
    else:
        print(f"❌ {result['message']}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="知识库初始化")
    parser.add_argument("--mode", choices=["word", "pdf", "excel", "all"], default="pdf",
                        help="word=Word全量重建, pdf=PDF追加(默认), excel=Excel追加, all=Word+PDF+Excel")
    args = parser.parse_args()

    if args.mode == "word":
        init_knowledge_base()
    elif args.mode == "pdf":
        init_pdf()
    elif args.mode == "excel":
        init_excel()
    elif args.mode == "all":
        init_knowledge_base()
        init_pdf()
        init_excel()