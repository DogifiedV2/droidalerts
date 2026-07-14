from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..logging_io import append_event, logs_dir
from .tracking import TrackEvent


def event_log_path() -> Path:
    return logs_dir() / "belt_events.jsonl"


def log_track_event(event: TrackEvent) -> dict[str, object]:
    track = event.track
    record: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
        "event": event.kind,
        "track_id": track.id,
        "droid": track.name,
        "confidence": round(track.confidence, 4),
        "raw_text": track.raw_text,
        "box": [round(value, 1) for value in track.box],
    }
    append_event(record, filename=event_log_path().name)
    return record
