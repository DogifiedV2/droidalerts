from __future__ import annotations

import hashlib
import re
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
    amount_text: str | None = None
    amount_value: float | None = None


class CreditHudDetector:
    """Locate the credits icon and fingerprint the displayed amount beside it."""

    def __init__(
        self,
        template_path: Path | None = None,
        *,
        icon_threshold: float = 0.82,
        amount_icon_threshold: float = 0.90,
    ) -> None:
        path = template_path or assets_dir() / "credit_icon.png"
        template = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if template is None or template.ndim != 3 or template.shape[2] != 4:
            raise RuntimeError(f"Credit icon template is missing or invalid: {path}")
        self.template = template
        self.icon_threshold = float(icon_threshold)
        # A partially covered icon can still be good enough for the stall
        # detector, but its neighbouring amount is commonly covered by the
        # chat panel too. Only trust numeric OCR on a clean icon match.
        self.amount_icon_threshold = max(
            self.icon_threshold,
            float(amount_icon_threshold),
        )

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
        # Credits are the first resource counter at the far-left edge. The old
        # 22% search band also included the visually similar scrap/book icon,
        # which could score higher and fingerprint the wrong counter.
        search_width = min(width, max(180, int(round(screen_width * 0.085))))
        search = bottom_bgr[:, :search_width]
        # Fortnite fits its HUD inside a 16:9 area. On narrower screens the UI
        # therefore scales with width rather than height. Using height alone
        # made the numeric confidence score fail on our compact and macOS
        # resolution cases even though the correct icon was present.
        hud_scale = min(screen_height / 1440.0, screen_width / 2560.0)
        nominal_size = max(36, int(round(99 * hud_scale)))
        candidate_sizes = sorted(
            {
                max(28, int(round(nominal_size * scale)))
                for scale in np.linspace(0.76, 1.12, 13)
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
            finite_scores = np.where(np.isfinite(scores), scores, -1.0)
            _minimum, maximum, _min_location, location = cv2.minMaxLoc(finite_scores)
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
        if best_score >= self.amount_icon_threshold:
            amount_text, amount_value = _read_credit_amount(amount)
        else:
            amount_text, amount_value = None, None
        return CreditHudObservation(
            visible=fingerprint is not None,
            fingerprint=fingerprint,
            icon_score=best_score,
            icon_visible=True,
            amount_text=amount_text,
            amount_value=amount_value,
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


_CREDIT_SUFFIXES = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12, "Q": 1e15}
_CREDIT_TEXT_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)([KMBTQ]?)$", re.IGNORECASE)


def parse_credit_amount(text: str) -> float | None:
    """Convert the compact value used by the credits HUD into base credits."""
    normalized = str(text).strip().replace(",", "").upper()
    match = _CREDIT_TEXT_PATTERN.fullmatch(normalized)
    if match is None:
        return None
    try:
        return float(match.group(1)) * _CREDIT_SUFFIXES[match.group(2)]
    except (OverflowError, ValueError):
        return None


def format_credit_rate(value: float) -> str:
    """Format a credits-per-minute value in the same compact HUD style."""
    amount = max(0.0, float(value))
    suffix = ""
    divisor = 1.0
    for candidate, candidate_divisor in (
        ("Q", 1e15),
        ("T", 1e12),
        ("B", 1e9),
        ("M", 1e6),
        ("K", 1e3),
    ):
        if amount >= candidate_divisor:
            suffix, divisor = candidate, candidate_divisor
            break
    compact = amount / divisor
    if compact >= 100:
        number = f"{compact:.0f}"
    elif compact >= 10:
        number = f"{compact:.1f}".rstrip("0").rstrip(".")
    else:
        number = f"{compact:.2f}".rstrip("0").rstrip(".")
    return f"{number}{suffix}"


def _normalize_credit_glyph(glyph: np.ndarray, width: int = 28, height: int = 40) -> np.ndarray:
    ys, xs = np.where(glyph > 0)
    output = np.zeros((height, width), dtype=np.uint8)
    if not len(xs):
        return output
    cropped = glyph[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    scale = min((width - 4) / max(1, cropped.shape[1]), (height - 4) / max(1, cropped.shape[0]))
    resized = cv2.resize(
        cropped,
        (max(1, round(cropped.shape[1] * scale)), max(1, round(cropped.shape[0] * scale))),
        interpolation=cv2.INTER_NEAREST,
    )
    x = (width - resized.shape[1]) // 2
    y = (height - resized.shape[0]) // 2
    output[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return output


def _build_credit_glyph_templates() -> dict[str, tuple[np.ndarray, ...]]:
    templates: dict[str, list[np.ndarray]] = {char: [] for char in "0123456789KMBTQ"}
    for font in (cv2.FONT_HERSHEY_DUPLEX, cv2.FONT_HERSHEY_SIMPLEX):
        for thickness in (1, 2, 3):
            for char in templates:
                canvas = np.zeros((64, 52), dtype=np.uint8)
                cv2.putText(canvas, char, (4, 50), font, 1.45, 255, thickness, cv2.LINE_AA)
                templates[char].append(_normalize_credit_glyph(canvas))
    # The in-game HUD uses a heavy italic face that differs substantially from
    # OpenCV's built-in fonts. Ship a small pre-rendered atlas so recognition
    # remains dependency-free in packaged builds and on non-Windows systems.
    atlas = cv2.imread(str(assets_dir() / "credit_glyph_templates.png"), cv2.IMREAD_GRAYSCALE)
    chars = "0123456789KMBTQ"
    if atlas is not None and atlas.shape[1] == len(chars) * 28 and atlas.shape[0] % 40 == 0:
        for row in range(atlas.shape[0] // 40):
            for column, char in enumerate(chars):
                templates[char].append(
                    atlas[row * 40 : (row + 1) * 40, column * 28 : (column + 1) * 28]
                )
    return {char: tuple(values) for char, values in templates.items()}


_CREDIT_GLYPH_TEMPLATES = _build_credit_glyph_templates()


def _recognize_credit_glyph(glyph: np.ndarray, candidates: str) -> tuple[str, float]:
    normalized = _normalize_credit_glyph(glyph)
    best_char, best_score = "", -1.0
    for char in candidates:
        for template in _CREDIT_GLYPH_TEMPLATES[char]:
            score = float(cv2.matchTemplate(normalized, template, cv2.TM_CCOEFF_NORMED)[0, 0])
            if score > best_score:
                best_char, best_score = char, score
    return best_char, best_score


def _read_credit_amount(amount_bgr: np.ndarray) -> tuple[str | None, float | None]:
    """Read the bright compact number beside the credits icon without an OCR dependency."""
    if amount_bgr is None or amount_bgr.size == 0:
        return None, None
    hsv = cv2.cvtColor(amount_bgr, cv2.COLOR_BGR2HSV)
    # Compression and bright scene details were joining themselves to the HUD
    # glyphs at the former V=175/S=100 threshold. The actual number fill stays
    # close to white, so a stricter mask cleanly separates every glyph.
    mask = cv2.inRange(hsv, (0, 0, 220), (179, 60, 255))
    mask_height, _mask_width = mask.shape
    mask[: int(round(mask_height * 0.20)), :] = 0
    mask[int(round(mask_height * 0.84)) :, :] = 0

    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    components: list[tuple[int, int, int, int, int, int]] = []
    for label, (x, y, width, height, area) in enumerate(stats[1:count], 1):
        x, y, width, height, area = map(int, (x, y, width, height, area))
        if (
            height >= mask_height * 0.38
            and mask_height * 0.10 <= width <= mask_height * 0.70
            and y <= mask_height * 0.48
            and y + height >= mask_height * 0.60
            and area >= mask_height * 2.0
        ):
            components.append((x, y, width, height, area, label))
    if not components:
        return None, None
    components.sort(key=lambda item: item[0])

    # Split the large components into tightly spaced strings. This discards
    # the next resource counter and isolated bright scene details.
    runs: list[list[tuple[int, int, int, int, int, int]]] = []
    for component in components:
        if (
            not runs
            or component[0] - (runs[-1][-1][0] + runs[-1][-1][2])
            > mask_height * 0.48
        ):
            runs.append([component])
        else:
            runs[-1].append(component)
    eligible_runs = [run for run in runs if len(run) >= 4]
    if not eligible_runs:
        return None, None
    grouped = max(eligible_runs, key=lambda run: (len(run), -run[0][0]))

    text: list[str] = []
    for index, (x, y, width, height, _area, label) in enumerate(grouped):
        # Use only this connected component. The former labels>0 condition
        # pulled unrelated components inside an overlapping bounding box into
        # the glyph and was a major source of wrong digits.
        glyph = np.where(
            labels[y : y + height, x : x + width] == label,
            255,
            0,
        ).astype(np.uint8)
        if index == len(grouped) - 1:
            char, score = _recognize_credit_glyph(glyph, "KMBTQ")
        else:
            char, score = _recognize_credit_glyph(glyph, "0123456789")
        if score < 0.30:
            return None, None
        text.append(char)

    # The credits HUD consistently renders two fractional digits. Inferring
    # the separator from the glyph sequence is more reliable than treating
    # the tiny decimal dot as text (it is often fragmented by compression).
    if len(text) >= 4 and text[-1] in _CREDIT_SUFFIXES:
        text.insert(len(text) - 3, ".")
    recognized = "".join(text)
    value = parse_credit_amount(recognized)
    return (recognized, value) if value is not None else (None, None)


class ScrapRateTracker:
    """Estimate credits/min from a rolling set of validated HUD readings."""

    def __init__(
        self,
        confirmation_seconds: float = 10.0,
        *,
        window_seconds: float = 300.0,
        idle_seconds: float = 30.0,
    ) -> None:
        # Retain the old argument for compatibility. Rates now update on the
        # first positive numeric delta instead of waiting.
        del confirmation_seconds
        self.window_seconds = max(30.0, float(window_seconds))
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.reset()

    def reset(self) -> None:
        self._samples: list[tuple[float, float, str]] = []
        self._lower_candidates: list[tuple[float, float, str]] = []
        self._upper_candidates: list[tuple[float, float, str]] = []
        self._last_increase_time: float | None = None
        self._idle = False
        self.last_status = "reset"

    @staticmethod
    def _suffix(amount_text: str | None) -> str:
        match = re.search(r"([KMBTQ])\s*$", str(amount_text or "").strip(), re.IGNORECASE)
        return match.group(1).upper() if match is not None else ""

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_seconds
        self._samples = [sample for sample in self._samples if sample[0] >= cutoff]

    def _rolling_rate(self) -> float | None:
        if len(self._samples) < 2:
            return None
        first_time, first_value, _first_suffix = self._samples[0]
        last_time, last_value, _last_suffix = self._samples[-1]
        elapsed = last_time - first_time
        if elapsed <= 0.0:
            return None
        # Report the actual balance change across the rolling window. The old
        # median-of-change-slopes ignored unchanged readings, so a short burst
        # remained displayed as the rate even after minutes of no earnings.
        rate = (last_value - first_value) * 60.0 / elapsed
        return rate if rate > 0.0 else None

    def observe(
        self,
        value: float | None,
        *,
        now: float,
        amount_text: str | None = None,
    ) -> float | None:
        if value is None or not np.isfinite(value):
            self.last_status = "unreadable"
            return None
        current = float(value)
        timestamp = float(now)
        suffix = self._suffix(amount_text)
        self._trim(timestamp)
        if not self._samples:
            self._samples.append((timestamp, current, suffix))
            self._last_increase_time = timestamp
            self._idle = False
            self.last_status = "baseline_started"
            return None

        previous_time, previous_value, previous_suffix = self._samples[-1]
        if timestamp <= previous_time:
            self.last_status = "unchanged"
            return None
        if current == previous_value:
            if self._idle:
                self.last_status = "idle"
                return 0.0
            self._samples.append((timestamp, current, suffix))
            self._trim(timestamp)
            if (
                self._last_increase_time is not None
                and timestamp - self._last_increase_time >= self.idle_seconds
            ):
                # Stop the active earning window after a sustained flat
                # balance. When earnings resume, the stopped time must not
                # dilute the new active rate.
                self._samples = [(timestamp, current, suffix)]
                self._lower_candidates.clear()
                self._upper_candidates.clear()
                self._idle = True
                self.last_status = "income_paused"
                return 0.0
            rate = self._rolling_rate()
            self.last_status = "unchanged_rate_updated" if rate is not None else "unchanged"
            return rate

        if suffix != previous_suffix:
            if previous_suffix and not suffix:
                # The game does not remove a magnitude suffix while the same
                # balance is increasing. Treat this as an incomplete OCR read.
                self.last_status = "missing_suffix_rejected"
                return None
            ratio = current / previous_value if previous_value > 0.0 else float("inf")
            suffix_order = "KMBTQ"
            previous_rank = suffix_order.find(previous_suffix)
            current_rank = suffix_order.find(suffix)
            adjacent = abs(current_rank - previous_rank) <= 1
            if not adjacent or not 0.5 <= ratio <= 2.0:
                # A dropped or invented suffix changes the value by orders of
                # magnitude. Start a clean baseline instead of emitting a
                # huge one-frame rate.
                self._samples = [(timestamp, current, suffix)]
                self._lower_candidates.clear()
                self._upper_candidates.clear()
                self._last_increase_time = timestamp
                self._idle = False
                self.last_status = "magnitude_changed_baseline_reset"
                return None

        if self._idle:
            if current > previous_value:
                self._samples = [(timestamp, current, suffix)]
                self._lower_candidates.clear()
                self._upper_candidates.clear()
                self._last_increase_time = timestamp
                self._idle = False
                self.last_status = "income_resumed"
            else:
                # A purchase/reset while idle establishes the new balance but
                # does not count as resumed earnings.
                self._samples = [(timestamp, current, suffix)]
                self._last_increase_time = timestamp
                self.last_status = "idle_balance_reset"
            return 0.0

        if current < previous_value * 0.95:
            candidate = (timestamp, current, suffix)
            if (
                self._lower_candidates
                and current >= self._lower_candidates[-1][1]
                and suffix == self._lower_candidates[-1][2]
            ):
                self._lower_candidates.append(candidate)
            else:
                self._lower_candidates = [candidate]
            if len(self._lower_candidates) < 2:
                self.last_status = "lower_reading_pending"
                return None

            first_lower_value = self._lower_candidates[0][1]
            while self._samples and self._samples[-1][1] > first_lower_value * 1.05:
                self._samples.pop()
            if self._samples and first_lower_value >= self._samples[-1][1] * 0.95:
                self._samples.extend(self._lower_candidates)
                self.last_status = "high_outlier_removed"
            else:
                # Two sustained lower readings indicate an actual purchase or
                # reset, so earnings begin from the new balance.
                self._samples = list(self._lower_candidates)
                self.last_status = "lower_balance_baseline_reset"
            self._last_increase_time = timestamp
            self._idle = False
            self._lower_candidates.clear()
            self._upper_candidates.clear()
            self._trim(timestamp)
            rate = self._rolling_rate()
            if rate is not None:
                self.last_status += "_rate_updated"
            return rate

        if current < previous_value:
            # Small backwards OCR flicker should not replace a good baseline.
            self.last_status = "backward_flicker_rejected"
            return None

        self._lower_candidates.clear()
        baseline_rate = self._rolling_rate()
        elapsed = timestamp - previous_time
        implied_rate = (current - previous_value) * 60.0 / elapsed
        if (
            len(self._samples) >= 3
            and baseline_rate is not None
            and implied_rate > baseline_rate * 8.0
        ):
            candidate = (timestamp, current, suffix)
            if (
                self._upper_candidates
                and current >= self._upper_candidates[-1][1]
                and suffix == self._upper_candidates[-1][2]
            ):
                self._upper_candidates.append(candidate)
            else:
                self._upper_candidates = [candidate]
            if len(self._upper_candidates) < 2:
                self.last_status = "large_jump_pending"
                return None
            # A sustained higher range is either a real lump gain or an OCR
            # digit correction. Do not count the discontinuity as earnings.
            self._samples = list(self._upper_candidates)
            self._upper_candidates.clear()
            self._last_increase_time = timestamp
            self._idle = False
            rate = self._rolling_rate()
            self.last_status = "large_jump_baseline_reset"
            if rate is not None:
                self.last_status += "_rate_updated"
            return rate

        self._upper_candidates.clear()
        self._samples.append((timestamp, current, suffix))
        self._last_increase_time = timestamp
        self._idle = False
        self._trim(timestamp)
        rate = self._rolling_rate()
        self.last_status = "rate_updated" if rate is not None else "collecting"
        return rate


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
