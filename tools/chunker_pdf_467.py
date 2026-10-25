#!/usr/bin/env python3
"""
467 号文档专用 PDF Chunker
（附件8：应当编报保险集团偿付能力报告的公司名单）

与通用 chunker_pdf.py 的核心区别：
1. 不把「（一）中国人民保险集团股份有限公司」等公司名当作 heading
2. 按「一、保险控股型集团」「二、非保险控股型集团」两个大章节分块
3. 确保所有公司名都在 chunk 中，不会被静默丢弃

可被外部调用的入口函数：
    parse_pdf_467(file_path, chunk_size=800) -> List[Dict[str, Any]]
"""

import re
from pathlib import Path
from typing import Any, Dict, List

import pdfplumber


class PDFChunker467:
    def __init__(self, file_path: str, chunk_size: int = 800):
        self.file_path = file_path
        self.chunk_size = chunk_size
        self.chunks: List[Dict[str, Any]] = []
        self.current_section = None

    def parse(self) -> List[Dict[str, Any]]:
        with pdfplumber.open(self.file_path) as pdf:
            all_lines: List[str] = []
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                for line in text.split("\n"):
                    line = line.strip()
                    if line:
                        all_lines.append(line)

        # 按「一、」「二、」等大章节头分块，不按（一）（二）分
        self._chunk_by_section(all_lines)

        return self.chunks

    def _chunk_by_section(self, lines: List[str]):
        section_re = re.compile(r"^[一二三四五六七八九十]+、")
        current_chunk_lines: List[str] = []

        for line in lines:
            if section_re.match(line):
                # 遇到新的大章节标题，先 flush 当前 chunk
                if current_chunk_lines:
                    self._flush(current_chunk_lines)
                self.current_section = line
                current_chunk_lines = [line]
            else:
                # 检查是否需要按 chunk_size 切分
                next_content = "\n".join(current_chunk_lines + [line])
                if len(next_content) > self.chunk_size and current_chunk_lines:
                    self._flush(current_chunk_lines)
                    current_chunk_lines = [line]
                else:
                    current_chunk_lines.append(line)

        if current_chunk_lines:
            self._flush(current_chunk_lines)

    def _flush(self, lines: List[str]):
        content = "\n".join(lines)
        if len(content) < 20:
            return
        chunk_id = f"{Path(self.file_path).stem}_chunk_{len(self.chunks)}"
        self.chunks.append({
            "id": chunk_id,
            "content": content,
            "metadata": {
                "file_name": Path(self.file_path).name,
                "file_type": "pdf",
                "section": self.current_section,
            },
        })

    def _add_chunk(self, content: str, metadata: Dict[str, Any]):
        if len(content) < 20:
            return
        chunk_id = f"{Path(self.file_path).stem}_chunk_{len(self.chunks)}"
        self.chunks.append({
            "id": chunk_id,
            "content": content,
            "metadata": {
                "file_name": Path(self.file_path).name,
                "file_type": "pdf",
                **metadata,
            },
        })


def parse_pdf_467(file_path: str, chunk_size: int = 800) -> List[Dict[str, Any]]:
    chunker = PDFChunker467(file_path, chunk_size=chunk_size)
    return chunker.parse()