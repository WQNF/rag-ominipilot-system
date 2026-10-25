# config/documents.py
import os
import re
from pathlib import Path

# ===== 项目数据根目录 =====
DATA_DIR = Path(__file__).parent.parent / "data"

# ===== 各类型文档的子目录 =====
DOC_DIRS = {
    "word": DATA_DIR / "word" / "docx",
    "pdf": DATA_DIR / "pdf",
    "excel": DATA_DIR / "excel",
    "doc_old": DATA_DIR / "word" / "doc",
}

# 支持的扩展名
EXTENSIONS = (".docx", ".doc", ".pdf", ".xls", ".xlsx")


def _normalize_name(filename: str) -> str:
    """
    从文件名中提取干净的可读名称（去掉编号前缀和扩展名）
    例如: "397_财政部...通知_银行函证工作操作指引.docx" -> "银行函证工作操作指引"
    """
    # 去掉扩展名
    name = Path(filename).stem
    # 去掉开头的数字编号（如 "397_"）
    name = re.sub(r"^\d+[_.]\s*", "", name)
    # 取最后一个 _ 后面的部分（通常是最具体的名称）
    parts = name.split("_")
    if len(parts) > 1:
        # 尝试找到最具体的名称片段
        # 取最后一段，如果太短（<4字）则取整名
        last = parts[-1]
        if len(last) >= 4:
            name = last
    return name


def scan_all_documents() -> list:
    """
    扫描 data/ 目录下所有文档，返回完整文件名列表。
    """
    all_docs = []
    for dir_path in DOC_DIRS.values():
        if dir_path.exists():
            for f in dir_path.iterdir():
                if f.is_file() and f.suffix.lower() in EXTENSIONS:
                    all_docs.append(f.name)
    all_docs.sort()
    return all_docs


# ===== 保留原有硬编码列表作为快速回退 =====
DOCUMENT_NAMES = [
    "金融机构反洗钱和反恐怖融资监督管理办法（修订稿）.docx",
    "金融机构信贷资产证券化管理办法.docx",
    "银行业金融机构消费者权益保护工作评价办法.docx",
    "商业银行预期信用损失法实施管理办法（试行）.pdf",
    "商业银行资本管理办法（试行）节选.pdf",
]

EXCEL_NAMES = [
    "普惠金融重点指标统计表（2026年一季度）.xlsx",
    "投诉分类统计表_2026年一季度.xlsx",
    "小微企业贷款分行业统计表_20251231.xlsx",
    "主要金融指标统计表-资本充足率.xlsx",
]

# ===== 动态扫描获取全部文档列表（优先使用） =====
_ALL_DOCS_CACHE = None


def get_all_doc_names(refresh: bool = False) -> list:
    """
    获取全部文档名称（带缓存）
    """
    global _ALL_DOCS_CACHE
    if _ALL_DOCS_CACHE is None or refresh:
        scanned = scan_all_documents()
        if scanned:
            _ALL_DOCS_CACHE = scanned
        else:
            # 扫描失败时回退到硬编码列表
            _ALL_DOCS_CACHE = DOCUMENT_NAMES + EXCEL_NAMES
    return _ALL_DOCS_CACHE


# 兼容旧代码：保持 ALL_DOC_NAMES 变量可用
ALL_DOC_NAMES = get_all_doc_names()

# ===== 预计算干净名称映射表 =====
# 用于模糊匹配：{ 干净名称: 原始文件名 }
_DISPLAY_NAME_MAP = None


def get_display_name_map(refresh: bool = False) -> dict:
    """
    生成 { 规范化名称: 原始文件名 } 的映射表
    """
    global _DISPLAY_NAME_MAP
    if _DISPLAY_NAME_MAP is None or refresh:
        _DISPLAY_NAME_MAP = {}
        for fname in get_all_doc_names(refresh=refresh):
            clean = _normalize_name(fname)
            _DISPLAY_NAME_MAP[clean] = fname
            # 也索引原始文件名本身
            _DISPLAY_NAME_MAP[fname] = fname
    return _DISPLAY_NAME_MAP