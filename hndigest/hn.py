"""Hacker News Firebase API access.

Summary:
    Fetches top story IDs, item metadata, top-level comments, and selects the
    most-replied comment used in the digest.

Adding functions:
    Add HN API-specific helpers here, especially anything that reads
    `item/{id}.json` or transforms HN comments. Keep linked article fetching in
    articles.py and topic decisions in filtering.py.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from .config import HN_API, HN_ITEM_URL
from .utils import html_to_text

class HNClient:
    def __init__(self, http: httpx.AsyncClient, timeout: float):
        self.http = http
        self.timeout = timeout

    async def _get_json(self, path: str) -> Any:
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                r = await self.http.get(f"{HN_API}/{path}.json", timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # transient Firebase hiccups
                last = e
                await asyncio.sleep(0.5 * (attempt + 1))
        raise last  # type: ignore[misc]

    async def top_story_ids(self, n: int) -> list[int]:
        ids = await self._get_json("topstories")
        return (ids or [])[:n]

    async def item(self, item_id: int) -> dict:
        data = await self._get_json(f"item/{item_id}")
        return data or {}


async def fetch_items(hn: HNClient, ids: list[int], concurrency: int) -> list[Optional[dict]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(i: int) -> Optional[dict]:
        async with sem:
            try:
                return await hn.item(i)
            except Exception:
                return None

    return await asyncio.gather(*[one(i) for i in ids])


async def fetch_top_comments(hn: HNClient, story: dict, limit: int) -> list[dict]:
    """Fetch the first `limit` top-level comments, full text, with reply counts."""
    kids = (story.get("kids") or [])[:limit]
    if not kids:
        return []
    raw = await asyncio.gather(*[hn.item(k) for k in kids], return_exceptions=True)
    out: list[dict] = []
    for c in raw:
        if isinstance(c, Exception) or not c:
            continue
        if c.get("type") != "comment" or c.get("dead") or c.get("deleted"):
            continue
        text = html_to_text(c.get("text", ""))
        if not text:
            continue
        out.append({"by": c.get("by", "anon"), "text": text, "replies": len(c.get("kids") or [])})
    return out


def pick_top_comment(comments: list[dict]) -> Optional[dict]:
    """The fetched top-level comment with the most direct replies (None if all have zero)."""
    if not comments:
        return None
    top = max(comments, key=lambda c: c["replies"])
    return top if top["replies"] > 0 else None
