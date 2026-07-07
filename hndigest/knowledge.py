"""Reader knowledge profile loading and cache identity.

Summary:
    Loads the user-maintained knowledge profile from an environment secret or
    local Markdown file, cleans template noise, trims it, and folds it into the
    summary cache key.

Adding functions:
    Add profile cleaning, validation, or cache-signature helpers here. Keep the
    actual explanation style and prompt wording in summarizer.py.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Optional

from .config import Config

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
