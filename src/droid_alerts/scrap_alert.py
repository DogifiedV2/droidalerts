from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import assets_dir


@dataclass(frozen=True)
class CreditHudObservation:
    visible: bool
    fingerprint: str | None = None
    icon_score: float = 0.0
    icon_visible: bool = False


class CreditHudDetector:
    """Locate the credits icon and fingerprint the displayed amount beside it."""

    def __init__(
        self,
        template_path: Path | None = None,
        *,
        icon_threshold: float = 0.82,
    ) -> None:
        path = template_path or assets_dir() / "credit_icon.png"
        template = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if template is None or template.ndim != 3 or template.shape[2] != 4:
            raise RuntimeError(f"Credit icon template is missing or invalid: {path}")
        self.template = template
        self.icon_threshold = float(icon_threshold)

    def detect(
        self,
        bottom_bgr: np.ndarray,
        *,
        screen_height: int,
        screen_width: int,
    ) -> CreditHudObservation:
        if bottom_bgr is None or bottom_bgr.size == 0:
            return CreditHudObservation(False)

        height, width = bottom_bgr.shape[:2]
        search_width = min(width, max(360, int(round(screen_width * 0.22))))
        search = bottom_bgr[:, :search_width]
        nominal_size = max(36, int(round(screen_height * 0.052)))
        candidate_sizes = sorted(
            {
                max(28, int(round(nominal_size * scale)))
                for scale in np.linspace(0.78, 1.28, 11)
            }
        )

        best_score = -1.0
        best_box: tuple[int, int, int, int] | None = None
        for size in candidate_sizes:
            rendered = cv2.resize(
                self.template,
                (size, size),
                interpolation=cv2.INTER_AREA if size < self.template.shape[0] else cv2.INTER_LINEAR,
            )
            alpha = rendered[:, :, 3]
            ys, xs = np.where(alpha > 40)
            if xs.size == 0 or ys.size == 0:
                continue
            x1, x2 = int(xs.min()), int(xs.max()) + 1
            y1, y2 = int(ys.min()), int(ys.max()) + 1
            icon = rendered[y1:y2, x1:x2, :3]
            mask = alpha[y1:y2, x1:x2]
            icon_h, icon_w = icon.shape[:2]
            if icon_h > height or icon_w > search_width:
                continue
            scores = cv2.matchTemplate(
                search,
                icon,
                cv2.TM_CCORR_NORMED,
                mask=mask,
            )
            _minimum, maximum, _min_location, location = cv2.minMaxLoc(scores)
            if maximum > best_score:
                best_score = float(maximum)
                best_box = (location[0], location[1], icon_w, icon_h)

        if best_box is None or best_score < self.icon_threshold:
            return CreditHudObservation(False, icon_score=max(0.0, best_score))

        icon_x, icon_y, icon_w, icon_h = best_box
        amount_x1 = icon_x + icon_w + max(3, int(round(icon_h * 0.07)))
        amount_x2 = min(width, amount_x1 + int(round(icon_h * 5.0)))
        amount_y1 = max(0, icon_y - int(round(icon_h * 0.04)))
        amount_y2 = min(height, icon_y + int(round(icon_h * 1.04)))
        amount = bottom_bgr[amount_y1:amount_y2, amount_x1:amount_x2]
        fingerprint = _amount_fingerprint(amount)
        return CreditHudObservation(
            visible=fingerprint is not None,
            fingerprint=fingerprint,
            icon_score=best_score,
            icon_visible=True,
        )


def _amount_fingerprint(amount_bgr: np.ndarray) -> str | None:
    """Hash only the bright, low-saturation text fill, excluding the scene."""
    if amount_bgr is None or amount_bgr.size == 0:
        return None
    hsv = cv2.cvtColor(amount_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 185), (179, 85, 255))

    component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    cleaned = np.zeros_like(mask)
    minimum_area = max(3, int(round(amount_bgr.shape[0] * amount_bgr.shape[0] * 0.001)))
    for label in range(1, component_count):
        if stats[label, cv2.CC_STAT_AREA] >= minimum_area:
            cleaned[labels == label] = 255

    ys, xs = np.where(cleaned > 0)
    if xs.size < 20 or ys.size < 20:
        return None
    x1, x2 = int(xs.min()), int(xs.max()) + 1
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    glyphs = cleaned[y1:y2, x1:x2]
    if glyphs.shape[0] < max(10, int(round(amount_bgr.shape[0] * 0.28))):
        return None

    target_height = 48
    scale = target_height / glyphs.shape[0]
    target_width = min(384, max(1, int(round(glyphs.shape[1] * scale))))
    normalized = cv2.resize(
        glyphs,
        (target_width, target_height),
        interpolation=cv2.INTER_NEAREST,
    )
    canvas = np.zeros((target_height, 384), dtype=np.uint8)
    canvas[:, :target_width] = normalized
    return hashlib.sha1(canvas.tobytes()).hexdigest()


class ScrapIncomeTracker:
    """Alert once when the visible credit text remains unchanged long enough."""

    def __init__(self, stall_seconds: float = 30.0) -> None:
        self.stall_seconds = max(1.0, float(stall_seconds))
        self.reset()

    def reset(self) -> None:
        self._fingerprint: str | None = None
        self._unchanged_since: float | None = None
        self._alerted = False

    @property
    def fingerprint(self) -> str | None:
        return self._fingerprint

    def unchanged_seconds(self, now: float) -> float:
        if self._unchanged_since is None:
            return 0.0
        return max(0.0, float(now) - self._unchanged_since)

    def observe(self, observation: CreditHudObservation, *, now: float) -> bool:
        fingerprint = observation.fingerprint if observation.visible else None
        if not fingerprint:
            self.reset()
            return False
        if fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            self._unchanged_since = float(now)
            self._alerted = False
            return False
        if self._unchanged_since is None:
            self._unchanged_since = float(now)
            return False
        if not self._alerted and now - self._unchanged_since >= self.stall_seconds:
            self._alerted = True
            return True
        return False


class ScrapVisibilityTracker:
    """Alert once when the credits icon remains absent for five minutes."""

    def __init__(self, inactive_seconds: float = 300.0) -> None:
        self.inactive_seconds = max(1.0, float(inactive_seconds))
        self.reset()

    def reset(self) -> None:
        self._missing_since: float | None = None
        self._alerted = False

    def observe(self, *, icon_visible: bool, now: float) -> bool:
        if icon_visible:
            self.reset()
            return False
        if self._missing_since is None:
            self._missing_since = float(now)
            return False
        if not self._alerted and now - self._missing_since >= self.inactive_seconds:
            self._alerted = True
            return True
        return False

    def missing_seconds(self, now: float) -> float:
        if self._missing_since is None:
            return 0.0
        return max(0.0, float(now) - self._missing_since)


def scrap_debug_detail(
    observation: CreditHudObservation,
    *,
    changed: bool,
    unchanged_seconds: float,
) -> str:
    if not observation.visible:
        return "Credits display not found; stall timer reset"
    if changed:
        return "Credits display changed; stall timer reset"
    return f"Credits display unchanged for {max(0.0, unchanged_seconds):.0f}s"
