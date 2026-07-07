"""Markdown and HTML rendering for generated digests.

Summary:
    Converts StoryResult objects and model JSON into the archived Markdown file
    and the self-contained HTML reading page, including typography and visual
    styling.

Adding functions:
    Add presentation-only helpers here when they change how existing results are
    displayed. Do not fetch data or call models from this module; pass any new
    data through StoryResult or summary fields first.
"""

from __future__ import annotations

import html
from datetime import datetime

from .config import Config, LABELS, StoryResult
from .utils import md_bold_to_html

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
  --body:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Source Han Sans SC","Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono","Roboto Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--body);
  line-height:1.72;font-size:16.5px;-webkit-font-smoothing:antialiased}
.wrap{max-width:720px;margin:0 auto;padding:0 22px}
header{border-bottom:2px solid var(--ink);margin:34px auto 6px;max-width:720px;padding:0 22px 14px}
header .mast{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
header h1{font-family:var(--body);font-size:clamp(22px,5vw,30px);font-weight:750;
  letter-spacing:0;margin:0}
header h1 .y{color:var(--hn)}
header .sub{font-family:var(--body);color:var(--muted);font-size:12.5px;margin:8px 0 0;
  letter-spacing:0}
main{padding-bottom:64px}
.card{border-bottom:1px solid var(--hair);padding:26px 0 24px}
.card:last-child{border-bottom:none}
.head{display:flex;gap:14px;align-items:flex-start}
.rank{font-family:var(--mono);font-size:14px;font-weight:700;color:var(--hn);
  line-height:1.5;min-width:30px;padding-top:2px;font-variant-numeric:tabular-nums}
.card h2{font-size:20px;font-weight:650;line-height:1.42;margin:0;letter-spacing:0}
.card h2 a{color:var(--ink);text-decoration:none;border-bottom:1.5px solid transparent;
  transition:border-color .12s ease,color .12s ease}
.card h2 a:hover{color:var(--hn);border-bottom-color:var(--hn)}
.title-tr{color:var(--muted);font-size:14px;font-style:italic;margin:5px 0 0 44px}
.meta{font-family:var(--body);color:var(--muted);font-size:12.5px;margin:7px 0 0 44px;
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
.topc .topc-meta{font-family:var(--body);color:var(--muted);font-size:11.5px;margin-bottom:5px;
  letter-spacing:0}
.topc p{margin:0 0 8px}
.topc p:last-child{margin-bottom:0}
.topc strong{color:var(--hn);font-weight:650}
.judge{margin:14px 0 0;padding:12px 14px;background:#F4F1FF;border:1px solid #E3DCFB;
  border-left:3px solid #6C5CE7;border-radius:8px;font-size:14.5px}
.judge-q{font-weight:600}
.judge-pred{margin:7px 0 0}
.judge-meta{font-family:var(--body);color:var(--muted);font-size:11.5px}
.judge-reb{margin:8px 0 0}
.judge-reb strong{color:#6C5CE7;font-weight:650}
.tags{margin:14px 0 0;display:flex;flex-wrap:wrap;gap:7px}
.tag{font-family:var(--body);font-size:11.5px;color:var(--muted);
  border:1px solid var(--hair);border-radius:999px;padding:3px 10px}
.err{margin:10px 0 0 44px;color:#A33;font-size:14px;font-family:var(--body)}
footer{max-width:720px;margin:0 auto;padding:0 22px 48px;color:var(--muted);
  font-family:var(--body);font-size:11.5px;letter-spacing:0}
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
