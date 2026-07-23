from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .classifier import Detection, DroidVisualDetector, column_drift_report
from .config import Thresholds
from .normalize import estimate_scale, normalize_band
from .row_finder import (
    analyze_phrase,
    band_has_phrase_evidence,
    find_candidate_rows,
    phrase_row_seeds,
    phrase_text_bands,
)

# Measured on reference-scale fixtures: the white spawn-phrase text bands are
# ~13px tall and stacked rows sit ~32-33px apart. Machines whose game UI is
# smaller/larger than the screen-size formula predicts (e.g. one 3440x1440
# ultrawide renders the HUD at ~0.84x) show up here as smaller bands/pitch.
REF_ROW_PITCH = 32.5
REF_PHRASE_BAND_H = 13.0


def _row_scale_ratio(bands: list[tuple[int, int]]) -> float | None:
    """How the measured phrase rows compare to reference scale (1.0 = as
    expected). Pitch between stacked rows is the precise signal; a single
    row falls back to the band height (quantized, ~8% granularity)."""
    starts = [start for start, _height in bands]
    pitches = [b - a for a, b in zip(starts, starts[1:])]
    pitches = [p for p in pitches if 20 <= p <= 45]
    if pitches:
        return float(np.median(pitches)) / REF_ROW_PITCH
    if bands:
        return float(np.median([height for _start, height in bands])) / REF_PHRASE_BAND_H
    return None


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
    # Alert-like rows the classifier dropped, with the reason (debug surface).
    rejections: list[dict] = field(default_factory=list)


class Pipeline:
    """Scale-normalize then classify: Tool V1's resolution-relative row finder
    measures the frame, the band is resized to the 44px-row reference scale,
    and the fixed-column detector runs on the result."""

    def __init__(
        self,
        template_dir: str | Path,
        thresholds: Thresholds | None = None,
        *,
        extra_checks: bool = False,
    ) -> None:
        thresholds = thresholds or Thresholds()
        self.thresholds = thresholds
        self.detector = DroidVisualDetector(
            template_dir,
            rarity_threshold=thresholds.rarity_threshold,
            droid_threshold=thresholds.droid_threshold,
            extra_checks=extra_checks,
        )

    def apply_thresholds(self, thresholds: Thresholds) -> None:
        """Hot-reload every threshold used by the pipeline and classifier."""
        self.thresholds = thresholds
        self.detector.rarity_threshold = thresholds.rarity_threshold
        self.detector.droid_threshold = thresholds.droid_threshold

    def detect(
        self,
        image_bgr: np.ndarray,
        *,
        screen_height: int | None = None,
        screen_width: int | None = None,
        known_scale: float | None = None,
        keep_normalized: bool = False,
    ) -> PipelineResult:
        # Candidate discovery is the expensive part of an empty-frame scan.
        # Live captures already have a screen-derived scale, so defer it until
        # the cheap phrase gate proves there is something worth classifying.
        candidates = []
        candidates_needed_for_scale = known_scale is None and screen_height is None
        if candidates_needed_for_scale:
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
        phrase_analysis = analyze_phrase(normalized)
        # Self-calibration: if the phrase rows measure noticeably smaller or
        # larger than reference, the screen-size scale formula is wrong for
        # this machine, renormalize at the measured scale. Kept only when
        # the corrected band re-measures at ~reference size.
        if known_scale is None:
            ratio = _row_scale_ratio(
                phrase_text_bands(normalized, analysis=phrase_analysis)
            )
            if ratio is not None and 0.5 <= ratio <= 1.5 and not (0.90 < ratio < 1.10):
                candidate_scale = scale * ratio
                renormalized = normalize_band(image_bgr, candidate_scale)
                renormalized_phrase_analysis = analyze_phrase(renormalized)
                check_ratio = _row_scale_ratio(
                    phrase_text_bands(
                        renormalized,
                        analysis=renormalized_phrase_analysis,
                    )
                )
                if check_ratio is not None and 0.90 <= check_ratio <= 1.10:
                    scale = candidate_scale
                    normalized = renormalized
                    phrase_analysis = renormalized_phrase_analysis
                    method = f"{method}+row-pitch"
        # Fast path: most live frames have no alerts at all. Every accepted
        # row must pass the spawn-phrase gate anyway, so a band without any
        # phrase-like white text can skip the expensive template pipeline.
        if not band_has_phrase_evidence(normalized, analysis=phrase_analysis):
            h, w = normalized.shape[:2]
            return PipelineResult(
                detections=[],
                scale=scale,
                scale_method=method,
                candidate_rows=len(candidates),
                normalized_shape=(h, w),
                meta={"skipped": "no-phrase-evidence"},
                normalized_image=normalized if keep_normalized else None,
            )
        if not candidates:
            candidates = find_candidate_rows(image_bgr)
            # Candidate rows only affect the diagnostic method when screen
            # dimensions already supplied the scale; preserve that metadata.
            if known_scale is None and screen_height is not None:
                _same_scale, candidate_method = estimate_scale(
                    screen_height=screen_height,
                    screen_width=screen_width,
                    candidates=candidates,
                    scale_min=self.thresholds.scale_min,
                    scale_max=self.thresholds.scale_max,
                )
                method = (
                    f"{candidate_method}+row-pitch"
                    if method.endswith("+row-pitch")
                    else candidate_method
                )
        extra_ys = phrase_row_seeds(normalized, analysis=phrase_analysis)
        detections = self.detector.detect(normalized, extra_row_ys=extra_ys)
        h, w = normalized.shape[:2]
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
            normalized_image=normalized if keep_normalized else None,
            rejections=list(self.detector.last_rejections),
        )
