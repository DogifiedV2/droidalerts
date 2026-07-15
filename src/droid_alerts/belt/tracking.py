from __future__ import annotations

from collections import Counter, deque
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
import math

from .ocr import DroidObservation

Box = tuple[float, float, float, float]


def _center(box: Box) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _intersection_over_union(first: Box, second: Box) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    union = first[2] * first[3] + second[2] * second[3] - intersection
    return intersection / union if union > 0 else 0.0


@dataclass(frozen=True)
class _PreparedObservation:
    name: str
    confidence: float
    raw_text: str
    box: Box
    source_index: int
    family: str
    family_confidence: float
    rarity: str
    rarity_confidence: float


@dataclass
class Track:
    id: int
    # A track has no public identity until the temporal vote confirms it. Once
    # populated, name is deliberately never changed.
    name: str
    box: Box
    created_at: float
    last_seen_at: float
    last_updated_at: float
    hits: int = 1
    confirmed: bool = False
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    confidence: float = 0.0
    raw_text: str = ""
    family: str = ""
    family_confidence: float = 0.0
    rarity: str = ""
    rarity_confidence: float = 0.0
    identity_votes: deque[str] = field(default_factory=deque, repr=False)
    vote_confidences: deque[float] = field(default_factory=deque, repr=False)
    vote_raw_texts: deque[str] = field(default_factory=deque, repr=False)
    family_votes: deque[tuple[str, str, float]] = field(default_factory=deque, repr=False)
    rarity_votes: deque[tuple[str, str, float]] = field(default_factory=deque, repr=False)
    last_center_x: float = 0.0
    last_center_y: float = 0.0
    entered_emitted: bool = False
    prediction_horizon: float = 0.75

    def predicted_box(self, now: float) -> Box:
        # Extrapolate through the expected next OCR result, but never let a
        # missed pass make a track run indefinitely across the screen.
        dt = min(self.prediction_horizon, max(0.0, now - self.last_updated_at))
        return (
            self.box[0] + self.velocity_x * dt,
            self.box[1] + self.velocity_y * dt,
            self.box[2],
            self.box[3],
        )


@dataclass(frozen=True)
class TrackEvent:
    kind: str
    track: Track


@dataclass
class TrackerUpdate:
    tracks: list[Track] = field(default_factory=list)
    events: list[TrackEvent] = field(default_factory=list)


class BeltTracker:
    """Geometry-first tracker for repeated observations of belt cards.

    Unconfirmed observations are associated by predicted geometry, then their
    labels become votes. A single card whose early OCR alternates between two
    names therefore remains one physical track. Once confirmed, a contradictory
    name cannot advance or keep that identity alive.

    Four matching labels in the latest five observations are required by
    default. An ``entered`` event is emitted immediately on confirmation, with
    no motion or screen-position requirement. The track ID prevents duplicate
    alerts for later reads of the same visible card.
    """

    def __init__(
        self,
        *,
        confirmation_hits: int = 4,
        confirmation_window: int = 5,
        timeout_seconds: float = 3.5,
        association_distance_ratio: float = 0.20,
        outside_margin: float = 60.0,
    ) -> None:
        self.confirmation_hits = max(1, int(confirmation_hits))
        self.confirmation_window = max(self.confirmation_hits, int(confirmation_window))
        # A lone color/component read is not enough to label an alert. Keep
        # name confirmation just as fast, while requiring one repeat for the
        # optional card attributes whenever the tracker itself uses >1 read.
        self.attribute_confirmation_hits = min(2, self.confirmation_hits)
        self.timeout_seconds = max(0.2, float(timeout_seconds))
        self.association_distance_ratio = min(1.0, max(0.02, float(association_distance_ratio)))
        self.outside_margin = max(0.0, float(outside_margin))
        self._tracks: list[Track] = []
        self._next_id = 1

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1

    def update(
        self,
        observations: Sequence[DroidObservation],
        now: float,
        frame_width: int,
    ) -> TrackerUpdate:
        frame_width = max(1, int(frame_width))
        events: list[TrackEvent] = []

        # Do not resurrect a stale track simply because a later observation is
        # close to its old position.
        active: list[Track] = []
        for track in self._tracks:
            if now - track.last_seen_at > self.timeout_seconds:
                if track.entered_emitted:
                    events.append(TrackEvent("exited", self._snapshot(track)))
            else:
                active.append(track)
        self._tracks = active

        prepared = self._prepare_observations(observations)
        prepared = self._deduplicate_observations(prepared)
        matches = self._associate(prepared, now, frame_width)
        matched_observations = {observation_index for observation_index in matches.values()}

        for track_index, observation_index in matches.items():
            track = self._tracks[track_index]
            observation = prepared[observation_index]
            self._apply_observation(track, observation, now)
            if self._should_emit_entered(track):
                track.entered_emitted = True
                events.append(TrackEvent("entered", self._snapshot(track)))

        for observation_index, observation in enumerate(prepared):
            if observation_index in matched_observations:
                continue
            if self._is_quarantined_conflict(observation, now, frame_width):
                continue
            track = self._new_track(observation, now)
            self._tracks.append(track)

        visible: list[Track] = []
        for track in self._tracks:
            predicted = self._association_box(track, now, frame_width)
            # Prediction is only a display/association aid. Expiring a track
            # from an extrapolated position discarded valid votes and emitted
            # early exits in the moving-belt recording. Only the real
            # last-seen timeout above owns track lifetime. If prediction became
            # implausible, keep the overlay on the last real observation too.
            if track.confirmed and not self._is_outside(predicted, frame_width):
                visible.append(self._snapshot(track, box=predicted))
        return TrackerUpdate(visible, events)

    def predict(self, now: float, frame_width: int) -> TrackerUpdate:
        return self.update([], now, frame_width)

    def _prepare_observations(
        self, observations: Sequence[DroidObservation]
    ) -> list[_PreparedObservation]:
        prepared: list[_PreparedObservation] = []
        for index, observation in enumerate(observations):
            try:
                box = tuple(float(value) for value in observation.box)
                if len(box) != 4 or box[2] <= 0 or box[3] <= 0 or not all(map(math.isfinite, box)):
                    continue
                name = str(observation.match.name)
                if not name:
                    continue
                confidence = min(
                    1.0,
                    max(0.0, float(observation.ocr_confidence) * float(observation.match.score)),
                )
                raw_text = str(observation.match.raw_text)
                family = str(getattr(observation, "family", "")).strip()
                family_confidence = min(
                    1.0,
                    max(0.0, float(getattr(observation, "family_confidence", 0.0))),
                )
                rarity = str(getattr(observation, "rarity", "")).strip()
                rarity_confidence = min(
                    1.0,
                    max(0.0, float(getattr(observation, "rarity_confidence", 0.0))),
                )
            except (AttributeError, TypeError, ValueError):
                continue
            prepared.append(
                _PreparedObservation(
                    name,
                    confidence,
                    raw_text,
                    box,  # type: ignore[arg-type]
                    index,
                    family,
                    family_confidence,
                    rarity,
                    rarity_confidence,
                )
            )
        return prepared

    @staticmethod
    def _deduplicate_observations(
        observations: list[_PreparedObservation],
    ) -> list[_PreparedObservation]:
        """Keep one vote when OCR reports the same physical label twice."""
        kept: list[_PreparedObservation] = []
        # Prefer the strongest reading, with source order as a deterministic
        # tie-breaker. Label identity intentionally has no role here.
        for observation in sorted(observations, key=lambda item: (-item.confidence, item.source_index)):
            if any(BeltTracker._same_physical_detection(observation.box, other.box) for other in kept):
                continue
            kept.append(observation)
        return sorted(kept, key=lambda item: item.source_index)

    @staticmethod
    def _same_physical_detection(first: Box, second: Box) -> bool:
        if _intersection_over_union(first, second) >= 0.45:
            return True
        first_center = _center(first)
        second_center = _center(second)
        return (
            abs(first_center[0] - second_center[0]) <= max(3.0, min(first[2], second[2]) * 0.18)
            and abs(first_center[1] - second_center[1])
            <= max(3.0, min(first[3], second[3]) * 0.45)
        )

    def _associate(
        self,
        observations: list[_PreparedObservation],
        now: float,
        frame_width: int,
    ) -> dict[int, int]:
        """Return a one-to-one predicted-geometry assignment.

        Confirmed identity is used only as a safety constraint; it is never a
        scoring shortcut while an unconfirmed track is collecting votes.
        """
        candidates: list[tuple[float, float, int, int, int]] = []
        for track_index, track in enumerate(self._tracks):
            predicted = self._association_box(track, now, frame_width)
            predicted_center = _center(predicted)
            for observation_index, observation in enumerate(observations):
                # Geometry decides identity while a track is collecting votes.
                # Once identity is confirmed, however, a contradictory label
                # must never carry or keep that identity alive. Leave the
                # observation free to start its own candidate track instead.
                if track.confirmed and observation.name != track.name:
                    continue
                observed_center = _center(observation.box)
                dx = abs(observed_center[0] - predicted_center[0])
                dy = abs(observed_center[1] - predicted_center[1])
                width = max(predicted[2], observation.box[2])
                height = max(predicted[3], observation.box[3])
                horizontal_limit = max(48.0, frame_width * self.association_distance_ratio, width * 2.0)
                strict_horizontal_limit = False
                if not track.confirmed and track.identity_votes:
                    leading_name, leading_votes = Counter(track.identity_votes).most_common(1)[0]
                    if leading_votes >= 2 and observation.name != leading_name:
                        # Exact names may still fluctuate on the same card, but
                        # a different exact name hundreds of pixels away is an
                        # adjacent blueprint, not another vote for this track.
                        # Keep conflicting reads in the card's immediate
                        # neighborhood instead of using the broad recovery gate.
                        horizontal_limit = min(horizontal_limit, max(36.0, width * 0.75))
                        strict_horizontal_limit = True
                vertical_limit = max(18.0, height * 1.5)
                overlap = _intersection_over_union(predicted, observation.box)
                if dy > vertical_limit or (
                    dx > horizontal_limit and (strict_horizontal_limit or overlap == 0.0)
                ):
                    continue
                # Predicted center distance is primary; overlap breaks close
                # calls without consulting the recognized identity.
                cost = dx / horizontal_limit + dy / vertical_limit - overlap * 0.55
                candidates.append((cost, -overlap, track.id, track_index, observation_index))

        assignments: dict[int, int] = {}
        used_observations: set[int] = set()
        for _, _, _, track_index, observation_index in sorted(candidates):
            if track_index in assignments or observation_index in used_observations:
                continue
            assignments[track_index] = observation_index
            used_observations.add(observation_index)
        return assignments

    def _is_quarantined_conflict(
        self,
        observation: _PreparedObservation,
        now: float,
        frame_width: int,
    ) -> bool:
        """Drop one-frame contradictory labels beside a confirmed track.

        They cannot update the confirmed identity, but immediately spawning a
        second track at the same location would let that shadow track steal the
        next correct observation. A genuinely new card gets another chance as
        soon as it separates geometrically or the old track expires.
        """

        observed_center = _center(observation.box)
        for track in self._tracks:
            if not track.confirmed or track.name == observation.name:
                continue
            predicted = self._association_box(track, now, frame_width)
            predicted_center = _center(predicted)
            width = max(predicted[2], observation.box[2])
            height = max(predicted[3], observation.box[3])
            association_limit = max(
                48.0,
                frame_width * self.association_distance_ratio,
                width * 2.0,
            )
            # Quarantine only labels that overlap the old card's immediate
            # predicted neighborhood. Adjacent cards must remain eligible to
            # start their own tracks.
            horizontal_limit = min(association_limit, max(36.0, width * 0.75))
            vertical_limit = max(18.0, height * 1.5)
            if (
                abs(observed_center[0] - predicted_center[0]) <= horizontal_limit
                and abs(observed_center[1] - predicted_center[1]) <= vertical_limit
            ):
                return True
        return False

    def _new_track(self, observation: _PreparedObservation, now: float) -> Track:
        center_x, center_y = _center(observation.box)
        track = Track(
            id=self._next_id,
            name="",
            box=observation.box,
            created_at=now,
            last_seen_at=now,
            last_updated_at=now,
            confidence=observation.confidence,
            raw_text=observation.raw_text,
            identity_votes=deque([observation.name], maxlen=self.confirmation_window),
            vote_confidences=deque([observation.confidence], maxlen=self.confirmation_window),
            vote_raw_texts=deque([observation.raw_text], maxlen=self.confirmation_window),
            family_votes=deque(
                [(observation.name, observation.family, observation.family_confidence)]
                if observation.family
                else (),
                maxlen=self.confirmation_window,
            ),
            rarity_votes=deque(
                [(observation.name, observation.rarity, observation.rarity_confidence)]
                if observation.rarity
                else (),
                maxlen=self.confirmation_window,
            ),
            last_center_x=center_x,
            last_center_y=center_y,
        )
        self._next_id += 1
        return track

    def _apply_observation(
        self,
        track: Track,
        observation: _PreparedObservation,
        now: float,
    ) -> None:
        if track.confirmed and observation.name != track.name:
            return
        new_center_x, new_center_y = _center(observation.box)
        dx = new_center_x - track.last_center_x
        dy = new_center_y - track.last_center_y
        dt = max(0.02, now - track.last_seen_at)
        measured_vx, measured_vy = dx / dt, dy / dt
        if track.hits == 1:
            track.velocity_x = measured_vx * 0.70
            track.velocity_y = measured_vy * 0.70
        else:
            track.velocity_x = track.velocity_x * 0.55 + measured_vx * 0.45
            track.velocity_y = track.velocity_y * 0.55 + measured_vy * 0.45

        # OCR cadence is hardware-dependent. The old fixed 0.75-second cap
        # expired between Windows results, freezing labels behind their cards.
        # Faster machines retain the conservative original horizon.
        track.prediction_horizon = min(1.5, max(0.75, dt * 1.35))

        track.box = observation.box
        track.last_seen_at = now
        track.last_updated_at = now
        track.last_center_x = new_center_x
        track.last_center_y = new_center_y
        track.hits += 1

        if observation.family and (not track.confirmed or not track.family):
            track.family_votes.append(
                (observation.name, observation.family, observation.family_confidence)
            )
        if observation.rarity and (not track.confirmed or not track.rarity):
            track.rarity_votes.append(
                (observation.name, observation.rarity, observation.rarity_confidence)
            )

        if not track.confirmed:
            track.identity_votes.append(observation.name)
            track.vote_confidences.append(observation.confidence)
            track.vote_raw_texts.append(observation.raw_text)
            candidate = self._confirmed_vote(track)
            if candidate is not None:
                # This is the only assignment to a non-empty public identity.
                # Conflicting future OCR readings therefore cannot rename it.
                track.name = candidate
                track.confirmed = True
                matching = [
                    (confidence, raw_text)
                    for name, confidence, raw_text in zip(
                        track.identity_votes,
                        track.vote_confidences,
                        track.vote_raw_texts,
                    )
                    if name == candidate
                ]
                track.confidence = sum(value[0] for value in matching) / len(matching)
                track.raw_text = matching[-1][1]
                self._assign_card_attributes(track)
        elif observation.name == track.name:
            # Geometry is always updated, but a contradictory label must not
            # alter the immutable identity or its displayed confidence.
            track.confidence = track.confidence * 0.70 + observation.confidence * 0.30
            track.raw_text = observation.raw_text
            self._assign_card_attributes(track)

    def _assign_card_attributes(self, track: Track) -> None:
        self._assign_attribute(track, track.family_votes, "family", "family_confidence")
        self._assign_attribute(track, track.rarity_votes, "rarity", "rarity_confidence")

    def _assign_attribute(
        self,
        track: Track,
        votes: deque[tuple[str, str, float]],
        value_attribute: str,
        confidence_attribute: str,
    ) -> None:
        """Freeze a repeated, unique-majority attribute for the confirmed identity."""

        if not track.confirmed or getattr(track, value_attribute):
            return
        matching = [
            (value, confidence)
            for name, value, confidence in votes
            if name == track.name and value
        ]
        counts = Counter(value for value, _confidence in matching)
        if not counts:
            return
        ranked = counts.most_common()
        value, vote_count = ranked[0]
        if vote_count < self.attribute_confirmation_hits or (
            len(ranked) > 1 and ranked[1][1] == vote_count
        ):
            return
        confidences = [confidence for item, confidence in matching if item == value]
        setattr(track, value_attribute, value)
        setattr(track, confidence_attribute, sum(confidences) / len(confidences))

    def _confirmed_vote(self, track: Track) -> str | None:
        counts = Counter(track.identity_votes)
        if not counts:
            return None
        ranked = counts.most_common()
        candidate, votes = ranked[0]
        if votes < self.confirmation_hits:
            return None
        if len(ranked) > 1 and ranked[1][1] == votes:
            return None
        return candidate

    @staticmethod
    def _should_emit_entered(track: Track) -> bool:
        return track.confirmed and not track.entered_emitted

    def _is_outside(self, box: Box, frame_width: int) -> bool:
        center_x, _ = _center(box)
        return center_x < -self.outside_margin or center_x > frame_width + self.outside_margin

    def _association_box(self, track: Track, now: float, frame_width: int) -> Box:
        """Return prediction unless it contradicts the last real observation."""

        predicted = track.predicted_box(now)
        if self._is_outside(predicted, frame_width) and not self._is_outside(track.box, frame_width):
            return track.box
        return predicted

    @staticmethod
    def _snapshot(track: Track, *, box: Box | None = None) -> Track:
        # Events and update results should not change under a caller's feet on
        # the next tracking pass.
        return replace(
            track,
            box=track.box if box is None else box,
            identity_votes=deque(track.identity_votes, maxlen=track.identity_votes.maxlen),
            vote_confidences=deque(track.vote_confidences, maxlen=track.vote_confidences.maxlen),
            vote_raw_texts=deque(track.vote_raw_texts, maxlen=track.vote_raw_texts.maxlen),
            family_votes=deque(track.family_votes, maxlen=track.family_votes.maxlen),
            rarity_votes=deque(track.rarity_votes, maxlen=track.rarity_votes.maxlen),
        )
