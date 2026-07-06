from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class RowCandidate:
    y0: int
    y1: int
    score: float

    @property
    def height(self) -> int:
        return self.y1 - self.y0


def find_candidate_rows(image_bgr: np.ndarray) -> list[RowCandidate]:
    """Locate likely alert rows inside a captured band.

    Resolution-relative by construction (Tool V1 port): HSV/edge foreground
    masks, dark-outline mask, vertical projection runs, plus rarity-color
    connected-component blobs. Returns merged candidates sorted by score.
    """
    h, w = image_bgr.shape[:2]
    if h <= 0 or w <= 0:
        return []

    masks = [
        foreground_mask(image_bgr, strict=True),
        foreground_mask(image_bgr, strict=False),
        dark_outline_mask(image_bgr),
    ]

    candidates: list[RowCandidate] = []
    for mask in masks:
        candidates.extend(projection_candidates(mask))
    candidates.extend(rarity_color_candidates(image_bgr))

    # Tight crops can have saturated backgrounds that hide projection peaks.
    if h <= 360 or not candidates:
        candidates.extend(sliding_candidates(h))

    merged = merge_candidates(candidates, h)
    return merged[:24]


def phrase_row_seeds(image_bgr: np.ndarray) -> list[int]:
    """Precise row-top seeds from the white 'spawned at the ...' phrase text.

    Runs on a reference-scale (normalized) band: scans the spawn-phrase
    columns (330-720) for white-text bands with dark outlines; each band of
    plausible glyph height marks a row top 13px above it (measured offset at
    reference scale). Far more position-accurate than projection candidates.
    """
    h, w = image_bgr.shape[:2]
    if w <= 340 or h < 20:
        return []
    x2 = min(w, 720)
    strip = image_bgr[:, 330:x2]
    b, g, r = cv2.split(strip)
    maxc = np.maximum.reduce([r, g, b])
    minc = np.minimum.reduce([r, g, b])
    white = (r > 180) & (g > 180) & (b > 180) & ((maxc - minc) < 60)
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    dark_near = cv2.dilate((gray < 100).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    profile = (white & dark_near).sum(axis=1)
    # Threshold above the inter-row floor (backdrop edges/dilation bleed) but
    # well below glyph-core counts; relative to strip width for narrow bands.
    threshold = max(20, int((x2 - 330) * 0.09))

    seeds: list[int] = []
    in_run = False
    start = 0
    for y in range(h):
        active = profile[y] >= threshold
        if active and not in_run:
            start = y
            in_run = True
        elif not active and in_run:
            in_run = False
            band_h = y - start
            if 8 <= band_h <= 30:
                seeds.append(max(0, start - 13))
    if in_run and 8 <= h - start <= 30:
        seeds.append(max(0, start - 13))
    return seeds


def band_has_phrase_evidence(image_bgr: np.ndarray, *, min_window_pixels: int = 600) -> bool:
    """Cheap whole-band pre-gate (~2ms): does any 44px-tall window in the
    spawn-phrase columns hold enough white outlined text to possibly be an
    alert row? Frames without alerts skip the expensive template pipeline.

    Deliberately laxer than the per-row gate (600 vs 700) - this must never
    veto a real alert, only skip obviously empty frames.
    """
    h, w = image_bgr.shape[:2]
    if w <= 340 or h < 20:
        return False
    strip = image_bgr[:, 330 : min(w, 720)]
    b, g, r = cv2.split(strip)
    maxc = np.maximum.reduce([r, g, b])
    minc = np.minimum.reduce([r, g, b])
    white = (r > 165) & (g > 165) & (b > 165) & ((maxc - minc) < 90)
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    dark_near = cv2.dilate((gray < 115).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    profile = (white & dark_near).sum(axis=1)
    if int(profile.sum()) < min_window_pixels:
        return False
    window = min(44, h)
    cumulative = np.concatenate([[0], np.cumsum(profile)])
    windowed = cumulative[window:] - cumulative[:-window]
    return bool(windowed.size == 0 or windowed.max() >= min_window_pixels)


def measured_row_heights(candidates: list[RowCandidate]) -> list[int]:
    """Heights of confident candidates only - used for scale estimation."""
    return [c.height for c in candidates if c.score >= 0.45 and 8 <= c.height <= 80]


def foreground_mask(image_bgr: np.ndarray, *, strict: bool) -> np.ndarray:
    """Mask alert glyph/icon foreground while suppressing arbitrary scenery.

    The game text is bright/colored with a dark outline. Backgrounds vary a
    lot, so this mask requires color/white pixels to be near dark outline or
    local edges, then adds only the nearby outline pixels back in.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    b, g, r = cv2.split(image_bgr)
    maxc = np.maximum.reduce([r, g, b])
    minc = np.minimum.reduce([r, g, b])

    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 75 if strict else 45, 170 if strict else 125)
    edge_near = cv2.dilate(edges, np.ones((5, 5), np.uint8)) > 0
    dark = gray < (105 if strict else 125)
    dark_near = cv2.dilate(dark.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0

    if strict:
        colored = (sat > 95) & (val > 125)
        white = (r > 200) & (g > 200) & (b > 200) & ((maxc - minc) < 75)
    else:
        colored = (sat > 55) & (val > 85)
        white = (r > 150) & (g > 150) & (b > 150) & ((maxc - minc) < 105)

    bright_text = (colored | white) & (dark_near | edge_near)
    outline = dark & (cv2.dilate(bright_text.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
    mask = (bright_text | outline).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 3), np.uint8))
    return mask


def dark_outline_mask(image_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    dark = (gray < 85).astype(np.uint8)
    return cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def projection_candidates(mask: np.ndarray) -> list[RowCandidate]:
    h, w = mask.shape[:2]
    if h <= 0 or w <= 0:
        return []
    projection = cv2.GaussianBlur(mask.astype(np.float32).sum(axis=1), (1, 9), 0).reshape(-1)
    threshold = max(6.0, w * 0.018, float(projection.max(initial=0)) * 0.18)
    candidates: list[RowCandidate] = []
    in_run = False
    start = 0
    max_value = 0.0
    for y, value in enumerate(projection):
        if value >= threshold and not in_run:
            start = y
            max_value = float(value)
            in_run = True
        elif in_run:
            max_value = max(max_value, float(value))

        if (value < threshold or y == h - 1) and in_run:
            end = y
            height = end - start + 1
            if 5 <= height <= 64:
                score = min(1.0, max_value / max(1.0, w * 0.22))
                candidates.append(RowCandidate(start, end, score))
            in_run = False
    return candidates


def sliding_candidates(height: int) -> list[RowCandidate]:
    candidates: list[RowCandidate] = []
    window = 36 if height > 80 else max(20, height)
    step = max(8, window // 3)
    for y0 in range(0, max(1, height - window + 1), step):
        candidates.append(RowCandidate(y0, min(height, y0 + window), 0.30))
    if height > window:
        candidates.append(RowCandidate(max(0, height - window), height, 0.30))
    return candidates


def rarity_color_candidates(image_bgr: np.ndarray) -> list[RowCandidate]:
    h, w = image_bgr.shape[:2]
    if h <= 0 or w <= 0:
        return []

    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 35, 115)
    edge_near = cv2.dilate(edges, np.ones((5, 5), np.uint8)) > 0

    cyan = (hue >= 82) & (hue <= 103)
    purple = (hue >= 122) & (hue <= 154)
    magenta = (hue >= 155) | (hue <= 4)
    orange = (hue >= 5) & (hue <= 26)
    color_mask = (sat > 80) & (val > 105) & (cyan | purple | magenta | orange) & edge_near
    mask = cv2.morphologyEx(color_mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 7), np.uint8))
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    candidates: list[RowCandidate] = []
    for idx in range(1, count):
        y = int(stats[idx, cv2.CC_STAT_TOP])
        cw = int(stats[idx, cv2.CC_STAT_WIDTH])
        ch = int(stats[idx, cv2.CC_STAT_HEIGHT])
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area < 18 or cw < 12 or ch < 6:
            continue
        if cw > max(260, int(w * 0.45)) or ch > 54:
            continue
        center = y + ch // 2
        row_half = max(18, min(28, ch + 10))
        candidates.append(RowCandidate(max(0, center - row_half), min(h, center + row_half), 0.62))
    return candidates


def merge_candidates(candidates: list[RowCandidate], image_height: int) -> list[RowCandidate]:
    if not candidates:
        return []
    expanded = [
        RowCandidate(
            y0=max(0, c.y0 - 4),
            y1=min(image_height, c.y1 + 4),
            score=c.score,
        )
        for c in candidates
    ]
    expanded.sort(key=lambda c: (c.y0, c.y1))
    merged: list[RowCandidate] = []
    current = expanded[0]
    for candidate in expanded[1:]:
        low_score_pair = candidate.score <= 0.31 and current.score <= 0.31
        merged_height = max(current.y1, candidate.y1) - min(current.y0, candidate.y0)
        if candidate.y0 <= current.y1 + 7 and not low_score_pair and merged_height <= 70:
            current = RowCandidate(
                y0=min(current.y0, candidate.y0),
                y1=max(current.y1, candidate.y1),
                score=max(current.score, candidate.score),
            )
        else:
            merged.append(current)
            current = candidate
    merged.append(current)
    merged.sort(key=lambda c: c.score, reverse=True)
    return merged
