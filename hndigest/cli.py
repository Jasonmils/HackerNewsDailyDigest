from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .config import Config, DEFAULT_KEYWORDS, DEFAULT_TOPIC_SCOPE

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


def main() -> None:
    try:
        cfg = parse_args()
        if cfg.judge:
            from .judge import run_judge

            asyncio.run(run_judge(cfg))
            return

        from .runner import run

        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        sys.exit(130)
