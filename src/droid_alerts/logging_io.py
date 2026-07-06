from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import data_dir


def logs_dir() -> Path:
    return data_dir() / "logs"


def debug_dir() -> Path:
    return data_dir() / "debug_screenshots"


def alert_samples_dir() -> Path:
    return data_dir() / "alert_samples"


def timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + f"_{int((time.time() % 1) * 1000):03d}"


def append_event(event: dict[str, Any], *, filename: str = "events.jsonl") -> None:
    path = logs_dir() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
