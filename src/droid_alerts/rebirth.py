from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .capture import PixelBox
from .config import assets_dir
from .normalize import scale_from_screen


REBIRTH_TEMPLATE_FILE = "rebirth_alert_jawa.png"
REBIRTH_MATCH_THRESHOLD = 0.72
REBIRTH_CONFIRM_FRAMES = 2
REBIRTH_RELEASE_FRAMES = 3

# The source texture is 480 px wide but the widget is rendered at roughly
# 320 px on the app's 2560x1440 reference HUD.
REFERENCE_TEMPLATE_SCALE = 2.0 / 3.0
HUD_SCALE_FACTORS = tuple(value / 100.0 for value in range(70, 131, 5))
COARSE_SEARCH_SCALE = 0.4
VERIFY_PADDING_PX = 12


@dataclass(frozen=True)
class RebirthMatch:
    matched: bool
    score: float
    box: tuple[int, int, int, int] | None = None
    template_scale: float = 0.0


def rebirth_template_path() -> Path:
    return assets_dir() / REBIRTH_TEMPLATE_FILE


def rebirth_region(chat_box: PixelBox, screen_width: int, screen_height: int) -> PixelBox:
    """Mirror the chat-alert band to the right and extend it over the Jawa.

    The Rebirth widget starts at approximately the same vertical position as
    the game's spawn messages, but its character artwork is taller than one
    chat band. Keeping the calibrated chat Y coordinate makes the region work
    with the same wide, ultrawide, compact, and manually moved layouts.
    """

    width = min(screen_width, max(chat_box.width, int(round(screen_width * 0.28))))
    top = max(0, chat_box.top - int(round(chat_box.height * 0.12)))
    bottom = min(screen_height, chat_box.top + int(round(chat_box.height * 2.0)))
    return PixelBox(
        left=max(0, screen_width - width),
        top=top,
        width=max(1, width),
        height=max(1, bottom - top),
    )


class RebirthAlertDetector:
    """Find the invariant Jawa portion of the Rebirth-available widget."""

    def __init__(
        self,
        template_path: Path | None = None,
        *,
        threshold: float = REBIRTH_MATCH_THRESHOLD,
    ) -> None:
        self.threshold = float(threshold)
        path = template_path or rebirth_template_path()
        try:
            encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        except OSError as exc:
            raise RuntimeError(f"Rebirth Alert template is unavailable: {path}") from exc
        rgba = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
        if rgba is None or rgba.ndim != 3 or rgba.shape[2] != 4:
            raise RuntimeError(f"Rebirth Alert template is invalid: {path}")

        alpha = rgba[:, :, 3]
        points = np.argwhere(alpha > 16)
        if points.size == 0:
            raise RuntimeError(f"Rebirth Alert template has no visible pixels: {path}")
        y0, x0 = points.min(axis=0)
        y1, x1 = points.max(axis=0) + 1
        visible = rgba[y0:y1, x0:x1]

        # The held droid is another widget layered over the left arm. Match the
        # Jawa's head and torso on the right so every droid variant uses the
        # same template evidence.
        invariant_x = int(round(visible.shape[1] * 0.38))
        visible = visible[:, invariant_x:]
        self._template_bgr = visible[:, :, :3]
        self._template_alpha = visible[:, :, 3]

    def detect(
        self,
        image_bgr: np.ndarray,
        *,
        screen_width: int,
        screen_height: int,
    ) -> RebirthMatch:
        if image_bgr.ndim != 3 or image_bgr.shape[2] < 3 or image_bgr.size == 0:
            return RebirthMatch(False, 0.0)

        hud_scale = scale_from_screen(screen_height, screen_width)
        base_scale = max(0.05, hud_scale * REFERENCE_TEMPLATE_SCALE)
        image_height, image_width = image_bgr.shape[:2]
        search_width = max(1, int(round(image_width * COARSE_SEARCH_SCALE)))
        search_height = max(1, int(round(image_height * COARSE_SEARCH_SCALE)))
        search_image = cv2.resize(
            image_bgr[:, :, :3],
            (search_width, search_height),
            interpolation=cv2.INTER_AREA,
        )
        search_gray = cv2.cvtColor(search_image, cv2.COLOR_BGR2GRAY)
        coarse_best = float("-inf")
        coarse_location = (0, 0)
        coarse_scale = 0.0

        for factor in HUD_SCALE_FACTORS:
            template_scale = base_scale * factor
            width = max(8, int(round(self._template_bgr.shape[1] * template_scale)))
            height = max(8, int(round(self._template_bgr.shape[0] * template_scale)))
            if width > image_width or height > image_height:
                continue
            coarse_width = max(8, int(round(width * COARSE_SEARCH_SCALE)))
            coarse_height = max(8, int(round(height * COARSE_SEARCH_SCALE)))
            if coarse_width > search_width or coarse_height > search_height:
                continue
            coarse_template = cv2.resize(
                self._template_bgr,
                (coarse_width, coarse_height),
                interpolation=cv2.INTER_AREA,
            )
            coarse_alpha = cv2.resize(
                self._template_alpha,
                (coarse_width, coarse_height),
                interpolation=cv2.INTER_AREA,
            )
            coarse_mask = np.where(coarse_alpha >= 160, 255, 0).astype(np.uint8)
            if cv2.countNonZero(coarse_mask) < 24:
                continue
            scores = cv2.matchTemplate(
                search_gray,
                cv2.cvtColor(coarse_template, cv2.COLOR_BGR2GRAY),
                cv2.TM_CCOEFF_NORMED,
                mask=coarse_mask,
            )
            finite_scores = np.where(np.isfinite(scores), scores, -1.0)
            _minimum, maximum, _min_location, max_location = cv2.minMaxLoc(finite_scores)
            if maximum > coarse_best:
                coarse_best = float(maximum)
                coarse_location = max_location
                coarse_scale = template_scale

        if coarse_scale <= 0.0:
            return RebirthMatch(False, 0.0)

        width = max(8, int(round(self._template_bgr.shape[1] * coarse_scale)))
        height = max(8, int(round(self._template_bgr.shape[0] * coarse_scale)))
        interpolation = cv2.INTER_AREA if coarse_scale < 1.0 else cv2.INTER_CUBIC
        template = cv2.resize(
            self._template_bgr,
            (width, height),
            interpolation=interpolation,
        )
        alpha = cv2.resize(
            self._template_alpha,
            (width, height),
            interpolation=cv2.INTER_AREA,
        )
        mask = np.where(alpha >= 160, 255, 0).astype(np.uint8)
        estimated_x = int(round(coarse_location[0] / COARSE_SEARCH_SCALE))
        estimated_y = int(round(coarse_location[1] / COARSE_SEARCH_SCALE))
        search_x0 = max(0, estimated_x - VERIFY_PADDING_PX)
        search_y0 = max(0, estimated_y - VERIFY_PADDING_PX)
        search_x1 = min(image_width, estimated_x + width + VERIFY_PADDING_PX)
        search_y1 = min(image_height, estimated_y + height + VERIFY_PADDING_PX)
        verification_image = image_bgr[search_y0:search_y1, search_x0:search_x1, :3]
        if verification_image.shape[1] < width or verification_image.shape[0] < height:
            return RebirthMatch(False, 0.0)
        scores = cv2.matchTemplate(
            verification_image,
            template,
            cv2.TM_CCOEFF_NORMED,
            mask=mask,
        )
        finite_scores = np.where(np.isfinite(scores), scores, -1.0)
        _minimum, maximum, _min_location, max_location = cv2.minMaxLoc(finite_scores)
        best_score = max(0.0, float(maximum))
        x = search_x0 + max_location[0]
        y = search_y0 + max_location[1]
        best_box = (x, y, x + width, y + height)

        return RebirthMatch(
            matched=best_score >= self.threshold,
            score=best_score,
            box=best_box,
            template_scale=coarse_scale,
        )


class RebirthPresenceGate:
    """Confirm an appearance once and re-arm only after the widget leaves."""

    def __init__(
        self,
        *,
        confirm_frames: int = REBIRTH_CONFIRM_FRAMES,
        release_frames: int = REBIRTH_RELEASE_FRAMES,
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
