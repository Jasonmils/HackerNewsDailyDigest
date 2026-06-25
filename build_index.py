#!/usr/bin/env python3
"""build_index.py — assemble the public site for the daily HN digest.

Scans a digests directory for `hn-digest-YYYY-MM-DD.html` files, copies the
HTML (and matching Markdown) into a clean output directory, and writes an
`index.html` landing page that lists every issue newest-first and features the
latest one. The output dir is what gets deployed to GitHub Pages — it
deliberately leaves behind anything private (ledger.json, .cache, run.log).

Pure stdlib, no third-party deps.

    python build_index.py                      # digests/ -> public/
    python build_index.py --digests d --out o  # custom paths
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
from datetime import date, datetime
from pathlib import Path

DIGEST_RE = re.compile(r"^hn-digest-(\d{4}-\d{2}-\d{2})\.html$")
COUNT_RE = re.compile(r'class="sub">[^<]*?(\d+)\s*(?:条|stories)', re.IGNORECASE)
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# Same palette / type as the per-day digest (see CSS_BLOCK in hn_digest.py).
CSS = """
:root{--bg:#FCFCFA;--ink:#1B1B1B;--muted:#6F6F68;--hair:#E7E6DF;
  --hn:#FF6600;--hn-soft:#FFF1E8;--card:#FFFFFF;
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
main{padding:18px 0 64px}
.feature{display:block;text-decoration:none;color:inherit;background:var(--hn-soft);
  border:1px solid var(--hair);border-radius:10px;padding:20px 22px;margin:8px 0 30px;
  transition:border-color .12s ease,transform .12s ease}
.feature:hover{border-color:var(--hn);transform:translateY(-1px)}
.feature .badge{display:inline-block;font-family:var(--mono);font-size:11px;font-weight:700;
  letter-spacing:.08em;text-transform:uppercase;color:#fff;background:var(--hn);
  border-radius:4px;padding:2px 7px;vertical-align:middle}
.feature .fdate{font-family:var(--mono);font-size:26px;font-weight:700;letter-spacing:-.01em;
  margin-left:10px;font-variant-numeric:tabular-nums}
.feature .fmeta{display:block;font-family:var(--mono);color:var(--muted);font-size:13px;margin-top:6px}
.arch-title{font-family:var(--mono);font-size:12.5px;color:var(--muted);text-transform:uppercase;
  letter-spacing:.08em;margin:0 0 4px;border-bottom:1px solid var(--hair);padding-bottom:8px}
ul.arch{list-style:none;margin:0;padding:0}
ul.arch li{display:flex;align-items:baseline;gap:12px;border-bottom:1px solid var(--hair);
  padding:13px 0}
ul.arch li:last-child{border-bottom:none}
ul.arch .d{font-family:var(--mono);font-weight:650;font-size:16px;text-decoration:none;
  color:var(--ink);font-variant-numeric:tabular-nums;border-bottom:1.5px solid transparent}
ul.arch .d:hover{color:var(--hn);border-bottom-color:var(--hn)}
ul.arch .wd{font-family:var(--mono);color:var(--muted);font-size:12.5px}
ul.arch .c{font-family:var(--mono);color:var(--muted);font-size:12.5px;margin-left:auto;
  font-variant-numeric:tabular-nums}
ul.arch .md{font-family:var(--mono);font-size:12px;color:var(--muted);text-decoration:none;
  border-bottom:1px dotted var(--muted)}
ul.arch .md:hover{color:var(--hn);border-bottom-color:var(--hn)}
.empty{color:var(--muted);font-family:var(--mono);padding:30px 0}
footer{max-width:720px;margin:0 auto;padding:0 22px 48px;color:var(--muted);
  font-family:var(--mono);font-size:12px}
"""


def discover(digests: Path) -> list[tuple[date, Path]]:
    """Return (date, html_path) for every dated digest, newest first."""
    found = []
    for p in digests.glob("hn-digest-*.html"):
        m = DIGEST_RE.match(p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        found.append((d, p))
    found.sort(key=lambda t: t[0], reverse=True)
    return found


def story_count(html_path: Path) -> str:
    """Best-effort 'N 条' label pulled from the digest's sub-header."""
    try:
        m = COUNT_RE.search(html_path.read_text(encoding="utf-8"))
    except OSError:
        m = None
    return f"{m.group(1)} 条" if m else ""


def build(digests: Path, out: Path) -> int:
    issues = discover(digests)

    out.mkdir(parents=True, exist_ok=True)
    # upload-pages-artifact runs no Jekyll, but this keeps any host happy.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    # Copy each issue's HTML (+ Markdown if present) into the public dir.
    for d, src in issues:
        shutil.copy2(src, out / src.name)
        md = src.with_suffix(".md")
        if md.exists():
            shutil.copy2(md, out / md.name)

    rows = []
    for d, src in issues:
        ds = d.isoformat()
        wd = WEEKDAYS[d.weekday()]
        cnt = story_count(src)
        md_name = src.with_suffix(".md").name
        md_link = (
            f'<a class="md" href="{html.escape(md_name)}">md</a>'
            if (out / md_name).exists() else ""
        )
        rows.append(
            f'<li><a class="d" href="{html.escape(src.name)}">{ds}</a>'
            f'<span class="wd">{wd}</span>'
            f'<span class="c">{cnt}{" · " if cnt and md_link else (" " if md_link else "")}{md_link}</span>'
            "</li>"
        )

    if issues:
        d0, src0 = issues[0]
        cnt0 = story_count(src0)
        wd0 = WEEKDAYS[d0.weekday()]
        meta0 = " · ".join(x for x in (cnt0, wd0) if x)
        feature = (
            f'<a class="feature" href="{html.escape(src0.name)}">'
            f'<span class="badge">最新</span>'
            f'<span class="fdate">{d0.isoformat()}</span>'
            f'<span class="fmeta">{meta0}</span></a>'
        )
        archive = (
            '<p class="arch-title">往期存档</p>\n<ul class="arch">\n'
            + "\n".join(rows[1:])
            + "\n</ul>"
        ) if len(rows) > 1 else ""
        body = feature + "\n" + archive
    else:
        body = '<p class="empty">还没有任何日报。先运行 hn_digest.py 生成一期。</p>'

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
        f'<p class="sub">中文摘要存档 · 共 {len(issues)} 期</p>'
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
