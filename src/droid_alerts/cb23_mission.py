from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .capture import PixelBox
from .config import assets_dir
from .normalize import scale_from_screen


CB23_TEMPLATE_FILE = "cb23_mission.png"
# The supplied portrait is the exact HUD asset. A high floor cleanly separates
# it from yellow toast lettering and other round droids in the portrait set.
CB23_MATCH_THRESHOLD = 0.95
CB23_CONFIRM_FRAMES = 2
CB23_RELEASE_FRAMES = 2

# The 512px portrait is rendered about 150px tall on the 2560x1440 reference
# HUD. Fortnite's HUD scale can vary independently, so probe around that size.
REFERENCE_TEMPLATE_SCALE = 0.29
HUD_SCALE_FACTORS = tuple(value / 100.0 for value in range(70, 136, 5))
COARSE_SEARCH_SCALE = 0.3
VERIFY_PADDING_PX = 10
LEFT_SCAN_WIDTH_RATIO = 0.55


@dataclass(frozen=True)
class CB23MissionMatch:
    matched: bool
    score: float
    mission_score: float = 0.0
    box: tuple[int, int, int, int] | None = None
    template_scale: float = 0.0


def cb23_template_path() -> Path:
    return assets_dir() / CB23_TEMPLATE_FILE


def cb23_mission_region(screen_width: int, screen_height: int) -> PixelBox:
    """Scan the full height of the left side because mission toasts can move."""

    return PixelBox(
        left=0,
        top=0,
        width=max(1, min(screen_width, int(round(screen_width * LEFT_SCAN_WIDTH_RATIO)))),
        height=max(1, screen_height),
    )


class CB23MissionDetector:
    """Find CB23's portrait and require the surrounding yellow mission toast."""

    def __init__(
        self,
        template_path: Path | None = None,
        *,
        threshold: float = CB23_MATCH_THRESHOLD,
    ) -> None:
        self.threshold = float(threshold)
        path = template_path or cb23_template_path()
        try:
            encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        except OSError as exc:
            raise RuntimeError(f"CB23 Mission template is unavailable: {path}") from exc
        rgba = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
        if rgba is None or rgba.ndim != 3 or rgba.shape[2] != 4:
            raise RuntimeError(f"CB23 Mission template is invalid: {path}")

        alpha = rgba[:, :, 3]
        points = np.argwhere(alpha > 16)
        if points.size == 0:
            raise RuntimeError(f"CB23 Mission template has no visible pixels: {path}")
        y0, x0 = points.min(axis=0)
        y1, x1 = points.max(axis=0) + 1
        visible = rgba[y0:y1, x0:x1]
        self._template_bgr = visible[:, :, :3]
        self._template_alpha = visible[:, :, 3]

    def detect(
        self,
        image_bgr: np.ndarray,
        *,
        screen_width: int,
        screen_height: int,
    ) -> CB23MissionMatch:
        if image_bgr.ndim != 3 or image_bgr.shape[2] < 3 or image_bgr.size == 0:
            return CB23MissionMatch(False, 0.0)

        image = image_bgr[:, :, :3]
        image_height, image_width = image.shape[:2]
        hud_scale = scale_from_screen(screen_height, screen_width)
        base_scale = max(0.04, hud_scale * REFERENCE_TEMPLATE_SCALE)
        coarse = cv2.resize(
            image,
            (
                max(1, int(round(image_width * COARSE_SEARCH_SCALE))),
                max(1, int(round(image_height * COARSE_SEARCH_SCALE))),
            ),
            interpolation=cv2.INTER_AREA,
        )

        coarse_best = float("-inf")
        coarse_location = (0, 0)
        coarse_scale = 0.0
        for factor in HUD_SCALE_FACTORS:
            template_scale = base_scale * factor
            width = max(8, int(round(self._template_bgr.shape[1] * template_scale)))
            height = max(8, int(round(self._template_bgr.shape[0] * template_scale)))
            coarse_width = max(8, int(round(width * COARSE_SEARCH_SCALE)))
            coarse_height = max(8, int(round(height * COARSE_SEARCH_SCALE)))
            if coarse_width > coarse.shape[1] or coarse_height > coarse.shape[0]:
                continue
            template, mask = self._resized_template(coarse_width, coarse_height)
            scores = cv2.matchTemplate(
                coarse,
                template,
                cv2.TM_CCORR_NORMED,
                mask=mask,
            )
            finite_scores = np.where(np.isfinite(scores), scores, -1.0)
            _minimum, maximum, _min_location, max_location = cv2.minMaxLoc(finite_scores)
            if maximum > coarse_best:
                coarse_best = float(maximum)
                coarse_location = max_location
                coarse_scale = template_scale

        if coarse_scale <= 0.0:
            return CB23MissionMatch(False, 0.0)

        width = max(8, int(round(self._template_bgr.shape[1] * coarse_scale)))
        height = max(8, int(round(self._template_bgr.shape[0] * coarse_scale)))
        template, mask = self._resized_template(width, height)
        estimated_x = int(round(coarse_location[0] / COARSE_SEARCH_SCALE))
        estimated_y = int(round(coarse_location[1] / COARSE_SEARCH_SCALE))
        search_x0 = max(0, estimated_x - VERIFY_PADDING_PX)
        search_y0 = max(0, estimated_y - VERIFY_PADDING_PX)
        search_x1 = min(image_width, estimated_x + width + VERIFY_PADDING_PX)
        search_y1 = min(image_height, estimated_y + height + VERIFY_PADDING_PX)
        verification = image[search_y0:search_y1, search_x0:search_x1]
        if verification.shape[1] < width or verification.shape[0] < height:
            return CB23MissionMatch(False, max(0.0, coarse_best))

        scores = cv2.matchTemplate(
            verification,
            template,
            cv2.TM_CCORR_NORMED,
            mask=mask,
        )
        finite_scores = np.where(np.isfinite(scores), scores, -1.0)
        _minimum, maximum, _min_location, max_location = cv2.minMaxLoc(finite_scores)
        score = max(0.0, float(maximum))
        x = search_x0 + max_location[0]
        y = search_y0 + max_location[1]
        box = (x, y, x + width, y + height)
        mission_score = self._mission_toast_score(image, box)
        return CB23MissionMatch(
            matched=score >= self.threshold and mission_score >= 1.0,
            score=score,
            mission_score=mission_score,
            box=box,
            template_scale=coarse_scale,
        )

    def _resized_template(self, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
        scale_down = width < self._template_bgr.shape[1] or height < self._template_bgr.shape[0]
        interpolation = cv2.INTER_AREA if scale_down else cv2.INTER_CUBIC
        template = cv2.resize(self._template_bgr, (width, height), interpolation=interpolation)
        alpha = cv2.resize(
            self._template_alpha,
            (width, height),
            interpolation=cv2.INTER_AREA,
        )
        mask = np.where(alpha >= 96, 255, 0).astype(np.uint8)
        return template, mask

    @staticmethod
    def _mission_toast_score(
        image_bgr: np.ndarray,
        box: tuple[int, int, int, int],
    ) -> float:
        """Return >=1 only when a wide yellow mission toast surrounds the portrait."""

        x1, y1, x2, y2 = box
        height = max(1, y2 - y1)
        left = max(0, x1 - int(round(height * 0.45)))
        right = min(image_bgr.shape[1], x2 + int(round(height * 3.0)))
        top = max(0, y1 - int(round(height * 0.45)))
        bottom = min(image_bgr.shape[0], y2 + int(round(height * 0.45)))
        neighborhood = image_bgr[top:bottom, left:right]
        if neighborhood.size == 0:
            return 0.0

        hsv = cv2.cvtColor(neighborhood, cv2.COLOR_BGR2HSV)
        yellow = cv2.inRange(hsv, (18, 145, 145), (42, 255, 255))
        ys, xs = np.where(yellow > 0)
        if len(xs) < max(20, int(neighborhood.size / 3 * 0.018)):
            return 0.0
        horizontal_span = int(xs.max() - xs.min() + 1)
        required_span = max(20, int(round(height * 1.65)))
        pixel_fraction = len(xs) / float(yellow.size)
        span_score = horizontal_span / required_span
        fraction_score = pixel_fraction / 0.035
        return min(span_score, fraction_score)


class CB23MissionGate:
    """Confirm two scans and emit once until the fast mission toast disappears."""

    def __init__(
        self,
        *,
        confirm_frames: int = CB23_CONFIRM_FRAMES,
        release_frames: int = CB23_RELEASE_FRAMES,
    ) -> None:
        self.confirm_frames = max(1, int(confirm_frames))
        self.release_frames = max(1, int(release_frames))
        self.reset()

    def reset(self) -> None:
        self.present = False
        self._matches = 0
        self._misses = 0

    def update(self, matched: bool) -> bool:
        if matched:
            self._misses = 0
            self._matches += 1
            if not self.present and self._matches >= self.confirm_frames:
                self.present = True
                return True
            return False

        self._matches = 0
        self._misses += 1
        if self.present and self._misses >= self.release_frames:
            self.present = False
        return False
