# -*- coding: utf-8 -*-
"""chunk 头部的构建与还原。

背景
----
旧做法把 `[Source: 文件名] | Chapter: … | Clause: …` 拼进 chunk 正文再入库，
后果有两个：
1. **检索泄漏**：每个片段都自带答案提示（题目里 95% 引用文档名），向量与 BM25
   都能靠标题命中，指标被抬高，评估失真；
2. **重复计量**：标题在正文里算一次、在 metadata 过滤里又算一次。

现在改为：**入库只存干净正文，标题等归属信息只放 metadata**，
在「给模型看」的展示层用 ensure_header() 还原成带来源的文本。
这样检索看到的是正文，"引用/溯源"看到的是完整头部，两者不互相污染。
"""
import re

HEADER_RE = re.compile(r"^\s*\[Source:[^\]]*\]")


def build_header(meta):
    """由 metadata 生成标准头部（不含换行）。"""
    meta = meta or {}
    parts = [f"[Source: {meta.get('file_name') or 'unknown'}]"]
    for key, label in (("chapter", "Chapter"), ("section", "Clause"),
                       ("sheet_name", "Sheet"), ("indicator", "Indicator"),
                       ("authority", "Authority")):
        val = str(meta.get(key) or "").strip()
        if val:
            parts.append(f"{label}: {val}")
    return " | ".join(parts)


def has_header(text):
    return bool(HEADER_RE.match(text or ""))


def ensure_header(text, meta):
    """给干净正文补上来源头部；已有头部则原样返回（兼容旧数据）。"""
    text = text or ""
    if has_header(text):
        return text
    header = build_header(meta)
    return f"{header}\n{text}"


def strip_header(text):
    """去掉头部，返回纯正文（兼容旧数据迁移）。"""
    text = text or ""
    m = HEADER_RE.match(text)
    if not m:
        return text
    rest = text[m.end():]
    # 头部可能还有 "| Chapter: …" 等，直到第一个换行
    nl = rest.find("\n")
    return rest[nl + 1:] if nl >= 0 else rest


def parse_header(text):
    """从带头部的文本里解析出归属信息（旧数据兼容）。"""
    text = text or ""
    m = HEADER_RE.match(text)
    if not m:
        return {}
    line = text[: text.find("\n")] if "\n" in text else text
    out = {}
    src = re.search(r"\[Source:\s*([^\]]+)\]", line)
    if src:
        out["file_name"] = src.group(1).strip()
    for key, label in (("chapter", "Chapter"), ("section", "Clause"),
                       ("sheet_name", "Sheet"), ("indicator", "Indicator"),
                       ("authority", "Authority")):
        mm = re.search(rf"{label}:\s*([^|]+)", line)
        if mm:
            out[key] = mm.group(1).strip()
    return out
