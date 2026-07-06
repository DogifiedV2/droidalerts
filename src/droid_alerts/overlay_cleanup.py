from __future__ import annotations

import cv2
import numpy as np


def clean_overlay(image: np.ndarray) -> np.ndarray:
    """Erase debug rectangles drawn by a previous run on annotated captures.

    Those overlays drew 2-3px full-width horizontal lines exactly on the row
    bounds, in the droid color: magenta (Rainbow), cyan (Diamond) or light
    gray (Beskar). Any near-full-width line of vivid OR bright low-saturation
    pixels is replaced with the nearest clean row; vivid full-height border
    columns are cleared too. Live captures never contain such overlays.
    """
    out = image.copy()
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    vivid = (sat > 100) & (val > 110)
    bright_gray = (sat < 70) & (val > 165)
    h, w = sat.shape

    line_rows: set[int] = set()
    for y in range(h):
        if int(vivid[y].sum()) > w * 0.8:
            line_rows.add(y)
            continue
        if int(bright_gray[y].sum()) > w * 0.95:
            # Box lines are near-uniform full-width strokes; the game's
            # translucent alert backdrop is brighter than scenery but far
            # noisier, so a tight variance gate separates them.
            row = out[y].astype(np.int32)
            if int(row.std(axis=0).max()) < 15:
                line_rows.add(y)

    padded: set[int] = set()
    for y in line_rows:
        padded.update({y - 1, y, y + 1})

    for y in sorted(padded):
        if not 0 <= y < h:
            continue
        source = None
        for dy in range(2, 10):
            for cand in (y - dy, y + dy):
                if 0 <= cand < h and cand not in padded:
                    source = cand
                    break
            if source is not None:
                break
        if source is not None:
            out[y] = out[source]

    for x in list(range(0, 4)) + list(range(w - 4, w)):
        if int(vivid[:, x].sum()) > h * 0.6 or int(bright_gray[:, x].sum()) > h * 0.8:
            out[:, x] = out[:, min(w - 1, max(0, x + (6 if x < 10 else -6)))]
    return out
