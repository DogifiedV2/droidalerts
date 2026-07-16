from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import time

import cv2
import numpy as np

from ..config import templates_dir
from .names import DROID_NAMES, droid_class
from .recognition import (
    CARD_FAMILIES,
    CardCandidate,
    CardContext,
    CardFrameResult,
    classify_card_family_border,
)


INDEX_VERSION = 1
INDEX_FILE = "belt_blueprints.npz"

HOG_WIDTH = 48
HOG_HEIGHT = 48
HOG_STRIDE = 2

FAMILY_HISTOGRAM_WEIGHT = 0.20
FAMILY_WORD_WEIGHT = 0.80

_HOG = cv2.HOGDescriptor(
    (HOG_WIDTH, HOG_HEIGHT),
    (16, 16),
    (8, 8),
    (8, 8),
    9,
)
_FAMILY_WORD_HOG = cv2.HOGDescriptor(
    (96, 24),
    (16, 16),
    (8, 8),
    (8, 8),
    9,
)

_FAMILY_ORDER = ("Default", "Gold", "Diamond", "Rainbow", "Beskar")
_FAMILY_MINIMUM_MARGINS = {
    # Default is the most dangerous fallback: Gold/Beskar captures from the
    # audited bad-connection run repeatedly landed only 0.02-0.04 ahead of it.
    # Abstain instead of publishing a confident but wrong Default family.
    "Default": 0.050,
    "Gold": 0.005,
    "Diamond": 0.030,
    "Rainbow": 0.015,
    # Default and Beskar are both low-saturation gray cards. A tighter margin
    # keeps a held low-FPS Default frame from becoming a false Beskar alert;
    # later, clearer frames can still attach the family to the confirmed track.
    "Beskar": 0.020,
}

_DISTINCTIVE_BORDER_MIN_CONFIDENCE = 0.68
_IDENTITY_MINIMUM_MARGINS = {
    # Several real R3 appearances were emitted as R9 at 0.034-0.062 margin.
    # Keep this pair stricter than the global floor; missing one is safer than
    # changing both its identity and its fixed Common/Rare class.
    "R3": 0.070,
    "R9": 0.070,
}

# Belt regions can include price labels above the cards and conveyor scenery
# below them. Probe a small set of heights at top, center, and bottom anchors,
# then keep the winning geometry for normal single-pass scans.
_GEOMETRY_HEIGHT_RATIOS = (1.0, 0.84, 0.76, 0.72, 0.65, 0.58)
_GEOMETRY_VERTICAL_ANCHORS = (0.0, 0.25, 0.45, 0.5, 0.75, 1.0)
_GEOMETRY_RETRY_MISSES = 8


def belt_template_index_path() -> Path:
    return templates_dir() / INDEX_FILE


def _unit_vector(values: np.ndarray) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if norm > 1e-6:
        vector /= norm
    return vector


def identity_features(art_bgr: np.ndarray) -> np.ndarray:
    """Return the scale-normalized descriptor used by the live scan."""

    if not isinstance(art_bgr, np.ndarray) or art_bgr.size == 0:
        raise ValueError("Blueprint artwork cannot be empty")
    gray = (
        cv2.cvtColor(art_bgr, cv2.COLOR_BGR2GRAY)
        if art_bgr.ndim == 3
        else np.asarray(art_bgr, dtype=np.uint8)
    )
    hog_image = cv2.resize(
        gray,
        (HOG_WIDTH, HOG_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )
    return _unit_vector(_HOG.compute(hog_image))


def _family_border_mask() -> np.ndarray:
    y, x = np.mgrid[:48, :48]
    return (x < 10) | (x >= 39) | (y < 5) | (y >= 40) | ((y >= 29) & (y < 34))


_FAMILY_BORDER_MASK = _family_border_mask()


def family_features(card_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Describe only the family frame and its small printed family label."""

    if not isinstance(card_bgr, np.ndarray) or card_bgr.ndim != 3 or card_bgr.size == 0:
        raise ValueError("Blueprint card cannot be empty")

    normalized = cv2.resize(card_bgr, (48, 48), interpolation=cv2.INTER_AREA)
    hsv_pixels = cv2.cvtColor(normalized, cv2.COLOR_BGR2HSV)[_FAMILY_BORDER_MASK]
    histogram = np.concatenate(
        (
            np.histogram(hsv_pixels[:, 0], bins=18, range=(0, 180))[0],
            np.histogram(hsv_pixels[:, 1], bins=8, range=(0, 256))[0],
            np.histogram(hsv_pixels[:, 2], bins=8, range=(0, 256))[0],
        )
    ).astype(np.float32)

    gray = cv2.cvtColor(card_bgr, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    word = gray[
        round(height * 0.70) : max(round(height * 0.70) + 1, round(height * 0.79)),
        round(width * 0.17) : max(round(width * 0.17) + 1, round(width * 0.45)),
    ]
    word = cv2.resize(word, (96, 24), interpolation=cv2.INTER_CUBIC)
    word = cv2.normalize(word, None, 0, 255, cv2.NORM_MINMAX)
    return _unit_vector(histogram), _unit_vector(_FAMILY_WORD_HOG.compute(word))


@dataclass(frozen=True)
class BeltTemplateIndex:
    identity_hog: np.ndarray
    identity_names: tuple[str, ...]
    identity_name_offsets: np.ndarray
    family_histograms: np.ndarray
    family_words: np.ndarray
    family_labels: tuple[str, ...]
    family_offsets: np.ndarray
    card_width_ratio: float
    art_left_ratio: float
    art_top_ratio: float
    art_width_ratio: float
    art_height_ratio: float

    @classmethod
    def load(cls, path: str | Path | None = None) -> "BeltTemplateIndex":
        index_path = Path(path) if path is not None else belt_template_index_path()
        try:
            archive = np.load(index_path, allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Blueprint template index could not be loaded: {index_path}") from exc

        try:
            version = int(np.asarray(archive["version"]).reshape(-1)[0])
            if version != INDEX_VERSION:
                raise RuntimeError(
                    f"Blueprint template index version {version} is unsupported; expected {INDEX_VERSION}"
                )
            index = cls(
                identity_hog=np.asarray(archive["identity_hog"], dtype=np.float32),
                identity_names=tuple(str(value) for value in archive["identity_names"].tolist()),
                identity_name_offsets=np.asarray(
                    archive["identity_name_offsets"], dtype=np.int32
                ),
                family_histograms=np.asarray(
                    archive["family_histograms"], dtype=np.float32
                ),
                family_words=np.asarray(archive["family_words"], dtype=np.float32),
                family_labels=tuple(str(value) for value in archive["family_labels"].tolist()),
                family_offsets=np.asarray(archive["family_offsets"], dtype=np.int32),
                card_width_ratio=float(archive["card_width_ratio"]),
                art_left_ratio=float(archive["art_left_ratio"]),
                art_top_ratio=float(archive["art_top_ratio"]),
                art_width_ratio=float(archive["art_width_ratio"]),
                art_height_ratio=float(archive["art_height_ratio"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Blueprint template index is incomplete: {index_path}") from exc
        finally:
            archive.close()
        index._validate(index_path)
        return index

    def _validate(self, path: Path) -> None:
        identity_count = self.identity_hog.shape[0]
        if (
            self.identity_hog.ndim != 2
            or identity_count < len(DROID_NAMES)
            or self.identity_hog.shape[1] != _HOG.getDescriptorSize()
        ):
            raise RuntimeError(f"Blueprint identity descriptors are invalid: {path}")
        if self.identity_names != tuple(DROID_NAMES):
            raise RuntimeError(f"Blueprint template index does not cover every known droid: {path}")
        if (
            self.identity_name_offsets.shape != (len(self.identity_names) + 1,)
            or int(self.identity_name_offsets[0]) != 0
            or int(self.identity_name_offsets[-1]) != identity_count
            or np.any(np.diff(self.identity_name_offsets) <= 0)
        ):
            raise RuntimeError(f"Blueprint identity offsets are invalid: {path}")

        family_count = self.family_histograms.shape[0]
        if (
            self.family_histograms.ndim != 2
            or self.family_words.ndim != 2
            or self.family_words.shape[0] != family_count
            or self.family_histograms.shape[1] != 34
            or self.family_words.shape[1] != _FAMILY_WORD_HOG.getDescriptorSize()
            or self.family_labels != _FAMILY_ORDER
            or self.family_offsets.shape != (len(self.family_labels) + 1,)
            or int(self.family_offsets[0]) != 0
            or int(self.family_offsets[-1]) != family_count
            or np.any(np.diff(self.family_offsets) <= 0)
        ):
            raise RuntimeError(f"Blueprint family descriptors are invalid: {path}")

        ratios = (
            self.card_width_ratio,
            self.art_left_ratio,
            self.art_top_ratio,
            self.art_width_ratio,
            self.art_height_ratio,
        )
        if not all(np.isfinite(value) and 0.01 < value < 2.0 for value in ratios):
            raise RuntimeError(f"Blueprint geometry is invalid: {path}")


@dataclass(frozen=True)
class TemplateRecognitionConfig:
    minimum_identity_similarity: float = 0.80
    # The retained stress captures contain repeatable 2BB/BB, DRK-1/VECT-ARM,
    # R3/R9, and PIT/ID10 confusions below this separation. It is safer to
    # wait for a clearer frame than publish a plausible but wrong identity.
    minimum_identity_margin: float = 0.060
    minimum_nameplate_dark_fraction: float = 0.33
    # A real card has a compact black nameplate surrounded by a lighter frame.
    # Full-width desktop/app panels can have a high dark fraction too, but lack
    # this side contrast (the audited false Epic measured only about 0.02).
    minimum_nameplate_side_contrast: float = 0.12
    nameplate_dark_value: int = 45
    nameplate_top_ratio: float = 0.62
    nameplate_bottom_ratio: float = 0.78
    minimum_proposal_spacing_ratio: float = 0.60
    name_y_ratio: float = 0.64
    name_height_ratio: float = 0.105


@dataclass(frozen=True)
class _Proposal:
    x: int
    name: str
    runner_up_name: str
    similarity: float
    margin: float
    dark_fraction: float
    accepted: bool
    reason: str


def _offset_box_y(
    box: tuple[int, int, int, int],
    offset: int,
) -> tuple[int, int, int, int]:
    x, y, width, height = box
    return x, y + offset, width, height


class TemplateCardRecognizer:
    """Find and identify every fully visible belt card without running OCR."""

    detector_name = "templates"

    def __init__(
        self,
        index: BeltTemplateIndex | None = None,
        *,
        index_path: str | Path | None = None,
        config: TemplateRecognitionConfig | None = None,
    ) -> None:
        started = time.perf_counter()
        self.index = index or BeltTemplateIndex.load(index_path)
        self.config = config or TemplateRecognitionConfig()
        self._geometry: tuple[float, float] | None = None
        self._geometry_misses = 0
        self.init_seconds = time.perf_counter() - started

    def analyze(self, frame_bgr: np.ndarray) -> CardFrameResult:
        started = time.perf_counter()
        if (
            not isinstance(frame_bgr, np.ndarray)
            or frame_bgr.ndim != 3
            or frame_bgr.shape[2] != 3
            or frame_bgr.size == 0
        ):
            raise ValueError("TemplateCardRecognizer requires a non-empty HxWx3 BGR frame")

        frame_height, frame_width = frame_bgr.shape[:2]
        if frame_height < 80 or frame_width < 80:
            return self._analyze_aligned(frame_bgr)

        if self._geometry is None:
            return self._search_geometry(frame_bgr, started=started)

        requested_ratio, vertical_anchor = self._geometry
        crop, crop_top, actual_ratio, actual_top_ratio = self._geometry_crop(
            frame_bgr,
            requested_ratio,
            vertical_anchor,
        )
        result = self._analyze_aligned(crop)
        accepted_count = self._safe_geometry_accepted_count(
            result,
            crop_top=crop_top,
            full_height=frame_height,
        )
        if accepted_count:
            self._geometry_misses = 0
        else:
            self._geometry_misses += 1
            if self._geometry_misses >= _GEOMETRY_RETRY_MISSES:
                return self._search_geometry(
                    frame_bgr,
                    started=started,
                )
        return self._geometry_result(
            result,
            frame_bgr,
            crop_top=crop_top,
            ratio=actual_ratio,
            top_ratio=actual_top_ratio,
            searched=False,
            started=started,
        )

    def _search_geometry(
        self,
        frame_bgr: np.ndarray,
        *,
        started: float,
    ) -> CardFrameResult:
        attempts: dict[
            tuple[int, int],
            tuple[float, float, int, float, CardFrameResult],
        ] = {}
        for requested_ratio in _GEOMETRY_HEIGHT_RATIOS:
            for vertical_anchor in _GEOMETRY_VERTICAL_ANCHORS:
                crop, crop_top, actual_ratio, actual_top_ratio = self._geometry_crop(
                    frame_bgr,
                    requested_ratio,
                    vertical_anchor,
                )
                key = crop.shape[0], crop_top
                if key in attempts:
                    continue
                attempts[key] = (
                    requested_ratio,
                    vertical_anchor,
                    crop_top,
                    actual_top_ratio,
                    self._analyze_aligned(crop),
                )

        attempt_values = list(attempts.values())
        accepted_attempts = [
            attempt
            for attempt in attempt_values
            if self._safe_geometry_accepted_count(
                attempt[4],
                crop_top=attempt[2],
                full_height=frame_bgr.shape[0],
            )
            > 0
        ]
        edge_spanning_attempts = [
            attempt
            for attempt in attempt_values
            if attempt[2] == 0
            and int(attempt[4].diagnostics.get("frame_shape", [0])[0])
            >= frame_bgr.shape[0]
            and int(attempt[4].diagnostics.get("accepted_count", 0)) > 0
        ]
        if edge_spanning_attempts:
            # If the unscaled full region already looks like a card, a smaller
            # internal retry would merely crop a card that touches the capture
            # boundary and pretend it is complete. Keep the edge-spanning
            # result so _geometry_result can quarantine it explicitly.
            selected = max(edge_spanning_attempts, key=self._geometry_score)
        elif accepted_attempts:
            selected = max(accepted_attempts, key=self._geometry_score)
        else:
            # An empty belt contains no evidence with which to calibrate. Keep
            # the full-height/default geometry until cards enter the region.
            selected = max(
                attempt_values,
                key=lambda attempt: attempt[4].diagnostics["frame_shape"][0],
            )

        requested_ratio, vertical_anchor, crop_top, top_ratio, result = selected
        actual_ratio = result.diagnostics["frame_shape"][0] / frame_bgr.shape[0]
        self._geometry = requested_ratio, vertical_anchor
        self._geometry_misses = 0
        summaries = []
        for _requested_ratio, attempt_anchor, attempt_top, attempt_top_ratio, attempt_result in sorted(
            attempt_values,
            key=lambda attempt: (-attempt[4].diagnostics["frame_shape"][0], attempt[2]),
        ):
            summaries.append(
                {
                    "height_ratio": round(
                        attempt_result.diagnostics["frame_shape"][0] / frame_bgr.shape[0],
                        4,
                    ),
                    "top_ratio": round(attempt_top_ratio, 4),
                    "vertical_anchor": attempt_anchor,
                    "accepted_count": int(
                        attempt_result.diagnostics.get("accepted_count", 0)
                    ),
                    "candidate_count": len(attempt_result.candidates),
                    "accepted_confidence": round(
                        sum(
                            candidate.ocr_confidence
                            for candidate in attempt_result.candidates
                            if candidate.accepted
                        ),
                        4,
                    ),
                }
            )
        return self._geometry_result(
            result,
            frame_bgr,
            crop_top=crop_top,
            ratio=actual_ratio,
            top_ratio=top_ratio,
            searched=True,
            started=started,
            attempts=summaries,
        )

    @staticmethod
    def _geometry_crop(
        frame_bgr: np.ndarray,
        ratio: float,
        vertical_anchor: float,
    ) -> tuple[np.ndarray, int, float, float]:
        frame_height = frame_bgr.shape[0]
        crop_height = min(frame_height, max(80, round(frame_height * ratio)))
        available = frame_height - crop_height
        crop_top = round(available * min(1.0, max(0.0, vertical_anchor)))
        crop_bottom = crop_top + crop_height
        return (
            frame_bgr[crop_top:crop_bottom],
            crop_top,
            crop_height / frame_height,
            crop_top / frame_height,
        )

    @staticmethod
    def _safe_geometry_accepted_count(
        result: CardFrameResult,
        *,
        crop_top: int,
        full_height: int,
    ) -> int:
        crop_height = int(result.diagnostics.get("frame_shape", [0])[0])
        if crop_top <= 0 or crop_top + crop_height >= full_height:
            return 0
        return sum(candidate.accepted for candidate in result.candidates)

    @staticmethod
    def _geometry_score(
        attempt: tuple[float, float, int, float, CardFrameResult],
    ) -> tuple[int, float, int, float]:
        _requested_ratio, _vertical_anchor, _crop_top, _top_ratio, result = attempt
        ratio = result.diagnostics["frame_shape"][0]
        accepted = [candidate for candidate in result.candidates if candidate.accepted]
        rejected_count = len(result.candidates) - len(accepted)
        return (
            len(accepted),
            sum(candidate.ocr_confidence for candidate in accepted),
            -rejected_count,
            ratio,
        )

    @staticmethod
    def _geometry_result(
        result: CardFrameResult,
        full_frame: np.ndarray,
        *,
        crop_top: int,
        ratio: float,
        top_ratio: float,
        searched: bool,
        started: float,
        attempts: list[dict[str, object]] | None = None,
    ) -> CardFrameResult:
        crop_height = int(result.diagnostics.get("frame_shape", [full_frame.shape[0]])[0])
        candidates = result.candidates
        translated: list[CardCandidate] = []
        for candidate in candidates:
            context = replace(
                candidate.context,
                art_box=_offset_box_y(candidate.context.art_box, crop_top),
                card_box=_offset_box_y(candidate.context.card_box, crop_top),
            )
            card_x, card_y, card_width, card_height = context.card_box
            touches_edge = (
                card_x <= 0
                or card_y <= 0
                or card_x + card_width >= full_frame.shape[1]
                or card_y + card_height >= full_frame.shape[0]
            )
            accepted = candidate.accepted and not touches_edge
            reason = (
                "card_touches_region_edge"
                if candidate.accepted and touches_edge
                else candidate.reason
            )
            if touches_edge and context.accepted:
                context = replace(context, accepted=False, reason="card_touches_region_edge")
            translated.append(
                replace(
                    candidate,
                    name_box=_offset_box_y(candidate.name_box, crop_top),
                    context=context,
                    accepted=accepted,
                    reason=reason,
                )
            )
        candidates = tuple(translated)

        diagnostics = dict(result.diagnostics)
        diagnostics["accepted_count"] = sum(candidate.accepted for candidate in candidates)
        diagnostics["edge_rejected_count"] = sum(
            candidate.reason == "card_touches_region_edge" for candidate in candidates
        )
        diagnostics.update(
            {
                "frame_shape": list(full_frame.shape),
                "geometry_crop_top": crop_top,
                "geometry_crop_height": crop_height,
                "geometry_height_ratio": round(ratio, 4),
                "geometry_top_ratio": round(top_ratio, 4),
                "geometry_searched": searched,
                "total_seconds": time.perf_counter() - started,
            }
        )
        if attempts is not None:
            diagnostics["geometry_attempts"] = attempts
        return CardFrameResult(result.text_observations, candidates, diagnostics)

    def _analyze_aligned(self, frame_bgr: np.ndarray) -> CardFrameResult:
        started = time.perf_counter()
        if (
            not isinstance(frame_bgr, np.ndarray)
            or frame_bgr.ndim != 3
            or frame_bgr.shape[2] != 3
            or frame_bgr.size == 0
        ):
            raise ValueError("TemplateCardRecognizer requires a non-empty HxWx3 BGR frame")

        frame_height, frame_width = frame_bgr.shape[:2]
        if frame_height < 80 or frame_width < 80:
            return CardFrameResult(
                (),
                (),
                {
                    "detector": self.detector_name,
                    "frame_shape": list(frame_bgr.shape),
                    "reason": "region_too_small",
                    "total_seconds": time.perf_counter() - started,
                },
            )

        geometry_at = time.perf_counter()
        query_hog, x_positions = self._sliding_descriptors(frame_bgr)
        descriptors_at = time.perf_counter()
        if not len(x_positions):
            return CardFrameResult(
                (),
                (),
                {
                    "detector": self.detector_name,
                    "frame_shape": list(frame_bgr.shape),
                    "window_count": 0,
                    "card_window_count": 0,
                    "accepted_count": 0,
                    "geometry_seconds": geometry_at - started,
                    "descriptor_seconds": descriptors_at - geometry_at,
                    "total_seconds": descriptors_at - started,
                },
            )

        dark_fractions, side_contrasts, fully_visible = self._card_evidence(
            frame_bgr,
            x_positions,
        )
        evidence_mask = fully_visible & (
            dark_fractions >= self.config.minimum_nameplate_dark_fraction
        ) & (
            side_contrasts >= self.config.minimum_nameplate_side_contrast
        )
        evidence_indices = np.flatnonzero(evidence_mask)
        evidence_at = time.perf_counter()

        proposals: list[_Proposal] = []
        if len(evidence_indices):
            template_scores = query_hog[evidence_indices] @ self.index.identity_hog.T
            name_scores = np.maximum.reduceat(
                template_scores,
                self.index.identity_name_offsets[:-1],
                axis=1,
            )
            best_name_indices = np.argmax(name_scores, axis=1)
            best_scores = name_scores[np.arange(len(name_scores)), best_name_indices]
            runner_up = name_scores.copy()
            runner_up[np.arange(len(runner_up)), best_name_indices] = -1.0
            runner_up_indices = np.argmax(runner_up, axis=1)
            margins = best_scores - runner_up[np.arange(len(runner_up)), runner_up_indices]

            for row, source_index in enumerate(evidence_indices):
                similarity = float(best_scores[row])
                margin = float(margins[row])
                accepted = True
                reason = "accepted_template"
                if similarity < self.config.minimum_identity_similarity:
                    accepted, reason = False, "low_template_similarity"
                elif margin < max(
                    self.config.minimum_identity_margin,
                    _IDENTITY_MINIMUM_MARGINS.get(
                        self.index.identity_names[int(best_name_indices[row])],
                        0.0,
                    ),
                ):
                    accepted, reason = False, "ambiguous_template_identity"
                proposals.append(
                    _Proposal(
                        x=int(x_positions[source_index]),
                        name=self.index.identity_names[int(best_name_indices[row])],
                        runner_up_name=self.index.identity_names[int(runner_up_indices[row])],
                        similarity=similarity,
                        margin=margin,
                        dark_fraction=float(dark_fractions[source_index]),
                        accepted=accepted,
                        reason=reason,
                    )
                )

        matched_at = time.perf_counter()
        proposals = self._keep_physical_peaks(proposals, frame_height, frame_width)
        candidates = tuple(self._candidate(frame_bgr, proposal) for proposal in proposals)
        completed_at = time.perf_counter()
        accepted_count = sum(candidate.accepted for candidate in candidates)
        diagnostics = {
            "detector": self.detector_name,
            "frame_shape": list(frame_bgr.shape),
            "window_count": len(x_positions),
            "card_window_count": int(evidence_mask.sum()),
            "proposal_count": len(proposals),
            "accepted_count": accepted_count,
            "ambiguous_count": sum(
                proposal.reason == "ambiguous_template_identity" for proposal in proposals
            ),
            "geometry_seconds": geometry_at - started,
            "descriptor_seconds": descriptors_at - geometry_at,
            "evidence_seconds": evidence_at - descriptors_at,
            "identity_match_seconds": matched_at - evidence_at,
            "attribute_seconds": completed_at - matched_at,
            "total_seconds": completed_at - started,
        }
        return CardFrameResult((), candidates, diagnostics)

    def recognize(self, frame_bgr: np.ndarray):
        return self.analyze(frame_bgr).observations

    def _sliding_descriptors(
        self,
        frame_bgr: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        frame_height, frame_width = frame_bgr.shape[:2]
        art_width = max(24.0, frame_height * self.index.art_width_ratio)
        art_top = max(0, round(frame_height * self.index.art_top_ratio))
        art_height = max(16, round(frame_height * self.index.art_height_ratio))
        art_bottom = min(frame_height, art_top + art_height)
        if art_bottom - art_top < 16:
            return (
                np.empty((0, _HOG.getDescriptorSize()), dtype=np.float32),
                np.empty(0, dtype=np.int32),
            )

        gray_strip = cv2.cvtColor(
            frame_bgr[art_top:art_bottom],
            cv2.COLOR_BGR2GRAY,
        )
        scale = HOG_WIDTH / art_width
        normalized_width = max(HOG_WIDTH, round(frame_width * scale))
        normalized = cv2.resize(
            gray_strip,
            (normalized_width, HOG_HEIGHT),
            interpolation=cv2.INTER_AREA,
        )
        if normalized_width < HOG_WIDTH:
            return (
                np.empty((0, _HOG.getDescriptorSize()), dtype=np.float32),
                np.empty(0, dtype=np.int32),
            )

        hog_values = _HOG.compute(normalized, winStride=(HOG_STRIDE, 8))
        if hog_values is None or not hog_values.size:
            return (
                np.empty((0, _HOG.getDescriptorSize()), dtype=np.float32),
                np.empty(0, dtype=np.int32),
            )
        query_hog = np.asarray(hog_values, dtype=np.float32).reshape(
            -1,
            _HOG.getDescriptorSize(),
        )
        query_hog /= np.maximum(
            np.linalg.norm(query_hog, axis=1, keepdims=True),
            1e-6,
        )

        count = len(query_hog)
        x_positions = np.rint(np.arange(count) * HOG_STRIDE / scale).astype(np.int32)
        return query_hog, x_positions

    def _card_evidence(
        self,
        frame_bgr: np.ndarray,
        x_positions: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        frame_height, frame_width = frame_bgr.shape[:2]
        card_width = max(24, round(frame_height * self.index.card_width_ratio))
        art_width = max(16, round(frame_height * self.index.art_width_ratio))
        art_left = round(frame_height * self.index.art_left_ratio)
        card_lefts = x_positions - art_left
        fully_visible = (card_lefts > 0) & (card_lefts + card_width < frame_width)

        band_top = max(0, round(frame_height * self.config.nameplate_top_ratio))
        band_bottom = min(
            frame_height,
            max(band_top + 1, round(frame_height * self.config.nameplate_bottom_ratio)),
        )
        gray = cv2.cvtColor(frame_bgr[band_top:band_bottom], cv2.COLOR_BGR2GRAY)
        dark_columns = (gray < self.config.nameplate_dark_value).sum(axis=0, dtype=np.int32)
        integral = np.pad(np.cumsum(dark_columns, dtype=np.int64), (1, 0))
        lefts = np.clip(x_positions, 0, frame_width)
        rights = np.clip(x_positions + art_width, 0, frame_width)
        areas = np.maximum(1, (rights - lefts) * max(1, band_bottom - band_top))
        dark_fractions = (integral[rights] - integral[lefts]) / areas

        side_lefts = np.clip(card_lefts, 0, frame_width)
        side_left_rights = np.clip(x_positions, 0, frame_width)
        side_right_lefts = np.clip(x_positions + art_width, 0, frame_width)
        side_rights = np.clip(card_lefts + card_width, 0, frame_width)
        band_height = max(1, band_bottom - band_top)
        left_areas = np.maximum(1, (side_left_rights - side_lefts) * band_height)
        right_areas = np.maximum(1, (side_rights - side_right_lefts) * band_height)
        left_dark = (integral[side_left_rights] - integral[side_lefts]) / left_areas
        right_dark = (integral[side_rights] - integral[side_right_lefts]) / right_areas
        side_contrasts = dark_fractions - (left_dark + right_dark) / 2.0
        return (
            np.asarray(dark_fractions, dtype=np.float32),
            np.asarray(side_contrasts, dtype=np.float32),
            fully_visible,
        )

    def _keep_physical_peaks(
        self,
        proposals: list[_Proposal],
        frame_height: int,
        frame_width: int,
    ) -> list[_Proposal]:
        if not proposals:
            return []
        spacing = frame_height * self.config.minimum_proposal_spacing_ratio
        maximum = max(1, int(np.ceil(frame_width / max(1.0, frame_height * 0.55))) + 1)
        ordered = sorted(
            proposals,
            key=lambda item: (
                not item.accepted,
                -item.similarity,
                -item.margin,
                item.x,
            ),
        )
        kept: list[_Proposal] = []
        for proposal in ordered:
            if any(abs(proposal.x - previous.x) < spacing for previous in kept):
                continue
            kept.append(proposal)
            if len(kept) >= maximum:
                break
        return sorted(kept, key=lambda item: item.x)

    def _candidate(self, frame_bgr: np.ndarray, proposal: _Proposal) -> CardCandidate:
        frame_height, frame_width = frame_bgr.shape[:2]
        card_width = max(24, round(frame_height * self.index.card_width_ratio))
        art_width = max(16, round(frame_height * self.index.art_width_ratio))
        art_height = max(16, round(frame_height * self.index.art_height_ratio))
        art_top = max(0, round(frame_height * self.index.art_top_ratio))
        card_left = proposal.x - round(frame_height * self.index.art_left_ratio)
        card_box = (card_left, 0, card_width, frame_height)
        art_box = (proposal.x, art_top, art_width, min(art_height, frame_height - art_top))
        name_height = max(8, round(frame_height * self.config.name_height_ratio))
        name_box = (
            proposal.x,
            round(frame_height * self.config.name_y_ratio),
            max(name_height, round(card_width * 0.62)),
            name_height,
        )

        family = ""
        family_confidence = 0.0
        rarity = ""
        rarity_confidence = 0.0
        if proposal.accepted:
            family, family_confidence = self._classify_family(
                frame_bgr,
                name_box,
                card_box,
            )
            rarity = droid_class(proposal.name)
            rarity_confidence = 1.0 if rarity else 0.0

        art = frame_bgr[
            art_box[1] : art_box[1] + art_box[3],
            art_box[0] : art_box[0] + art_box[2],
        ]
        art_standard_deviation = float(np.std(art)) if art.size else 0.0
        confidence = min(
            0.99,
            0.75
            + max(0.0, proposal.similarity - self.config.minimum_identity_similarity) * 0.80
            + max(0.0, proposal.margin) * 1.50,
        )
        context = CardContext(
            art_box=art_box,
            card_box=card_box,
            nameplate_dark_fraction=proposal.dark_fraction,
            art_standard_deviation=art_standard_deviation,
            art_edge_density=0.0,
            frame_line_ratio=1.0,
            accepted=proposal.accepted,
            reason=proposal.reason,
        )
        return CardCandidate(
            canonical_name=proposal.name,
            raw_text=f"template:{proposal.name}",
            ocr_confidence=confidence,
            name_box=name_box,
            context=context,
            accepted=proposal.accepted,
            reason=proposal.reason,
            family=family,
            family_confidence=family_confidence,
            rarity=rarity,
            rarity_confidence=rarity_confidence,
            raw_best_similarity=proposal.similarity,
            runner_up_identity=proposal.runner_up_name,
            identity_margin=proposal.margin,
        )

    def _classify_family(
        self,
        frame_bgr: np.ndarray,
        name_box: tuple[int, int, int, int],
        card_box: tuple[int, int, int, int],
    ) -> tuple[str, float]:
        # The cyan Diamond frame has a uniquely precise color signature in the
        # collected library. Use it as an inexpensive high-confidence shortcut.
        border_family, border_confidence = classify_card_family_border(
            frame_bgr,
            name_box,
            card_box,
        )
        if (
            border_family in {"Gold", "Diamond", "Rainbow"}
            and border_confidence >= _DISTINCTIVE_BORDER_MIN_CONFIDENCE
        ):
            return border_family, max(0.90, border_confidence)

        x, y, width, height = card_box
        card = frame_bgr[y : y + height, x : x + width]
        if card.size == 0:
            return "", 0.0
        histogram, word = family_features(card)
        scores = (
            FAMILY_HISTOGRAM_WEIGHT * (self.index.family_histograms @ histogram)
            + FAMILY_WORD_WEIGHT * (self.index.family_words @ word)
        )
        family_scores = np.maximum.reduceat(scores, self.index.family_offsets[:-1])
        best_index = int(np.argmax(family_scores))
        best_score = float(family_scores[best_index])
        runner_up = np.delete(family_scores, best_index)
        margin = best_score - float(np.max(runner_up))
        family = self.index.family_labels[best_index]
        if family not in CARD_FAMILIES or margin < _FAMILY_MINIMUM_MARGINS[family]:
            return "", 0.0
        confidence = min(0.99, 0.82 + margin * 5.0)
        return family, confidence
