#!/usr/bin/env python3
"""
460 号文档专用 PDF Chunker
（附件1：寿险合同负债评估的折现率曲线）

与通用 chunker_pdf.py 的核心区别：
1. 使用 page.chars（字符级）按坐标重建文本，解决多列排版问题
2. 针对 460 号文档的特定排版结构优化
3. 确保关键文本（如 "750日移动平均国债收益率曲线"）不被拆分

为什么不用 extract_words()：
- extract_words() 内部有词分组算法，当上下行字符间距不均匀时，
  容易把不同行的字符错误合并（如 "750" 被并入下一行的 "终极利率过渡曲线"）
- page.chars 返回每个字符的独立坐标，我们按精确 Y 坐标分行，
  确保每个字符归入正确的行

可被外部调用的入口函数：
    parse_pdf_460(file_path, chunk_size=800) -> List[Dict[str, Any]]
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import pytesseract
except ImportError:
    pytesseract = None


class PDFChunker460:
    """460 号文档专用分块器，使用 page.chars 字符级坐标重建"""

    def __init__(
        self,
        file_path: str,
        chunk_size: int = 800,
        ocr_images: bool = False,
        ocr_full_page: bool = False,
        ocr_lang: Optional[str] = None,
    ):
        self.file_path = file_path
        self.chunk_size = chunk_size
        self.ocr_images = ocr_images
        self.ocr_full_page = ocr_full_page
        self.ocr_lang = ocr_lang or os.getenv("PDF_OCR_LANG", "chi_sim+eng")
        self.chunks: List[Dict[str, Any]] = []
        self.current_section = None
        self.current_page_num = 0
        self._ocr_ready = self._configure_ocr()

    # ===== OCR 配置 =====
    def _configure_ocr(self) -> bool:
        if pytesseract is None or Image is None:
            return False
        tesseract_cmd = os.getenv("TESSERACT_CMD")
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    # ===== 主解析入口 =====
    def parse(self) -> List[Dict[str, Any]]:
        with pdfplumber.open(self.file_path) as pdf:
            all_text_blocks: List[Tuple[int, Dict[str, Any]]] = []
            table_count = 0
            image_count = 0

            for page_num, page in enumerate(pdf.pages, start=1):
                self.current_page_num = page_num

                # ★ 核心改进：使用 page.chars 按字符级坐标重建文本
                text_blocks = self._extract_text_blocks(page)

                # OCR 全页回退
                if self.ocr_full_page and self._should_run_page_ocr(text_blocks):
                    ocr_text = self._ocr_page(page)
                    if ocr_text:
                        text_blocks.extend(self._text_to_blocks(ocr_text, source="page_ocr"))

                tables = page.extract_tables() or []
                images = page.images if hasattr(page, "images") else []

                for block in text_blocks:
                    all_text_blocks.append((page_num, block))

                total_chars = sum(len(b[1]["text"]) for b in all_text_blocks)
                if total_chars > self.chunk_size * 3:
                    self._flush_text_blocks(all_text_blocks)

                for tbl in tables:
                    if not tbl:
                        continue
                    self._flush_text_blocks(all_text_blocks)
                    table_md = self._table_to_markdown(tbl)
                    if not table_md:
                        continue
                    table_count += 1
                    self._add_chunk(
                        content=f"表格 {table_count}:\n{table_md}",
                        metadata={
                            "type": "table",
                            "table_num": table_count,
                            "page": page_num,
                            "section": self.current_section,
                        },
                    )

                for img in images:
                    image_count += 1
                    self._flush_text_blocks(all_text_blocks)
                    content = self._build_image_chunk(page, img, image_count)
                    self._add_chunk(
                        content=content,
                        metadata={
                            "type": "image",
                            "image_num": image_count,
                            "page": page_num,
                            "section": self.current_section,
                        },
                    )

            self._flush_text_blocks(all_text_blocks)

        return self.chunks

    # ===== ★ 核心方法：page.chars 按字符级坐标重建文本 =====
    def _extract_text_blocks(self, page) -> List[Dict[str, Any]]:
        """
        使用 page.chars 获取每个字符及其坐标，按阅读顺序重建文本。

        为什么不用 extract_words()：
        - extract_words() 内部有词分组算法，当字符间距不均匀时，
          容易把不同行的字符错误合并（如 "750" 被并入下一行的 "终极利率过渡曲线"）
        - page.chars 返回每个字符的独立坐标，我们自己按 Y 分组到行，
          再按 X 排序，确保每个字符归入正确的行
        """
        chars = page.chars

        if not chars:
            # 回退到 extract_text
            text = page.extract_text() or ""
            return self._text_to_blocks(text, source="native")

        # 按字符级坐标重建文本
        text = self._chars_to_text(chars)

        # ★ 后处理：修复 PDF 字符错位导致的文本错误
        # （如 "750" 被错误定位到下一行的 Y 坐标）
        text = self._post_process_text(text)

        if len(text) < 20:
            # 文本太短，回退到 extract_text
            text = page.extract_text() or ""
            return self._text_to_blocks(text, source="native")

        return self._text_to_blocks(text, source="chars_rebuilt")

    def _chars_to_text(self, chars: list) -> str:
        """
        将 page.chars（单个字符列表）按坐标重建为文本。

        算法：
        1. 按 top 坐标分组（同一行），容差 3px（比 extract_words 更精确）
        2. 每行内按 x0 排序（从左到右）
        3. 字符间距 > 3px 时插入空格（词边界检测）
        4. 不同行用换行符连接

        这解决了 extract_words 将 "750" 错误合并到下一行的问题，
        因为我们是按字符的精确 Y 坐标分行的。
        """
        if not chars:
            return ""

        y_tolerance = 3  # 同一行的 Y 坐标容差（像素）
        lines = []
        current_line = []
        current_top = None

        for c in sorted(chars, key=lambda c: (c['top'], c['x0'])):
            if current_top is None or abs(c['top'] - current_top) < y_tolerance:
                current_line.append(c)
            else:
                lines.append(current_line)
                current_line = [c]
            current_top = c['top']

        if current_line:
            lines.append(current_line)

        text_lines = []
        space_threshold = 3  # 字符间距超过此值（px）则插入空格

        for line in lines:
            line.sort(key=lambda c: c['x0'])
            line_text = ''
            prev_x1 = None
            for c in line:
                if prev_x1 is not None and c['x0'] - prev_x1 > space_threshold:
                    line_text += ' '
                line_text += c['text']
                prev_x1 = c['x1']
            text_lines.append(line_text)

        return '\n'.join(text_lines)

    # ===== ★ 文本后处理：修复 PDF 字符错位 =====
    def _post_process_text(self, text: str) -> str:
        """
        修复因 PDF 内部字符错位导致的文本错误。

        已知问题：
        1. "750" 被定位到下一行，形成 "7终5极0利率过渡曲线"
        2. 右侧数学表达式与左侧正文混排
        """
        # 修复 "日移动平均国债收益率曲线"（缺少 "750" 前缀）
        # 同时修复下一行的 "7终5极0利率过渡曲线"
        text = re.sub(
            r'日移动平均国债收益率曲线\n7终5极0利率过渡曲线',
            '750日移动平均国债收益率曲线\n终极利率过渡曲线',
            text
        )

        # 修复正文中混入的右侧数学表达式
        # "750 日移动平均国债收益t >率4曲0 线详" → "750日移动平均国债收益率曲线详见"
        text = re.sub(
            r'750 日移动平均国债收益t >率4曲0 线详',
            '750日移动平均国债收益率曲线详见',
            text
        )

        # 修复数学表达式中的 "0 <  ≤ 20" → "0 < t ≤ 20"（补回被右侧列吞掉的 t）
        text = re.sub(r'0\s*<\s*≤\s*20', '0 < t ≤ 20', text)
        text = re.sub(r'20\s*<\s*≤\s*40', '20 < t ≤ 40', text)

        # 修复 "在20t<年度 ≤第4一0次" → "在20<t≤40年度第一次"
        text = re.sub(r'在20t<年度\s*≤第4一0次', '在20<t≤40年度第一次', text)

        return text

    # ===== 文本块构建 =====
    def _text_to_blocks(self, text: str, source: str) -> List[Dict[str, Any]]:
        lines = text.split("\n")
        text_blocks = []
        for raw_line in lines:
            line = self._normalize_text(raw_line)
            if not line:
                continue
            is_heading = bool(
                re.match(r"^第[一二三四五六七八九十百千万0-9]+章", line)
                or re.match(r"^第[一二三四五六七八九十百千万0-9]+条", line)
                or re.match(r"^[一二三四五六七八九十]+、", line)
                or re.match(r"^（[一二三四五六七八九十]+）", line)
                or (re.match(r"^\d+(\.\d+)*\s+\S", line) and len(line) < 40)
            )
            text_blocks.append(
                {
                    "text": line,
                    "is_heading": is_heading,
                    "y0": 0,
                    "source": source,
                }
            )
        return text_blocks

    def _normalize_text(self, text: str) -> str:
        text = text.replace("\x00", " ").replace("\t", " ")
        text = re.sub(r"[ ]{2,}", " ", text)
        text = re.sub(r"\n{2,}", "\n", text)
        return text.strip()

    # ===== OCR 相关 =====
    def _should_run_page_ocr(self, text_blocks: List[Dict[str, Any]]) -> bool:
        if not self._ocr_ready:
            return False
        if not text_blocks:
            return True
        total_chars = sum(len(block["text"]) for block in text_blocks)
        return total_chars < 80

    def _build_image_chunk(self, page, image_obj: Dict[str, Any], image_num: int) -> str:
        content = f"[图片 {image_num}]"
        if not self.ocr_images or not self._ocr_ready:
            return content
        ocr_text = self._ocr_image_region(page, image_obj)
        if not ocr_text:
            return content
        return f"{content}\n[OCR]\n{ocr_text}"

    def _ocr_page(self, page) -> str:
        if not self._ocr_ready:
            return ""
        try:
            pil_image = page.to_image(resolution=200).original
        except Exception:
            return ""
        return self._run_ocr(pil_image)

    def _ocr_image_region(self, page, image_obj: Dict[str, Any]) -> str:
        if not self._ocr_ready:
            return ""
        bbox = self._image_bbox(page, image_obj)
        if bbox is None:
            return ""
        try:
            cropped = page.crop(bbox)
            pil_image = cropped.to_image(resolution=240).original
        except Exception:
            return ""
        return self._run_ocr(pil_image)

    def _image_bbox(self, page, image_obj: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
        try:
            x0 = max(0.0, float(image_obj.get("x0", 0.0)))
            top = max(0.0, float(image_obj.get("top", 0.0)))
            x1 = min(float(page.width), float(image_obj.get("x1", page.width)))
            bottom = min(float(page.height), float(image_obj.get("bottom", page.height)))
        except Exception:
            return None
        if x1 <= x0 or bottom <= top:
            return None
        return (x0, top, x1, bottom)

    def _run_ocr(self, pil_image) -> str:
        if not self._ocr_ready or pil_image is None:
            return ""
        try:
            gray = pil_image.convert("L")
            text = pytesseract.image_to_string(gray, lang=self.ocr_lang)
        except Exception:
            return ""
        text = self._normalize_text(text)
        if len(text) < 8:
            return ""
        return text

    # ===== 分块逻辑 =====
    def _flush_text_blocks(self, blocks: List[Tuple[int, Dict[str, Any]]]):
        if not blocks:
            return

        current_chunk_text: List[str] = []
        current_heading = self.current_section

        for page_num, block in blocks:
            text = block["text"]
            is_heading = block["is_heading"]

            if is_heading:
                if current_chunk_text:
                    content = "\n".join(current_chunk_text)
                    if len(content) >= 20:
                        self._add_chunk(
                            content=content,
                            metadata={"type": "text", "section": current_heading, "page": page_num},
                        )
                    current_chunk_text = []
                current_heading = text
                self.current_section = current_heading
                current_chunk_text.append(text)
            else:
                next_content = "\n".join(current_chunk_text + [text])
                if len(next_content) > self.chunk_size and current_chunk_text:
                    content = "\n".join(current_chunk_text)
                    if len(content) >= 20:
                        self._add_chunk(
                            content=content,
                            metadata={"type": "text", "section": current_heading, "page": page_num},
                        )
                    current_chunk_text = [text]
                else:
                    current_chunk_text.append(text)

        if current_chunk_text:
            content = "\n".join(current_chunk_text)
            if len(content) >= 20:
                self._add_chunk(
                    content=content,
                    metadata={"type": "text", "section": current_heading, "page": blocks[-1][0]},
                )

        blocks.clear()

    def _table_to_markdown(self, table: List[List[Any]]) -> str:
        if not table:
            return ""
        rows = [row for row in table if any(cell and str(cell).strip() for cell in row)]
        if not rows:
            return ""
        max_cols = max(len(row) for row in rows)
        normalized_rows = []
        for row in rows:
            normalized = [str(cell).strip() if cell else "" for cell in row]
            while len(normalized) < max_cols:
                normalized.append("")
            normalized_rows.append(normalized)
        header = normalized_rows[0]
        markdown = "| " + " | ".join(header) + " |\n"
        markdown += "| " + " | ".join(["---"] * len(header)) + " |\n"
        for row in normalized_rows[1:]:
            markdown += "| " + " | ".join(row) + " |\n"
        return markdown

    def _add_chunk(self, content: str, metadata: Dict[str, Any]):
        if len(content) < 20:
            return
        chunk_id = f"{Path(self.file_path).stem}_chunk_{len(self.chunks)}"
        self.chunks.append(
            {
                "id": chunk_id,
                "content": content,
                "metadata": {
                    "file_name": Path(self.file_path).name,
                    "file_type": "pdf",
                    "page": metadata.get("page"),
                    "type": metadata.get("type"),
                    "section": metadata.get("section"),
                    "table_num": metadata.get("table_num"),
                    "image_num": metadata.get("image_num"),
                },
            }
        )


def parse_pdf_460(
    file_path: str,
    chunk_size: int = 800,
    ocr_images: bool = False,
    ocr_full_page: bool = False,
    ocr_lang: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    460 号文档专用解析入口。

    与 parse_pdf_advanced 的区别：
    - 使用 page.chars（字符级）按坐标重建文本，解决多列排版问题
    - 确保 "750日移动平均国债收益率曲线" 等关键文本不会被拆分

    用法：
        from tools.chunker_pdf_460 import parse_pdf_460
        chunks = parse_pdf_460("path/to/460.pdf")
    """
    chunker = PDFChunker460(
        file_path=file_path,
        chunk_size=chunk_size,
        ocr_images=ocr_images,
        ocr_full_page=ocr_full_page,
        ocr_lang=ocr_lang,
    )
    return chunker.parse()


if __name__ == "__main__":
    pdf_file = r"D:\Python_exercise\OmniPilot_企业智能运营助手\data\pdf\460_中国银保监会关于实施_保险公司偿付能力监管规则（Ⅱ）_有关事项的通知_附件1：寿险合同负债评估的折现率曲线.pdf"
    chunks = parse_pdf_460(pdf_file)
    print(f"共生成 {len(chunks)} 个 chunk\n")
    for i, chunk in enumerate(chunks):
        meta = chunk["metadata"]
        print(f"--- Chunk {i+1}/{len(chunks)} (page={meta.get('page')}, type={meta.get('type')}) ---")
        print(chunk["content"][:300])
        if len(chunk["content"]) > 300:
            print(f"    ... (共 {len(chunk['content'])} 字符)")
        print()