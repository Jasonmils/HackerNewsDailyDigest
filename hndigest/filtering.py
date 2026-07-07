"""Story topic filtering.

Summary:
    Implements strict keyword matching and semantic LLM classification for
    deciding which Hacker News stories belong in the digest topic scope.

Adding functions:
    Add new ranking/filtering strategies here when they operate on story
    metadata before summarization. Keep fetching in hn/articles and keep final
    summary generation in summarizer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from .config import KEYWORD_EDGE_RE, KEYWORD_END_RE
from .utils import html_to_text, parse_json

@dataclass(frozen=True)
class KeywordPattern:
    raw: str
    regex: re.Pattern[str]


def _keyword_body_regex(keyword: str) -> str:
    """Build a regex body for one keyword, without the outer word edges."""
    if keyword == "ai":
        return r"a\.?i\.?"
    parts = [p for p in re.split(r"\s+", keyword) if p]
    if len(parts) > 1:
        return r"[\s/_-]+".join(re.escape(p) for p in parts)
    return re.escape(keyword)


def build_keyword_patterns(keywords: list[str]) -> list[KeywordPattern]:
    """Compile keyword filters.

    Keywords match on alphanumeric boundaries, so "ai" matches "AI" but not
    "airplane". Add a trailing "*" for intentional prefix matching, e.g. acquir*.
    """
    patterns: list[KeywordPattern] = []
    for raw_kw in keywords:
        raw = raw_kw.strip()
        if not raw:
            continue
        prefix = raw.endswith("*")
        keyword = raw[:-1].strip().lower() if prefix else raw.lower()
        if not keyword:
            continue
        body = _keyword_body_regex(keyword)
        end = "" if prefix else KEYWORD_END_RE
        regex = re.compile(KEYWORD_EDGE_RE + body + end, re.IGNORECASE)
        patterns.append(KeywordPattern(raw=raw, regex=regex))
    return patterns


def matched_keywords(text: str, patterns: list[KeywordPattern]) -> list[str]:
    """Return keyword labels that match text using strict keyword boundaries."""
    return [p.raw for p in patterns if p.regex.search(text or "")]


def story_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def semantic_filter_prompt(
    stories: list[dict],
    topic_scope: str,
    keywords: list[str],
    keyword_hits: dict[int, list[str]],
) -> str:
    """Build a compact batch-classification prompt for HN story metadata."""
    rows = []
    for s in stories:
        sid = int(s.get("id"))
        title = s.get("title", "")
        url = s.get("url", "")
        post_text = html_to_text(s.get("text", ""))
        if len(post_text) > 500:
            post_text = post_text[:500] + "..."
        rows.append({
            "id": sid,
            "title": title,
            "domain": story_domain(url),
            "url": url,
            "hn_post_text": post_text,
            "keyword_hits": keyword_hits.get(sid, []),
        })
    return (
        "You are filtering Hacker News stories for a narrowly scoped daily digest.\n"
        f"Topic scope: {topic_scope}\n\n"
        "The keyword list is only a hint, not the final rule. Include a story when its "
        "main subject is substantially about the topic scope, even if the exact keywords "
        "do not appear. Exclude stories where a keyword-like string is incidental or a "
        "word collision, such as 'AI' inside 'airplane', or 'agent' meaning an unrelated "
        "human intermediary. When uncertain, be selective.\n\n"
        "Return only this JSON shape:\n"
        '{"decisions":[{"id":123,"include":true,"labels":["AI/LLM"],"reason":"short reason"}]}\n\n'
        f"Keyword hints: {keywords}\n"
        f"Stories: {json.dumps(rows, ensure_ascii=False)}"
    )


async def semantic_filter_stories(ctx: "Ctx", stories: list[dict], log) -> Optional[set[int]]:
    """Use a cheap LLM classifier to keep stories semantically related to cfg.topic_scope."""
    cfg = ctx.cfg
    if not stories:
        return set()
    patterns = build_keyword_patterns(cfg.keywords)
    keyword_hits = {
        int(s.get("id")): matched_keywords(s.get("title", ""), patterns)
        for s in stories
        if s.get("id") is not None
    }
    included: set[int] = set()
    batch_size = max(1, cfg.filter_batch_size)
    for start in range(0, len(stories), batch_size):
        batch = stories[start:start + batch_size]
        prompt = semantic_filter_prompt(batch, cfg.topic_scope, cfg.keywords, keyword_hits)
        try:
            msg = await ctx.client.chat.completions.create(
                model=cfg.filter_model,
                max_tokens=3000,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a precise topic classifier. Return compact JSON only. "
                            "Do not include Markdown or prose."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as e:
            log(f"› Semantic filter failed, falling back to strict keywords: {e}")
            return None
        if msg.usage:
            ctx.usage["filter_input"] += msg.usage.prompt_tokens
            ctx.usage["filter_output"] += msg.usage.completion_tokens
        parsed = parse_json(msg.choices[0].message.content or "")
        decisions = parsed.get("decisions") if isinstance(parsed, dict) else None
        if not isinstance(decisions, list):
            log("› Semantic filter returned invalid JSON, falling back to strict keywords")
            return None
        batch_ids = {int(s.get("id")) for s in batch if s.get("id") is not None}
        for d in decisions:
            if not isinstance(d, dict) or not d.get("include"):
                continue
            try:
                sid = int(d.get("id"))
            except Exception:
                continue
            if sid in batch_ids:
                included.add(sid)
    return included


def strict_filter_stories(stories: list[dict], keywords: list[str]) -> list[dict]:
    patterns = build_keyword_patterns(keywords)
    return [s for s in stories if matched_keywords(s.get("title", ""), patterns)]
