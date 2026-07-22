from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .capture import PixelBox
from .config import assets_dir, data_dir
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


REFERENCE_SCREEN_HEIGHT = 1080
READY_SCORE_THRESHOLD = 0.72
LEVEL_SCORE_THRESHOLD = 0.67
DEFAULT_SCAN_INTERVAL_SECONDS = 5.0
DEFAULT_CONFIRMATION_SCANS = 2
DEFAULT_RELEASE_SCANS = 3


@dataclass(frozen=True)
class RebirthObservation:
    ready: bool
    ready_score: float
    level: int | None
    level_score: float


class RebirthHudDetector:
    """Recognize the persistent Rebirth HUD without scanning at chat-watch speed."""

    def __init__(self, ready_template_path: Path | None = None) -> None:
        path = ready_template_path or assets_dir() / "rebirth_ready_mask.png"
        template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if template is None or template.size == 0:
            raise RuntimeError(f"Rebirth READY template is unavailable: {path}")
        self.ready_template = cv2.threshold(template, 127, 255, cv2.THRESH_BINARY)[1]
        self.digit_templates = _build_digit_templates()

    def detect(
        self,
        bottom_bgr: np.ndarray,
        *,
        screen_height: int,
        screen_width: int | None = None,
    ) -> RebirthObservation:
        if bottom_bgr.size == 0 or screen_height <= 0:
            return RebirthObservation(False, 0.0, None, 0.0)

        # A 1920x1200 display commonly runs the game in a centered 1920x1080
        # viewport. The capture still reports 1200 pixels, so scaling from the
        # physical height would unnecessarily shrink the HUD and weaken OCR.
        effective_height = _effective_screen_height(
            bottom_bgr,
            screen_height,
            screen_width=screen_width,
        )
        scale = REFERENCE_SCREEN_HEIGHT / float(effective_height)
        normalized = cv2.resize(
            bottom_bgr,
            (
                max(1, int(round(bottom_bgr.shape[1] * scale))),
                max(1, int(round(bottom_bgr.shape[0] * scale))),
            ),
            interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR,
        )
        mask = _green_hud_mask(normalized)
        ready_score = self._ready_score(mask)
        ready = ready_score >= READY_SCORE_THRESHOLD
        if not ready:
            return RebirthObservation(False, ready_score, None, 0.0)

        level, level_score = self._read_level(mask)
        return RebirthObservation(True, ready_score, level, level_score)

    def _ready_score(self, mask: np.ndarray) -> float:
        best = 0.0
        # Join the nearby READY glyphs into word-sized candidates first. This
        # avoids running multi-scale template matching across an ultrawide
        # frame and keeps the five-second scan effectively invisible in-game.
        joined = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            np.ones((3, 13), dtype=np.uint8),
        )
        contours, _hierarchy = cv2.findContours(
            joined,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            ratio = width / max(1, height)
            if not (24 <= height <= 80 and 3.5 <= ratio <= 9.0):
                continue
            candidate = mask[y : y + height, x : x + width]
            ys, xs = np.where(candidate > 0)
            if not len(xs):
                continue
            candidate = candidate[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
            normalized = cv2.resize(
                candidate,
                (self.ready_template.shape[1], self.ready_template.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
            score = float(
                cv2.matchTemplate(
                    normalized,
                    self.ready_template,
                    cv2.TM_CCOEFF_NORMED,
                )[0, 0]
            )
            best = max(best, score)
        return best

    def _read_level(self, mask: np.ndarray) -> tuple[int | None, float]:
        component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
        candidates: list[_DigitCandidate] = []
        for x, y, width, height, area in stats[1:component_count]:
            x, y, width, height, area = map(int, (x, y, width, height, area))
            if not (12 <= height <= 34 and 0.28 <= width / height <= 1.30):
                continue
            if area < max(24, int(height * height * 0.18)):
                continue
            candidates.append(_DigitCandidate(x, y, width, height, 0, 0.0))

        candidates.sort(key=lambda item: (item.x, item.y))
        best: tuple[int, float, int] | None = None
        for start in range(len(candidates)):
            if not _has_rebirth_icon(mask, candidates[start]):
                continue
            group = [candidates[start]]
            for following in candidates[start + 1 :]:
                previous = group[-1]
                reference_height = max(previous.height, following.height)
                gap = following.x - previous.right
                if gap > reference_height * 0.55:
                    break
                if gap < -reference_height * 0.25:
                    continue
                if abs(following.center_y - previous.center_y) > reference_height * 0.35:
                    continue
                if max(previous.height, following.height) / min(previous.height, following.height) > 1.35:
                    continue
                group.append(following)
                if len(group) >= 3:
                    break

            for length in range(1, len(group) + 1):
                digits: list[_DigitCandidate] = []
                for raw in group[:length]:
                    glyph = mask[raw.y : raw.y + raw.height, raw.x : raw.x + raw.width]
                    digit, score = _recognize_digit(glyph, self.digit_templates)
                    if score < LEVEL_SCORE_THRESHOLD:
                        digits = []
                        break
                    digits.append(
                        _DigitCandidate(
                            raw.x,
                            raw.y,
                            raw.width,
                            raw.height,
                            digit,
                            score,
                        )
                    )
                if not digits:
                    continue
                level = int("".join(str(item.digit) for item in digits))
                score = sum(item.score for item in digits) / len(digits)
                # A recognized adjacent glyph is stronger structural evidence
                # than a slightly higher-scoring prefix. Without this strict
                # preference, level 25 can flicker between 2, 0, and 25.
                ranking = (len(digits), score, level)
                if best is None or ranking[:2] > best[:2]:
                    best = ranking

        if best is None:
            return None, 0.0
        return best[2], min(1.0, best[1])


class RebirthAlertTracker:
    """Alert once per READY appearance despite fluctuating level OCR."""

    def __init__(
        self,
        state_path: Path | None = None,
        *,
        confirmation_scans: int = DEFAULT_CONFIRMATION_SCANS,
        release_scans: int = DEFAULT_RELEASE_SCANS,
    ) -> None:
        self.state_path = state_path or data_dir() / "rebirth_state.json"
        self.confirmation_scans = max(1, int(confirmation_scans))
        self.release_scans = max(1, int(release_scans))
        self.last_alerted_level = self._load_last_alerted_level()
        self._candidate_level: int | None = None
        self._candidate_count = 0
        self._ready_misses = 0
        self._ready_episode_alerted = False

    def observe(self, observation: RebirthObservation) -> int | None:
        if not observation.ready:
            self._candidate_level = None
            self._candidate_count = 0
            self._ready_misses += 1
            if self._ready_misses >= self.release_scans:
                self._ready_episode_alerted = False
            return None

        self._ready_misses = 0
        if self._ready_episode_alerted:
            return None
        if observation.level is None:
            self._candidate_level = None
            self._candidate_count = 0
            return None

        if observation.level == self._candidate_level:
            self._candidate_count += 1
        else:
            self._candidate_level = observation.level
            self._candidate_count = 1

        if self._candidate_count < self.confirmation_scans:
            return None
        if observation.level == self.last_alerted_level:
            # This is normally an app restart while the same READY indicator
            # is still present. Latch it without sending the alert again.
            self._ready_episode_alerted = True
            return None
        if self.last_alerted_level is not None and observation.level < self.last_alerted_level:
            # Rebirth levels only advance. Keep waiting for a stable corrected
            # read instead of persisting a partial digit such as 0 or 2.
            return None

        self.last_alerted_level = observation.level
        self._ready_episode_alerted = True
        try:
            self._save()
        except OSError:
            # A read-only/full data folder must not suppress the actual alert.
            pass
        return observation.level

    def _load_last_alerted_level(self) -> int | None:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            value = payload.get("last_alerted_level") if isinstance(payload, dict) else None
            return int(value) if value is not None else None
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"last_alerted_level": self.last_alerted_level}, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.state_path)


@dataclass(frozen=True)
class _DigitCandidate:
    x: int
    y: int
    width: int
    height: int
    digit: int
    score: float

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0


def _effective_screen_height(
    bottom_bgr: np.ndarray,
    screen_height: int,
    *,
    screen_width: int | None,
) -> int:
    """Infer a centered viewport height from a full-width bottom letterbox."""

    if bottom_bgr.ndim != 3 or bottom_bgr.shape[0] < 2 or screen_width is None:
        return screen_height
    expected_bar = max(0, int(round((screen_height - screen_width * 9 / 16) / 2)))
    if expected_bar < 8:
        return screen_height
    dark_fraction = np.mean(np.max(bottom_bgr[:, :, :3], axis=2) <= 24, axis=1)
    bottom_bar = 0
    for fraction in reversed(dark_fraction):
        if fraction < 0.90:
            break
        bottom_bar += 1

    # Ignore a few naturally dark edge rows and implausibly large bars. The
    # game viewport is centered, so the matching top bar has the same height.
    if (
        bottom_bar < 8
        or bottom_bar * 2 >= screen_height * 0.35
        or abs(bottom_bar - expected_bar) > max(8, int(round(expected_bar * 0.35)))
    ):
        return screen_height
    return max(1, screen_height - bottom_bar * 2)


def _green_hud_mask(image_bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (42, 170, 160), (90, 255, 255))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def _normalize_glyph(glyph: np.ndarray, width: int = 28, height: int = 40) -> np.ndarray:
    ys, xs = np.where(glyph > 0)
    output = np.zeros((height, width), dtype=np.uint8)
    if not len(xs):
        return output
    cropped = glyph[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    scale = min((width - 4) / cropped.shape[1], (height - 4) / cropped.shape[0])
    resized = cv2.resize(
        cropped,
        (
            max(1, int(round(cropped.shape[1] * scale))),
            max(1, int(round(cropped.shape[0] * scale))),
        ),
        interpolation=cv2.INTER_NEAREST,
    )
    x = (width - resized.shape[1]) // 2
    y = (height - resized.shape[0]) // 2
    output[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return output


def _build_digit_templates() -> dict[int, tuple[np.ndarray, ...]]:
    templates: dict[int, list[np.ndarray]] = {digit: [] for digit in range(10)}
    fonts = (
        cv2.FONT_HERSHEY_SIMPLEX,
        cv2.FONT_HERSHEY_DUPLEX,
        cv2.FONT_HERSHEY_COMPLEX,
        cv2.FONT_HERSHEY_TRIPLEX,
    )
    for digit in range(10):
        for font in fonts:
            for scale in (1.0, 1.2, 1.4):
                for thickness in (2, 3, 4):
                    canvas = np.zeros((80, 80), dtype=np.uint8)
                    cv2.putText(
                        canvas,
                        str(digit),
                        (10, 60),
                        font,
                        scale,
                        255,
                        thickness,
                        cv2.LINE_AA,
                    )
                    for shear in (0.0, 0.15, 0.25):
                        italic = cv2.warpAffine(
                            canvas,
                            np.float32([[1.0, -shear, 10.0], [0.0, 1.0, 0.0]]),
                            (90, 80),
                        )
                        templates[digit].append(_normalize_glyph(italic))
    return {digit: tuple(values) for digit, values in templates.items()}


def _recognize_digit(
    glyph: np.ndarray,
    templates: dict[int, tuple[np.ndarray, ...]],
) -> tuple[int, float]:
    normalized = _normalize_glyph(glyph)
    best_digit = 0
    best_score = -1.0
    for digit, variants in templates.items():
        score = max(
            float(cv2.matchTemplate(normalized, variant, cv2.TM_CCOEFF_NORMED)[0, 0])
            for variant in variants
        )
        if score > best_score:
            best_digit = digit
            best_score = score
    return best_digit, best_score


def _has_rebirth_icon(mask: np.ndarray, digit: _DigitCandidate) -> bool:
    height = digit.height
    left = max(0, int(round(digit.x - height * 2.7)))
    right = max(left, int(round(digit.x - height * 0.25)))
    top = max(0, int(round(digit.center_y - height * 1.15)))
    bottom = min(mask.shape[0], int(round(digit.center_y + height * 1.15)))
    icon_window = mask[top:bottom, left:right]
    if icon_window.size == 0:
        return False

    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(icon_window)
    qualifying = [
        tuple(map(int, stat))
        for stat in stats[1:count]
        if int(stat[4]) >= max(18, int(height * height * 0.12))
    ]
    if len(qualifying) < 2:
        return False
    x1 = min(item[0] for item in qualifying)
    y1 = min(item[1] for item in qualifying)
    x2 = max(item[0] + item[2] for item in qualifying)
    y2 = max(item[1] + item[3] for item in qualifying)
    union_width = x2 - x1
    union_height = y2 - y1
    total_area = sum(item[4] for item in qualifying)
    return (
        0.9 * height <= union_width <= 2.3 * height
        and 0.9 * height <= union_height <= 2.3 * height
        and 0.55 <= union_width / max(1, union_height) <= 1.65
        and total_area >= height * height * 0.55
    )
