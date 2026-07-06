from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .classifier import Detection, DroidVisualDetector, column_drift_report
from .config import Thresholds
from .normalize import estimate_scale, normalize_band
from .row_finder import band_has_phrase_evidence, find_candidate_rows, phrase_row_seeds


@dataclass
class PipelineResult:
    detections: list[Detection]
    scale: float
    scale_method: str
    candidate_rows: int
    normalized_shape: tuple[int, int]
    meta: dict[str, Any] = field(default_factory=dict)
    candidate_row_boxes: list[tuple[int, int, int, int]] = field(default_factory=list)
    phrase_row_boxes: list[tuple[int, int, int, int]] = field(default_factory=list)
    normalized_image: np.ndarray | None = None


class Pipeline:
    """Scale-normalize then classify: Tool V1's resolution-relative row finder
    measures the frame, the band is resized to the 44px-row reference scale,
    and the fixed-column detector runs on the result."""

    def __init__(self, template_dir: str | Path, thresholds: Thresholds | None = None) -> None:
        thresholds = thresholds or Thresholds()
        self.thresholds = thresholds
        self.detector = DroidVisualDetector(
            template_dir,
            rarity_threshold=thresholds.rarity_threshold,
            droid_threshold=thresholds.droid_threshold,
        )

    def detect(
        self,
        image_bgr: np.ndarray,
        *,
        screen_height: int | None = None,
        screen_width: int | None = None,
        known_scale: float | None = None,
        keep_normalized: bool = False,
    ) -> PipelineResult:
        candidates = find_candidate_rows(image_bgr)
        if known_scale is not None:
            scale, method = float(known_scale), "known"
        else:
            scale, method = estimate_scale(
                screen_height=screen_height,
                screen_width=screen_width,
                candidates=candidates,
                scale_min=self.thresholds.scale_min,
                scale_max=self.thresholds.scale_max,
            )
        normalized = normalize_band(image_bgr, scale)
        # Fast path: most live frames have no alerts at all. Every accepted
        # row must pass the spawn-phrase gate anyway, so a band without any
        # phrase-like white text can skip the expensive template pipeline.
        if not band_has_phrase_evidence(normalized.image):
            h, w = normalized.image.shape[:2]
            return PipelineResult(
                detections=[],
                scale=scale,
                scale_method=method,
                candidate_rows=len(candidates),
                normalized_shape=(h, w),
                meta={"skipped": "no-phrase-evidence"},
                normalized_image=normalized.image if keep_normalized else None,
            )
        extra_ys = phrase_row_seeds(normalized.image)
        detections = self.detector.detect(normalized.image, extra_row_ys=extra_ys)
        h, w = normalized.image.shape[:2]
        candidate_row_boxes = [
            (0, max(0, int(round(candidate.y0 / scale))), w, min(h, int(round(candidate.y1 / scale))))
            for candidate in candidates
        ]
        phrase_row_boxes = [
            (0, max(0, min(h - 1, int(y))), w, min(h, max(0, int(y)) + 44))
            for y in extra_ys
        ]
        return PipelineResult(
            detections=detections,
            scale=scale,
            scale_method=method,
            candidate_rows=len(candidates),
            normalized_shape=(h, w),
            meta={"column_drift": column_drift_report(w)},
            candidate_row_boxes=candidate_row_boxes,
            phrase_row_boxes=phrase_row_boxes,
            normalized_image=normalized.image if keep_normalized else None,
        )
