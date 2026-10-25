"""特殊文件分块"""
"""397、《银行函证工作操作指引》专用解析器"""

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


# ===== 标题识别模式 =====
# 一级标题：一、二、三、...
LEVEL1_PATTERN = re.compile(r'^[一二三四五六七八九十]+、')
# 二级标题：（一）（二）（三）... 或 (一) (二) (三)
LEVEL2_PATTERN = re.compile(r'^[（(][一二三四五六七八九十]+[）)]')
# 表单标题：银行询证函（格式一）、银行询证函（格式二）、验资业务银行询证函
FORM_PATTERN = re.compile(r'^(银行询证函（格式[一二]）|验资业务银行询证函)')


def _is_level1_heading(text: str) -> bool:
    return bool(LEVEL1_PATTERN.match(text))


def _is_level2_heading(text: str) -> bool:
    return bool(LEVEL2_PATTERN.match(text))


def _is_form_heading(text: str) -> bool:
    return bool(FORM_PATTERN.match(text))


def parse_bank_letter_guideline(file_path: str, chunk_size: int = 800) -> List[Dict[str, Any]]:
    """
    专门用于解析《银行函证工作操作指引》这类模板式文档。

    标题层级识别：
      - 一级：一、二、三、... → 大章节
      - 二级：（一）（二）（三）... → 每个二级标题独立成块
      - 表单级：银行询证函（格式一/二）、验资业务银行询证函 → 独立成块

    每个 (X) 小节作为一个 chunk，保留该小节下所有段落和表格。
    超长小节按 chunk_size 切分。
    """
    doc = Document(file_path)
    chunks = []

    current_level1 = None
    current_level2 = None
    current_form = None
    current_content = []

    def make_chapter() -> str:
        """生成章节元数据：优先表单级 > 一级 > 二级"""
        return current_form or current_level1 or current_level2 or ""

    def flush_chunk():
        nonlocal current_content
        if not current_content:
            return

        # 内容本身已包含 heading 文本（由下方逻辑加入），不作 prefix 前缀
        full_content = "\n".join(current_content)

        # 超长则按句子切分
        if len(full_content) > chunk_size:
            sentences = re.split(r'(?<=[。！？])\s*', full_content)
            temp = []
            for sent in sentences:
                if len("\n".join(temp + [sent])) > chunk_size and temp:
                    chunk_id = f"{Path(file_path).stem}_section_{len(chunks)}"
                    chunks.append({
                        "id": chunk_id,
                        "content": "\n".join(temp),
                        "metadata": {
                            "file_name": Path(file_path).name,
                            "file_type": "docx",
                            "chapter": make_chapter(),
                            "section": current_level2 or current_form or "",
                            "authority": "", "publish_date": "", "effective_date": "",
                            "doc_number": "", "indicator": "", "sheet_name": "",
                            "time_dimension": "", "tags": [], "normative_strength": ""
                        }
                    })
                    temp = [sent]
                else:
                    temp.append(sent)
            if temp:
                chunks.append({
                    "id": f"{Path(file_path).stem}_section_{len(chunks)}",
                    "content": "\n".join(temp),
                    "metadata": {
                        "file_name": Path(file_path).name,
                        "file_type": "docx",
                        "chapter": make_chapter(),
                        "section": current_level2 or current_form or "",
                        "authority": "", "publish_date": "", "effective_date": "",
                        "doc_number": "", "indicator": "", "sheet_name": "",
                        "time_dimension": "", "tags": [], "normative_strength": ""
                    }
                })
        else:
            chunk_id = f"{Path(file_path).stem}_section_{len(chunks)}"
            chunks.append({
                "id": chunk_id,
                "content": full_content,
                "metadata": {
                    "file_name": Path(file_path).name,
                    "file_type": "docx",
                    "chapter": make_chapter(),
                    "section": current_level2 or current_form or "",
                    "authority": "", "publish_date": "", "effective_date": "",
                    "doc_number": "", "indicator": "", "sheet_name": "",
                    "time_dimension": "", "tags": [], "normative_strength": ""
                }
            })

        current_content = []

    # ===== 遍历文档 =====
    for element in doc.element.body:
        if element.tag.endswith('p'):
            para = Paragraph(element, doc)
            text = clean_text(para.text)
            if not text:
                continue

            # 1) 表单级标题（最高优先级）
            if _is_form_heading(text):
                flush_chunk()
                current_level2 = None
                current_form = text
                current_content.append(text)

            # 2) 一级标题
            elif _is_level1_heading(text):
                flush_chunk()
                current_level1 = text
                current_level2 = None
                current_form = None
                current_content.append(text)

            # 3) 二级标题 → 每个二级标题独立成块
            elif _is_level2_heading(text):
                flush_chunk()
                current_level2 = text
                current_content.append(text)

            # 4) 普通段落
            else:
                current_content.append(text)

        elif element.tag.endswith('tbl'):
            table = Table(element, doc)
            table_md = parse_table_to_markdown(table)
            if table_md:
                current_content.append(f"表格内容：\n{table_md}")

    flush_chunk()
    return chunks