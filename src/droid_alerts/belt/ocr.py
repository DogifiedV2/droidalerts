from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Any, Protocol

import cv2
import numpy as np

from .matching import NameMatch


@dataclass(frozen=True)
class TextObservation:
    text: str
    confidence: float
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class DroidObservation:
    match: NameMatch
    ocr_confidence: float
    box: tuple[int, int, int, int]


class OcrEngine(Protocol):
    def read(self, image_bgr: np.ndarray) -> list[TextObservation]: ...


class RapidOcrEngine:
    # CardRecognizer scans only the lower name-row band. The supplied moving
    # belt video needed this small upscale to read short names consistently.
    card_input_scale = 1.25
    card_ocr_band = (0.35, 1.0)
    # RapidOCR 3.x promotes wide inputs to a 2000px detector canvas. On
    # Windows that made each belt pass take about a second. Staying just below
    # its 1500px tier retained the complete cards in the Windows regression
    # capture while substantially reducing detector work. Keep the proven Mac
    # path unchanged.
    card_max_input_width = 1490 if sys.platform == "win32" else None

    def __init__(self) -> None:
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise RuntimeError(
                "RapidOCR is not installed. Run: pip install -r requirements.txt"
            ) from exc
        self._engine = RapidOCR(
            params={
                "Global.use_cls": False,
                "Global.log_level": "warning",
                # Avoid expanding a thin, wide belt strip to several thousand
                # pixels while retaining enough detail for card names.
                "Det.limit_type": "max",
                "Det.limit_side_len": 1600,
            }
        )

    def read(self, image_bgr: np.ndarray) -> list[TextObservation]:
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        result = self._engine(rgb, use_cls=False)
        return _parse_rapidocr_result(result)


def _quad_to_box(points: Any) -> tuple[int, int, int, int] | None:
    try:
        array = np.asarray(points, dtype=float).reshape(-1, 2)
        if len(array) < 2:
            return None
        x1, y1 = np.floor(array.min(axis=0)).astype(int)
        x2, y2 = np.ceil(array.max(axis=0)).astype(int)
        return int(x1), int(y1), max(1, int(x2 - x1)), max(1, int(y2 - y1))
    except (TypeError, ValueError):
        return None


def _parse_rapidocr_result(result: Any) -> list[TextObservation]:
    """Accept RapidOCR 2.x list output and 3.x OCRResult output."""

    if result is None:
        return []
    payload = getattr(result, "txts", None)
    boxes = getattr(result, "boxes", None)
    scores = getattr(result, "scores", None)
    if payload is not None and boxes is not None:
        score_values = scores if scores is not None else [1.0] * len(payload)
        return [
            TextObservation(str(text), float(score), box)
            for text, score, points in zip(payload, score_values, boxes)
            if (box := _quad_to_box(points)) is not None
        ]
    if isinstance(result, tuple):
        result = result[0]
    observations: list[TextObservation] = []
    for item in result or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        box = _quad_to_box(item[0])
        if box is not None:
            observations.append(TextObservation(str(item[1]), float(item[2]), box))
    return observations
