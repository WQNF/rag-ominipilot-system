#!/usr/bin/env python3
"""
解析银行业监管类 Excel 统计报表，将每一行转换为自然语言句子，输出 JSONL 知识库。
依赖：pip install pandas openpyxl
用法：python chunker_excel.py <输入.xlsx> <输出.jsonl>
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd

def extract_metadata_from_excel(excel_path):
    """
    从 Excel 文件名和内容中提取基础元数据。
    返回字典。
    """
    metadata = {
        "file_name": Path(excel_path).name,
        "doc_id": "",
        "file_type": "xlsx",
        "source_url": str(excel_path),
        "publish_date": None,
        "effective_date": None,
        "authority": None,
        "doc_number": None
    }
    return metadata


def detect_header_row(df_raw):
    """
    自动检测真正的表头行（0-based index）。
    遍历前 15 行，根据非空列数、字符串比例、关键词命中计算得分，返回最高分所在行。
    """
    best_row = 0
    best_score = 0
    for row_idx in range(min(15, len(df_raw))):
        row = df_raw.iloc[row_idx]
        non_null = row.notna().sum()
        str_cells = [v for v in row if pd.notna(v) and isinstance(v, str) and str(v).strip()]
        str_count = len(str_cells)
        kw_count = sum(
            1 for v in str_cells
            if any(kw in str(v) for kw in [
                '指标', '项目', '机构', '银行', '余额', '比率',
                '率', '合计', '季度', '月份', '当期', '累计', '总额',
                '资产', '负债', '贷款', '收入', '支出', '保费',
                '资金', '拨备', '资本', '不良', '正常', '关注',
            ])
        )
        score = non_null * 1 + str_count * 2 + kw_count * 3
        # 惩罚「仅第 0 列有值」的行（典型合并标题行，非真正的多列列名行）
        if non_null <= 2 and str_count <= 1:
            score = 0
        if score > best_score:
            best_score = score
            best_row = row_idx
    return best_row


def build_column_names(header_series):
    """
    从检测到的表头行提取列名，处理合并单元格（向前填充 None），并去重。
    """
    col_names = []
    for i, val in enumerate(header_series):
        if pd.notna(val) and str(val).strip():
            col_names.append(str(val).strip())
        else:
            col_names.append(None)
    # 向前填充（处理合并单元格）
    for i in range(1, len(col_names)):
        if col_names[i] is None:
            col_names[i] = col_names[i - 1]
    # 剩余 None 用列号填充
    for i in range(len(col_names)):
        if col_names[i] is None:
            col_names[i] = f"列{i + 1}"
    # 处理重复列名：追加 _2, _3... 后缀（向前填充会制造重复）
    seen = {}
    unique_names = []
    for name in col_names:
        if name in seen:
            seen[name] += 1
            unique_names.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            unique_names.append(name)
    return unique_names


def get_indicator_from_row(row, df):
    """
    智能提取行的指标名。
    当列0是时间标签（季度/月/年）时，自动用列1作为指标名。
    同时过滤脚注行（注：、数字+顿号开头）。
    返回 (indicator: str, skip: bool)
    """
    col0 = row.iloc[0]
    col0_str = str(col0).strip() if pd.notna(col0) else ""

    # 跳过脚注行
    if col0_str and (col0_str.startswith("注") or col0_str.startswith("数据来源") or col0_str.startswith("说明")):
        return "", True
    if col0_str and re.match(r'^\d+[、，,]', col0_str):
        return "", True

    # 列0是时间标签 → 用列1
    time_keywords = ['季度', '月', '年', '当期', '累计', '同比', '环比']
    if col0_str and any(kw in col0_str for kw in time_keywords):
        if len(df.columns) > 1:
            col1 = row.iloc[1]
            col1_str = str(col1).strip() if pd.notna(col1) else ""
            if col1_str:
                return col1_str, False
        return col0_str, False

    # 列0为空但列1有值 → 用列1
    if not col0_str and len(df.columns) > 1:
        col1 = row.iloc[1]
        col1_str = str(col1).strip() if pd.notna(col1) else ""
        if col1_str:
            return col1_str, False

    return col0_str, False


def parse_excel_to_chunks(file_path, doc_meta: Optional[Dict[str, Any]] = None):
    """
    读取 Excel 文件，自动检测表头行，将每一行转为自然语言句子。
    返回知识单元列表，每个单元包含 content 和 metadata。
    doc_meta: 外部传入的文档级元数据（如 doc_id/title/authority/publish_date/source_url）
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

        # 归一化空格：全  国→全国，北  京→北京，避免 reranker/LLM 匹配失败
        df.columns = [re.sub(r'\s+', '', str(c)) for c in df.columns]
        for col in df.columns:
            df[col] = df[col].apply(lambda x: re.sub(r'\s+', '', str(x)) if isinstance(x, str) else x)
        # DEBUG: 验证归一化是否生效
        sample_row = df.iloc[0].tolist()
        print(f"  [DEBUG] 归一化后第1行前3列: {[str(v)[:30] for v in sample_row[:3]]}")

        print(f"[Sheet] {sheet_name}: 检测到表头在第 {header_row + 1} 行, 列名={col_names[:8]}...")

        for idx, row in df.iterrows():
            if row.isnull().all():
                continue

            indicator, skip = get_indicator_from_row(row, df)
            if skip or not indicator:
                continue
            # 跳过表头描述行
            if any(keyword in indicator for keyword in ["指标", "项目", "序号"]):
                continue
            # 跳过子表标题行：仅第 0 列有值、且包含子表标题关键词
            other_cols_filled = sum(1 for c in range(1, len(df.columns)) if pd.notna(row.iloc[c]))
            subtitle_keywords = ["情况表", "资产负债", "指标分机构", "监管指标", "资产总负债",
                                 "小微企业贷款", "涉农贷款", "偿付能力", "资金运用"]
            if other_cols_filled == 0 and indicator and any(kw in indicator for kw in subtitle_keywords):
                continue

            # 按列位置遍历，避免重复列名导致 row[col_name] 返回 Series
            desc_parts = []
            for col_idx in range(len(df.columns)):
                col_name = df.columns[col_idx]
                val = row.iloc[col_idx]
                if pd.notna(val):
                    desc_parts.append(f"{col_name}: {val}")
            if not desc_parts:
                continue

            sentence = f"在{sheet_name}工作表中，{'，'.join(desc_parts)}。"
            chunk_id = f"{Path(file_path).stem}_{sheet_name}_row{idx}"

            time_dimension = None
            for col in df.columns:
                col_str = str(col)
                if "期" in col_str or "季度" in col_str or "月" in col_str:
                    time_dimension = col_str
                    break

            # 构建富 indicator：行标签 + 所有有值列名，使 Milvus filter 无论搜指标名还是地区名都能命中
            ind_col_names = [str(df.columns[c]) for c in range(len(df.columns)) if pd.notna(row.iloc[c])]
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
                    "tags": ["table_row"]
                }
            })

        # ===== 列方向切块：每个机构/口径一列，汇总该列所有指标 =====
        MAX_PER_COL_CHUNK = 20
        col_data = {}  # {col_idx: [(indicator, value), ...]}
        for idx, row in df.iterrows():
            if row.isnull().all():
                continue
            indicator, skip = get_indicator_from_row(row, df)
            if skip or not indicator:
                continue
            if any(keyword in indicator for keyword in ["指标", "项目", "序号"]):
                continue
            other_cols_filled = sum(1 for c in range(1, len(df.columns)) if pd.notna(row.iloc[c]))
            if other_cols_filled == 0 and indicator and any(kw in indicator for kw in subtitle_keywords):
                continue
            for col_idx in range(1, len(df.columns)):
                col_name = df.columns[col_idx]
                val = row.iloc[col_idx]
                if pd.notna(val):
                    if col_idx not in col_data:
                        col_data[col_idx] = []
                    col_data[col_idx].append((indicator, val))

        for col_idx, pairs in col_data.items():
            col_name = df.columns[col_idx]
            # 检测该列是否所有值都与 indicator 相同（即该列实际存储的是指标名，不是数值）
            # 经营情况表的第一列"数值"就是这种情况，跳过以精简检索
            all_same = all(str(indicator) == str(val) for indicator, val in pairs)
            if all_same:
                print(f"  [列切块] 跳过列 '{col_name}'（所有值均与 indicator 相同，无实际数值）")
                continue
            # 经营情况表：只保留"本年累计/截至当期"列汇总块（题干正好问这个口径）
            # "本年累计/截至当期"格式清晰：indicator: value，帮助 LLM 准确提取
            # 只保留目标口径列，不会混合其他口径，避免跨类别比较
            if len(df.columns) <= 3 and len(col_data) <= 2:
                if "本年累计" in str(col_name) or "截至当期" in str(col_name):
                    print(f"  [列切块] ✅ 保留列 '{col_name}'（题干目标口径，格式清晰有助检索）")
                else:
                    print(f"  [列切块] 跳过列 '{col_name}'（非题干目标口径，避免干扰）")
                    continue
            for group_i in range(0, len(pairs), MAX_PER_COL_CHUNK):
                group = pairs[group_i:group_i + MAX_PER_COL_CHUNK]
                desc_parts = [f"{indicator}: {val}" for indicator, val in group]
                sentence = f"在{sheet_name}工作表中，{col_name}口径下各指标数值：{'，'.join(desc_parts)}。"
                chunk_id_col = f"{Path(file_path).stem}_{sheet_name}_col{col_idx}_g{group_i // MAX_PER_COL_CHUNK}"
                chunks.append({
                    "id": chunk_id_col,
                    "content": sentence,
                    "metadata": {
                        **metadata_base,
                        "sheet_name": sheet_name,
                        "column_name": col_name,
                        "indicator": col_name,
                        "time_dimension": time_dimension,
                        "normative_strength": None,
                        "tags": ["table_column", "institution_view"]
                    }
                })

    return chunks


def main():
    if len(sys.argv) != 3:
        print("用法: python chunker_excel.py <输入.xlsx> <输出.jsonl>")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    chunks = parse_excel_to_chunks(input_path)

    with open(output_path, 'w', encoding='utf-8') as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + '\n')

    print(f"解析完成，共生成 {len(chunks)} 个知识单元，已保存至 {output_path}")


if __name__ == "__main__":
    main()