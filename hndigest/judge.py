"""Interactive judgment and prediction calibration mode.

Summary:
    Runs the optional predict-before-reveal workflow, records forecasts in the
    ledger, and scores due predictions with Brier-style calibration feedback.

Adding functions:
    Add functions here when they affect the interactive judge loop, prediction
    prompts, reveal behavior, or ledger scoring UI. Keep non-interactive daily
    digest orchestration in runner.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .config import Config, LABELS, MD_BOLD_RE, StoryResult
from .runner import _close_ctx, _collect_stories, _open_ctx, _write_digest, _log
from .storage import Ledger, OUTCOME_VALUES

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
