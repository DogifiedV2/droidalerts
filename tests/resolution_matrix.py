from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class ResolutionCase:
    name: str
    width: int
    height: int
    profile: str

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


# One source of truth for every detector that depends on screen geometry.
RESOLUTION_CASES = (
    ResolutionCase("720p", 1280, 720, "wide"),
    ResolutionCase("768p", 1366, 768, "wide"),
    ResolutionCase("900p", 1600, 900, "wide"),
    ResolutionCase("1080p", 1920, 1080, "wide"),
    ResolutionCase("1440p", 2560, 1440, "wide"),
    ResolutionCase("2160p", 3840, 2160, "wide"),
    ResolutionCase("16:10-small", 1280, 800, "16:10"),
    ResolutionCase("16:10-large", 1920, 1200, "16:10"),
    ResolutionCase("ultrawide", 3440, 1440, "ultrawide"),
    ResolutionCase("compact-real", 1440, 1040, "compact"),
    ResolutionCase("macos-nonstandard", 1728, 1117, "16:10"),
)


def resize_for_screen(
    image: np.ndarray,
    *,
    source_scale: float,
    target_scale: float,
) -> np.ndarray:
    """Uniformly resize HUD pixels without distorting their aspect ratio."""

    ratio = target_scale / source_scale
    if abs(ratio - 1.0) < 0.001:
        return image.copy()
    interpolation = cv2.INTER_AREA if ratio < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(image, None, fx=ratio, fy=ratio, interpolation=interpolation)


def place_inside(image: np.ndarray, canvas: np.ndarray, *, x: int, y: int) -> None:
    """Place as much of ``image`` as fits on ``canvas`` at the given anchor."""

    if x >= canvas.shape[1] or y >= canvas.shape[0]:
        return
    width = min(image.shape[1], canvas.shape[1] - max(0, x))
    height = min(image.shape[0], canvas.shape[0] - max(0, y))
    if width <= 0 or height <= 0:
        return
    canvas[max(0, y) : max(0, y) + height, max(0, x) : max(0, x) + width] = image[
        :height, :width
    ]
