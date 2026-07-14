from __future__ import annotations

from datetime import datetime, timezone

from ..logging_io import append_event, timestamp
from .tracking import TrackEvent


def log_track_event(event: TrackEvent, *, alerted: bool = False) -> dict[str, object]:
    track = event.track
    record: dict[str, object] = {
        "ts": timestamp(),
        "event_type": f"belt_{event.kind}",
        "source": "belt_tracker",
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
        "event": event.kind,
        "track_id": track.id,
        "droid": track.name,
        "rarity": "",
        "alerted": bool(alerted),
        "is_priority": bool(alerted),
        "confidence": round(track.confidence, 4),
        "raw_text": track.raw_text,
        "box": [round(value, 1) for value in track.box],
        "detail": f"{event.kind.title()} belt area · {track.confidence:.0%} confidence",
    }
    append_event(record)
    return record
