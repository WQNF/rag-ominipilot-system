import os
import re
import json
from pathlib import Path
from typing import Dict, Optional


def _extract_from_filename(filename: str) -> Dict[str, str]:
    """
    从文件名中自动提取元数据。
    命名格式: {编号}_{标题/发文机关}_{子标题}.{扩展名}

    例如:
    385_国家金融监督管理总局关于印发《保险公司资本保证金管理办法》的通知_附件：xxx.doc
    → doc_id=NFRA_385, authority=国家金融监督管理总局, title=保险公司资本保证金管理办法
    """
    meta = {
        "doc_id": "",
        "title": "",
        "authority": "",
        "publish_date": "",
        "source_url": "",
        "doc_number": "",
    }

    stem = Path(filename).stem
    parts = stem.split("_", 1)
    doc_num = parts[0] if parts else ""

    if doc_num.isdigit():
        meta["doc_id"] = f"NFRA_{doc_num}"

    remaining = parts[1] if len(parts) > 1 else stem

    authority_keywords = sorted([
        "国家金融监督管理总局", "中国银行保险监督管理委员会", "中国银保监会",
        "中国人民银行", "财政部", "财政部办公厅", "金融监管总局办公厅",
        "国家知识产权局", "国家版权局", "中国证券监督管理委员会",
        "国家互联网信息办公室", "国家外汇管理局",
    ], key=len, reverse=True)

    found_authorities = []
    for kw in authority_keywords:
        if kw in remaining:
            found_authorities.append(kw)
            remaining = remaining.replace(kw, "", 1)

    if found_authorities:
        meta["authority"] = "、".join(found_authorities)

    title_patterns = [
        r'关于印发[《〈](.+?)[》〉]',
        r'关于[印发]*[《〈](.+?)[》〉]',
        r'《(.+?)》',
    ]
    for pat in title_patterns:
        m = re.search(pat, remaining)
        if m:
            meta["title"] = m.group(1)
            break

    if not meta["title"]:
        if meta["authority"]:
            for kw in found_authorities:
                remaining = remaining.replace(kw, "")
            remaining = remaining.strip("_")
        for prefix in ["关于印发", "关于", "的通知", "的批复"]:
            remaining = remaining.replace(prefix, "")
        remaining = remaining.strip("_").strip()
        if remaining:
            meta["title"] = remaining.split("_")[0] if "_" in remaining else remaining

    date_match = re.search(r"(\d{4})年", remaining)
    if date_match:
        meta["publish_date"] = date_match.group(1)

    if meta["authority"]:
        if "国家金融监督管理总局" in meta["authority"]:
            meta["source_url"] = "https://www.nfra.gov.cn"
        elif "中国人民银行" in meta["authority"]:
            meta["source_url"] = "https://www.pbc.gov.cn"

    return meta


def load_manifest(data_dir: str = None) -> Dict[str, Dict[str, str]]:
    """
    加载所有文件的元数据映射。
    返回: {文件名: {doc_id, title, authority, publish_date, source_url, doc_number}}

    优先级: manual_meta.json > 自动从文件名提取
    """
    if data_dir is None:
        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data"
        )

    manual_path = os.path.join(data_dir, "manual_meta.json")
    manual_meta: Dict[str, Dict[str, str]] = {}
    if os.path.exists(manual_path):
        with open(manual_path, "r", encoding="utf-8") as f:
            manual_meta = json.load(f)

    file_dirs = [
        os.path.join(data_dir, "word", "doc"),
        os.path.join(data_dir, "word", "docx"),
        os.path.join(data_dir, "pdf"),
        os.path.join(data_dir, "excel"),
    ]

    manifest: Dict[str, Dict[str, str]] = {}

    for dir_path in file_dirs:
        if not os.path.isdir(dir_path):
            continue
        for fname in os.listdir(dir_path):
            file_path = os.path.join(dir_path, fname)
            if not os.path.isfile(file_path):
                continue

            if fname in manual_meta:
                manifest[fname] = manual_meta[fname]
            else:
                manifest[fname] = _extract_from_filename(fname)

            ext = Path(fname).suffix.lower()
            if ext == ".doc":
                manifest[fname]["file_type"] = "doc"
            elif ext == ".docx":
                manifest[fname]["file_type"] = "docx"
            elif ext == ".pdf":
                manifest[fname]["file_type"] = "pdf"
            elif ext in (".xls", ".xlsx"):
                manifest[fname]["file_type"] = "excel"

    return manifest


if __name__ == "__main__":
    manifest = load_manifest()
    print(f"共加载 {len(manifest)} 个文件的元数据")
    for fname, meta in list(manifest.items())[:5]:
        print(f"\n📄 {fname}")
        for k, v in meta.items():
            if v:
                print(f"  {k}: {v}")