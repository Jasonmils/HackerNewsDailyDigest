from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from openai import AsyncOpenAI

from .articles import fetch_article_text
from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEFAULT_HEADERS,
    HN_ITEM_URL,
    PRICING,
    Config,
    StoryResult,
)
from .context import Ctx
from .filtering import semantic_filter_stories, strict_filter_stories
from .hn import HNClient, fetch_items, fetch_top_comments, pick_top_comment
from .knowledge import knowledge_cache_key, load_knowledge_profile
from .render import render_html, render_markdown
from .storage import Cache
from .summarizer import summarize_story
from .utils import _none

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
            "ERROR: DeepSeek API key not set. Fill in DEEPSEEK_API_KEY in "
            "hndigest/config.py, or set the DEEPSEEK_API_KEY environment variable."
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
