from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import data_dir


EVENT_LOG_LOCK = threading.RLock()


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
    with EVENT_LOG_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")


def append_event_safely(
    event: dict[str, Any],
    *,
    filename: str = "events.jsonl",
    on_error: Callable[[Exception], None] | None = None,
) -> bool:
    """Write an event without allowing log I/O to interrupt runtime work."""

    try:
        append_event(event, filename=filename)
    except Exception as exc:
        if on_error is not None:
            try:
                on_error(exc)
            except Exception:
                pass
        return False
    return True
