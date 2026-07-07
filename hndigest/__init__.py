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
