from __future__ import annotations

import html
import json
import re
from typing import Optional

from .config import MD_BOLD_RE, TAG_RE


def md_bold_to_html(s: str) -> str:
    """Escape for HTML, then turn the model's **bold** markers into <strong>."""
    return MD_BOLD_RE.sub(r"<strong>\1</strong>", html.escape(s))


def html_to_text(s: str) -> str:
    """HN comment/post bodies are small HTML fragments."""
    if not s:
        return ""
    s = s.replace("</p>", "\n\n").replace("<p>", "\n\n")
    s = TAG_RE.sub("", s)
    return html.unescape(s).strip()


async def _none() -> None:
    return None


def extract_json_object(raw: str) -> Optional[str]:
    """Return the first balanced JSON object from raw text."""
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(raw[start:], start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
    return None


def parse_json(raw: str) -> Optional[dict]:
    """Tolerant JSON extraction from a model response."""
    if not raw:
        return None
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        return json.loads(raw)
    except Exception:
        obj = extract_json_object(raw)
        if not obj:
            return None
        try:
            return json.loads(obj)
        except Exception:
            return None
    return None
