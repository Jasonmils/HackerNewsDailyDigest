"""Local PDF detection and text extraction.

Summary:
    Identifies linked PDF responses and extracts page text with pypdf so PDF
    stories can be summarized from document content instead of title/comments
    only.

Adding functions:
    Add PDF-only parsing improvements here, such as page filtering, metadata
    extraction, or OCR fallback hooks. Keep HTTP downloading in articles.py and
    model summarization in summarizer.py.
"""

from __future__ import annotations

import re
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse

PDF_MAGIC = b"%PDF"


def looks_like_pdf(url: str, content_type: str, content_start: bytes) -> bool:
    ctype = (content_type or "").lower()
    path = urlparse(url or "").path.lower()
    head = (content_start or b"")[:1024].lstrip()
    return (
        "application/pdf" in ctype
        or path.endswith(".pdf")
        or head.startswith(PDF_MAGIC)
    )


def _clean_pdf_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_text(content: bytes, char_limit: int, max_pages: int) -> Optional[str]:
    if not content:
        return None
    try:
        from pypdf import PdfReader
    except Exception:
        return None

    try:
        reader = PdfReader(BytesIO(content))
    except Exception:
        return None

    page_limit = max(1, max_pages)
    chunks: list[str] = []
    for i, page in enumerate(reader.pages[:page_limit], start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        text = _clean_pdf_text(text)
        if text:
            chunks.append(f"[PDF page {i}]\n{text}")

    extracted = "\n\n".join(chunks).strip()
    if len(extracted) < 80:
        return None
    if len(extracted) > char_limit:
        extracted = extracted[:char_limit].rstrip() + "\n\n[... PDF text truncated ...]"
    return "[PDF text extracted from linked document]\n" + extracted
