#!/usr/bin/env python3
"""build_index.py — assemble the public site for the daily HN digest.

Scans a digests directory for `hn-digest-YYYY-MM-DD.html` files and produces a
deploy-ready directory (default ./public) containing:

  * every digest's HTML — with a "← 返回全部日报" button injected at the top
    and bottom (so old issues get the button too, no re-generation needed);
  * the matching Markdown;
  * an `index.html` landing page that lists every issue newest-first and, under
    each day, previews the top 10 stories (title + one-line summary). The latest
    day is expanded; older days are collapsible to keep the archive tidy.

The output dir deliberately leaves behind anything private (ledger.json,
.cache, run.log). Pure stdlib, no third-party deps.

    python build_index.py                      # digests/ -> public/
    python build_index.py --digests d --out o  # custom paths
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
from datetime import datetime
from pathlib import Path

DIGEST_RE = re.compile(r"^hn-digest-(\d{4}-\d{2}-\d{2})\.html$")
COUNT_RE = re.compile(r'class="sub">[^<]*?(\d+)\s*(?:条|stories)', re.IGNORECASE)
CARD_RE = re.compile(r'<article class="card">(.*?)</article>', re.S)
TITLE_RE = re.compile(r'<h2><a href="([^"]*)"[^>]*>(.*?)</a></h2>', re.S)
SUMMARY_RE = re.compile(r'<p class="summary">(.*?)</p>', re.S)
TAG_STRIP = re.compile(r"<[^>]+>")
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

PREVIEW_LIMIT = 10  # stories previewed per day on the index

# "← 返回全部日报" button injected into each digest page. Self-contained styles
# so it also works on older digests that predate this feature.
HOME_TOP = (
    "<style>"
    ".digest-home-bar{max-width:720px;margin:18px auto 2px;padding:0 22px}"
    ".digest-home{display:inline-flex;align-items:center;gap:7px;"
    'font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:12.5px;'
    "color:#6F6F68;background:#fff;border:1px solid #E7E6DF;border-radius:999px;"
    "padding:7px 15px;text-decoration:none;transition:color .12s,border-color .12s}"
    ".digest-home:hover{color:#FF6600;border-color:#FF6600}"
    "</style>"
    '<div class="digest-home-bar"><a class="digest-home" href="index.html">← 返回全部日报</a></div>'
)
HOME_BOTTOM = (
    '<div class="digest-home-bar" style="margin:8px auto 40px">'
    '<a class="digest-home" href="index.html">← 返回全部日报</a></div>'
)

CSS = """
:root{--bg:#FCFCFA;--ink:#1B1B1B;--muted:#6F6F68;--hair:#E7E6DF;
  --hn:#FF6600;--hn-soft:#FFF1E8;--hover:#F4F3EE;--card:#FFFFFF;
  --body:"Inter","Helvetica Neue",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Hiragino Sans","Noto Sans CJK SC","PingFang SC","Microsoft YaHei",sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono","Roboto Mono",Menlo,Consolas,monospace;}
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
main{padding:14px 0 64px}

details.day{border-bottom:1px solid var(--hair)}
details.day:last-child{border-bottom:none}
details.day>summary{list-style:none;cursor:pointer;display:flex;align-items:baseline;gap:12px;
  padding:16px 6px;border-radius:8px;transition:background .12s;-webkit-tap-highlight-color:transparent}
details.day>summary::-webkit-details-marker{display:none}
details.day>summary::before{content:"▸";color:var(--muted);font-size:11px;line-height:1.6;
  transition:transform .15s ease;display:inline-block}
details.day[open]>summary::before{transform:rotate(90deg)}
details.day>summary:hover{background:var(--hover)}
summary .d{font-family:var(--mono);font-weight:650;font-size:16.5px;color:var(--ink);
  font-variant-numeric:tabular-nums;letter-spacing:-.01em}
summary .wd{font-family:var(--mono);color:var(--muted);font-size:12.5px}
summary .cnt{font-family:var(--mono);color:var(--muted);font-size:12.5px;margin-left:auto;
  font-variant-numeric:tabular-nums}
summary .badge{font-family:var(--mono);font-size:10px;font-weight:700;letter-spacing:.07em;
  text-transform:uppercase;color:#fff;background:var(--hn);border-radius:4px;padding:2px 7px}
details.day[open]{background:linear-gradient(var(--hn-soft),transparent 64px);border-radius:8px}

.day-body{padding:2px 6px 20px 26px}
.day-links{margin:0 0 14px}
.day-links .full{font-family:var(--mono);font-size:12.5px;color:var(--hn);text-decoration:none;
  border-bottom:1px solid transparent}
.day-links .full:hover{border-bottom-color:var(--hn)}
.day-links .md{font-family:var(--mono);font-size:11.5px;color:var(--muted);text-decoration:none;
  margin-left:14px;border-bottom:1px dotted var(--muted)}
.day-links .md:hover{color:var(--hn);border-bottom-color:var(--hn)}

ol.stories{list-style:none;margin:0;padding:0}
ol.stories li{display:flex;gap:13px;padding:12px 0;border-top:1px solid var(--hair)}
ol.stories li:first-child{border-top:none}
ol.stories .r{font-family:var(--mono);font-size:12.5px;font-weight:700;color:var(--hn);
  min-width:22px;padding-top:3px;font-variant-numeric:tabular-nums}
ol.stories .st{flex:1;min-width:0}
ol.stories .t{font-size:15.5px;font-weight:600;line-height:1.42;color:var(--ink);
  text-decoration:none;border-bottom:1.5px solid transparent}
ol.stories .t:hover{color:var(--hn);border-bottom-color:var(--hn)}
ol.stories .s{margin:5px 0 0;color:var(--muted);font-size:13.5px;line-height:1.62}

.empty{color:var(--muted);font-family:var(--mono);padding:30px 0}
footer{max-width:720px;margin:0 auto;padding:0 22px 48px;color:var(--muted);
  font-family:var(--mono);font-size:12px}
"""


def discover(digests: Path) -> list[tuple[str, Path]]:
    """Return (YYYY-MM-DD, html_path) for every dated digest, newest first."""
    found = []
    for p in digests.glob("hn-digest-*.html"):
        m = DIGEST_RE.match(p.name)
        if m:
            found.append((m.group(1), p))
    found.sort(key=lambda t: t[0], reverse=True)
    return found


def count_label(text: str) -> str:
    m = COUNT_RE.search(text)
    return f"{m.group(1)} 条" if m else ""


def extract_stories(text: str, limit: int = PREVIEW_LIMIT) -> list[dict]:
    """Pull (href, title, summary) from a digest's cards. Title/summary stay
    HTML-escaped (entities preserved) so they can be dropped straight in."""
    out = []
    for chunk in CARD_RE.findall(text):
        mt = TITLE_RE.search(chunk)
        if not mt:
            continue
        href = mt.group(1)
        title = TAG_STRIP.sub("", mt.group(2)).strip()
        ms = SUMMARY_RE.search(chunk)
        summary = TAG_STRIP.sub("", ms.group(1)).strip() if ms else ""
        out.append({"href": href, "title": title, "summary": summary})
        if len(out) >= limit:
            break
    return out


def inject_home(text: str) -> str:
    """Add the 'back to index' button to a digest page (idempotent)."""
    if "digest-home" in text:
        return text
    text = text.replace("<body>\n", "<body>\n" + HOME_TOP + "\n", 1)
    text = text.replace("</body>", HOME_BOTTOM + "\n</body>", 1)
    return text


def render_day(date_str: str, src_name: str, md_name: str | None,
               stories: list[dict], cnt: str, latest: bool) -> str:
    wd = WEEKDAYS[datetime.strptime(date_str, "%Y-%m-%d").weekday()]
    badge = '<span class="badge">最新</span>' if latest else ""
    open_attr = " open" if latest else ""
    items = "\n".join(
        f'<li><span class="r">{n:02d}</span><div class="st">'
        f'<a class="t" href="{html.escape(s["href"], quote=True)}" '
        f'target="_blank" rel="noopener">{s["title"]}</a>'
        + (f'<p class="s">{s["summary"]}</p>' if s["summary"] else "")
        + "</div></li>"
        for n, s in enumerate(stories, 1)
    ) or '<li><span class="st" style="color:var(--muted)">（这期没有可预览的条目）</span></li>'
    md_link = f'<a class="md" href="{html.escape(md_name)}">md</a>' if md_name else ""
    return (
        f'<details class="day"{open_attr}>'
        f'<summary><span class="d">{date_str}</span><span class="wd">{wd}</span>'
        f'<span class="cnt">{cnt}</span>{badge}</summary>'
        f'<div class="day-body">'
        f'<div class="day-links">'
        f'<a class="full" href="{html.escape(src_name)}">阅读完整日报 · 要点 / 评论 / 反驳 →</a>'
        f"{md_link}</div>"
        f'<ol class="stories">{items}</ol>'
        f"</div></details>"
    )


def build(digests: Path, out: Path) -> int:
    issues = discover(digests)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("", encoding="utf-8")

    blocks = []
    for i, (date_str, src) in enumerate(issues):
        text = src.read_text(encoding="utf-8")
        # Copy the digest with the back button injected.
        (out / src.name).write_text(inject_home(text), encoding="utf-8")
        md = src.with_suffix(".md")
        md_name = md.name if md.exists() else None
        if md_name:
            shutil.copy2(md, out / md_name)

        stories = extract_stories(text)
        cnt = count_label(text) or f"{len(stories)} 条"
        blocks.append(render_day(date_str, src.name, md_name, stories, cnt, latest=(i == 0)))

    body = (
        "\n".join(blocks)
        if blocks
        else '<p class="empty">还没有任何日报。先运行 hn_digest.py 生成一期。</p>'
    )
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    page = (
        "<!doctype html>\n"
        '<html lang="zh">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Hacker News 每日热榜 · 中文摘要存档</title>\n"
        '<meta name="description" content="每天从 Hacker News 抓取热榜并生成结构化中文摘要的日报存档。">\n'
        f"<style>{CSS}</style>\n</head>\n<body>\n"
        '<header><div class="mast">'
        '<h1><span class="y">Y</span> Hacker News 每日热榜</h1></div>'
        f'<p class="sub">中文摘要存档 · 共 {len(issues)} 期 · 点开每天看前 {PREVIEW_LIMIT} 条概要</p>'
        "</header>\n"
        f'<main><div class="wrap">\n{body}\n</div></main>\n'
        f"<footer>updated {html.escape(now)} · hn-digest agent</footer>\n"
        "</body>\n</html>\n"
    )
    (out / "index.html").write_text(page, encoding="utf-8")
    return len(issues)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the public HN-digest site + index page")
    ap.add_argument("--digests", default="./digests", help="source dir with hn-digest-*.html")
    ap.add_argument("--out", default="./public", help="output dir to deploy (default ./public)")
    args = ap.parse_args()

    n = build(Path(args.digests), Path(args.out))
    print(f"[build_index] {n} issue(s) -> {args.out}/index.html")


if __name__ == "__main__":
    main()
