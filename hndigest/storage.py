from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

class Cache:
    def __init__(self, root: Path, enabled: bool):
        self.root = root / ".cache"
        self.enabled = enabled
        if enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def get(self, item_id: int | str) -> Optional[dict]:
        if not self.enabled:
            return None
        p = self.root / f"{item_id}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def set(self, item_id: int | str, summary: dict) -> None:
        if not self.enabled or summary is None:
            return
        try:
            (self.root / f"{item_id}.json").write_text(
                json.dumps(summary, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass


# Outcome label → numeric score used for Brier (1=came true, 0=wrong, .5=partial).
OUTCOME_VALUES = {"hit": 1.0, "partial": 0.5, "miss": 0.0}


class Ledger:
    """Local prediction台账: append-only-ish JSON list of forecasts + their grades."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
        return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add(self, entry: dict) -> None:
        self.entries.append(entry)
        self._save()

    def due(self, today: str) -> list[dict]:
        """Open predictions whose resolve_by date has arrived."""
        return [
            e for e in self.entries
            if e.get("status") == "open" and (e.get("resolve_by") or "9999") <= today
        ]

    def resolve(self, entry_id: str, outcome: str, note: str) -> None:
        for e in self.entries:
            if e.get("id") == entry_id:
                e["status"] = "resolved"
                e["outcome"] = outcome
                val = OUTCOME_VALUES.get(outcome, 0.0)
                conf = (e.get("confidence") or 0) / 100.0
                e["score"] = round((conf - val) ** 2, 4)
                e["note"] = note
                break
        self._save()

    def stats(self) -> Optional[dict]:
        graded = [e for e in self.entries if e.get("status") == "resolved" and e.get("score") is not None]
        if not graded:
            return None
        n = len(graded)
        mean_brier = sum(e["score"] for e in graded) / n
        hits = sum(1 for e in graded if e.get("outcome") == "hit")
        return {"n": n, "brier": mean_brier, "hits": hits, "open": sum(1 for e in self.entries if e.get("status") == "open")}

