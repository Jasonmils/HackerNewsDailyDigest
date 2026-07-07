from __future__ import annotations

from typing import Optional

import httpx
import trafilatura

from .config import DEFAULT_HEADERS

async def fetch_article_text(
    http: httpx.AsyncClient, url: str, timeout: float, char_limit: int
) -> Optional[str]:
    try:
        r = await http.get(url, timeout=timeout, follow_redirects=True, headers=DEFAULT_HEADERS)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    ctype = r.headers.get("content-type", "").lower()
    if "html" not in ctype and "text/plain" not in ctype:
        return None  # PDFs, video, images, etc.
    try:
        extracted = trafilatura.extract(
            r.text, include_comments=False, include_tables=True, favor_recall=True
        )
    except Exception:
        return None
    if not extracted:
        return None
    extracted = extracted.strip()
    if len(extracted) > char_limit:
        extracted = extracted[:char_limit] + "\n\n[... truncated ...]"
    return extracted

