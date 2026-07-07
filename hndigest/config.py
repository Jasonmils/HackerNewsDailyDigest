from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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
    pdf_max_pages: int = 12           # first PDF pages to extract before summarization
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
