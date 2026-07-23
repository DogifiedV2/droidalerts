from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

DEFAULT_IDLE_SCAN_FPS = 4
DEFAULT_ACTIVE_SCAN_FPS = 8
MINIMUM_SCAN_FPS = 1
MAXIMUM_SCAN_FPS = 20
TEMPLATE_IDLE_BACKOFF_START = 12


def normalize_scan_fps(idle_scan_fps: int, active_scan_fps: int) -> tuple[int, int]:
    idle = min(MAXIMUM_SCAN_FPS, max(MINIMUM_SCAN_FPS, int(idle_scan_fps)))
    active = min(MAXIMUM_SCAN_FPS, max(MINIMUM_SCAN_FPS, int(active_scan_fps)))
    return min(idle, active), active


def adaptive_template_interval(
    empty_card_scans: int,
    idle_scan_fps: int = DEFAULT_IDLE_SCAN_FPS,
    active_scan_fps: int = DEFAULT_ACTIVE_SCAN_FPS,
) -> float:
    if max(0, int(empty_card_scans)) >= TEMPLATE_IDLE_BACKOFF_START:
        return 1.0 / idle_scan_fps
    return 1.0 / active_scan_fps


@dataclass
class AdaptiveScanScheduler:
    idle_scan_fps: int = DEFAULT_IDLE_SCAN_FPS
    active_scan_fps: int = DEFAULT_ACTIVE_SCAN_FPS
    empty_candidate_scans: int = 0
    previous_completed_at: float | None = None

    def __post_init__(self) -> None:
        self.idle_scan_fps, self.active_scan_fps = normalize_scan_fps(
            self.idle_scan_fps, self.active_scan_fps
        )

    def record(self, *, card_window_count: int, captured_at: float, completed_at: float) -> dict[str, float | int | None]:
        self.empty_candidate_scans = 0 if card_window_count else self.empty_candidate_scans + 1
        scan_seconds = max(0.001, completed_at - captured_at)
        scan_interval_seconds = (
            completed_at - self.previous_completed_at
            if self.previous_completed_at is not None else None
        )
        sample_seconds = scan_interval_seconds if scan_interval_seconds is not None else max(
            scan_seconds, 1.0 / self.active_scan_fps
        )
        interval = adaptive_template_interval(
            self.empty_candidate_scans, self.idle_scan_fps, self.active_scan_fps
        )
        self.previous_completed_at = completed_at
        return {
            "scan_seconds": scan_seconds,
            "scan_interval_seconds": scan_interval_seconds,
            "scan_fps": 1.0 / max(0.001, sample_seconds),
            "scan_throughput_fps": 1.0 / scan_seconds,
            "next_scan_interval_seconds": interval,
            "next_scan_at": captured_at + interval,
            "empty_candidate_scans": self.empty_candidate_scans,
        }


def build_tracks_payload(tracks: Iterable[object]) -> list[dict[str, object]]:
    return [
        {
            "id": track.id,
            "name": track.name,
            "family": str(getattr(track, "family", "") or ""),
            "rarity": str(getattr(track, "rarity", "") or ""),
            "box": tuple(round(value) for value in track.box),
            "confidence": track.confidence,
        }
        for track in tracks
    ]
