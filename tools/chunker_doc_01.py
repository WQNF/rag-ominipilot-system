#!/usr/bin/env python3
"""
解析 .doc 文件（旧版 Word 格式）的工具。
依赖：需要安装 Microsoft Word 或 LibreOffice。
如果安装了 Word，使用 pywin32 转换；否则尝试用 LibreOffice 转换。
转换后的 .docx 文件会临时保存，并调用 chunker_docx_01 进行解析。
"""

import os
import tempfile
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

from tools.chunker_docx_01 import parse_docx_to_chunks_advanced


def convert_with_word(doc_path: str, docx_path: str) -> bool:
    """使用 Microsoft Word 转换（需要 pywin32 和 Word 安装）"""
    import time
    try:
        import win32com.client as win32
        import pythoncom

        # 1) 先彻底清理上一个 Word 进程（避免 RPC_E_CALL_REJECTED）
        os.system('taskkill /f /im WINWORD.EXE 2>nul')
        time.sleep(0.5)

        # 2) 重新初始化 COM 线程模型
        pythoncom.CoInitialize()

        # 3) 用 Dispatch 替代 EnsureDispatch（避免 gencache 缓存冲突）
        word = win32.Dispatch('Word.Application')
        word.Visible = False
        word.DisplayAlerts = False      # ← 关键：禁止所有弹窗

        doc = word.Documents.Open(doc_path)
        doc.SaveAs(docx_path, FileFormat=16)
        doc.Close()
        word.Quit()
        word = None
        pythoncom.CoUninitialize()
        return True
    except Exception as e:
        print(f"Word 转换失败: {e}")
        # 兜底清理
        try:
            os.system('taskkill /f /im WINWORD.EXE 2>nul')
        except:
            pass
        return False


def convert_with_libreoffice(doc_path: str, docx_path: str) -> bool:
    """使用 LibreOffice 转换（自动检测安装路径）"""
    import time

    # 自动检测 LibreOffice 安装路径
    # 优先使用环境变量 LIBREOFFICE_PATH，其次自动检测默认路径
    env_path = os.getenv('LIBREOFFICE_PATH', '').strip()
    if env_path:
        libreoffice_paths = [env_path]
    else:
        libreoffice_paths = [
            'libreoffice',  # 默认 PATH 查找
            r'C:\Program Files\LibreOffice\program\soffice.exe',
            r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
            r'D:\Program Files\LibreOffice\program\soffice.exe',
        ]

    # 找到第一个存在的路径
    soffice = None
    for p in libreoffice_paths:
        if p == 'libreoffice' or Path(p).exists():
            soffice = p
            break

    if soffice is None:
        print("LibreOffice 未安装或未找到安装路径。")
        return False

    try:
        outdir = str(Path(docx_path).parent)
        result = subprocess.run(
            [soffice, '--headless', '--convert-to', 'docx', '--outdir', outdir, doc_path],
            check=True, timeout=120,
            capture_output=True, text=True
        )
        # LibreOffice 输出文件名 = 原文件名（不含扩展名）+ .docx
        original_name = Path(doc_path).stem + '.docx'
        generated = Path(outdir) / original_name
        if generated.exists() and generated != Path(docx_path):
            # 等待文件写入完成
            time.sleep(0.5)
            generated.rename(docx_path)
        return True
    except subprocess.TimeoutExpired:
        print("LibreOffice 转换超时（>120秒）")
        return False
    except Exception as e:
        print(f"LibreOffice 转换失败: {e}")
        return False


def parse_doc_to_chunks(file_path: str, chunk_size: int = 800, ocr_lang: Optional[str] = 'eng', doc_meta: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    解析 .doc 文件，返回 chunk 列表。

    参数:
        file_path: .doc 文件路径
        chunk_size: 分块大小（字符数）
        ocr_lang: OCR 语言（例如 'eng'），为 None 则跳过 OCR
        doc_meta: 外部传入的文档级元数据（如 doc_id/title/authority/publish_date/source_url）

    返回:
        与 parse_docx_to_chunks_advanced 相同格式的 chunk 列表
    """
    if not file_path.lower().endswith('.doc'):
        raise ValueError("文件不是 .doc 格式，请使用 .doc 文件。")

    # 创建临时 .docx 文件
    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp:
        tmp_docx = tmp.name

    # 尝试转换（Windows 下优先用 Word，失败则尝试 LibreOffice）
    converted = False
    if os.name == 'nt':
        converted = convert_with_word(file_path, tmp_docx)
    if not converted:
        converted = convert_with_libreoffice(file_path, tmp_docx)
    if not converted:
        raise RuntimeError("无法将 .doc 文件转换为 .docx，请确保已安装 Microsoft Word 或 LibreOffice。")

    try:
        # 调用高级 docx 解析器
        chunks = parse_docx_to_chunks_advanced(tmp_docx, chunk_size=chunk_size, ocr_lang=ocr_lang or 'eng', doc_meta=doc_meta)
        # 修复 file_name 元数据：覆盖临时文件的名称，恢复原始 .doc 文件名称
        original_name = Path(file_path).name
        for chunk in chunks:
            if "metadata" in chunk:
                chunk["metadata"]["file_name"] = original_name
                chunk["metadata"]["file_type"] = "doc"
    finally:
        # 清理临时文件
        if os.path.exists(tmp_docx):
            os.unlink(tmp_docx)

    return chunks