from __future__ import annotations

from typing import Optional

from openai import AsyncOpenAI

from .utils import html_to_text, parse_json

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


async def repair_model_json(
    client: AsyncOpenAI,
    model: str,
    raw: str,
    usage: dict,
) -> Optional[dict]:
    """Ask the model to convert its malformed answer into strict JSON."""
    if not raw.strip():
        return None
    try:
        msg = await client.chat.completions.create(
            model=model,
            max_tokens=2500,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You repair malformed JSON from an earlier model response. "
                        "Return exactly one valid JSON object, with no Markdown, comments, "
                        "or explanatory text. Preserve the original fields and meanings."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Repair this into one valid JSON object. Escape quotes and newlines "
                        "inside strings correctly. Output JSON only:\n\n"
                        f"{raw[:12000]}"
                    ),
                },
            ],
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception:
        return None
    if msg.usage:
        usage["input"] += msg.usage.prompt_tokens
        usage["output"] += msg.usage.completion_tokens
    return parse_json(msg.choices[0].message.content or "")


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
        repaired = await repair_model_json(client, model, raw, usage)
        if repaired is None:
            return None, "could not parse model JSON"
        return repaired, None
    return parsed, None

