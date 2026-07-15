from __future__ import annotations

from datetime import datetime, timezone

from ..logging_io import append_event, timestamp
from .tracking import TrackEvent


def log_track_event(event: TrackEvent, *, alerted: bool = False) -> dict[str, object]:
    track = event.track
    family = str(getattr(track, "family", "") or "")
    card_rarity = str(getattr(track, "rarity", "") or "")
    rarity = " ".join(value for value in (family, card_rarity) if value)
    rarity_detail = f" · {rarity}" if rarity else ""
    attribute_confidences = [
        float(confidence)
        for value, confidence in (
            (family, getattr(track, "family_confidence", 0.0)),
            (card_rarity, getattr(track, "rarity_confidence", 0.0)),
        )
        if value
    ]
    record: dict[str, object] = {
        "ts": timestamp(),
        "event_type": f"belt_{event.kind}",
        "source": "belt_tracker",
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
        "event": event.kind,
        "track_id": track.id,
        "droid": track.name,
        "rarity": rarity,
        "card_family": family,
        "card_rarity": card_rarity,
        "alerted": bool(alerted),
        "is_priority": bool(alerted),
        "confidence": round(track.confidence, 4),
        "rarity_confidence": round(min(attribute_confidences, default=0.0), 4),
        "family_confidence": round(float(getattr(track, "family_confidence", 0.0)), 4),
        "card_rarity_confidence": round(
            float(getattr(track, "rarity_confidence", 0.0)),
            4,
        ),
        "raw_text": track.raw_text,
        "box": [round(value, 1) for value in track.box],
        "detail": (
            f"{event.kind.title()} belt area{rarity_detail} · "
            f"{track.confidence:.0%} confidence"
        ),
    }
    append_event(record)
    return record
