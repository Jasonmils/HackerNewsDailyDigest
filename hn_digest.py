#!/usr/bin/env python3
"""
hn_digest.py — Hacker News 每日热榜 Agent

Pipeline:
  1. Fetch top story IDs from the Hacker News Firebase API
  2. Concurrently fetch each story's metadata, article body, and top comments
  3. Summarize each story with DeepSeek (TL;DR + key points + discussion themes + tags)
  4. Render a daily digest as Markdown (+ a self-contained HTML reading page)

Usage:
  export DEEPSEEK_API_KEY=sk-...          # or fill in DEEPSEEK_API_KEY below
  python hn_digest.py                          # top 10 AI/LLM + crypto + business/startup stories
  python hn_digest.py --keywords ""            # disable the topic filter, digest raw top stories
  python hn_digest.py --num 20 --lang en       # top 20, English summaries
  python hn_digest.py --keywords AI,LLM,crypto # custom keyword hints for topic filtering
  python hn_digest.py --filter-mode strict     # exact title keywords only, no semantic filter
  python hn_digest.py --knowledge ./knowledge_profile.md # adapt explanations to you
  python hn_digest.py --proxy http://127.0.0.1:7897   # route API + fetches through a local proxy
  python hn_digest.py --judge                   # judgment mode: predict before reveal, log to ledger
  python hn_digest.py --grade-only              # only score due predictions from the ledger

Requires: openai, httpx, trafilatura  (see requirements.txt)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
import trafilatura
from openai import AsyncOpenAI

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
HN_API = "https://hacker-news.firebaseio.com/v0"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={}"

# DeepSeek API. The API is OpenAI-compatible, so we talk to it through the
# `openai` SDK by pointing base_url at DeepSeek's endpoint.
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Your DeepSeek API key. Leave it blank and paste your key here, or set the
# DEEPSEEK_API_KEY environment variable instead (the env var wins if both set).
DEEPSEEK_API_KEY = ""

# Standard list prices, USD per 1M tokens (input, output). Used only for the
# rough cost line printed at the end — update if DeepSeek changes rates.
PRICING = {
    "deepseek-v4-pro": (0.435, 0.87),    # DeepSeek-V4-Pro (cache-miss input price)
    "deepseek-v4-flash": (0.14, 0.28),   # DeepSeek-V4-Flash (cache-miss input price)
    # legacy aliases (deprecated 2026-07-24; map to V4-Flash non-thinking/thinking)
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}

# Default topic filter: AI/LLM, crypto/Bitcoin, and business/startup news.
# Pass --keywords "" to disable filtering and digest the raw top stories instead.
DEFAULT_KEYWORDS = (
    "ai,llm,gpt,openai,xai,anthropic,claude,gemini,llama,mistral,agent,agents,"
    "bitcoin,crypto,blockchain,ethereum,web3,defi,"
    "startup,funding,raise,raises,raised,raising,valuation,ipo,acquir*,acquisition,"
    "venture,vc,founder,growth,saas"
)

DEFAULT_TOPIC_SCOPE = (
    "AI/LLM systems, AI agents, foundation models, machine learning infrastructure, "
    "crypto/Bitcoin/Ethereum/blockchain/Web3/DeFi, and startup/business stories about "
    "funding, venture capital, acquisitions, valuations, IPOs, founders, SaaS, or growth."
)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HN-Digest/1.0; +https://news.ycombinator.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en;q=0.9",
}

LABELS = {
    "zh": {
        "title": "Hacker News 每日热榜", "count": "条", "model": "模型",
        "points": "分", "comments": "评论", "hn": "HN 讨论", "summary": "概要",
        "keypoints": "要点", "discussion": "讨论", "context": "联系你的知识背景",
        "failed": "处理失败",
        "noinfo": "未能抓取正文，以下基于标题与讨论",
        "top_comment": "回复最多的评论", "replies": "回复",
        "forecast": "预测问题", "prediction": "我的判断", "confidence": "置信度",
        "rebuttal": "最强反驳", "resolve_by": "到期",
    },
    "en": {
        "title": "Hacker News Daily", "count": "stories", "model": "model",
        "points": "points", "comments": "comments", "hn": "HN thread", "summary": "Summary",
        "keypoints": "Key points", "discussion": "Discussion", "context": "Context for you",
        "failed": "failed",
        "noinfo": "Article body unavailable; summary based on title and discussion",
        "top_comment": "Most-replied comment", "replies": "replies",
        "forecast": "Forecast", "prediction": "My call", "confidence": "confidence",
        "rebuttal": "Strongest rebuttal", "resolve_by": "due",
    },
}

TAG_RE = re.compile(r"<[^>]+>")
MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
KEYWORD_EDGE_RE = r"(?<![A-Za-z0-9])"
KEYWORD_END_RE = r"(?![A-Za-z0-9])"


def md_bold_to_html(s: str) -> str:
    """Escape for HTML, then turn the model's **bold** markers into <strong>."""
    return MD_BOLD_RE.sub(r"<strong>\1</strong>", html.escape(s))


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    num_stories: int = 10
    model: str = "deepseek-v4-pro"
    lang: str = "zh"                  # "zh" | "en"
    keywords: list[str] = field(default_factory=lambda: [k for k in DEFAULT_KEYWORDS.split(",") if k])
    topic_scope: str = DEFAULT_TOPIC_SCOPE
    filter_mode: str = "semantic"     # "semantic" | "strict"
    filter_model: str = "deepseek-v4-flash"
    filter_batch_size: int = 40
    knowledge_path: Optional[Path] = Path("./knowledge_profile.md")
    knowledge_char_limit: int = 12_000
    pool: int = 200                   # candidate pool size when filtering by keyword
    max_concurrency: int = 6          # parallel article-fetch + LLM slots
    meta_concurrency: int = 20        # parallel HN metadata fetches (cheap)
    max_comments: int = 8             # top-level comments fed to the summarizer
    article_char_limit: int = 12_000
    comment_char_limit: int = 4_000
    request_timeout: float = 25.0
    output_dir: Path = Path("./digests")
    proxy: Optional[str] = None
    fetch_articles: bool = True
    html: bool = True
    cache: bool = True
    thinking: bool = True             # DeepSeek-V4 thinking (chain-of-thought) mode
    reasoning_effort: str = "high"    # "high" | "max", only used when thinking is on
    judge: bool = False               # judgment mode: predict-before-reveal calibration loop
    judge_horizon_days: int = 30      # default resolve-by horizon for new predictions
    grade_only: bool = False          # only grade due predictions, then exit


@dataclass
class StoryResult:
    rank: int
    id: int
    title: str
    url: str
    hn_url: str
    score: int
    by: str
    comments_count: int
    summary: Optional[dict] = None
    error: Optional[str] = None
    cached: bool = False
    top_comment: Optional[dict] = None
    prediction: Optional[dict] = None   # judge mode: {"prediction","confidence","resolve_by"}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def html_to_text(s: str) -> str:
    """HN comment/post bodies are small HTML fragments."""
    if not s:
        return ""
    s = s.replace("</p>", "\n\n").replace("<p>", "\n\n")
    s = TAG_RE.sub("", s)
    return html.unescape(s).strip()


async def _none() -> None:
    return None


def parse_json(raw: str) -> Optional[dict]:
    """Tolerant JSON extraction from a model response."""
    if not raw:
        return None
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


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


def clean_knowledge_profile(text: str, limit: int) -> str:
    """Remove template comments and trim the user's knowledge profile for prompting."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    lines = [line.rstrip() for line in text.splitlines()]
    compact = "\n".join(line for line in lines if line.strip()).strip()
    substantive = "\n".join(
        line for line in compact.splitlines() if not line.lstrip().startswith("#")
    ).strip()
    if len(substantive) < 20:
        return ""
    if len(compact) > limit:
        suffix = "\n\n[... trimmed ...]"
        compact = compact[: max(0, limit - len(suffix))].rstrip() + suffix
    return compact


def load_knowledge_profile(cfg: Config, log) -> Optional[str]:
    """Load the reader's self-maintained knowledge profile from env or file."""
    env_profile = os.environ.get("HN_DIGEST_KNOWLEDGE", "").strip()
    if env_profile:
        profile = clean_knowledge_profile(env_profile, cfg.knowledge_char_limit)
        if profile:
            log("› Loaded reader knowledge profile from HN_DIGEST_KNOWLEDGE")
            return profile

    if cfg.knowledge_path is None:
        return None
    try:
        if not cfg.knowledge_path.exists():
            return None
        profile = clean_knowledge_profile(
            cfg.knowledge_path.read_text(encoding="utf-8"),
            cfg.knowledge_char_limit,
        )
    except Exception as e:
        log(f"› Could not read knowledge profile {cfg.knowledge_path}: {e}")
        return None
    if profile:
        log(f"› Loaded reader knowledge profile from {cfg.knowledge_path}")
        return profile
    return None


def knowledge_cache_key(item_id: int, knowledge_profile: Optional[str]) -> str:
    if not knowledge_profile:
        return str(item_id)
    sig = hashlib.sha1(knowledge_profile.encode("utf-8")).hexdigest()[:12]
    return f"{item_id}-kp-{sig}"


# --------------------------------------------------------------------------- #
# Hacker News API client
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Article fetching
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Summarization (DeepSeek)
# --------------------------------------------------------------------------- #
def build_system(lang: str) -> str:
    if lang == "zh":
        return (
            "你是一位资深科技分析师，为时间有限的技术读者撰写 Hacker News 每日热榜摘要。"
            "语言要精炼但不能把背景压没：直接给结论、数字和实体名，不要"
            "“本文讨论了/介绍了”这类空话和营销腔，不要编造原文中没有的事实。"
            "尽量少用缩写、简称和首字母缩略词；确需使用时，在首次出现处给出全称，"
            "必要时再加一句简短解释，例如「RAG（检索增强生成）」「FDA（美国食品药品监督管理局）」"
            "——宁可多用几个字写清楚，也不要留下读者看不懂的缩写。"
            "如果提供了读者知识画像，请用它来决定哪些概念可以略过、哪些概念需要用读者熟悉的领域做桥接解释。"
            "始终只返回一个 JSON 对象，不包含任何额外文字、说明或 Markdown 代码块。"
        )
    return (
        "You are a senior tech analyst writing daily Hacker News digests for time-pressed "
        "technical readers. Be concise and information-dense without deleting necessary context: lead with the "
        "conclusion, the number, the entity — never filler like 'this article discusses/"
        "explores', never invented facts. Avoid abbreviations, acronyms, and initialisms "
        "where possible; when one is necessary, spell out the full term on first use, with a "
        "short gloss if non-obvious (e.g. 'RAG (retrieval-augmented generation)') — better to "
        "spend a few extra words than leave the reader with an opaque acronym. If a reader "
        "knowledge profile is provided, use it to decide what can be assumed and what needs "
        "bridging via concepts the reader already knows. Always return a "
        "single JSON object with no extra text, explanation, or Markdown fences."
    )


def build_prompt(
    story: dict,
    article_text: Optional[str],
    comments: list[dict],
    lang: str,
    comment_char_limit: int,
    knowledge_profile: Optional[str] = None,
    judge: bool = False,
) -> str:
    title = story.get("title", "")
    url = story.get("url", "")
    post_body = html_to_text(story.get("text", ""))  # Ask HN / Show HN bodies

    parts: list[str] = [f"Title: {title}"]
    if url:
        parts.append(f"URL: {url}")
    if post_body:
        parts.append(f"Original post:\n{post_body}")
    if article_text:
        parts.append(f"Article content:\n{article_text}")
    elif not post_body:
        parts.append(
            "(The article body could not be fetched. Summarize from the title and the "
            "discussion only, and note in `summary` that information is limited.)"
        )
    if comments:
        used = 0
        blocks: list[str] = []
        for c in comments:
            text = c["text"]
            if used + len(text) > comment_char_limit:
                text = text[: max(0, comment_char_limit - used)]
            if not text:
                break
            blocks.append(f"[{c['by']}] {text}")
            used += len(text)
            if used >= comment_char_limit:
                break
        if blocks:
            parts.append("Top Hacker News comments:\n" + "\n\n".join(blocks))

    if knowledge_profile:
        parts.append(
            "Reader knowledge profile (use this to adapt explanations; do not quote it back):\n"
            f"{knowledge_profile}\n\n"
            "Adaptation rules:\n"
            "- If the story uses concepts the reader likely knows, rely on them and move fast.\n"
            "- If it uses concepts outside the profile, add a compact bridge from known concepts to the new idea.\n"
            "- Prefer one useful comparison over generic background."
        )

    if lang == "zh":
        fields = [
            '  "title_translation": 字符串，将标题（Title 字段）翻译成简体中文，'
            "准确直译，保留专有名词/产品名/公司名不译；若标题本身已是中文则原样返回；",
            '  "summary": 字符串，1-2 句话（约 50-90 字；若需解释背景可适当放宽）'
            "点出最核心的结论、数字和为什么值得关注，不要泛泛而谈；",
            '  "reader_context": 字符串，1 句话，把本文最容易卡住的概念连接到读者知识画像；'
            "如果没有提供知识画像或无需桥接，写空字符串；",
            '  "key_points": 字符串数组，3-4 条，每条约 20-40 字（同样为展开缩写可适当放宽），并用 Markdown **粗体** '
            "标出该条里唯一最关键的实体/数字/结论（例如 \"**xAI 完成 60 亿美元融资**，用于扩张算力\"）；",
            '  "discussion": 字符串，1 句话点出 HN 评论区的核心分歧或共识（无评论则写空字符串）；',
            '  "tags": 字符串数组，2-3 个具体的主题标签（避免“技术”这类泛标签，用 "LLM 推理"、'
            '"开源协议" 等具体词）；',
        ]
        if judge:
            fields.append(
                '  "forecast_question": 字符串，一个**可证伪、有明确时限**的预测问题，'
                "答案应为是/否型，聚焦该故事的近期走向（例如 \"该模型是否会在 90 天内开源权重？\"、"
                "\"这家公司是否会在 6 个月内宣布下一轮融资？\"）。要具体、可验证，避免空泛；"
            )
            fields.append(
                '  "rebuttal": 字符串，针对该故事最主流/最乐观叙事的**最强一条反驳**（steelman），'
                "尽量引用上面 HN 评论里最有力的反方观点，一两句话点到要害。"
            )
        schema = (
            "请用简体中文输出一个 JSON 对象，语言要极度精炼，杜绝套话，字段如下：\n"
            + "\n".join(fields)
            + "\n只输出该 JSON，不要任何其他内容。"
        )
    else:
        fields = [
            '  "summary": string, 1-2 sentences (~35-60 words; relax slightly if needed to '
            "explain context) stating the core conclusion, number, and why it matters — not a vague overview;",
            '  "reader_context": string, one sentence bridging the hardest concept to the reader knowledge profile; '
            "empty string if no profile is provided or no bridge is needed;",
            '  "key_points": array of 3-4 strings, each ~12-24 words (relax slightly to expand an acronym), with the one key '
            "entity/number/conclusion in that point wrapped in Markdown **bold** "
            "(e.g. \"**xAI raised $6B** to expand compute\");",
            '  "discussion": string, 1 sentence naming the core disagreement or consensus in '
            "the HN comments (empty string if none);",
            '  "tags": array of 2-3 specific topic tags (avoid vague tags like "tech"; prefer '
            'things like "LLM inference", "open-source licensing");',
        ]
        if judge:
            fields.append(
                '  "forecast_question": string, one **falsifiable, time-bounded** yes/no '
                "forecasting question about the story's near future (e.g. \"Will this model's "
                "weights be open-sourced within 90 days?\", \"Will the company announce a "
                "follow-on round within 6 months?\"). Be specific and verifiable;"
            )
            fields.append(
                '  "rebuttal": string, the single **strongest counter-argument** (steelman) '
                "against the story's main/most-optimistic framing, drawing on the strongest "
                "dissenting HN comment above; one or two sentences, straight to the point."
            )
        schema = (
            "Output a single JSON object in English, ruthlessly concise, no filler, with these "
            "fields:\n"
            + "\n".join(fields)
            + "\nOutput only the JSON, nothing else."
        )
    parts.append(schema)
    return "\n\n".join(parts)


async def summarize_story(
    client: AsyncOpenAI,
    model: str,
    story: dict,
    article_text: Optional[str],
    comments: list[dict],
    lang: str,
    usage: dict,
    comment_char_limit: int,
    thinking: bool,
    reasoning_effort: str,
    knowledge_profile: Optional[str] = None,
    judge: bool = False,
) -> tuple[Optional[dict], Optional[str]]:
    prompt = build_prompt(
        story, article_text, comments, lang, comment_char_limit, knowledge_profile, judge
    )
    extra: dict = {}
    # Reasoning tokens share the max_tokens budget with the final answer, so
    # give thinking mode much more room than a plain non-thinking call needs.
    max_tokens = 8000 if thinking else 1200
    if thinking:
        extra["extra_body"] = {"thinking": {"type": "enabled"}}
        extra["reasoning_effort"] = reasoning_effort
    else:
        extra["extra_body"] = {"thinking": {"type": "disabled"}}
    try:
        msg = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": build_system(lang)},
                {"role": "user", "content": prompt},
            ],
            **extra,
        )
    except Exception as e:
        return None, f"LLM error: {e}"

    if msg.usage:
        usage["input"] += msg.usage.prompt_tokens
        usage["output"] += msg.usage.completion_tokens
    raw = msg.choices[0].message.content or ""
    parsed = parse_json(raw)
    if parsed is None:
        return None, "could not parse model JSON"
    return parsed, None


# --------------------------------------------------------------------------- #
# Per-story orchestration
# --------------------------------------------------------------------------- #
class Cache:
    def __init__(self, root: Path, enabled: bool):
        self.root = root / ".cache"
        self.enabled = enabled
        if enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def get(self, item_id: int | str) -> Optional[dict]:
        if not self.enabled:
            return None
        p = self.root / f"{item_id}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def set(self, item_id: int | str, summary: dict) -> None:
        if not self.enabled or summary is None:
            return
        try:
            (self.root / f"{item_id}.json").write_text(
                json.dumps(summary, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass


# Outcome label → numeric score used for Brier (1=came true, 0=wrong, .5=partial).
OUTCOME_VALUES = {"hit": 1.0, "partial": 0.5, "miss": 0.0}


class Ledger:
    """Local prediction台账: append-only-ish JSON list of forecasts + their grades."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add(self, entry: dict) -> None:
        self.entries.append(entry)
        self._save()

    def due(self, today: str) -> list[dict]:
        """Open predictions whose resolve_by date has arrived."""
        return [
            e for e in self.entries
            if e.get("status") == "open" and (e.get("resolve_by") or "9999") <= today
        ]

    def resolve(self, entry_id: str, outcome: str, note: str) -> None:
        for e in self.entries:
            if e.get("id") == entry_id:
                e["status"] = "resolved"
                e["outcome"] = outcome
                val = OUTCOME_VALUES.get(outcome, 0.0)
                conf = (e.get("confidence") or 0) / 100.0
                e["score"] = round((conf - val) ** 2, 4)
                e["note"] = note
                break
        self._save()

    def stats(self) -> Optional[dict]:
        graded = [e for e in self.entries if e.get("status") == "resolved" and e.get("score") is not None]
        if not graded:
            return None
        n = len(graded)
        mean_brier = sum(e["score"] for e in graded) / n
        hits = sum(1 for e in graded if e.get("outcome") == "hit")
        return {"n": n, "brier": mean_brier, "hits": hits, "open": sum(1 for e in self.entries if e.get("status") == "open")}


@dataclass
class Ctx:
    hn: HNClient
    http: httpx.AsyncClient
    client: AsyncOpenAI
    cfg: Config
    sem: asyncio.Semaphore
    usage: dict
    cache: Cache
    knowledge_profile: Optional[str] = None


async def summarize_one(rank: int, story: dict, ctx: Ctx) -> StoryResult:
    cfg = ctx.cfg
    sid = int(story.get("id"))
    url = story.get("url", "")
    res = StoryResult(
        rank=rank,
        id=sid,
        title=story.get("title", ""),
        url=url,
        hn_url=HN_ITEM_URL.format(sid),
        score=story.get("score", 0),
        by=story.get("by", "anon"),
        comments_count=story.get("descendants", 0),
    )

    # Judge mode needs the forecast_question/rebuttal fields, which normal cached
    # summaries don't have — so bypass the cache read when judging.
    cache_id = knowledge_cache_key(sid, ctx.knowledge_profile)
    cached = None if cfg.judge else ctx.cache.get(cache_id)

    async with ctx.sem:
        article_task = (
            fetch_article_text(ctx.http, url, cfg.request_timeout, cfg.article_char_limit)
            if (url and cfg.fetch_articles and not cached)
            else _none()
        )
        comments_task = fetch_top_comments(ctx.hn, story, cfg.max_comments)
        article_text, comments = await asyncio.gather(article_task, comments_task)
        res.top_comment = pick_top_comment(comments)

        if cached:
            res.summary = cached
            res.cached = True
            return res

        summary, err = await summarize_story(
            ctx.client, cfg.model, story, article_text, comments, cfg.lang, ctx.usage,
            cfg.comment_char_limit, cfg.thinking, cfg.reasoning_effort,
            ctx.knowledge_profile, cfg.judge,
        )

    res.summary = summary
    res.error = err
    if summary:
        ctx.cache.set(cache_id, summary)
    return res


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_markdown(results: list[StoryResult], cfg: Config, generated_at: datetime) -> str:
    lbl = LABELS[cfg.lang]
    date_str = generated_at.strftime("%Y-%m-%d")
    ok = [r for r in results if r and r.summary]
    out: list[str] = [f"# {lbl['title']} · {date_str}", ""]
    out.append(
        f"> {len(ok)} {lbl['count']} · {generated_at.strftime('%Y-%m-%d %H:%M %Z')} · "
        f"{lbl['model']} `{cfg.model}`"
    )
    out.append("")
    for r in results:
        if not r:
            continue
        s = r.summary
        out.append(f"## {r.rank}. [{r.title}]({r.url or r.hn_url})")
        if s and s.get("title_translation") and s["title_translation"] != r.title:
            out.append(f"*{s['title_translation']}*")
        out.append(
            f"▲ {r.score} {lbl['points']} · {r.by} · "
            f"[{r.comments_count} {lbl['comments']}]({r.hn_url})"
        )
        out.append("")
        if s:
            if s.get("summary"):
                out.append(f"📝 **{lbl['summary']}**：{s['summary']}")
            if s.get("reader_context"):
                out.append("")
                out.append(f"🧭 **{lbl['context']}**：{s['reader_context']}")
            kps = s.get("key_points") or []
            if kps:
                out.append("")
                out.append(f"**🔑 {lbl['keypoints']}**")
                out.extend(f"- {p}" for p in kps)
            if s.get("discussion"):
                out.append("")
                out.append(f"💬 **{lbl['discussion']}**：{s['discussion']}")
            tags = s.get("tags") or []
            if tags:
                out.append("")
                out.append("🏷️ " + " · ".join(f"`{t}`" for t in tags))
            if s.get("forecast_question"):
                out.append("")
                out.append(f"🧠 **{lbl['forecast']}**：{s['forecast_question']}")
                if r.prediction:
                    pr = r.prediction
                    out.append(
                        f"- {lbl['prediction']}：{pr['prediction'] or '—'} "
                        f"（{lbl['confidence']} {pr['confidence']}% · {lbl['resolve_by']} {pr['resolve_by']}）"
                    )
                if s.get("rebuttal"):
                    out.append(f"- **{lbl['rebuttal']}**：{s['rebuttal']}")
        elif r.error:
            out.append(f"_({lbl['failed']}: {r.error})_")
        if r.top_comment:
            tc = r.top_comment
            out.append("")
            out.append(
                f"🔥 **{lbl['top_comment']}**（{tc['by']} · {tc['replies']} {lbl['replies']}）："
            )
            out.append("")
            out.extend(f"> {line}" if line else ">" for line in tc["text"].split("\n"))
        out.extend(["", "---", ""])
    return "\n".join(out)


CSS_BLOCK = """
:root{
  --bg:#FCFCFA; --ink:#1B1B1B; --muted:#6F6F68; --hair:#E7E6DF;
  --hn:#FF6600; --hn-soft:#FFF1E8; --card:#FFFFFF;
  --body:"Inter","Helvetica Neue",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Hiragino Sans","Noto Sans CJK SC","PingFang SC","Microsoft YaHei",sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono","Roboto Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--body);
  line-height:1.66;font-size:16.5px;-webkit-font-smoothing:antialiased}
.wrap{max-width:720px;margin:0 auto;padding:0 22px}
header{border-bottom:2px solid var(--ink);margin:34px auto 6px;max-width:720px;padding:0 22px 14px}
header .mast{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
header h1{font-family:var(--mono);font-size:clamp(22px,5vw,30px);font-weight:700;
  letter-spacing:-.01em;margin:0}
header h1 .y{color:var(--hn)}
header .sub{font-family:var(--mono);color:var(--muted);font-size:12.5px;margin:8px 0 0;
  text-transform:uppercase;letter-spacing:.06em}
main{padding-bottom:64px}
.card{border-bottom:1px solid var(--hair);padding:26px 0 24px}
.card:last-child{border-bottom:none}
.head{display:flex;gap:14px;align-items:flex-start}
.rank{font-family:var(--mono);font-size:14px;font-weight:700;color:var(--hn);
  line-height:1.5;min-width:30px;padding-top:2px;font-variant-numeric:tabular-nums}
.card h2{font-size:20px;font-weight:650;line-height:1.32;margin:0;letter-spacing:-.01em}
.card h2 a{color:var(--ink);text-decoration:none;border-bottom:1.5px solid transparent;
  transition:border-color .12s ease,color .12s ease}
.card h2 a:hover{color:var(--hn);border-bottom-color:var(--hn)}
.title-tr{color:var(--muted);font-size:14px;font-style:italic;margin:5px 0 0 44px}
.meta{font-family:var(--mono);color:var(--muted);font-size:12.5px;margin:7px 0 0 44px;
  font-variant-numeric:tabular-nums}
.meta a{color:var(--muted);text-decoration:none;border-bottom:1px dotted var(--muted)}
.meta a:hover{color:var(--hn);border-bottom-color:var(--hn)}
.body{margin:14px 0 0 44px}
.summary{margin:0}
.context{margin:12px 0 0;padding:10px 13px;background:#F5F7FA;border:1px solid var(--hair);
  border-radius:6px;font-size:14.5px;color:var(--ink)}
.context strong{color:var(--hn);font-weight:650}
.kp{margin:12px 0 0;padding:0;list-style:none}
.kp li{position:relative;padding-left:18px;margin:5px 0}
.kp li::before{content:"";position:absolute;left:0;top:.66em;width:5px;height:5px;
  background:var(--hn);border-radius:50%}
.kp li strong,.summary strong,.disc strong{color:var(--hn);font-weight:700}
.disc{margin:14px 0 0;padding:11px 14px;background:var(--hn-soft);border-radius:8px;
  font-size:15px}
.disc strong{font-weight:650}
.topc{margin:14px 0 0;padding:10px 14px;background:var(--card);border:1px solid var(--hair);
  border-left:3px solid var(--hn);border-radius:6px;font-size:14px;color:var(--ink)}
.topc .topc-meta{font-family:var(--mono);color:var(--muted);font-size:11.5px;margin-bottom:5px;
  text-transform:uppercase;letter-spacing:.03em}
.topc p{margin:0 0 8px}
.topc p:last-child{margin-bottom:0}
.topc strong{color:var(--hn);font-weight:650}
.judge{margin:14px 0 0;padding:12px 14px;background:#F4F1FF;border:1px solid #E3DCFB;
  border-left:3px solid #6C5CE7;border-radius:8px;font-size:14.5px}
.judge-q{font-weight:600}
.judge-pred{margin:7px 0 0}
.judge-meta{font-family:var(--mono);color:var(--muted);font-size:11.5px}
.judge-reb{margin:8px 0 0}
.judge-reb strong{color:#6C5CE7;font-weight:650}
.tags{margin:14px 0 0;display:flex;flex-wrap:wrap;gap:7px}
.tag{font-family:var(--mono);font-size:11.5px;color:var(--muted);
  border:1px solid var(--hair);border-radius:999px;padding:3px 10px}
.err{margin:10px 0 0 44px;color:#A33;font-size:14px;font-family:var(--mono)}
footer{max-width:720px;margin:0 auto;padding:0 22px 48px;color:var(--muted);
  font-family:var(--mono);font-size:11.5px;text-transform:uppercase;letter-spacing:.06em}
@media(max-width:560px){
  .title-tr,.meta,.body,.err{margin-left:0}
  .rank{min-width:26px}
}
"""


def render_html(results: list[StoryResult], cfg: Config, generated_at: datetime) -> str:
    lbl = LABELS[cfg.lang]
    date_str = generated_at.strftime("%Y-%m-%d")
    ok = [r for r in results if r and r.summary]

    cards: list[str] = []
    for r in results:
        if not r:
            continue
        s = r.summary or {}
        title = html.escape(r.title)
        link = html.escape(r.url or r.hn_url, quote=True)
        hn = html.escape(r.hn_url, quote=True)
        title_tr = s.get("title_translation") or ""
        title_tr_html = (
            f'<p class="title-tr">{html.escape(title_tr)}</p>'
            if title_tr and title_tr != r.title
            else ""
        )
        summ = md_bold_to_html(s.get("summary", "")) if s else ""
        kps = "".join(f"<li>{md_bold_to_html(p)}</li>" for p in (s.get("key_points") or []))
        disc = md_bold_to_html(s.get("discussion", "")) if s else ""
        tags = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in (s.get("tags") or []))

        body_bits: list[str] = []
        if summ:
            body_bits.append(f'<p class="summary">{summ}</p>')
        if s.get("reader_context"):
            ctx_html = md_bold_to_html(s["reader_context"])
            body_bits.append(f'<p class="context"><strong>🧭 {lbl["context"]}</strong> {ctx_html}</p>')
        if kps:
            body_bits.append(f'<ul class="kp">{kps}</ul>')
        if disc:
            body_bits.append(f'<p class="disc"><strong>💬 {lbl["discussion"]}</strong> {disc}</p>')
        if tags:
            body_bits.append(f'<div class="tags">{tags}</div>')
        if s.get("forecast_question"):
            jb = [
                f'<div class="judge-q">🧠 {lbl["forecast"]}: '
                f'{md_bold_to_html(s["forecast_question"])}</div>'
            ]
            if r.prediction:
                pr = r.prediction
                jb.append(
                    f'<div class="judge-pred">{lbl["prediction"]}: '
                    f'{html.escape(pr["prediction"] or "—")} '
                    f'<span class="judge-meta">({lbl["confidence"]} {pr["confidence"]}% · '
                    f'{lbl["resolve_by"]} {html.escape(pr["resolve_by"])})</span></div>'
                )
            if s.get("rebuttal"):
                jb.append(
                    f'<div class="judge-reb"><strong>{lbl["rebuttal"]}</strong> '
                    f'{md_bold_to_html(s["rebuttal"])}</div>'
                )
            body_bits.append(f'<div class="judge">{"".join(jb)}</div>')
        if r.top_comment:
            tc = r.top_comment
            tc_paras = "".join(
                f"<p>{md_bold_to_html(p.strip())}</p>"
                for p in tc["text"].split("\n")
                if p.strip()
            )
            meta_line = f'{html.escape(tc["by"])} · {tc["replies"]} {lbl["replies"]}'
            body_bits.append(
                f'<div class="topc"><div class="topc-meta">🔥 {lbl["top_comment"]} · {meta_line}</div>'
                f'{tc_paras}</div>'
            )
        body = f'<div class="body">{"".join(body_bits)}</div>' if body_bits else ""
        err = "" if s else f'<p class="err">{html.escape(r.error or lbl["failed"])}</p>'

        cards.append(
            f'<article class="card">'
            f'<div class="head"><span class="rank">{r.rank:02d}</span>'
            f'<h2><a href="{link}" target="_blank" rel="noopener">{title}</a></h2></div>'
            f"{title_tr_html}"
            f'<div class="meta">▲ {r.score} {lbl["points"]} · {html.escape(r.by)} · '
            f'<a href="{hn}" target="_blank" rel="noopener">{r.comments_count} {lbl["comments"]}</a></div>'
            f"{body}{err}</article>"
        )

    body_html = "\n".join(cards)
    gen = html.escape(generated_at.strftime("%Y-%m-%d %H:%M %Z"))
    model = html.escape(cfg.model)
    return (
        "<!doctype html>\n"
        f'<html lang="{cfg.lang}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{lbl['title']} · {date_str}</title>\n"
        f"<style>{CSS_BLOCK}</style>\n</head>\n<body>\n"
        '<header><div class="mast">'
        f'<h1><span class="y">Y</span> {lbl["title"]}</h1></div>'
        f'<p class="sub">{date_str} &nbsp;·&nbsp; {len(ok)} {lbl["count"]} &nbsp;·&nbsp; {model}</p>'
        "</header>\n"
        f'<main><div class="wrap">\n{body_html}\n</div></main>\n'
        f"<footer>generated {gen} · hn-digest agent</footer>\n"
        "</body>\n</html>\n"
    )


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def build_client(api_key: str, proxy: Optional[str]):
    if proxy:
        http_client = httpx.AsyncClient(proxy=proxy)
        return AsyncOpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL, http_client=http_client), http_client
    return AsyncOpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL), None


def _log(m) -> None:
    print(m, file=sys.stderr, flush=True)


async def _open_ctx(cfg: Config):
    api_key = os.environ.get("DEEPSEEK_API_KEY") or DEEPSEEK_API_KEY
    if not api_key:
        sys.exit(
            "ERROR: DeepSeek API key not set. Fill in DEEPSEEK_API_KEY near the top of "
            "hn_digest.py, or set the DEEPSEEK_API_KEY environment variable."
        )
    limits = httpx.Limits(max_connections=cfg.max_concurrency * 4 + cfg.meta_concurrency)
    http = httpx.AsyncClient(proxy=cfg.proxy, limits=limits, headers=DEFAULT_HEADERS)
    client, extra_client = build_client(api_key, cfg.proxy)
    hn = HNClient(http, cfg.request_timeout)
    knowledge_profile = load_knowledge_profile(cfg, _log)
    ctx = Ctx(
        hn=hn, http=http, client=client, cfg=cfg,
        sem=asyncio.Semaphore(cfg.max_concurrency),
        usage={"input": 0, "output": 0, "filter_input": 0, "filter_output": 0},
        cache=Cache(cfg.output_dir, cfg.cache),
        knowledge_profile=knowledge_profile,
    )
    return ctx, http, extra_client


async def _close_ctx(http: httpx.AsyncClient, client, extra_client) -> None:
    await http.aclose()
    if extra_client is not None:
        await extra_client.aclose()
    try:
        await client.close()
    except Exception:
        pass


async def _collect_stories(ctx: Ctx, log) -> list[StoryResult]:
    cfg = ctx.cfg
    pool_n = cfg.pool if cfg.keywords else cfg.num_stories
    log(f"› Fetching top {pool_n} story IDs …")
    ids = await ctx.hn.top_story_ids(pool_n)

    log(f"› Fetching metadata for {len(ids)} stories …")
    items = [it for it in await fetch_items(ctx.hn, ids, cfg.meta_concurrency) if it and it.get("title")]

    if cfg.keywords:
        if cfg.filter_mode == "strict":
            items = strict_filter_stories(items, cfg.keywords)
            log(f"› {len(items)} stories match strict title keywords {cfg.keywords}")
        else:
            log(f"› Semantic topic filtering with {cfg.filter_model} …")
            semantic_ids = await semantic_filter_stories(ctx, items, log)
            if semantic_ids is None:
                items = strict_filter_stories(items, cfg.keywords)
                log(f"› {len(items)} stories match fallback strict title keywords {cfg.keywords}")
            else:
                items = [it for it in items if int(it.get("id")) in semantic_ids]
                log(f"› {len(items)} stories match topic scope semantically")

    selected = items[: cfg.num_stories]
    if not selected:
        log("› No stories to summarize.")
        return []

    log(f"› Summarizing {len(selected)} stories with {cfg.model} …")
    return list(await asyncio.gather(
        *[summarize_one(i + 1, it, ctx) for i, it in enumerate(selected)]
    ))


def _write_digest(results: list[StoryResult], cfg: Config, ctx: Ctx, log) -> list[Path]:
    generated_at = datetime.now().astimezone()
    date_str = generated_at.strftime("%Y-%m-%d")
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    md_path = cfg.output_dir / f"hn-digest-{date_str}.md"
    md_path.write_text(render_markdown(results, cfg, generated_at), encoding="utf-8")
    paths = [md_path]
    if cfg.html:
        html_path = cfg.output_dir / f"hn-digest-{date_str}.html"
        html_path.write_text(render_html(results, cfg, generated_at), encoding="utf-8")
        paths.append(html_path)

    n_ok = sum(1 for r in results if r and r.summary)
    n_cached = sum(1 for r in results if r and r.cached)
    pin, pout = PRICING.get(cfg.model, (0.0, 0.0))
    fpin, fpout = PRICING.get(cfg.filter_model, (0.0, 0.0))
    summary_cost = ctx.usage["input"] / 1e6 * pin + ctx.usage["output"] / 1e6 * pout
    filter_cost = (
        ctx.usage.get("filter_input", 0) / 1e6 * fpin
        + ctx.usage.get("filter_output", 0) / 1e6 * fpout
    )
    cost = summary_cost + filter_cost
    log(f"✓ {n_ok}/{len(results)} summarized ({n_cached} from cache)")
    log(
        f"  summary tokens: {ctx.usage['input']:,} in / {ctx.usage['output']:,} out"
    )
    if ctx.usage.get("filter_input") or ctx.usage.get("filter_output"):
        log(
            f"  filter tokens: {ctx.usage['filter_input']:,} in / "
            f"{ctx.usage['filter_output']:,} out"
        )
    log(f"  estimated cost: ≈ ${cost:.4f}")
    for p in paths:
        log(f"  → {p}")
    return paths


async def run(cfg: Config) -> list[Path]:
    ctx, http, extra_client = await _open_ctx(cfg)
    try:
        results = await _collect_stories(ctx, _log)
    finally:
        await _close_ctx(http, ctx.client, extra_client)
    if not results:
        return []
    return _write_digest(results, cfg, ctx, _log)


# --------------------------------------------------------------------------- #
# Judgment mode — interactive predict-before-reveal calibration loop
# --------------------------------------------------------------------------- #
JUDGE_LABELS = {
    "zh": {
        "intro": "🧠 判断力模式：每条先做判断，再揭晓讨论与最强反驳。",
        "grade_header": "📒 有 {n} 条到期预测待复盘打分：",
        "question": "预测问题", "you_said": "你当时的判断", "confidence": "置信度",
        "created": "记录于", "resolve_by": "到期",
        "grade_prompt": "结果？(h=命中 / m=未中 / p=部分)：",
        "grade_help": "请输入 h / m / p。",
        "note_prompt": "复盘备注(可空)：",
        "calib": "📊 已结算 {n} 条 · 平均 Brier {brier}（越低越准）· 命中 {hits} 条",
        "no_q": "（模型未给出可预测问题，跳过本条）",
        "pred_prompt": "你的预测（是/否，或一句话）：",
        "conf_prompt": "置信度(0-100，回车默认 50)：",
        "hidden": "🙈 讨论与最强反驳已隐藏 —— 先写下你的判断。",
        "reveal": "——————— 揭晓 ———————",
        "rebuttal": "最强反驳", "saved": "✅ 已写入台账，到期日 {resolve_by}。",
        "stats": "📊 台账：进行中 {open} 条 · 已结算 {n} 条 · 平均 Brier {brier}",
        "nothing_due": "📒 暂无到期预测。",
    },
    "en": {
        "intro": "🧠 Judgment mode: predict each story first, then reveal the discussion + steelman.",
        "grade_header": "📒 {n} prediction(s) are due for scoring:",
        "question": "Question", "you_said": "You said", "confidence": "confidence",
        "created": "logged", "resolve_by": "due",
        "grade_prompt": "Outcome? (h=hit / m=miss / p=partial): ",
        "grade_help": "Please enter h / m / p.",
        "note_prompt": "Retro note (optional): ",
        "calib": "📊 Resolved {n} · mean Brier {brier} (lower=better) · {hits} hits",
        "no_q": "(No forecastable question from the model; skipping.)",
        "pred_prompt": "Your prediction (yes/no, or a sentence): ",
        "conf_prompt": "Confidence (0-100, Enter for 50): ",
        "hidden": "🙈 Discussion + steelman hidden — commit your call first.",
        "reveal": "——————— REVEAL ———————",
        "rebuttal": "Strongest rebuttal", "saved": "✅ Logged to ledger, due {resolve_by}.",
        "stats": "📊 Ledger: {open} open · {n} resolved · mean Brier {brier}",
        "nothing_due": "📒 No predictions due yet.",
    },
}


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _ask_confidence(prompt: str) -> int:
    while True:
        raw = _ask(prompt)
        if not raw:
            return 50
        try:
            return max(0, min(100, int(round(float(raw)))))
        except ValueError:
            print("  0-100.")


def strip_md_bold(s: str) -> str:
    return MD_BOLD_RE.sub(r"\1", s)


def grade_due(ledger: Ledger, today: str, jl: dict) -> None:
    due = ledger.due(today)
    if not due:
        print("\n" + jl["nothing_due"])
        return
    print("\n" + jl["grade_header"].format(n=len(due)))
    for e in due:
        print()
        print(f"• {e.get('title', '')}")
        if e.get("url"):
            print(f"  {e['url']}")
        print(f"  {jl['question']}: {e.get('question', '')}")
        print(f"  {jl['you_said']}: {e.get('prediction', '')}  ({jl['confidence']} {e.get('confidence')}%)")
        print(f"  {jl['created']} {e.get('created', '')} · {jl['resolve_by']} {e.get('resolve_by', '')}")
        outcome = ""
        while outcome not in OUTCOME_VALUES:
            outcome = {"h": "hit", "m": "miss", "p": "partial"}.get(
                _ask("  " + jl["grade_prompt"]).lower(), ""
            )
            if outcome not in OUTCOME_VALUES:
                print("  " + jl["grade_help"])
        note = _ask("  " + jl["note_prompt"])
        ledger.resolve(e["id"], outcome, note)
    st = ledger.stats()
    if st:
        print("\n" + jl["calib"].format(n=st["n"], brier=round(st["brier"], 3), hits=st["hits"]))


def predict_and_reveal(r: StoryResult, ledger: Ledger, cfg: Config, today: str, jl: dict) -> None:
    s = r.summary or {}
    lbl = LABELS[cfg.lang]
    print("\n" + "=" * 64)
    print(f"{r.rank}. {r.title}")
    tr = s.get("title_translation")
    if tr and tr != r.title:
        print(f"   {tr}")
    print(f"   ▲ {r.score} · {r.comments_count} {lbl['comments']} · {r.hn_url}")
    if s.get("summary"):
        print(f"\n📝 {s['summary']}")
    for p in (s.get("key_points") or []):
        print(f"   • {strip_md_bold(p)}")
    tags = s.get("tags") or []
    if tags:
        print("   🏷  " + " · ".join(tags))

    q = s.get("forecast_question")
    if not q:
        print("\n" + jl["no_q"])
        return

    print(f"\n❓ {jl['question']}: {q}")
    print(jl["hidden"])
    pred = _ask("\n" + jl["pred_prompt"])
    conf = _ask_confidence(jl["conf_prompt"])

    print("\n" + jl["reveal"])
    if s.get("discussion"):
        print(f"💬 {lbl['discussion']}: {strip_md_bold(s['discussion'])}")
    if r.top_comment:
        tc = r.top_comment
        print(f"🔥 {lbl['top_comment']}（{tc['by']} · {tc['replies']} {lbl['replies']}）:")
        print("   " + strip_md_bold(tc["text"]).replace("\n", "\n   "))
    if s.get("rebuttal"):
        print(f"\n🧠 {jl['rebuttal']}: {strip_md_bold(s['rebuttal'])}")

    resolve_by = (
        datetime.strptime(today, "%Y-%m-%d") + timedelta(days=cfg.judge_horizon_days)
    ).strftime("%Y-%m-%d")
    ledger.add({
        "id": f"hn-{r.id}-{int(datetime.now().timestamp() * 1000)}",
        "hn_id": r.id, "title": r.title, "url": r.url or r.hn_url,
        "question": q, "prediction": pred, "confidence": conf,
        "created": today, "resolve_by": resolve_by,
        "status": "open", "outcome": None, "score": None, "note": "",
    })
    r.prediction = {"prediction": pred, "confidence": conf, "resolve_by": resolve_by}
    print("\n" + jl["saved"].format(resolve_by=resolve_by))


async def run_judge(cfg: Config) -> list[Path]:
    jl = JUDGE_LABELS[cfg.lang]
    ledger = Ledger(cfg.output_dir / "ledger.json")
    today = datetime.now().strftime("%Y-%m-%d")

    # Step A — score any predictions whose horizon has elapsed.
    grade_due(ledger, today, jl)
    if cfg.grade_only:
        return []

    # Step B — fetch + summarize (judge schema active).
    ctx, http, extra_client = await _open_ctx(cfg)
    try:
        results = await _collect_stories(ctx, _log)
    finally:
        await _close_ctx(http, ctx.client, extra_client)
    if not results:
        return []

    # Step C — interactive predict-then-reveal per story.
    print("\n" + jl["intro"])
    for r in results:
        if r and r.summary:
            predict_and_reveal(r, ledger, cfg, today, jl)

    # Step D — write the archival digest (full, with the judgment block).
    paths = _write_digest(results, cfg, ctx, _log)
    st = ledger.stats()
    if st:
        print("\n" + jl["stats"].format(open=st["open"], n=st["n"], brier=round(st["brier"], 3)))
    return paths


def parse_args() -> Config:
    p = argparse.ArgumentParser(description="Hacker News 每日热榜 Agent")
    p.add_argument("--num", type=int, default=10, help="number of stories (default 10)")
    p.add_argument("--model", default="deepseek-v4-pro", help="DeepSeek model id (deepseek-v4-pro | deepseek-v4-flash)")
    p.add_argument("--lang", choices=["zh", "en"], default="zh", help="summary language")
    p.add_argument(
        "--keywords",
        default=DEFAULT_KEYWORDS,
        help="comma-separated keyword hints (strict whole-word matching in --filter-mode strict; "
        "append * for prefixes; default: AI/LLM + crypto + business/startup topics; "
        'pass --keywords "" to disable and digest the raw top stories)',
    )
    p.add_argument(
        "--topic-scope",
        default=DEFAULT_TOPIC_SCOPE,
        help="semantic topic scope used by --filter-mode semantic",
    )
    p.add_argument(
        "--filter-mode",
        choices=["semantic", "strict"],
        default="semantic",
        help="topic filter mode: semantic LLM classifier (default) or strict title keywords",
    )
    p.add_argument(
        "--filter-model",
        default="deepseek-v4-flash",
        help="cheap model used for semantic pre-filtering",
    )
    p.add_argument(
        "--filter-batch-size",
        type=int,
        default=40,
        help="stories per semantic filter request",
    )
    p.add_argument(
        "--knowledge",
        default="./knowledge_profile.md",
        help="reader knowledge profile Markdown file; env HN_DIGEST_KNOWLEDGE takes precedence",
    )
    p.add_argument(
        "--no-knowledge",
        action="store_true",
        help="disable reader knowledge profile injection",
    )
    p.add_argument(
        "--knowledge-char-limit",
        type=int,
        default=12000,
        help="maximum characters from the reader knowledge profile",
    )
    p.add_argument("--pool", type=int, default=200, help="candidate pool when filtering by keyword")
    p.add_argument("--concurrency", type=int, default=6, help="parallel summarization slots")
    p.add_argument("--max-comments", type=int, default=8, help="top comments fed to the model")
    p.add_argument("--out", default="./digests", help="output directory")
    p.add_argument(
        "--proxy",
        default=os.environ.get("HN_DIGEST_PROXY") or os.environ.get("HTTPS_PROXY"),
        help="proxy URL for the API and article fetches (e.g. http://127.0.0.1:7897)",
    )
    p.add_argument("--no-articles", action="store_true", help="skip article fetch (title + comments only)")
    p.add_argument("--no-html", action="store_true", help="Markdown only, skip the HTML page")
    p.add_argument("--no-cache", action="store_true", help="force fresh summaries")
    p.add_argument("--no-thinking", action="store_true", help="disable DeepSeek-V4 thinking mode")
    p.add_argument(
        "--reasoning-effort", choices=["high", "max"], default="high",
        help="thinking-mode reasoning effort (default high)",
    )
    p.add_argument(
        "--judge", action="store_true",
        help="judgment mode: hide the discussion, force a prediction + confidence, then "
        "reveal + steelman, and log to the prediction ledger (interactive)",
    )
    p.add_argument(
        "--horizon", type=int, default=30,
        help="judgment mode: days until a new prediction is due for scoring (default 30)",
    )
    p.add_argument(
        "--grade-only", action="store_true",
        help="judgment mode: only score due predictions from the ledger, then exit",
    )
    a = p.parse_args()
    return Config(
        num_stories=a.num,
        model=a.model,
        lang=a.lang,
        keywords=[k for k in a.keywords.split(",") if k.strip()],
        topic_scope=a.topic_scope,
        filter_mode=a.filter_mode,
        filter_model=a.filter_model,
        filter_batch_size=a.filter_batch_size,
        knowledge_path=None if a.no_knowledge else Path(a.knowledge),
        knowledge_char_limit=a.knowledge_char_limit,
        pool=a.pool,
        max_concurrency=a.concurrency,
        max_comments=a.max_comments,
        output_dir=Path(a.out),
        proxy=a.proxy,
        fetch_articles=not a.no_articles,
        html=not a.no_html,
        cache=not a.no_cache,
        thinking=not a.no_thinking,
        reasoning_effort=a.reasoning_effort,
        judge=a.judge or a.grade_only,
        judge_horizon_days=a.horizon,
        grade_only=a.grade_only,
    )


if __name__ == "__main__":
    try:
        cfg = parse_args()
        asyncio.run(run_judge(cfg) if cfg.judge else run(cfg))
    except KeyboardInterrupt:
        sys.exit(130)
