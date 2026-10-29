"""Global daily LLM budget (SPEC §12), tracked from the per-call usage log.

Every LLM call's usage is appended to cache/usage/<UTC date>.jsonl (on the Modal Volume when deployed).
Today's spend is the sum of that file's cost_usd; calls with an unknown price count as $0.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import CACHE_DIR

USAGE_DIR = CACHE_DIR / "usage"
DEFAULT_DAILY_USD = 3.0
_lock = threading.Lock()


class BudgetReached(Exception):
    """str(e) is the user-facing message."""


def daily_limit_usd() -> float:
    try:
        return float(os.environ.get("DAILY_LLM_BUDGET_USD") or DEFAULT_DAILY_USD)
    except ValueError:
        return DEFAULT_DAILY_USD


def _today_file(usage_dir: Path | None = None) -> Path:
    return (usage_dir or USAGE_DIR) / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"


def record(usage: dict, usage_dir: Path | None = None) -> None:
    """Append one call's usage (Usage.as_dict()) to today's log."""
    path = _today_file(usage_dir)
    line = json.dumps({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), **usage})
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def spent_today(usage_dir: Path | None = None) -> float:
    path = _today_file(usage_dir)
    if not path.exists():
        return 0.0
    total = 0.0
    with _lock, open(path, encoding="utf-8") as f:
        for line in f:
            try:
                total += json.loads(line).get("cost_usd") or 0.0
            except ValueError:
                continue
    return total


def check(usage_dir: Path | None = None) -> None:
    """Raise BudgetReached when today's estimated spend has reached the daily limit."""
    limit = daily_limit_usd()
    if spent_today(usage_dir) >= limit:
        raise BudgetReached(
            f"Daily AI budget reached (${limit:.2f}, resets at 00:00 UTC). "
            "Charts and the 3D viewer still work; AI investigations are paused until then.")
