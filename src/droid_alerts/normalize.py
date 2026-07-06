from __future__ import annotations

from dataclasses import dataclass
from statistics import median

import cv2
import numpy as np

from .config import REFERENCE_ROW_HEIGHT_PX, REFERENCE_SCREEN_HEIGHT, REFERENCE_SCREEN_WIDTH
from .row_finder import RowCandidate, measured_row_heights

# Median height of confident row-finder candidates measured on reference-scale
# (2560x1440) captures. Row-finder candidates hug the glyph band, which is
# shorter than the full 44px classifier row. Calibrated against Tool V1's
# live roi dumps; used only when the source screen height is unknown.
REFERENCE_CANDIDATE_HEIGHT_PX = 40.0


@dataclass
class NormalizedBand:
    image: np.ndarray
    scale: float  # source pixels per reference pixel; y_src = y_norm * scale
    method: str


def scale_from_screen(screen_height: int, screen_width: int | None = None) -> float:
    """The game fits its HUD to a 16:9 box: on 16:9 screens the UI scales
    with height, but on narrower aspects (e.g. 1440x1040, ~4:3) it scales
    with WIDTH — measured on a real 4:3 user whose text was 0.5625x reference
    (= 1440/2560), not the 0.72x that height alone predicts. min() of both
    ratios reproduces that fit exactly, and equals the old height-based value
    on every 16:9 screen (zero behavior change there)."""
    height_scale = screen_height / REFERENCE_SCREEN_HEIGHT
    if screen_width is None:
        return height_scale
    return min(height_scale, screen_width / REFERENCE_SCREEN_WIDTH)


def scale_from_rows(candidates: list[RowCandidate]) -> float | None:
    heights = measured_row_heights(candidates)
    if len(heights) < 1:
        return None
    return float(median(heights)) / REFERENCE_CANDIDATE_HEIGHT_PX


def estimate_scale(
    *,
    screen_height: int | None = None,
    screen_width: int | None = None,
    candidates: list[RowCandidate] | None = None,
    scale_min: float = 0.4,
    scale_max: float = 2.5,
) -> tuple[float, str]:
    """Estimate source scale relative to the 2560x1440 reference.

    Screen size is the primary signal when known (live capture); width
    matters on non-16:9 aspects (see scale_from_screen). Measured row
    heights are the fallback for fixtures of unknown provenance, and a
    sanity check otherwise. Clamped to catch bad row detections.
    """
    row_scale = scale_from_rows(candidates or [])
    if screen_height is not None:
        scale = scale_from_screen(screen_height, screen_width)
        method = "screen"
        if row_scale is not None and not (0.65 <= row_scale / max(scale, 1e-6) <= 1.55):
            method = "screen(row-mismatch)"
    elif row_scale is not None:
        scale = row_scale
        method = "rows"
    else:
        scale = 1.0
        method = "default"
    return float(min(scale_max, max(scale_min, scale))), method


def normalize_band(band: np.ndarray, scale: float) -> NormalizedBand:
    """Resize a captured band back to reference scale (44px rows) so the
    fixed-column classifier's constants are valid again."""
    if abs(scale - 1.0) < 0.02:
        return NormalizedBand(image=band, scale=1.0, method="identity")
    h, w = band.shape[:2]
    new_w = max(1, int(round(w / scale)))
    new_h = max(1, int(round(h / scale)))
    interpolation = cv2.INTER_AREA if scale > 1.0 else cv2.INTER_CUBIC
    resized = cv2.resize(band, (new_w, new_h), interpolation=interpolation)
    return NormalizedBand(image=resized, scale=scale, method="resize")


def normalize_row(row: np.ndarray, measured_height: int) -> np.ndarray:
    """Resize a single row crop so its height matches the reference row height."""
    scale = measured_height / REFERENCE_ROW_HEIGHT_PX
    return normalize_band(row, scale).image
