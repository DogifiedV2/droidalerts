from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
import time

import cv2
import numpy as np

from .learned_identity import (
    LearnedIdentityModel,
    LearnedIdentityResult,
    UNKNOWN_IDENTITY,
)
from .models import CardCandidate, CardContext, CardFrameResult
from .names import droid_class
from .template_recognition import (
    BeltTemplateIndex,
    TemplateCardRecognizer,
    TemplateRecognitionConfig,
    identity_minimum_margin,
    identity_features,
)


@dataclass(frozen=True)
class ScaleRecognitionConfig:
    """Bounds for the slower two-dimensional card search."""

    minimum_card_height: int = 48
    minimum_card_height_ratio: float = 0.12
    maximum_card_height_ratio: float = 0.96
    scale_factor: float = 1.13
    position_step_ratio: float = 0.025
    proposals_per_scale: int = 10
    small_card_proposals_per_scale: int = 24
    small_card_height: int = 100
    maximum_evidence_proposals: int = 320
    maximum_output_candidates: int = 24
    minimum_nameplate_dark_fraction: float = 0.28
    minimum_nameplate_side_contrast: float = 0.07
    nameplate_dark_value: int = 45
    minimum_identity_similarity: float = 0.80
    minimum_identity_margin: float = 0.060
    minimum_art_standard_deviation: float = 18.0
    learned_conflict_confidence: float = 0.88
    learned_conflict_margin: float = 0.30
    maximum_learned_candidates: int = 24


@dataclass(frozen=True)
class _EvidenceProposal:
    evidence: float
    dark_fraction: float
    side_contrast: float
    card_box: tuple[int, int, int, int]


@dataclass(frozen=True)
class _IdentityProposal:
    evidence: _EvidenceProposal
    name: str
    runner_up_name: str
    similarity: float
    margin: float
    accepted: bool
    reason: str
    learned_name: str = ""
    learned_confidence: float = 0.0
    learned_margin: float = 0.0


class ScaleInvariantCardRecognizer:
    """Find cards at independent positions and scales.

    The existing recognizer is faster and remains the primary path. This
    bounded search is intended to run periodically so camera zoom, letterbox
    padding, and non-standard resolutions cannot force every card to share one
    global height.
    """

    detector_name = "templates-scale"

    def __init__(
        self,
        index: BeltTemplateIndex | None = None,
        *,
        index_path: str | Path | None = None,
        template_config: TemplateRecognitionConfig | None = None,
        config: ScaleRecognitionConfig | None = None,
        learned_model: LearnedIdentityModel | None = None,
        load_learned_model: bool = True,
        classify_rejected_attributes: bool = False,
    ) -> None:
        started = time.perf_counter()
        self.index = index or BeltTemplateIndex.load(index_path)
        self.template_config = template_config or TemplateRecognitionConfig()
        self.config = config or ScaleRecognitionConfig(
            minimum_identity_similarity=self.template_config.minimum_identity_similarity,
            minimum_identity_margin=self.template_config.minimum_identity_margin,
            minimum_nameplate_dark_fraction=min(
                self.template_config.minimum_nameplate_dark_fraction,
                0.28,
            ),
            minimum_nameplate_side_contrast=min(
                self.template_config.minimum_nameplate_side_contrast,
                0.07,
            ),
            nameplate_dark_value=self.template_config.nameplate_dark_value,
        )
        self.family_recognizer = TemplateCardRecognizer(
            self.index,
            config=self.template_config,
        )
        self.classify_rejected_attributes = bool(classify_rejected_attributes)
        self.learned_model = learned_model
        self.learned_model_status = "provided" if learned_model is not None else "disabled"
        if learned_model is None and load_learned_model:
            try:
                self.learned_model = LearnedIdentityModel()
                self.learned_model_status = "loaded"
            except RuntimeError as exc:
                self.learned_model_status = f"unavailable:{exc}"
        self.init_seconds = time.perf_counter() - started

    def analyze(self, frame_bgr: np.ndarray) -> CardFrameResult:
        started = time.perf_counter()
        self._validate_frame(frame_bgr)
        frame_height, frame_width = frame_bgr.shape[:2]
        if frame_height < 48 or frame_width < 80:
            return CardFrameResult(
                (),
                {
                    "detector": self.detector_name,
                    "frame_shape": list(frame_bgr.shape),
                    "reason": "region_too_small",
                    "accepted_count": 0,
                    "total_seconds": time.perf_counter() - started,
                },
            )

        evidence_started = time.perf_counter()
        evidence = self._find_nameplate_evidence(frame_bgr)
        evidence_completed = time.perf_counter()
        identities = self._identify(frame_bgr, evidence)
        identity_completed = time.perf_counter()
        selected = self._keep_physical_peaks(identities)
        candidates = tuple(self._candidate(frame_bgr, proposal) for proposal in selected)
        completed = time.perf_counter()
        return CardFrameResult(
            candidates,
            {
                "detector": self.detector_name,
                "frame_shape": list(frame_bgr.shape),
                "card_window_count": len(evidence),
                "proposal_count": len(identities),
                "accepted_count": sum(item.accepted for item in candidates),
                "ambiguous_count": sum(
                    item.reason
                    in {
                        "ambiguous_template_identity",
                        "learned_identity_conflict",
                    }
                    for item in candidates
                ),
                "scale_count": len(self._card_heights(frame_height)),
                "learned_model": self.learned_model_status,
                "evidence_seconds": evidence_completed - evidence_started,
                "identity_match_seconds": identity_completed - evidence_completed,
                "attribute_seconds": completed - identity_completed,
                "total_seconds": completed - started,
            },
        )

    def recognize(self, frame_bgr: np.ndarray):
        return self.analyze(frame_bgr).observations

    @staticmethod
    def _validate_frame(frame_bgr: np.ndarray) -> None:
        if (
            not isinstance(frame_bgr, np.ndarray)
            or frame_bgr.ndim != 3
            or frame_bgr.shape[2] != 3
            or frame_bgr.size == 0
        ):
            raise ValueError(
                "ScaleInvariantCardRecognizer requires a non-empty HxWx3 BGR frame"
            )

    def _card_heights(self, frame_height: int) -> tuple[int, ...]:
        minimum = max(
            self.config.minimum_card_height,
            round(frame_height * self.config.minimum_card_height_ratio),
        )
        maximum = min(
            frame_height - 2,
            round(frame_height * self.config.maximum_card_height_ratio),
        )
        if minimum > maximum:
            return ()
        heights: list[int] = []
        value = float(minimum)
        while value <= maximum:
            heights.append(round(value))
            value *= self.config.scale_factor
        if heights and heights[-1] < maximum * 0.92:
            heights.append(maximum)
        return tuple(sorted(set(heights)))

    def _find_nameplate_evidence(
        self,
        frame_bgr: np.ndarray,
    ) -> list[_EvidenceProposal]:
        frame_height, frame_width = frame_bgr.shape[:2]
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        dark = (gray < self.config.nameplate_dark_value).astype(np.uint8)
        integral = cv2.integral(dark, sdepth=cv2.CV_32S)
        proposals: list[_EvidenceProposal] = []

        for height in self._card_heights(frame_height):
            card_width = round(height * self.index.card_width_ratio)
            if card_width >= frame_width - 2:
                continue
            art_left = round(height * self.index.art_left_ratio)
            art_width = round(height * self.index.art_width_ratio)
            band_top = round(height * self.template_config.nameplate_top_ratio)
            band_bottom = max(
                band_top + 2,
                round(height * self.template_config.nameplate_bottom_ratio),
            )
            step = max(2, round(height * self.config.position_step_ratio))
            x_positions = np.arange(
                1,
                frame_width - card_width - 1,
                step,
                dtype=np.int32,
            )
            y_positions = np.arange(
                1,
                frame_height - height - 1,
                step,
                dtype=np.int32,
            )
            if not len(x_positions) or not len(y_positions):
                continue

            name_x1 = x_positions + art_left
            name_x2 = x_positions + art_left + art_width
            name_y1 = y_positions + band_top
            name_y2 = y_positions + band_bottom
            band_height = np.maximum(1, name_y2 - name_y1)
            name_dark = _rectangle_sums(
                integral,
                name_x1,
                name_y1,
                name_x2,
                name_y2,
            )
            name_area = np.maximum(
                1,
                (name_x2 - name_x1)[None, :] * band_height[:, None],
            )
            dark_fraction = name_dark / name_area

            left_x1 = x_positions
            left_x2 = x_positions + art_left
            right_x1 = x_positions + art_left + art_width
            right_x2 = x_positions + card_width
            left_area = np.maximum(
                1,
                (left_x2 - left_x1)[None, :] * band_height[:, None],
            )
            right_area = np.maximum(
                1,
                (right_x2 - right_x1)[None, :] * band_height[:, None],
            )
            left_dark = (
                _rectangle_sums(
                    integral,
                    left_x1,
                    name_y1,
                    left_x2,
                    name_y2,
                )
                / left_area
            )
            right_dark = (
                _rectangle_sums(
                    integral,
                    right_x1,
                    name_y1,
                    right_x2,
                    name_y2,
                )
                / right_area
            )
            side_contrast = dark_fraction - (left_dark + right_dark) / 2.0
            valid = (
                dark_fraction >= self.config.minimum_nameplate_dark_fraction
            ) & (
                side_contrast >= self.config.minimum_nameplate_side_contrast
            )
            if not np.any(valid):
                continue

            evidence_score = np.where(
                valid,
                dark_fraction + side_contrast * 0.8,
                0.0,
            ).astype(np.float32)
            local_size = max(3, round((height * 0.18) / step))
            if local_size % 2 == 0:
                local_size += 1
            local_maximum = cv2.dilate(
                evidence_score,
                np.ones((local_size, local_size), dtype=np.uint8),
            )
            y_indices, x_indices = np.where(
                valid & (evidence_score >= local_maximum - 1e-6)
            )
            if not len(x_indices):
                continue
            values = evidence_score[y_indices, x_indices]
            proposal_limit = (
                self.config.small_card_proposals_per_scale
                if height <= self.config.small_card_height
                else self.config.proposals_per_scale
            )
            order = np.argsort(values)[::-1][:proposal_limit]
            for result_index in order:
                y_index = int(y_indices[result_index])
                x_index = int(x_indices[result_index])
                proposals.append(
                    _EvidenceProposal(
                        evidence=float(evidence_score[y_index, x_index]),
                        dark_fraction=float(dark_fraction[y_index, x_index]),
                        side_contrast=float(side_contrast[y_index, x_index]),
                        card_box=(
                            int(x_positions[x_index]),
                            int(y_positions[y_index]),
                            card_width,
                            height,
                        ),
                    )
                )

        proposals.sort(key=lambda item: item.evidence, reverse=True)
        return proposals[: self.config.maximum_evidence_proposals]

    def _identify(
        self,
        frame_bgr: np.ndarray,
        evidence: list[_EvidenceProposal],
    ) -> list[_IdentityProposal]:
        valid_evidence: list[_EvidenceProposal] = []
        artwork: list[np.ndarray] = []
        descriptors: list[np.ndarray] = []
        for proposal in evidence:
            art, _art_box = self._art_crop(frame_bgr, proposal.card_box)
            if art.size == 0 or float(np.std(art)) < self.config.minimum_art_standard_deviation:
                continue
            valid_evidence.append(proposal)
            artwork.append(art)
            descriptors.append(identity_features(art))
        if not descriptors:
            return []

        queries = np.stack(descriptors)
        template_scores = queries @ self.index.identity_hog.T
        name_scores = np.maximum.reduceat(
            template_scores,
            self.index.identity_name_offsets[:-1],
            axis=1,
        )
        best_name_indices = np.argmax(name_scores, axis=1)
        best_scores = name_scores[np.arange(len(name_scores)), best_name_indices]
        runner_scores = name_scores.copy()
        runner_scores[np.arange(len(runner_scores)), best_name_indices] = -1.0
        runner_indices = np.argmax(runner_scores, axis=1)
        margins = (
            best_scores
            - runner_scores[np.arange(len(runner_scores)), runner_indices]
        )

        learned_results: dict[int, LearnedIdentityResult] = {}
        if self.learned_model is not None:
            eligible = [
                index
                for index in range(len(valid_evidence))
                if (
                    float(best_scores[index])
                    >= self.config.minimum_identity_similarity
                    and float(margins[index])
                    >= identity_minimum_margin(
                        self.index.identity_names[
                            int(best_name_indices[index])
                        ],
                        self.config.minimum_identity_margin,
                    )
                )
            ]
            eligible.sort(
                key=lambda index: (
                    float(best_scores[index]),
                    float(margins[index]),
                ),
                reverse=True,
            )
            eligible = eligible[: self.config.maximum_learned_candidates]
            try:
                predictions = self.learned_model.predict(
                    [artwork[index] for index in eligible]
                )
                learned_results = dict(zip(eligible, predictions))
            except RuntimeError as exc:
                self.learned_model = None
                self.learned_model_status = f"failed:{exc}"

        results: list[_IdentityProposal] = []
        for index, source in enumerate(valid_evidence):
            name = self.index.identity_names[int(best_name_indices[index])]
            runner_up = self.index.identity_names[int(runner_indices[index])]
            similarity = float(best_scores[index])
            margin = float(margins[index])
            accepted = True
            reason = "accepted_scale_template"
            if similarity < self.config.minimum_identity_similarity:
                accepted = False
                reason = "low_template_similarity"
            elif margin < identity_minimum_margin(
                name,
                self.config.minimum_identity_margin,
            ):
                accepted = False
                reason = "ambiguous_template_identity"

            learned_name = ""
            learned_confidence = 0.0
            learned_margin = 0.0
            if index in learned_results:
                learned = learned_results[index]
                learned_name = learned.name
                learned_confidence = learned.confidence
                learned_margin = learned.margin
                strong_learned = (
                    learned.name != UNKNOWN_IDENTITY
                    and learned.confidence >= self.config.learned_conflict_confidence
                    and learned.margin >= self.config.learned_conflict_margin
                )
                if accepted and strong_learned and learned.name != name:
                    accepted = False
                    reason = "learned_identity_conflict"

            results.append(
                _IdentityProposal(
                    evidence=source,
                    name=name,
                    runner_up_name=runner_up,
                    similarity=similarity,
                    margin=margin,
                    accepted=accepted,
                    reason=reason,
                    learned_name=learned_name,
                    learned_confidence=learned_confidence,
                    learned_margin=learned_margin,
                )
            )
        return results

    def _keep_physical_peaks(
        self,
        proposals: list[_IdentityProposal],
    ) -> list[_IdentityProposal]:
        ordered = sorted(
            proposals,
            key=lambda item: (
                item.accepted,
                item.similarity,
                item.margin,
                item.evidence.evidence,
            ),
            reverse=True,
        )
        kept: list[_IdentityProposal] = []
        for proposal in ordered:
            overlapping = [
                previous
                for previous in kept
                if _same_physical_card(
                    proposal.evidence.card_box,
                    previous.evidence.card_box,
                )
            ]
            if overlapping:
                if proposal.accepted:
                    accepted_conflicts = [
                        previous
                        for previous in overlapping
                        if (
                            previous.accepted
                            and previous.name != proposal.name
                            and _similar_card_geometry(
                                proposal.evidence.card_box,
                                previous.evidence.card_box,
                            )
                        )
                    ]
                    if accepted_conflicts:
                        conflict = accepted_conflicts[0]
                        if abs(proposal.similarity - conflict.similarity) <= 0.025:
                            conflict_index = kept.index(conflict)
                            kept[conflict_index] = replace(
                                conflict,
                                accepted=False,
                                reason="conflicting_scale_identity",
                            )
                continue
            kept.append(proposal)
            if len(kept) >= self.config.maximum_output_candidates:
                break
        return sorted(
            kept,
            key=lambda item: (
                item.evidence.card_box[0],
                item.evidence.card_box[1],
            ),
        )

    def _candidate(
        self,
        frame_bgr: np.ndarray,
        proposal: _IdentityProposal,
    ) -> CardCandidate:
        card_box = proposal.evidence.card_box
        art, art_box = self._art_crop(frame_bgr, card_box)
        x, y, width, height = card_box
        name_height = max(8, round(height * self.template_config.name_height_ratio))
        name_box = (
            art_box[0],
            y + round(height * self.template_config.name_y_ratio),
            max(name_height, round(width * 0.62)),
            name_height,
        )
        accepted = proposal.accepted
        reason = proposal.reason
        frame_height, frame_width = frame_bgr.shape[:2]
        if (
            x <= 0
            or y <= 0
            or x + width >= frame_width
            or y + height >= frame_height
        ):
            accepted = False
            reason = "card_touches_region_edge"

        family_result = None
        rarity = ""
        rarity_confidence = 0.0
        if accepted or self.classify_rejected_attributes:
            family_result = self.family_recognizer.classify_family_details(
                frame_bgr,
                name_box,
                card_box,
            )
        if accepted:
            rarity = droid_class(proposal.name)
            rarity_confidence = 1.0 if rarity else 0.0
        confidence = min(
            0.99,
            0.75
            + max(
                0.0,
                proposal.similarity - self.config.minimum_identity_similarity,
            )
            * 0.80
            + max(0.0, proposal.margin) * 1.50,
        )
        edge_density = 0.0
        if art.size:
            gray = cv2.cvtColor(art, cv2.COLOR_BGR2GRAY)
            edge_density = float(np.mean(cv2.Canny(gray, 80, 180) > 0))
        context = CardContext(
            art_box=art_box,
            card_box=card_box,
            nameplate_dark_fraction=proposal.evidence.dark_fraction,
            art_standard_deviation=float(np.std(art)) if art.size else 0.0,
            art_edge_density=edge_density,
            frame_line_ratio=proposal.evidence.side_contrast,
            accepted=accepted,
            reason=reason,
        )
        return CardCandidate(
            canonical_name=proposal.name,
            raw_text=f"template:scale:{proposal.name}",
            identity_confidence=confidence,
            name_box=name_box,
            context=context,
            accepted=accepted,
            reason=reason,
            family=family_result.family if family_result is not None else "",
            family_confidence=(
                family_result.confidence if family_result is not None else 0.0
            ),
            rarity=rarity,
            rarity_confidence=rarity_confidence,
            raw_best_similarity=proposal.similarity,
            runner_up_identity=proposal.runner_up_name,
            identity_margin=proposal.margin,
            family_best_similarity=(
                family_result.best_similarity if family_result is not None else 0.0
            ),
            runner_up_family=(
                family_result.runner_up_family if family_result is not None else ""
            ),
            family_margin=family_result.margin if family_result is not None else 0.0,
        )

    def _art_crop(
        self,
        frame_bgr: np.ndarray,
        card_box: tuple[int, int, int, int],
    ) -> tuple[np.ndarray, tuple[int, int, int, int]]:
        x, y, _width, height = card_box
        art_x = x + round(height * self.index.art_left_ratio)
        art_y = y + round(height * self.index.art_top_ratio)
        art_width = max(16, round(height * self.index.art_width_ratio))
        art_height = max(16, round(height * self.index.art_height_ratio))
        frame_height, frame_width = frame_bgr.shape[:2]
        art_right = min(frame_width, art_x + art_width)
        art_bottom = min(frame_height, art_y + art_height)
        art_x = max(0, art_x)
        art_y = max(0, art_y)
        box = (
            art_x,
            art_y,
            max(0, art_right - art_x),
            max(0, art_bottom - art_y),
        )
        return (
            frame_bgr[art_y:art_bottom, art_x:art_right],
            box,
        )


class HybridCardRecognizer:
    """Combine fast aligned matching with periodic scale recovery."""

    detector_name = "hybrid-v2"

    def __init__(
        self,
        index: BeltTemplateIndex | None = None,
        *,
        index_path: str | Path | None = None,
        template_config: TemplateRecognitionConfig | None = None,
        scale_config: ScaleRecognitionConfig | None = None,
        scale_scan_interval_seconds: float = 0.50,
        fast_recognizer: TemplateCardRecognizer | None = None,
        scale_recognizer: ScaleInvariantCardRecognizer | None = None,
        load_learned_model: bool = True,
        maximum_scale_cpu_fraction: float = 0.25,
        classify_rejected_attributes: bool = False,
    ) -> None:
        started = time.perf_counter()
        shared_index = index
        if shared_index is None and fast_recognizer is None and scale_recognizer is None:
            shared_index = BeltTemplateIndex.load(index_path)
        self.fast_recognizer = fast_recognizer or TemplateCardRecognizer(
            shared_index,
            index_path=index_path if shared_index is None else None,
            config=template_config,
            geometry_search_enabled=False,
            classify_rejected_attributes=classify_rejected_attributes,
        )
        self.scale_recognizer = scale_recognizer or ScaleInvariantCardRecognizer(
            shared_index or self.fast_recognizer.index,
            template_config=template_config,
            config=scale_config,
            load_learned_model=load_learned_model,
            classify_rejected_attributes=classify_rejected_attributes,
        )
        self.scale_scan_interval_seconds = max(
            0.05,
            float(scale_scan_interval_seconds),
        )
        self.maximum_scale_cpu_fraction = min(
            1.0,
            max(0.05, float(maximum_scale_cpu_fraction)),
        )
        self._last_scale_scan_at = float("-inf")
        self._last_scale_scan_seconds = 0.0
        self.init_seconds = time.perf_counter() - started

    def analyze(
        self,
        frame_bgr: np.ndarray,
        *,
        now: float | None = None,
        force_scale: bool = False,
    ) -> CardFrameResult:
        started = time.perf_counter()
        timestamp = time.monotonic() if now is None else float(now)
        fast_result = self.fast_recognizer.analyze(frame_bgr)
        effective_scale_interval = max(
            self.scale_scan_interval_seconds,
            self._last_scale_scan_seconds / self.maximum_scale_cpu_fraction,
        )
        run_scale = force_scale or (
            timestamp - self._last_scale_scan_at >= effective_scale_interval
        )
        scale_result = CardFrameResult((), {})
        if run_scale:
            self._last_scale_scan_at = timestamp
            scale_started = time.perf_counter()
            scale_result = self.scale_recognizer.analyze(frame_bgr)
            self._last_scale_scan_seconds = time.perf_counter() - scale_started
            accepted_scale = [
                candidate
                for candidate in scale_result.candidates
                if candidate.accepted
            ]
            if accepted_scale and hasattr(
                self.fast_recognizer,
                "set_card_geometry",
            ):
                ordered = sorted(
                    accepted_scale,
                    key=lambda item: item.context.card_box[3],
                )
                representative = ordered[len(ordered) // 2]
                self.fast_recognizer.set_card_geometry(
                    representative.context.card_box,
                    frame_bgr.shape,
                )

        candidates = _merge_candidates(
            fast_result.candidates,
            scale_result.candidates,
        )
        completed = time.perf_counter()
        diagnostics = {
            "detector": self.detector_name,
            "frame_shape": list(frame_bgr.shape),
            # Raw multi-scale nameplate evidence exists in many HUD elements.
            # Only accepted scale cards should keep the high-rate scheduler
            # active.
            "card_window_count": int(
                fast_result.diagnostics.get("card_window_count", 0) or 0
            )
            + sum(item.accepted for item in scale_result.candidates),
            "proposal_count": len(candidates),
            "accepted_count": sum(item.accepted for item in candidates),
            "ambiguous_count": sum(
                item.reason
                in {
                    "ambiguous_template_identity",
                    "conflicting_detector_identity",
                    "conflicting_scale_identity",
                    "learned_identity_conflict",
                }
                for item in candidates
            ),
            "scale_scan_ran": run_scale,
            "scale_scan_interval_seconds": effective_scale_interval,
            "scale_cpu_budget_fraction": self.maximum_scale_cpu_fraction,
            "fast": fast_result.diagnostics,
            "scale": scale_result.diagnostics,
            "total_seconds": completed - started,
        }
        return CardFrameResult(candidates, diagnostics)

    def recognize(self, frame_bgr: np.ndarray):
        return self.analyze(frame_bgr).observations


def _rectangle_sums(
    integral: np.ndarray,
    x1: np.ndarray,
    y1: np.ndarray,
    x2: np.ndarray,
    y2: np.ndarray,
) -> np.ndarray:
    return (
        integral[y2[:, None], x2[None, :]]
        - integral[y1[:, None], x2[None, :]]
        - integral[y2[:, None], x1[None, :]]
        + integral[y1[:, None], x1[None, :]]
    )


def _intersection_over_union(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    if not intersection:
        return 0.0
    union = first[2] * first[3] + second[2] * second[3] - intersection
    return intersection / union if union > 0 else 0.0


def _same_physical_card(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    if _intersection_over_union(first, second) >= 0.25:
        return True
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    smaller_area = min(first[2] * first[3], second[2] * second[3])
    if smaller_area > 0 and intersection / smaller_area >= 0.55:
        return True
    first_center = (first[0] + first[2] / 2, first[1] + first[3] / 2)
    second_center = (second[0] + second[2] / 2, second[1] + second[3] / 2)
    distance = math.hypot(
        first_center[0] - second_center[0],
        first_center[1] - second_center[1],
    )
    return distance < min(first[3], second[3]) * 0.32


def _similar_card_geometry(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    first_area = first[2] * first[3]
    second_area = second[2] * second[3]
    if first_area <= 0 or second_area <= 0:
        return False
    area_ratio = min(first_area, second_area) / max(first_area, second_area)
    return area_ratio >= 0.60 and _intersection_over_union(first, second) >= 0.20


def _merge_candidates(
    fast_candidates: tuple[CardCandidate, ...],
    scale_candidates: tuple[CardCandidate, ...],
) -> tuple[CardCandidate, ...]:
    merged: list[CardCandidate] = list(fast_candidates)
    for scale_candidate in scale_candidates:
        overlaps = [
            (index, candidate)
            for index, candidate in enumerate(merged)
            if _same_physical_card(
                scale_candidate.context.card_box,
                candidate.context.card_box,
            )
        ]
        accepted_overlaps = [
            item for item in overlaps if item[1].accepted
        ]
        credible_accepted_overlaps = [
            item
            for item in accepted_overlaps
            if _similar_card_geometry(
                scale_candidate.context.card_box,
                item[1].context.card_box,
            )
        ]
        if (
            not scale_candidate.accepted
            and scale_candidate.reason
            in {
                "conflicting_scale_identity",
                "learned_identity_conflict",
            }
            and credible_accepted_overlaps
        ):
            for index, existing in credible_accepted_overlaps:
                merged[index] = _rejected_candidate(
                    existing,
                    scale_candidate.reason,
                )
            merged.append(scale_candidate)
            continue
        if scale_candidate.accepted and accepted_overlaps:
            same_identity = [
                item
                for item in accepted_overlaps
                if item[1].canonical_name == scale_candidate.canonical_name
            ]
            if same_identity:
                index, existing = same_identity[0]
                existing_score = (
                    existing.raw_best_similarity + existing.identity_margin * 0.5
                )
                scale_score = (
                    scale_candidate.raw_best_similarity
                    + scale_candidate.identity_margin * 0.5
                )
                if scale_score > existing_score:
                    merged[index] = scale_candidate
                continue
            if not credible_accepted_overlaps:
                continue
            for index, existing in credible_accepted_overlaps:
                merged[index] = _rejected_candidate(
                    existing,
                    "conflicting_detector_identity",
                )
            merged.append(
                _rejected_candidate(
                    scale_candidate,
                    "conflicting_detector_identity",
                )
            )
            continue
        if scale_candidate.accepted:
            rejected_overlap_indices = {
                index
                for index, candidate in overlaps
                if not candidate.accepted
            }
            if rejected_overlap_indices:
                merged = [
                    candidate
                    for index, candidate in enumerate(merged)
                    if index not in rejected_overlap_indices
                ]
            merged.append(scale_candidate)
            continue
        if overlaps:
            continue
        merged.append(scale_candidate)

    accepted = [item for item in merged if item.accepted]
    rejected = sorted(
        (item for item in merged if not item.accepted),
        key=lambda item: (
            item.raw_best_similarity,
            item.identity_margin,
        ),
        reverse=True,
    )[:16]
    return tuple(
        sorted(
            accepted + rejected,
            key=lambda item: (
                item.context.card_box[0],
                item.context.card_box[1],
                not item.accepted,
            ),
        )
    )


def _rejected_candidate(
    candidate: CardCandidate,
    reason: str,
) -> CardCandidate:
    return replace(
        candidate,
        accepted=False,
        reason=reason,
        context=replace(
            candidate.context,
            accepted=False,
            reason=reason,
        ),
    )
