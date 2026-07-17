from __future__ import annotations

from dataclasses import dataclass, field, replace
import time
from typing import Iterable

import cv2
import numpy as np

from .matching import NameMatch, compact
from .names import DROID_NAMES, droid_class
from .ocr import DroidObservation, OcrEngine, TextObservation


UNKNOWN = "UNKNOWN"
CARD_FAMILIES = ("Default", "Gold", "Diamond", "Beskar", "Rainbow")

_CANONICAL_BY_COMPACT = {compact(name): name for name in DROID_NAMES}
_FAMILY_BY_COMPACT = {compact(family): family for family in CARD_FAMILIES}


def exact_canonical_name(raw_text: str) -> str | None:
    """Return a canonical droid name only for an exact compact match.

    Punctuation, spacing, and case may differ, but characters may not be added,
    removed, substituted, or fuzzily corrected.
    """

    key = compact(raw_text)
    return _CANONICAL_BY_COMPACT.get(key) if key else None


def exact_card_family(raw_text: str) -> str | None:
    """Return the card family printed below a blueprint name.

    A text detector can join the left rarity word to the smaller badge beside it, for
    example ``DEFAULT RARE`` or ``BESKARC``. The card geometry check below
    restricts this prefix match to the expected left badge position.
    """

    key = compact(raw_text)
    if not key:
        return None
    for family_key, family in _FAMILY_BY_COMPACT.items():
        if key.startswith(family_key):
            return family
    return None


def classify_card_family_border(
    frame_bgr: np.ndarray,
    name_box: tuple[int, int, int, int],
    card_box: tuple[int, int, int, int],
) -> tuple[str, float]:
    """Fallback for the distinctive Gold, Diamond, and Rainbow frames.

    Default and Beskar are deliberately not guessed from color because both
    are low-saturation metal/gray under some lighting. The family label OCR is
    authoritative whenever it is available.
    """

    _name_x, name_y, _name_width, name_height = name_box
    card_x, _card_y, card_width, _card_height = card_box
    # A clipped card can expose unrelated colored scenery in this band.
    if name_height <= 0 or card_width < 5.0 * name_height:
        return "", 0.0

    y1 = max(0, round(name_y + 1.20 * name_height))
    y2 = min(frame_bgr.shape[0], round(name_y + 1.70 * name_height))
    x1 = max(0, round(card_x))
    x2 = min(frame_bgr.shape[1], round(card_x + card_width))
    crop = frame_bgr[y1:y2, x1:x2]
    if crop.size == 0 or crop.shape[0] < max(3, round(0.35 * name_height)):
        return "", 0.0

    hue, saturation, value = cv2.split(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
    valid = value > 60
    valid_count = int(valid.sum())
    if valid_count < max(20, round(crop.shape[0] * crop.shape[1] * 0.10)):
        return "", 0.0

    vivid = (saturation >= 70) & (value >= 100)
    vivid_count = int(vivid.sum())
    vivid_fraction = vivid_count / valid_count
    orange_fraction = float(
        ((hue >= 5) & (hue <= 35) & (saturation >= 80) & (value >= 100)).sum()
    ) / valid_count
    cyan_fraction = float(
        ((hue >= 75) & (hue <= 105) & (saturation >= 70) & (value >= 100)).sum()
    ) / valid_count

    diverse_bins = 0
    hue_spread = 0
    if vivid_count:
        histogram = np.histogram(hue[vivid], bins=np.arange(0, 181, 15))[0]
        significant = max(5, round(vivid_count * 0.03))
        occupied = np.flatnonzero(histogram > significant)
        diverse_bins = len(occupied)
        bin_count = len(histogram)
        hue_spread = max(
            (
                min(abs(int(first) - int(second)), bin_count - abs(int(first) - int(second)))
                for first in occupied
                for second in occupied
            ),
            default=0,
        )

    # Gold frames pick up small red/magenta highlights as they move. Those
    # colors remain close on the circular hue wheel, unlike a real Rainbow
    # frame, which spans both warm and cool hues even when one section happens
    # to dominate the sampled band.
    if vivid_fraction >= 0.35 and diverse_bins >= 3 and hue_spread >= 4:
        return "Rainbow", min(1.0, vivid_fraction / 0.60)
    if orange_fraction >= 0.45:
        return "Gold", min(1.0, orange_fraction / 0.75)
    if cyan_fraction >= 0.45:
        return "Diamond", min(1.0, cyan_fraction / 0.75)
    return "", 0.0


@dataclass(frozen=True)
class CardRecognitionConfig:
    minimum_ocr_confidence: float = 0.70
    minimum_family_ocr_confidence: float = 0.60
    minimum_name_height: int = 8
    dark_pixel_value: int = 105
    minimum_nameplate_dark_fraction: float = 0.38
    minimum_art_height_in_names: float = 3.40
    minimum_art_standard_deviation: float = 18.0
    minimum_art_edge_density: float = 0.008
    minimum_frame_line_ratio: float = 0.42


@dataclass(frozen=True)
class CardContext:
    art_box: tuple[int, int, int, int]
    card_box: tuple[int, int, int, int]
    nameplate_dark_fraction: float
    art_standard_deviation: float
    art_edge_density: float
    frame_line_ratio: float
    accepted: bool
    reason: str


@dataclass(frozen=True)
class CardCandidate:
    canonical_name: str
    raw_text: str
    ocr_confidence: float
    name_box: tuple[int, int, int, int]
    context: CardContext
    accepted: bool
    reason: str
    family: str = ""
    family_confidence: float = 0.0
    rarity: str = ""
    rarity_confidence: float = 0.0
    raw_best_similarity: float = 0.0
    runner_up_identity: str = ""
    identity_margin: float = 0.0
    family_best_similarity: float = 0.0
    runner_up_family: str = ""
    family_margin: float = 0.0

    @property
    def identity(self) -> str:
        """The safe public identity; rejected candidates are always UNKNOWN."""

        return self.canonical_name if self.accepted else UNKNOWN

    def to_droid_observation(self) -> DroidObservation | None:
        if not self.accepted:
            return None
        match = NameMatch(name=self.canonical_name, score=1.0, raw_text=self.raw_text)
        return DroidObservation(
            match,
            self.ocr_confidence,
            self.context.card_box,
            self.family,
            self.family_confidence,
            self.rarity,
            self.rarity_confidence,
        )


@dataclass(frozen=True)
class CardFrameResult:
    """One OCR pass, including every raw observation for diagnostics."""

    text_observations: tuple[TextObservation, ...]
    candidates: tuple[CardCandidate, ...]
    diagnostics: dict[str, object] = field(default_factory=dict)

    @property
    def observations(self) -> list[DroidObservation]:
        results: list[DroidObservation] = []
        for candidate in self.candidates:
            observation = candidate.to_droid_observation()
            if observation is not None:
                results.append(observation)
        return results


@dataclass(frozen=True)
class _ExactTextCandidate:
    name: str
    raw_text: str
    confidence: float
    box: tuple[int, int, int, int]


class CardRecognizer:
    """Recognize exact canonical droid cards in a full belt-region frame.

    OCR proposes text only. Card/nameplate geometry establishes that the text
    belongs to a card. No image-template assets are needed by this path.
    """

    def __init__(
        self,
        ocr_engine: OcrEngine,
        *,
        target_names: Iterable[str] | None = None,
        config: CardRecognitionConfig | None = None,
    ) -> None:
        self.ocr_engine = ocr_engine
        self.config = config or CardRecognitionConfig()

        raw_targets = tuple(target_names or ())
        self._target_filter_enabled = bool(raw_targets)
        self._target_names = {
            name
            for raw_name in raw_targets
            if (name := exact_canonical_name(raw_name)) is not None
        }

    def analyze(self, frame_bgr: np.ndarray) -> CardFrameResult:
        started = time.perf_counter()
        _validate_bgr_frame(frame_bgr)
        text_observations = tuple(self._read_text(frame_bgr))
        read_at = time.perf_counter()
        exact_candidates = _exact_text_candidates(text_observations, frame_bgr.shape)
        matched_at = time.perf_counter()
        candidates = tuple(self._analyze_candidate(frame_bgr, item) for item in exact_candidates)
        context_at = time.perf_counter()
        candidates = self._keep_dominant_card_row(candidates)
        row_at = time.perf_counter()
        candidates = self._attach_card_attributes(candidates, text_observations, frame_bgr)
        completed_at = time.perf_counter()
        diagnostics = {
            "frame_shape": list(frame_bgr.shape),
            "ocr_read_seconds": read_at - started,
            "exact_match_seconds": matched_at - read_at,
            "card_context_seconds": context_at - matched_at,
            "row_filter_seconds": row_at - context_at,
            "attribute_seconds": completed_at - row_at,
            "total_seconds": completed_at - started,
            "ocr_input": dict(getattr(self, "_last_ocr_input_details", {})),
            "engine": dict(getattr(self.ocr_engine, "last_diagnostics", {})),
        }
        return CardFrameResult(text_observations, candidates, diagnostics)

    def recognize(self, frame_bgr: np.ndarray) -> list[DroidObservation]:
        return self.analyze(frame_bgr).observations

    def _read_text(self, frame_bgr: np.ndarray) -> list[TextObservation]:
        """OCR only the name-row band, then remap boxes to the full card frame.

        Card context is still measured against the entire selected region. The
        smaller OCR input avoids spending seconds detecting text in artwork and
        lets temporal confirmation sample the moving card often enough.
        """

        source = frame_bgr
        y_offset = 0
        band = getattr(self.ocr_engine, "card_ocr_band", None)
        if isinstance(band, (tuple, list)) and len(band) == 2:
            try:
                top = min(1.0, max(0.0, float(band[0])))
                bottom = min(1.0, max(top, float(band[1])))
            except (TypeError, ValueError):
                top, bottom = 0.0, 1.0
            frame_height = frame_bgr.shape[0]
            y_offset = max(0, min(frame_height - 1, round(frame_height * top)))
            y2 = max(y_offset + 1, min(frame_height, round(frame_height * bottom)))
            source = frame_bgr[y_offset:y2]

        scale_value = getattr(self.ocr_engine, "card_input_scale", None)
        if scale_value is None:
            advertised = getattr(self.ocr_engine, "input_scales", ())
            try:
                scale_value = max(float(value) for value in advertised)
            except (TypeError, ValueError):
                scale_value = 1.0
        try:
            scale = float(scale_value)
        except (TypeError, ValueError):
            scale = 1.0
        if not np.isfinite(scale) or scale <= 0:
            scale = 1.0

        max_width_value = getattr(self.ocr_engine, "card_max_input_width", None)
        try:
            max_width = int(max_width_value) if max_width_value is not None else 0
        except (TypeError, ValueError):
            max_width = 0
        if max_width > 0 and source.shape[1] * scale > max_width:
            scale = max_width / source.shape[1]

        ocr_input = source
        if scale != 1.0:
            ocr_input = cv2.resize(
                source,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA,
            )
        self._last_ocr_input_details = {
            "frame_shape": list(frame_bgr.shape),
            "band_shape": list(source.shape),
            "ocr_input_shape": list(ocr_input.shape),
            "band": list(band) if isinstance(band, (tuple, list)) else None,
            "y_offset": y_offset,
            "scale": scale,
            "max_input_width": max_width or None,
        }
        observations: list[TextObservation] = []
        for item in self.ocr_engine.read(ocr_input):
            x, y, width, height = item.box
            observations.append(
                TextObservation(
                    item.text,
                    item.confidence,
                    (
                        round(x / scale),
                        round(y / scale) + y_offset,
                        max(1, round(width / scale)),
                        max(1, round(height / scale)),
                    ),
                )
            )
        return observations

    def _analyze_candidate(self, frame_bgr: np.ndarray, item: _ExactTextCandidate) -> CardCandidate:
        context = analyze_card_context(frame_bgr, item.box, self.config)
        reason = context.reason
        accepted = context.accepted

        if item.confidence < self.config.minimum_ocr_confidence:
            accepted, reason = False, "low_ocr_confidence"
        elif self._target_filter_enabled and item.name not in self._target_names:
            accepted, reason = False, "not_targeted"

        return CardCandidate(
            canonical_name=item.name,
            raw_text=item.raw_text,
            ocr_confidence=item.confidence,
            name_box=item.box,
            context=context,
            accepted=accepted,
            reason=reason,
        )

    def _keep_dominant_card_row(self, candidates: tuple[CardCandidate, ...]) -> tuple[CardCandidate, ...]:
        """Reject otherwise-plausible canonical text away from the belt row.

        Non-target cards still vote for the row. This matters when a user tracks
        only one name: neighboring cards, rather than a matching HUD label, must
        establish where the moving belt is.
        """

        eligible = [
            index
            for index, candidate in enumerate(candidates)
            if candidate.context.accepted
            and candidate.ocr_confidence >= self.config.minimum_ocr_confidence
        ]
        if len(eligible) <= 1:
            return candidates

        median_height = float(np.median([candidates[index].name_box[3] for index in eligible]))
        # Perspective and distance move edge-card nameplates vertically by
        # more than one local text height in the supplied captures.
        tolerance = max(12.0, median_height * 2.50)
        ordered = sorted(
            eligible,
            key=lambda index: candidates[index].name_box[1] + candidates[index].name_box[3] / 2,
        )
        clusters: list[list[int]] = []
        for index in ordered:
            center_y = candidates[index].name_box[1] + candidates[index].name_box[3] / 2
            if not clusters:
                clusters.append([index])
                continue
            previous = clusters[-1][-1]
            previous_y = candidates[previous].name_box[1] + candidates[previous].name_box[3] / 2
            if center_y - previous_y <= tolerance:
                clusters[-1].append(index)
            else:
                clusters.append([index])

        def cluster_key(cluster: list[int]) -> tuple[int, float, float]:
            frame_evidence = sum(candidates[index].context.frame_line_ratio for index in cluster)
            xs = [candidates[index].name_box[0] for index in cluster]
            horizontal_span = float(max(xs) - min(xs)) if len(xs) > 1 else 0.0
            return len(cluster), frame_evidence, horizontal_span

        dominant = set(max(clusters, key=cluster_key))
        updated: list[CardCandidate] = []
        for index, candidate in enumerate(candidates):
            if index in eligible and index not in dominant and candidate.accepted:
                candidate = replace(candidate, accepted=False, reason="off_card_row")
            updated.append(candidate)
        return tuple(updated)

    def _attach_card_attributes(
        self,
        candidates: tuple[CardCandidate, ...],
        text_observations: tuple[TextObservation, ...],
        frame_bgr: np.ndarray,
    ) -> tuple[CardCandidate, ...]:
        """Pair the visual tier and attach the identity's fixed droid class."""

        updated: list[CardCandidate] = []
        for candidate in candidates:
            if not candidate.accepted:
                updated.append(candidate)
                continue

            name_x, name_y, name_width, name_height = candidate.name_box
            card_x, _card_y, card_width, _card_height = candidate.context.card_box
            badge_center_y_min = name_y + name_height * 0.55
            badge_center_y_max = name_y + name_height * 1.45
            badge_left_min = name_x - name_height * 0.65
            badge_left_max = name_x + max(name_height * 1.75, name_width * 0.45)
            matches: list[tuple[float, float, str, TextObservation]] = []

            for observation in text_observations:
                family = exact_card_family(observation.text)
                if family is None or observation.confidence < self.config.minimum_family_ocr_confidence:
                    continue
                x, y, width, height = observation.box
                center_x = x + width / 2
                center_y = y + height / 2
                if not (card_x <= center_x <= card_x + card_width):
                    continue
                if not (badge_center_y_min <= center_y <= badge_center_y_max):
                    continue
                if not (badge_left_min <= x <= badge_left_max):
                    continue
                horizontal_offset = abs(x - name_x) / max(1.0, name_height)
                matches.append(
                    (horizontal_offset, -float(observation.confidence), family, observation)
                )

            if matches:
                _offset, _negative_confidence, family, observation = min(matches)
                candidate = replace(
                    candidate,
                    family=family,
                    family_confidence=float(observation.confidence),
                )
            else:
                family, family_confidence = classify_card_family_border(
                    frame_bgr,
                    candidate.name_box,
                    candidate.context.card_box,
                )
                candidate = replace(
                    candidate,
                    family=family,
                    family_confidence=family_confidence,
                )
            rarity = droid_class(candidate.canonical_name)
            candidate = replace(
                candidate,
                rarity=rarity,
                rarity_confidence=1.0 if rarity else 0.0,
            )
            updated.append(candidate)
        return tuple(updated)


def analyze_card_context(
    frame_bgr: np.ndarray,
    name_box: tuple[int, int, int, int],
    config: CardRecognitionConfig | None = None,
) -> CardContext:
    """Measure conservative card/nameplate evidence around an OCR name box."""

    settings = config or CardRecognitionConfig()
    frame_height, frame_width = frame_bgr.shape[:2]
    x, y, width, height = _clip_box(name_box, frame_width, frame_height)
    if width <= 0 or height < settings.minimum_name_height:
        return _empty_context((x, y, width, height), "invalid_name_box")

    # In the supplied views, card artwork is about five name-heights tall and
    # the full card is at least six name-heights wide. Starting left of the
    # printed name accounts for short, left-aligned names such as R2 and CB.
    card_width = max(width + 2 * height, 6 * height)
    card_x1 = max(0, round(x - 1.2 * height))
    card_x2 = min(frame_width, card_x1 + card_width)
    art_y1 = max(0, round(y - 5.0 * height))
    art_y2 = max(0, min(frame_height, round(y - 0.1 * height)))
    card_y2 = min(frame_height, round(y + 1.6 * height))
    art_box = (card_x1, art_y1, max(0, card_x2 - card_x1), max(0, art_y2 - art_y1))
    card_box = (card_x1, art_y1, max(0, card_x2 - card_x1), max(0, card_y2 - art_y1))

    if art_box[2] < 2 * height or art_box[3] < settings.minimum_art_height_in_names * height:
        return _empty_context(name_box, "insufficient_art_above", art_box=art_box, card_box=card_box)

    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    plate_y1 = max(0, round(y - 0.3 * height))
    plate_y2 = min(frame_height, round(y + 1.6 * height))
    plate = gray[plate_y1:plate_y2, card_x1:card_x2]
    art = gray[art_y1:art_y2, card_x1:card_x2]
    if plate.size == 0 or art.size == 0 or plate.shape[0] < 0.9 * height:
        return _empty_context(name_box, "incomplete_nameplate", art_box=art_box, card_box=card_box)

    dark_fraction = float(np.mean(plate <= settings.dark_pixel_value))
    art_standard_deviation = float(np.std(art))
    edges = cv2.Canny(art, 60, 160)
    edge_density = float(np.mean(edges > 0))
    frame_line_ratio = _longest_vertical_line_ratio(edges, height, settings.minimum_frame_line_ratio)

    reason = "accepted_exact"
    accepted = True
    if dark_fraction < settings.minimum_nameplate_dark_fraction:
        accepted, reason = False, "not_dark_nameplate"
    elif art_standard_deviation < settings.minimum_art_standard_deviation:
        accepted, reason = False, "flat_art_region"
    elif edge_density < settings.minimum_art_edge_density:
        accepted, reason = False, "empty_art_region"
    elif frame_line_ratio < settings.minimum_frame_line_ratio:
        accepted, reason = False, "missing_card_frame"

    return CardContext(
        art_box=art_box,
        card_box=card_box,
        nameplate_dark_fraction=dark_fraction,
        art_standard_deviation=art_standard_deviation,
        art_edge_density=edge_density,
        frame_line_ratio=frame_line_ratio,
        accepted=accepted,
        reason=reason,
    )



def _exact_text_candidates(
    observations: tuple[TextObservation, ...],
    frame_shape: tuple[int, ...],
) -> list[_ExactTextCandidate]:
    frame_height, frame_width = frame_shape[:2]
    candidates: list[_ExactTextCandidate] = []
    clean: list[TextObservation] = []
    for observation in observations:
        box = _clip_box(observation.box, frame_width, frame_height)
        if box[2] <= 0 or box[3] <= 0:
            continue
        item = TextObservation(observation.text, float(observation.confidence), box)
        clean.append(item)
        name = exact_canonical_name(item.text)
        if name is not None:
            candidates.append(_ExactTextCandidate(name, item.text, item.confidence, item.box))

    # Text detectors can split a multiword card name. Joining only adjacent
    # words on the same baseline preserves exact matching without fuzzy edits.
    ordered = sorted(clean, key=lambda item: (item.box[1] + item.box[3] / 2, item.box[0]))
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            left, right = (first, second) if first.box[0] <= second.box[0] else (second, first)
            if not _same_text_line(left.box, right.box):
                continue
            gap = right.box[0] - (left.box[0] + left.box[2])
            max_height = max(left.box[3], right.box[3])
            if gap < -0.35 * max_height or gap > 1.6 * max_height:
                continue
            raw_text = f"{left.text} {right.text}"
            name = exact_canonical_name(raw_text)
            if name is None:
                continue
            box = _union_box(left.box, right.box)
            candidates.append(_ExactTextCandidate(name, raw_text, min(left.confidence, right.confidence), box))

    return _deduplicate_exact_candidates(candidates)


def _deduplicate_exact_candidates(items: list[_ExactTextCandidate]) -> list[_ExactTextCandidate]:
    kept: list[_ExactTextCandidate] = []
    for item in sorted(items, key=lambda candidate: candidate.confidence, reverse=True):
        if any(
            old.name == item.name and _intersection_over_union(old.box, item.box) >= 0.55
            for old in kept
        ):
            continue
        kept.append(item)
    return sorted(kept, key=lambda candidate: (candidate.box[0], candidate.box[1]))


def _same_text_line(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
    first_center = first[1] + first[3] / 2
    second_center = second[1] + second[3] / 2
    return abs(first_center - second_center) <= 0.55 * max(first[3], second[3])


def _longest_vertical_line_ratio(edges: np.ndarray, name_height: int, minimum_ratio: float) -> float:
    art_height = edges.shape[0]
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=max(10, round(art_height * 0.18)),
        minLineLength=max(8, round(art_height * minimum_ratio)),
        maxLineGap=max(3, round(name_height * 0.75)),
    )
    best = 0.0
    if lines is None:
        return best
    for line in lines:
        x1, y1, x2, y2 = (int(value) for value in line.reshape(4))
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if dy >= 3 * max(1, dx):
            best = max(best, dy / max(1, art_height))
    return best


def _empty_context(
    name_box: tuple[int, int, int, int],
    reason: str,
    *,
    art_box: tuple[int, int, int, int] | None = None,
    card_box: tuple[int, int, int, int] | None = None,
) -> CardContext:
    fallback = (name_box[0], name_box[1], max(0, name_box[2]), max(0, name_box[3]))
    return CardContext(art_box or fallback, card_box or fallback, 0.0, 0.0, 0.0, 0.0, False, reason)


def _validate_bgr_frame(frame_bgr: np.ndarray) -> None:
    if not isinstance(frame_bgr, np.ndarray) or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
        raise ValueError("CardRecognizer requires a non-empty HxWx3 BGR frame")
    if frame_bgr.size == 0 or frame_bgr.shape[0] < 2 or frame_bgr.shape[1] < 2:
        raise ValueError("CardRecognizer requires a non-empty HxWx3 BGR frame")


def _clip_box(
    box: tuple[int, int, int, int],
    frame_width: int,
    frame_height: int,
) -> tuple[int, int, int, int]:
    x, y, width, height = (int(round(value)) for value in box)
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(frame_width, x + max(0, width)), min(frame_height, y + max(0, height))
    return x1, y1, max(0, x2 - x1), max(0, y2 - y1)


def _union_box(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x1, y1 = min(first[0], second[0]), min(first[1], second[1])
    x2 = max(first[0] + first[2], second[0] + second[2])
    y2 = max(first[1] + first[3], second[1] + second[3])
    return x1, y1, x2 - x1, y2 - y1


def _intersection_over_union(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> float:
    x1, y1 = max(first[0], second[0]), max(first[1], second[1])
    x2 = min(first[0] + first[2], second[0] + second[2])
    y2 = min(first[1] + first[3], second[1] + second[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = first[2] * first[3] + second[2] * second[3] - intersection
    return intersection / union if union > 0 else 0.0
