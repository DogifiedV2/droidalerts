from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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
    family: str = ""
    family_confidence: float = 0.0
    rarity: str = ""
    rarity_confidence: float = 0.0


class OcrEngine(Protocol):
    def read(self, image_bgr: np.ndarray) -> list[TextObservation]: ...
