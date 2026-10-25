import re
import os
import io
from pathlib import Path
from typing import List, Dict, Any, Optional, Optional, Optional
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table
from docx.oxml.ns import qn
from PIL import Image
import pytesseract

# 可选：设置Tesseract路径（Windows示例）
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
#tesseract_cmd = os.getenv("TESSERACT_CMD")
#if tesseract_cmd:
#    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
def clean_text(text: str) -> str:
    """去除多余空白和换行"""
    return re.sub(r'\s+', ' ', text).strip()

def ocr_image(image_bytes: bytes, lang: str = 'eng') -> str:
    """OCR识别图片中的文字，返回文本"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert('L')  # 灰度化提高识别率
        text = pytesseract.image_to_string(img, lang=lang)
        return clean_text(text)
    except Exception as e:
        print(f"OCR失败: {e}")
        return ""

def parse_table_to_markdown(table: Table, table_idx: int) -> str:
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

def is_heading(para: Paragraph) -> bool:
    """判断段落是否是真正的章节标题（章/节/部分级），排除第X条等具体条款内容。
    
    注意：第X条不作为标题处理，因为条款内容通常和编号在同一行，
    如果当作标题会导致条款正文内容丢失。
    """
    if para.style.name.startswith('Heading'):
        return True
    text = para.text.strip()
    # 仅保留真正的章节标题级别（章/节/部分），排除条款级别
    if re.match(r'^第[一二三四五六七八九十百千万0-9]+章', text):
        return True
    if re.match(r'^第[一二三四五六七八九十百千万0-9]+节', text):
        return True
    if re.match(r'^第[一二三四五六七八九十百千万0-9]+部分', text):
        return True
    # 一、二、三、等中文编号（顶级章节标题）
    if re.match(r'^[一二三四五六七八九十]+[、]', text):
        return True
    # 附录、附件、附则
    if re.match(r'^(附录|附件|附则)', text):
        return True
    return False


def is_clause(text: str) -> bool:
    """判断文本是否以条款编号开头（如 '第一条'、'第二十条'）"""
    return bool(re.match(r'^第[一二三四五六七八九十百千万0-9]+条', text))

def get_inline_images(doc, paragraph: Paragraph) -> List[bytes]:
    """提取段落中的内联图片（blip）并返回图片二进制列表"""
    images = []
    # 获取文档所有图片关系映射
    image_rels = {}
    for rel_id, rel in doc.part.rels.items():
        if not rel.is_external and "image" in rel.target_part.content_type:
            image_rels[rel_id] = rel.target_part.blob
    # 查找段落中的绘图元素
    for run in paragraph.runs:
        drawings = run._element.xpath('.//w:drawing')
        for drawing in drawings:
            blips = drawing.xpath('.//a:blip')
            for blip in blips:
                rid = blip.get(qn('r:embed'))
                if rid and rid in image_rels:
                    images.append(image_rels[rid])
    return images

def parse_docx_to_chunks_advanced(file_path: str, chunk_size: int = 800, ocr_lang: str = 'eng', doc_meta: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    高级解析器：处理段落、标题、表格，并对嵌入图片进行OCR识别。
    返回每个 chunk 包含 content 和 metadata。
    doc_meta: 外部传入的文档级元数据（如 doc_id/title/authority/publish_date/source_url），会合并到每个 chunk
    """
    if doc_meta is None:
        doc_meta = {}
    doc = Document(file_path)
    chunks = []
    current_chapter = None   # 当前章标题（如 "第六章 合作机构管理"）
    current_heading = None   # 当前条/节标题（如 "第四十四条"）
    current_text = []

    def flush_text():
        nonlocal current_text
        if current_text:
            content = "\n".join(current_text)
            # 将标题作为上下文前缀合并到内容中
            # 构建层级前缀：章 > 条
            hierarchy_parts = []
            if current_chapter:
                hierarchy_parts.append(current_chapter)
            if current_heading and current_heading != current_chapter:
                hierarchy_parts.append(current_heading)
            if hierarchy_parts:
                prefix = " > ".join(hierarchy_parts)
                content = f"{prefix}\n{content}"
            if len(content) > chunk_size:
                sentences = re.split(r'(?<=[。！？])\s*', content)
                temp = []
                for sent in sentences:
                    if len("\n".join(temp + [sent])) > chunk_size and temp:
                        chunk_id = f"{Path(file_path).stem}_chunk_{len(chunks)}"
                        chunks.append({
                            "id": chunk_id,
                            "content": "\n".join(temp),
                            "metadata": {
                                "file_name": Path(file_path).name,
                                "file_type": "docx",
                                "chapter": current_heading or "",
                                "section": "",
                                "authority": doc_meta.get("authority", ""),
                                "publish_date": doc_meta.get("publish_date", ""),
                                "effective_date": "",
                                "doc_number": doc_meta.get("doc_number", ""),
                                "doc_id": doc_meta.get("doc_id", ""),
                                "indicator": "",
                                "sheet_name": "",
                                "time_dimension": "",
                                "tags": [],
                                "normative_strength": ""
                            }
                        })
                        temp = [sent]
                    else:
                        temp.append(sent)
                if temp:
                    chunks.append({
                        "id": f"{Path(file_path).stem}_chunk_{len(chunks)}",
                        "content": "\n".join(temp),
                        "metadata": {
                            "file_name": Path(file_path).name,
                            "file_type": "docx",
                            "chapter": current_heading or "",
                            "section": "",
                            "authority": doc_meta.get("authority", ""),
                            "publish_date": doc_meta.get("publish_date", ""),
                            "effective_date": "",
                            "doc_number": doc_meta.get("doc_number", ""),
                            "doc_id": doc_meta.get("doc_id", ""),
                            "indicator": "",
                            "sheet_name": "",
                            "time_dimension": "",
                            "tags": [],
                            "normative_strength": ""
                        }
                    })
            else:
                chunk_id = f"{Path(file_path).stem}_chunk_{len(chunks)}"
                chunks.append({
                    "id": chunk_id,
                    "content": content,
                    "metadata": {
                        "file_name": Path(file_path).name,
                        "file_type": "docx",
                        "chapter": current_heading or "",
                        "section": "",
                        "authority": doc_meta.get("authority", ""),
                        "publish_date": doc_meta.get("publish_date", ""),
                        "effective_date": "",
                        "doc_number": doc_meta.get("doc_number", ""),
                        "doc_id": doc_meta.get("doc_id", ""),
                        "indicator": "",
                        "sheet_name": "",
                        "time_dimension": "",
                        "tags": [],
                        "normative_strength": ""
                    }
                })
            current_text = []

    # 遍历文档元素（段落和表格）
    for element in doc.element.body:
        if element.tag.endswith('p'):  # 段落
            para = Paragraph(element, doc)
            para_text = clean_text(para.text)
            img_bytes_list = get_inline_images(doc, para)
            ocr_results = []
            for img_bytes in img_bytes_list:
                ocr_text = ocr_image(img_bytes, lang=ocr_lang)
                if ocr_text:
                    ocr_results.append(ocr_text)
            if ocr_results:
                para_text += " [公式识别: " + " | ".join(ocr_results) + "]"
            if not para_text and not img_bytes_list:
                continue

            if is_heading(para):
                # ① 相同标题重复出现（如模板标记） → 追加到当前内容
                if para_text and para_text == current_heading:
                    current_text.append(para_text)
                    continue

                # ② 判断是否是括号子标题（（一）（二）（三）...）
                #    此类应合并到当前大章节下，不触发切分边界
                is_bracket_sub = bool(re.match(
                    r'^[（(][一二三四五六七八九十百千万]+[）)]', para_text))

                if is_bracket_sub and current_heading is not None:
                    # 括号子标题 → 不flush，直接追加到内容
                    current_text.append(para_text)
                else:
                    # 大章节标题变更 → 保存已有内容，更新章节
                    if current_text:
                        flush_text()
                    current_heading = para_text
            else:
                current_text.append(para_text)
        elif element.tag.endswith('tbl'):  # 表格
            flush_text()
            table = Table(element, doc)
            table_md = parse_table_to_markdown(table, len(chunks))
            if table_md:
                chunk_id = f"{Path(file_path).stem}_table_{len(chunks)}"
                chunks.append({
                    "id": chunk_id,
                    "content": f"表格内容：\n{table_md}",
                    "metadata": {
                        "file_name": Path(file_path).name,
                        "file_type": "docx",
                        "chapter": current_heading or "",
                        "section": "",
                        "authority": doc_meta.get("authority", ""),
                        "publish_date": doc_meta.get("publish_date", ""),
                        "effective_date": "",
                        "doc_number": doc_meta.get("doc_number", ""),
                        "indicator": "",
                        "sheet_name": "",
                        "time_dimension": "",
                        "tags": [],
                        "normative_strength": ""
                    }
                })
    flush_text()
    return chunks