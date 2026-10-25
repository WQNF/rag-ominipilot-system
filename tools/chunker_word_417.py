"""特殊文件分块"""
"""417_商业银行资本管理办法_附件22：商业银行信息披露内容和要求,专用解析器"""
import re
from pathlib import Path
from typing import List, Dict, Any
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table

def clean_text(text: str) -> str:
    """去除多余空白和换行"""
    return re.sub(r'\s+', ' ', text).strip()

def parse_table_to_markdown(table: Table) -> str:
    """将 docx 表格转换为 Markdown 格式字符串"""
    rows = []
    for row in table.rows:
        row_cells = [clean_text(cell.text) for cell in row.cells]
        rows.append(row_cells)
    if not rows:
        return ""
    markdown = "| " + " | ".join(rows[0]) + " |\n"
    markdown += "| " + " | ".join(["---"] * len(rows[0])) + " |\n"
    for row in rows[1:]:
        markdown += "| " + " | ".join(row) + " |\n"
    return markdown

def is_level1_heading(text: str) -> bool:
    """判断是否一级标题（如"一、适用范围"）"""
    return re.match(r'^[一二三四五六七八九十]+、', text) is not None

def is_level2_heading(text: str) -> bool:
    """判断是否二级标题（如"（一）风险管理、关键审慎监管指标和风险加权资产概览"）"""
    return re.match(r'^（[一二三四五六七八九十]+）', text) is not None

def is_table_heading(text: str) -> bool:
    """判断是否表格标题行（如"表格KM1：监管并表关键审慎监管指标"）"""
    return re.match(r'^表格[A-Z0-9]+[：:]', text) is not None

def parse_disclosure_docx(file_path: str) -> List[Dict[str, Any]]:
    """
    专门用于解析《商业银行信息披露内容和要求》这类文档：
    - 一级标题：一、二、三、...
    - 二级标题：（一）、（二）...
    - 表格按独立 chunk 切分，并附表格标题和路径
    """
    doc = Document(file_path)
    chunks = []
    
    current_level1 = None        # 一级标题内容
    current_level2 = None        # 二级标题内容
    current_section_text = []    # 当前二级标题下累积的非表格文本
    
    def flush_section():
        """将当前二级标题下的累积文本保存为一个 chunk"""
        nonlocal current_section_text
        if current_section_text:
            content = "\n".join(current_section_text)
            path_parts = []
            if current_level1:
                path_parts.append(current_level1)
            if current_level2:
                path_parts.append(current_level2)
            prefix = " > ".join(path_parts) + "\n" if path_parts else ""
            full_content = prefix + content
            chunk_id = f"{Path(file_path).stem}_sec_{len(chunks)}"
            chunks.append({
                "id": chunk_id,
                "content": full_content,
                "metadata": {
                    "file_name": Path(file_path).name,
                    "file_type": "docx",
                    "chapter": current_level1 or "",
                    "section": current_level2 or "",
                    "table_name": "",
                    "table_number": "",
                }
            })
            current_section_text = []
    
    # 遍历文档元素（段落和表格）
    for element in doc.element.body:
        if element.tag.endswith('p'):  # 段落
            para = Paragraph(element, doc)
            text = clean_text(para.text)
            if not text:
                continue
            # 检测一级标题
            if is_level1_heading(text):
                flush_section()
                current_level1 = text
                current_level2 = None
                continue
            # 检测二级标题
            if is_level2_heading(text):
                flush_section()
                current_level2 = text
                # 表格标题（如"（一）表格KM1：..."）不加入文本，等待表格元素
                if not is_table_heading(text):
                    current_section_text.append(text)
            else:
                # 普通段落
                if current_level2 is None and current_level1 is not None:
                    current_level2 = "概述"
                    current_section_text = []
                current_section_text.append(text)
        elif element.tag.endswith('tbl'):  # 表格
            try:
                table = Table(element, doc)
                table_md = parse_table_to_markdown(table)
                if not table_md:
                    continue
                flush_section()
                table_title = current_level2 if current_level2 and is_table_heading(current_level2) else ""
                path_parts = []
                if current_level1:
                    path_parts.append(current_level1)
                if current_level2:
                    path_parts.append(current_level2)
                prefix = " > ".join(path_parts) + "\n" if path_parts else ""
                content = prefix + f"表格内容：\n{table_md}"
                chunk_id = f"{Path(file_path).stem}_table_{len(chunks)}"
                table_number = ""
                table_name = ""
                if table_title:
                    match = re.match(r'^（[一二三四五六七八九十]+）\s*表格([A-Z0-9]+)[：:](.*)', table_title)
                    if match:
                        table_number = match.group(1)
                        table_name = match.group(2).strip()
                chunks.append({
                    "id": chunk_id,
                    "content": content,
                    "metadata": {
                        "file_name": Path(file_path).name,
                        "file_type": "docx",
                        "chapter": current_level1 or "",
                        "section": current_level2 or "",
                        "table_name": table_name,
                        "table_number": table_number,
                    }
                })
            except Exception as e:
                print(f"[警告] 表格处理异常，已跳过: {e}")
    
    # 最后保存剩余的文本
    flush_section()
    return chunks