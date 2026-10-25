"""
['090_2024年9月人身险公司经营情况表_2024年9月人身险公司经营情况表.xlsx']
['092_2024年9月保险业经营情况表_2024年9月保险业经营情况表.xlsx']
['091_2024年9月财产保险公司经营情况表_2024年9月财产险公司经营情况表.xlsx']
['057_2025年3月人身险公司经营情况表_2025年3月人身险公司经营情况表.xlsx']
['059_2025年3月保险业经营情况表_2025年3月保险业经营情况表.xlsx']
['058_2025年3月财产保险公司经营情况表_2025年3月财产险公司经营情况表.xlsx']
['033_2025年9月人身险公司经营情况表_2025年9月人身险公司经营情况表.xlsx']
['035_2025年9月保险业经营情况表_2025年9月保险业经营情况表.xlsx']
['034_2025年9月财产保险公司经营情况表_2025年9月财产险公司经营情况表.xlsx']"""

import pandas as pd
import json
import sys
from pathlib import Path
from typing import Dict, Any, Optional
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
    if "保险金额" in s or "保额" in s:
        return "sum_insured"
    if "净资产" in s:
        return "equity"
    return "other"

SECTION_KEYWORDS = [
    ("premium", ["原保险保费收入"]),
    ("claim", ["赔付支出", "赔款支出"]),
    ("sum_insured", ["保险金额", "新增保险金额"]),
    ("asset", ["总资产"]),
]

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

def parse_operating_table_to_chunks(file_path, doc_meta: Optional[Dict[str, Any]] = None):
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
        print(f"  [Sheet] {sheet_name}: header_row={header_row}")
        col_names = build_column_names(df_raw.iloc[header_row])
        print(f"  [Sheet] {sheet_name}: cols={col_names}")

        data_start = header_row + 1
        df = df_raw.iloc[data_start:].copy()
        df.columns = col_names
        df = df.reset_index(drop=True)
        print(f"  [Sheet] {sheet_name}: after reset, shape={df.shape}")

        indicators = []
        values = []
        periods = []
        indicator_sections = []
        current_section = "premium"
        for idx, row in df.iterrows():
            if row.isnull().all():
                continue
            indicator, skip = get_indicator_from_row(row, df)
            if skip or not indicator:
                continue
            if any(keyword in indicator for keyword in ["指标", "项目", "序号"]):
                continue
            sec = detect_section(indicator)
            if sec:
                current_section = sec
            val = None
            period = None
            for col_name, col_val in row.items():
                col_lower = str(col_name).lower()
                if "本年累计" in col_lower or "截至当期" in col_lower:
                    val = col_val
                    period = str(col_name)
                    break
            if val is None and df.shape[1] >= 2:
                val = row.iloc[1]
                period = str(df.columns[1])
            if pd.notna(val):
                indicators.append(indicator)
                values.append(val)
                periods.append(period)
                indicator_sections.append(current_section)

        print(f"  [Sheet] {sheet_name}: indicators={len(indicators)}, values={len(values)}")
        if indicators:
            print(f"  [Sheet] {sheet_name}: first 3 indicators={indicators[:3]}, first 3 values={values[:3]}")

        hierarchy = build_hierarchy(indicators)
        print(f"  [Sheet] {sheet_name}: hierarchy={len(hierarchy)}")
        if hierarchy:
            print(f"  [Sheet] {sheet_name}: first 3 nodes={[(n['name'], n['level'], n['parent']) for n in hierarchy[:3]]}")

        for i, node in enumerate(hierarchy):
            full_path = get_full_path(hierarchy, node)
            val = values[i]
            metric_type = infer_metric_type(full_path)
            if i < len(indicator_sections) and indicator_sections[i] != "premium":
                metric_type = indicator_sections[i]
            elif metric_type == "other" and node["parent"]:
                parent_path = get_full_path(hierarchy, {"name": node["parent"], "parent": None, "index": 0, "level": 1})
                metric_type = infer_metric_type(parent_path) or metric_type
            period = periods[i]
            if not pd.notna(val):
                continue
            val_str = str(val)
            try:
                num_val = float(val_str)
            except (ValueError, TypeError):
                num_val = None
            content = f"{full_path}：{val_str}"
            chunk_id = f"{Path(file_path).stem}_{sheet_name}_{node['name'].replace(':', '')}"
            chunks.append({
                "id": chunk_id,
                "content": content,
                "metadata": {
                    **metadata_base,
                    "sheet_name": sheet_name,
                    "indicator_path": full_path,
                    "indicator_name": node["name"],
                    "parent_indicator": node["parent"],
                    "level": node["level"],
                    "metric_type": metric_type,
                    "period": period,
                    "value": num_val,
                    "tags": ["operating_table", "semantic_chunk", metric_type]
                }
            })

    return chunks

def main():
    if len(sys.argv) != 3:
        print("用法: python chunker_excel_33_34_35.py <输入.xlsx> <输出.jsonl>")
        sys.exit(1)
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    chunks = parse_operating_table_to_chunks(input_path)
    with open(output_path, 'w', encoding='utf-8') as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + '\n')
    print(f"解析完成，共生成 {len(chunks)} 个知识单元，已保存至 {output_path}")

if __name__ == "__main__":
    main()