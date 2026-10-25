"""FILES = [
128_2023年银行业总资产、总负债（季度）_银行业金融机构资产负债情况表(季度).xlsx",
131_2023年银行业金融机构普惠型小微企业贷款情况（季度）_银行业金融机构普惠型小微企业贷款情况表(季度).xlsx",
132_2023年银行业金融机构保障性安居工程贷款情况表(季度)_银行业金融机构保障性安居工程贷款情况表(季度).xlsx",
]"""

import pandas as pd
import json
import sys
import re
from pathlib import Path
from typing import Dict, Any, Optional
from tools.chunker_excel import detect_header_row, build_column_names, get_indicator_from_row

def normalize_indicator(name):
    if pd.isna(name):
        return ""
    s = str(name).strip()
    s = s.replace("  ", " ").replace("\n", "")
    return s

def infer_metric_type(indicator_full_path):
    s = indicator_full_path.lower()
    if any(keyword in s for keyword in ["保费", "原保险", "财产险", "人身险", "寿险", "意外险", "健康险", "机动车辆保险", "责任险", "农业保险"]):
        return "premium"
    if "赔款" in s or "赔付" in s:
        return "claim"
    if "资产" in s:
        return "asset"
    if "负债" in s:
        return "liability"
    if "贷款" in s:
        return "loan"
    if "保险金额" in s or "保额" in s:
        return "sum_insured"
    if "净资产" in s:
        return "equity"
    return "other"

SECTION_KEYWORDS = [
    ("asset", ["总资产"]),
    ("liability", ["总负债"]),
    ("loan", ["贷款"]),
]


def _map_col_label(col_idx):
    """将列索引映射为可读的季度标签"""
    labels = {
        1: "2023年一季度", 2: "2023年二季度", 3: "2023年三季度", 4: "2023年四季度",
        5: "2022年一季度", 6: "2022年二季度", 7: "2022年三季度", 8: "2022年四季度",
    }
    return labels.get(col_idx, f"col_{col_idx}")

def detect_section(indicator):
    s = indicator.strip()
    for sec_type, keywords in SECTION_KEYWORDS:
        for kw in keywords:
            if kw in s:
                return sec_type
    return None

def build_hierarchy(indicators):
    hierarchy = []
    stack = []
    for idx, indicator in enumerate(indicators):
        if not indicator:
            continue
        level = 1
        parent = None
        if "其中：" in indicator or "其中:" in indicator:
            level = 2
            if stack:
                parent = stack[-1]["name"]
        else:
            stack = [{"name": indicator, "level": level}]
        hierarchy.append({
            "index": idx,
            "name": indicator,
            "level": level,
            "parent": parent
        })
    return hierarchy

def get_full_path(hierarchy, current):
    if current["parent"] is None:
        return current["name"]
    for node in hierarchy:
        if node["name"] == current["parent"]:
            parent_path = get_full_path(hierarchy, node)
            return f"{parent_path} > {current['name']}"
    return current["name"]

def parse_asset_liability_table_to_chunks(file_path, doc_meta: Optional[Dict[str, Any]] = None):
    if doc_meta is None:
        doc_meta = {}
    xls = pd.ExcelFile(file_path)
    chunks = []
    metadata_base = {
        "file_name": Path(file_path).name,
        "source_file": str(file_path),
        "authority": doc_meta.get("authority", ""),
        "publish_date": doc_meta.get("publish_date", ""),
        "doc_id": doc_meta.get("doc_id", ""),
    }

    for sheet_name in xls.sheet_names:
        df_raw = xls.parse(sheet_name, header=None)
        print(f"  [Sheet] {sheet_name}: raw shape={df_raw.shape}")
        header_row = detect_header_row(df_raw)
        print(f"  [Sheet] {sheet_name}: detected header at row {header_row}")
        col_names = build_column_names(df_raw.iloc[header_row])
        print(f"  [Sheet] {sheet_name}: col_names={col_names[:10]}...")

        # ===== 表头之后的详情 section =====
        # 表头前几行是标题/元数据，真正的「银行业金融机构合计」数据在表头后、
        # 第一个子机构标题（如"其中：商业银行合计"）之前
        data_start = header_row + 1
        df = df_raw.iloc[data_start:].copy()
        df.columns = col_names
        df = df.reset_index(drop=True)
        print(f"  [Sheet] {sheet_name}: detail data shape={df.shape}")

        current_institution = "银行业金融机构合计"
        # 131/132 的第一行indicator就是"银行业金融机构合计"，不需要再加前缀
        fname = Path(file_path).name
        if any(prefix in fname for prefix in ["131_", "132_"]):
            current_institution = ""
        for idx, row in df.iterrows():
            if row.isnull().all():
                continue
            indicator, skip = get_indicator_from_row(row, df)
            if skip or not indicator:
                continue
            if any(keyword in indicator for keyword in ["指标", "项目", "序号", "时间"]):
                continue

            # 判断是否为机构标题行：第1列有值，其余列全空
            other_cols_na = all(pd.isna(row.iloc[c]) for c in range(1, len(df.columns)))
            if other_cols_na and indicator:
                # 清理机构名：去掉编号前缀如 "2.大型商业银行" → "大型商业银行"
                clean_name = re.sub(r'^\d+[\.、\s]+', '', indicator).strip()
                if clean_name and not any(kw in indicator for kw in ["注", "数据来源", "说明"]):
                    current_institution = clean_name
                    print(f"    [DEBUG] 机构切换: {current_institution}")
                continue

            # 构建完整路径
            if current_institution:
                full_path = f"{current_institution} > {indicator}"
            else:
                full_path = indicator

            mt = infer_metric_type(full_path)
            # 如果 infer_metric_type 返回 other，根据文件名兜底
            if mt == "other":
                if "贷款" in fname:
                    mt = "loan"
                elif "安居" in fname:
                    mt = "loan"

            desc_parts = []
            periods = []
            for col_idx in range(len(df.columns)):
                col_name = df.columns[col_idx]
                val = row.iloc[col_idx]
                if pd.isna(val):
                    continue
                col_str = str(col_name).strip()
                if col_str == "":
                    continue
                desc_parts.append(f"{col_str}: {val}")
                periods.append(col_str)
            print(f"    [DEBUG] row idx={idx} indicator='{indicator}' desc_parts={len(desc_parts)} other_cols_na={other_cols_na}")
            if desc_parts:
                # 术语标签：关键词前置帮助向量搜索命中
                tag = ""
                if "银行业金融机构合计" in full_path:
                    if "总资产" in full_path:
                        tag = "年-季度总资产："
                    elif "总负债" in full_path:
                        tag = "年-季度总负债："
                    else:
                        tag = "年-季度贷款："
                elif "商业银行合计" in full_path:
                    if "总资产" in full_path:
                        tag = "季度-季度总资产："
                    elif "总负债" in full_path:
                        tag = "季度-季度总负债："
                    else:
                        tag = "季度-季度贷款："
                else:
                    fname = Path(file_path).name
                    if any(prefix in fname for prefix in ["131_", "132_"]):
                        tag = "年-季度贷款："
                    else:
                        tag = ""
                content = f"{tag}在 {sheet_name}工作表中，{full_path}，{'，'.join(desc_parts)}。"
                chunks.append({
                    "content": content,
                    "metadata": {
                        **metadata_base,
                        "sheet_name": sheet_name,
                        "indicator": full_path,
                        "type": mt,
                        "period": " | ".join(periods),
                    }
                })

    print(f"\n[Done] Total chunks: {len(chunks)}")
    return chunks

if __name__ == "__main__":
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        chunks = parse_asset_liability_table_to_chunks(file_path)
        print(f"\nFirst 5 chunks:")
        for i, c in enumerate(chunks[:5]):
            print(f"  [{i}] {c['content'][:120]}")
        with open("debug_chunks.json", "w", encoding="utf-8") as f:
            json.dump(chunks[:20], f, ensure_ascii=False, indent=2)