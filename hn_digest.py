#!/usr/bin/env python3
"""Compatibility entrypoint for the Hacker News daily digest agent.

The implementation lives in the ``hndigest`` package so individual pieces can be
changed without editing one large script. Existing usage remains unchanged:

    python hn_digest.py
"""

from hndigest.cli import main, parse_args
from hndigest.config import Config, StoryResult
from hndigest.utils import parse_json

__all__ = ["Config", "StoryResult", "parse_args", "parse_json", "run", "run_judge", "main"]


def __getattr__(name: str):
    if name == "run":
        from hndigest.runner import run

        return run
    if name == "run_judge":
        from hndigest.judge import run_judge

        return run_judge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    main()
