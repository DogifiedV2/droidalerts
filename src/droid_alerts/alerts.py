from __future__ import annotations

import hashlib
import time
from pathlib import Path

import cv2
import numpy as np

from .classifier import Detection
from .config import AppConfig, sounds_dir


def row_hash(row_bgr: np.ndarray) -> str:
    if row_bgr.size == 0:
        return "empty"
    small = cv2.resize(row_bgr, (96, 24), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return hashlib.sha1(gray.tobytes()).hexdigest()[:16]


class AlertPolicy:
    """Merged policy: the reference detector's per-combo score gates live inside
    Detection.should_alert; this layer adds target filtering, per-combo
    cooldown, and frame/row-hash dedupe so a persisting chat row doesn't
    re-alert every capture."""

    def __init__(self, config: AppConfig) -> None:
        self._last_combo_alert: dict[tuple[str, str], float] = {}
        self._recent_hashes: dict[str, float] = {}
        self.apply_config(config)

    def apply_config(self, config: AppConfig) -> None:
        """Adopt new settings without losing cooldown/dedupe state, so the
        watcher can hot-reload config changes mid-run."""
        self.targets = config.targets
        self.cooldown_seconds = config.alert_cooldown_seconds
        self.dedupe_seconds = config.dedupe_seconds
        self.sound_enabled = config.sound_enabled

    def should_alert(self, detection: Detection, row_digest: str) -> bool:
        if (detection.droid, detection.rarity) not in self.targets:
            return False
        if not detection.should_alert:  # reference per-combo threshold table
            return False

        now = time.monotonic()
        seen = self._recent_hashes.get(row_digest)
        self._recent_hashes[row_digest] = now
        if seen is not None and now - seen < self.dedupe_seconds:
            return False

        combo = (detection.droid, detection.rarity)
        last = self._last_combo_alert.get(combo)
        if last is not None and now - last < self.cooldown_seconds:
            return False
        self._last_combo_alert[combo] = now
        self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        cutoff = now - max(self.dedupe_seconds, self.cooldown_seconds) * 4
        self._recent_hashes = {k: v for k, v in self._recent_hashes.items() if v >= cutoff}

    def notify(self, detection: Detection) -> None:
        if not self.sound_enabled:
            return
        try:
            import winsound

            wav = _alert_wav()
            if wav is not None:
                winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winsound.Beep(1200, 220)
                winsound.Beep(1600, 220)
        except Exception:
            pass


def _alert_wav() -> Path | None:
    directory = sounds_dir()
    if not directory.exists():
        return None
    for path in sorted(directory.glob("*.wav")):
        return path
    return None
