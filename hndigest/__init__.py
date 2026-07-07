"""Package public surface for the HN digest agent.

Summary:
    Exposes the stable package-level imports while lazy-loading heavier runtime
    modules so simple imports and CLI help do not require network/model deps.

Adding functions:
    Keep this file thin. Add new public re-exports only for APIs that callers
    should import from `hndigest`; implementation should live in a focused
    module such as runner, summarizer, filtering, or render.
"""

from .config import Config, StoryResult

__all__ = ["Config", "StoryResult", "run", "run_judge"]


def __getattr__(name: str):
    if name == "run":
        from .runner import run

        return run
    if name == "run_judge":
        from .judge import run_judge

        return run_judge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
