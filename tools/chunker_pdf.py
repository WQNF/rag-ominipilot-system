#!/usr/bin/env python3
"""
PDF chunker with basic text extraction, table extraction, image OCR, and
full-page OCR fallback for scanned PDFs.

Optional OCR dependencies:
    pip install pytesseract Pillow

Optional environment variables:
    TESSERACT_CMD       Path to tesseract.exe when it is not in PATH
    PDF_OCR_LANG        OCR language pack, default: chi_sim+eng
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

try:
    from PIL import Image
except ImportError:  # pragma: no cover - optional dependency
    Image = None

try:
    import pytesseract
except ImportError:  # pragma: no cover - optional dependency
    pytesseract = None


class PDFChunker:
    def __init__(
        self,
        file_path: str,
        chunk_size: int = 800,
        ocr_images: bool = False,
        ocr_full_page: bool = False,
        ocr_lang: Optional[str] = None,
        doc_meta: Optional[Dict[str, Any]] = None,
    ):
        self.file_path = file_path
        self.chunk_size = chunk_size
        self.ocr_images = ocr_images
        self.ocr_full_page = ocr_full_page
        self.ocr_lang = ocr_lang or os.getenv("PDF_OCR_LANG", "chi_sim+eng")
        self.doc_meta = doc_meta or {}
        self.chunks: List[Dict[str, Any]] = []
        self.current_section = None
        self.current_page_num = 0
        self._ocr_ready = self._configure_ocr()

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

    def parse(self) -> List[Dict[str, Any]]:
        with pdfplumber.open(self.file_path) as pdf:
            all_text_blocks: List[Tuple[int, Dict[str, Any]]] = []
            table_count = 0
            image_count = 0

            for page_num, page in enumerate(pdf.pages, start=1):
                self.current_page_num = page_num

                text_blocks = self._extract_text_blocks(page)
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

    def _extract_text_blocks(self, page) -> List[Dict[str, Any]]:
        text = page.extract_text() or ""
        return self._text_to_blocks(text, source="native")

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
                    "authority": self.doc_meta.get("authority", ""),
                    "publish_date": self.doc_meta.get("publish_date", ""),
                    "effective_date": "",
                    "doc_number": self.doc_meta.get("doc_number", ""),
                    "doc_id": self.doc_meta.get("doc_id", ""),
                    "indicator": "",
                    "sheet_name": "",
                    "time_dimension": "",
                    "tags": [],
                    "normative_strength": "",
                },
            }
        )


def parse_pdf_advanced(
    file_path: str,
    chunk_size: int = 800,
    ocr_images: bool = False,
    ocr_full_page: bool = False,
    ocr_lang: Optional[str] = None,
    doc_meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    chunker = PDFChunker(
        file_path=file_path,
        chunk_size=chunk_size,
        ocr_images=ocr_images,
        ocr_full_page=ocr_full_page,
        ocr_lang=ocr_lang,
        doc_meta=doc_meta,
    )
    return chunker.parse()


if __name__ == "__main__":
    pdf_file = "example.pdf"
    chunks = parse_pdf_advanced(pdf_file, ocr_images=True, ocr_full_page=True)
    for chunk in chunks:
        print(chunk["content"][:200])
        print("-" * 50)