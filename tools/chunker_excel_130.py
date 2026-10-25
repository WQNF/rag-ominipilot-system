"""
130 号文档专用分块器 — 商业银行主要指标分机构类情况表（季度）
- 特殊处理：col0 是季度标签（一季度/二季度/三季度/四季度），需跟踪并拼入每个 chunk
- 列切块按季度拆分，避免混合所有季度数据
- 复用 chunker_excel 的通用工具函数
"""

import re
from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd

from tools.chunker_excel import (
    detect_header_row,
    build_column_names,
    get_indicator_from_row,
    extract_metadata_from_excel,
)


def parse_excel_130_to_chunks(file_path, doc_meta: Optional[Dict[str, Any]] = None):
    """
    读取 130 号 Excel 文件，将每一行转为自然语言句子。
    返回知识单元列表，每个单元包含 content 和 metadata。
    doc_meta: 外部传入的文档级元数据
    """
    if doc_meta is None:
        doc_meta = {}
    chunks = []
    metadata_base = extract_metadata_from_excel(file_path)
    metadata_base["authority"] = doc_meta.get("authority", "")
    metadata_base["publish_date"] = doc_meta.get("publish_date", "")
    metadata_base["doc_id"] = doc_meta.get("doc_id", "")
    xl = pd.ExcelFile(file_path)

    for sheet_name in xl.sheet_names:
        df_raw = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
        if df_raw.empty:
            continue

        header_row = detect_header_row(df_raw)
        col_names = build_column_names(df_raw.iloc[header_row])

        data_start = header_row + 1
        df = df_raw.iloc[data_start:].copy()
        df.columns = col_names
        df = df.reset_index(drop=True)

        # 归一化空格
        df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
        for col in df.columns:
            df[col] = df[col].apply(
                lambda x: re.sub(r'\s+', '', str(x)) if isinstance(x, str) else x
            )

        print(f"[Sheet] {sheet_name}: 检测到表头在第 {header_row + 1} 行, 列名={col_names[:8]}...")

        subtitle_keywords = [
            "情况表", "资产负债", "指标分机构", "监管指标", "资产总负债",
            "小微企业贷款", "涉农贷款", "偿付能力", "资金运用",
        ]

        # ===== 行切块：跟踪季度标签 =====
        current_quarter = ""
        for idx, row in df.iterrows():
            if row.isnull().all():
                continue

            col0_str = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
            time_keywords = ['季度', '月', '年', '当期', '累计', '同比', '环比']
            if any(kw in col0_str for kw in time_keywords):
                current_quarter = col0_str

            indicator, skip = get_indicator_from_row(row, df)
            if skip or not indicator:
                continue
            if any(keyword in indicator for keyword in ["指标", "项目", "序号"]):
                continue
            other_cols_filled = sum(
                1 for c in range(1, len(df.columns)) if pd.notna(row.iloc[c])
            )
            if other_cols_filled == 0 and indicator and any(
                kw in indicator for kw in subtitle_keywords
            ):
                continue

            desc_parts = []
            for col_idx in range(len(df.columns)):
                col_name = df.columns[col_idx]
                val = row.iloc[col_idx]
                if pd.notna(val):
                    val_str = str(val).strip()
                    if col_idx == 0 and any(kw in val_str for kw in time_keywords):
                        continue  # col0 是时间标签，已作为 quarter 前缀，跳过
                    desc_parts.append(f"{col_name}: {val}")
            if not desc_parts:
                continue

            if current_quarter:
                sentence = (
                    f"在{sheet_name}工作表中，{current_quarter}，"
                    f"{'，'.join(desc_parts)}。"
                )
            else:
                sentence = f"在{sheet_name}工作表中，{'，'.join(desc_parts)}。"

            chunk_id = (
                f"{Path(file_path).stem}_{sheet_name}_row{idx}"
            )

            time_dimension = current_quarter if current_quarter else None
            if time_dimension is None:
                for col in df.columns:
                    col_str = str(col)
                    if "期" in col_str or "季度" in col_str or "月" in col_str:
                        time_dimension = col_str
                        break

            ind_col_names = [
                str(df.columns[c])
                for c in range(len(df.columns))
                if pd.notna(row.iloc[c])
            ]
            indicator_rich = f"{indicator} | {' | '.join(ind_col_names)}"

            chunks.append({
                "id": chunk_id,
                "content": sentence,
                "metadata": {
                    **metadata_base,
                    "sheet_name": sheet_name,
                    "row_index": idx,
                    "indicator": indicator_rich[:200],
                    "time_dimension": time_dimension,
                    "normative_strength": None,
                    "tags": ["table_row"],
                },
            })

        # ===== 列切块：按季度拆分 =====
        MAX_PER_COL_CHUNK = 20
        col_data = {}  # {(col_idx, quarter): [(indicator, value), ...]}
        current_quarter = ""
        for idx, row in df.iterrows():
            if row.isnull().all():
                continue

            col0_str = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
            time_keywords = ['季度', '月', '年', '当期', '累计', '同比', '环比']
            if any(kw in col0_str for kw in time_keywords):
                current_quarter = col0_str

            indicator, skip = get_indicator_from_row(row, df)
            if skip or not indicator:
                continue
            if any(keyword in indicator for keyword in ["指标", "项目", "序号"]):
                continue
            other_cols_filled = sum(
                1 for c in range(1, len(df.columns)) if pd.notna(row.iloc[c])
            )
            if other_cols_filled == 0 and indicator and any(
                kw in indicator for kw in subtitle_keywords
            ):
                continue
            for col_idx in range(1, len(df.columns)):
                val = row.iloc[col_idx]
                if pd.notna(val):
                    key = (col_idx, current_quarter)
                    if key not in col_data:
                        col_data[key] = []
                    col_data[key].append((indicator, val))

        for (col_idx, quarter), pairs in col_data.items():
            col_name = df.columns[col_idx]
            all_same = all(
                str(indicator) == str(val) for indicator, val in pairs
            )
            if all_same:
                print(
                    f"  [列切块] 跳过列 '{col_name}'"
                    f"（所有值均与 indicator 相同，无实际数值）"
                )
                continue

            for group_i in range(0, len(pairs), MAX_PER_COL_CHUNK):
                group = pairs[group_i : group_i + MAX_PER_COL_CHUNK]
                desc_parts = [f"{indicator}: {val}" for indicator, val in group]
                if quarter:
                    sentence = (
                        f"在{sheet_name}工作表中，{quarter}，"
                        f"{col_name}口径下各指标数值：{'，'.join(desc_parts)}。"
                    )
                else:
                    sentence = (
                        f"在{sheet_name}工作表中，"
                        f"{col_name}口径下各指标数值：{'，'.join(desc_parts)}。"
                    )
                chunk_id_col = (
                    f"{Path(file_path).stem}_{sheet_name}"
                    f"_col{col_idx}_{quarter}"
                    f"_g{group_i // MAX_PER_COL_CHUNK}"
                )
                chunks.append({
                    "id": chunk_id_col,
                    "content": sentence,
                    "metadata": {
                        **metadata_base,
                        "sheet_name": sheet_name,
                        "column_name": col_name,
                        "indicator": col_name,
                        "time_dimension": quarter if quarter else None,
                        "normative_strength": None,
                        "tags": ["table_column", "institution_view"],
                    },
                })

    return chunks