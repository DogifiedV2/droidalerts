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
    vote_intervals: deque[float] = field(default_factory=deque, repr=False)
    family_votes: deque[tuple[str, str, float]] = field(default_factory=deque, repr=False)
    rarity_votes: deque[tuple[str, str, float]] = field(default_factory=deque, repr=False)
    last_center_x: float = 0.0
    last_center_y: float = 0.0
    entered_emitted: bool = False
    prediction_horizon: float = 0.75
    confirmation_mode: str = ""

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
    # Accepted-observation index -> physical track ID. Consumers such as the
    # local template collector can retain the best source pixels for an
    # appearance without reimplementing the association algorithm.
    observation_track_ids: dict[int, int] = field(default_factory=dict)


class BeltTracker:
    """Ordered motion tracker for repeated observations of belt cards.

    Belt cards cannot overtake one another. Observations are therefore assigned
    globally in horizontal order, using exact labels as strong evidence without
    allowing two same-named cards to collapse into one track. A single card
    whose early OCR alternates between two names can still remain one physical
    track. Once confirmed, a contradictory name cannot advance or keep that
    identity alive.

    Template identities require four consecutive labels by default. On hardware
    producing less than 0.75 scan FPS, three consecutive, high-confidence exact
    reads are accepted instead so short-lived cards still have a physically
    possible path to confirmation. Template events also wait for three stable
    family and rarity reads. The track ID prevents duplicate alerts for later
    reads of the same visible card.
    """

    def __init__(
        self,
        *,
        confirmation_hits: int = 4,
        confirmation_window: int = 5,
        timeout_seconds: float = 3.5,
        association_distance_ratio: float = 0.20,
        outside_margin: float = 60.0,
        slow_confirmation_hits: int = 3,
        slow_cadence_seconds: float = 4.0 / 3.0,
        slow_minimum_confidence: float = 0.78,
        template_conflict_grace_seconds: float = 0.50,
        template_attribute_confirmation_hits: int = 3,
    ) -> None:
        self.confirmation_hits = max(1, int(confirmation_hits))
        self.slow_confirmation_hits = max(2, int(slow_confirmation_hits))
        self.confirmation_window = max(
            self.confirmation_hits,
            self.slow_confirmation_hits,
            int(template_attribute_confirmation_hits),
            int(confirmation_window),
        )
        # A lone color/component read is not enough to label an alert. Keep
        # name confirmation just as fast, while requiring one repeat for the
        # optional card attributes whenever the tracker itself uses >1 read.
        self.attribute_confirmation_hits = min(2, self.confirmation_hits)
        self.template_attribute_confirmation_hits = max(
            2,
            int(template_attribute_confirmation_hits),
        )
        self.timeout_seconds = max(0.2, float(timeout_seconds))
        self.association_distance_ratio = min(1.0, max(0.02, float(association_distance_ratio)))
        self.outside_margin = max(0.0, float(outside_margin))
        self.slow_cadence_seconds = max(0.2, float(slow_cadence_seconds))
        self.slow_minimum_confidence = min(
            1.0,
            max(0.0, float(slow_minimum_confidence)),
        )
        self.template_conflict_grace_seconds = max(
            0.1,
            float(template_conflict_grace_seconds),
        )
        self._tracks: list[Track] = []
        self._next_id = 1

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1

    def diagnostic_state(self) -> list[dict[str, object]]:
        return [
            {
                "id": track.id,
                "name": track.name,
                "confirmed": track.confirmed,
                "hits": track.hits,
                "identity_votes": list(track.identity_votes),
                "confirmation_mode": track.confirmation_mode,
                "last_seen_at": track.last_seen_at,
                "velocity_x": track.velocity_x,
                "velocity_y": track.velocity_y,
                "box": list(track.box),
            }
            for track in self._tracks
        ]

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
        observation_track_ids: dict[int, int] = {}

        for track_index, observation_index in matches.items():
            track = self._tracks[track_index]
            observation = prepared[observation_index]
            self._apply_observation(track, observation, now)
            observation_track_ids[observation.source_index] = track.id
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
            observation_track_ids[observation.source_index] = track.id

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
        return TrackerUpdate(visible, events, observation_track_ids)

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
        """Return a global one-to-one assignment that cannot cross belt order.

        A greedy nearest-box match fails when a slow OCR pass takes long enough
        for every card to occupy its neighbour's old position. Sequence
        alignment lets exact-name anchors shift the whole row while insertions
        and exits remain possible. Matching still uses geometry, so duplicate
        names and isolated OCR mistakes do not merge physical cards.
        """

        if not self._tracks or not observations:
            return {}

        ordered_tracks = sorted(
            enumerate(self._tracks),
            key=lambda item: (_center(item[1].box)[0], item[1].id),
        )
        ordered_observations = sorted(
            enumerate(observations),
            key=lambda item: (_center(item[1].box)[0], item[1].source_index),
        )
        track_count = len(ordered_tracks)
        observation_count = len(ordered_observations)
        skip_cost = 0.80
        belt_shift = self._estimate_belt_shift(observations, now, frame_width)

        # State ordering is total cost, number of skips, then negative matches.
        # The latter two make equal-cost decisions deterministic and favour
        # retaining a physical track.
        states: list[list[tuple[float, int, int] | None]] = [
            [None] * (observation_count + 1) for _ in range(track_count + 1)
        ]
        previous: list[list[tuple[int, int, str] | None]] = [
            [None] * (observation_count + 1) for _ in range(track_count + 1)
        ]
        states[0][0] = (0.0, 0, 0)

        def consider(
            row: int,
            column: int,
            candidate: tuple[float, int, int],
            origin: tuple[int, int, str],
        ) -> None:
            current = states[row][column]
            if current is None or candidate < current:
                states[row][column] = candidate
                previous[row][column] = origin

        for row in range(track_count + 1):
            for column in range(observation_count + 1):
                state = states[row][column]
                if state is None:
                    continue
                total, skips, negative_matches = state
                if row < track_count:
                    consider(
                        row + 1,
                        column,
                        (total + skip_cost, skips + 1, negative_matches),
                        (row, column, "track"),
                    )
                if column < observation_count:
                    consider(
                        row,
                        column + 1,
                        (total + skip_cost, skips + 1, negative_matches),
                        (row, column, "observation"),
                    )
                if row < track_count and column < observation_count:
                    _track_index, track = ordered_tracks[row]
                    _observation_index, observation = ordered_observations[column]
                    match_cost = self._association_cost(
                        track,
                        observation,
                        now,
                        frame_width,
                        belt_shift,
                    )
                    if match_cost is not None:
                        consider(
                            row + 1,
                            column + 1,
                            (total + match_cost, skips, negative_matches - 1),
                            (row, column, "match"),
                        )

        assignments: dict[int, int] = {}
        row, column = track_count, observation_count
        while row or column:
            origin = previous[row][column]
            if origin is None:
                break
            previous_row, previous_column, action = origin
            if action == "match":
                track_index = ordered_tracks[previous_row][0]
                observation_index = ordered_observations[previous_column][0]
                assignments[track_index] = observation_index
            row, column = previous_row, previous_column
        return dict(sorted(assignments.items()))

    def _association_cost(
        self,
        track: Track,
        observation: _PreparedObservation,
        now: float,
        frame_width: int,
        belt_shift: float,
    ) -> float | None:
        if track.confirmed and observation.name != track.name:
            return None

        predicted = self._association_box(track, now, frame_width)
        if belt_shift:
            predicted = (
                predicted[0] + belt_shift,
                predicted[1],
                predicted[2],
                predicted[3],
            )
        predicted_center = _center(predicted)
        observed_center = _center(observation.box)
        dx = abs(observed_center[0] - predicted_center[0])
        dy = abs(observed_center[1] - predicted_center[1])
        width = max(predicted[2], observation.box[2])
        height = max(predicted[3], observation.box[3])
        generic_limit = max(
            48.0,
            frame_width * self.association_distance_ratio,
            width * 2.0,
        )
        leading_name, leading_votes = self._leading_identity(track)
        exact_name = bool(leading_name) and observation.name == leading_name
        horizontal_limit = generic_limit
        strict_horizontal_limit = False
        if exact_name:
            # A 0.3 FPS card can move through more than one old card slot.
            # Exact identity may bridge that distance, but order still prevents
            # a same-named card from jumping across its neighbours.
            horizontal_limit = max(horizontal_limit, frame_width * 0.65, width * 3.0)
        elif leading_votes >= 2:
            horizontal_limit = min(horizontal_limit, max(36.0, width * 0.75))
            strict_horizontal_limit = True

        vertical_limit = max(18.0, height * 1.5)
        overlap = _intersection_over_union(predicted, observation.box)
        if dy > vertical_limit or (
            dx > horizontal_limit and (strict_horizontal_limit or overlap == 0.0)
        ):
            return None

        cost = dx / horizontal_limit + dy / vertical_limit - overlap * 0.55
        if exact_name:
            cost -= 0.90
        elif leading_name:
            # A conflict is evidence, not a hard ban: the first OCR read can be
            # wrong and geometry must still be able to retain the card.
            cost += 0.60 if leading_votes >= 2 else 0.35
        return cost

    def _estimate_belt_shift(
        self,
        observations: list[_PreparedObservation],
        now: float,
        frame_width: int,
    ) -> float:
        """Estimate shared row motion from two or more consistent exact labels."""

        tracks_by_name: dict[str, list[Track]] = {}
        for track in self._tracks:
            name, _votes = self._leading_identity(track)
            if name:
                tracks_by_name.setdefault(name, []).append(track)
        observations_by_name: dict[str, list[_PreparedObservation]] = {}
        for observation in observations:
            observations_by_name.setdefault(observation.name, []).append(observation)

        shifts: list[float] = []
        widths: list[float] = []
        for name, tracks in tracks_by_name.items():
            matching_observations = observations_by_name.get(name, [])
            # Duplicate labels are resolved by ordered geometry, not by an
            # ambiguous global-motion anchor.
            if len(tracks) != 1 or len(matching_observations) != 1:
                continue
            predicted = self._association_box(tracks[0], now, frame_width)
            shifts.append(
                _center(matching_observations[0].box)[0] - _center(predicted)[0]
            )
            widths.append(max(predicted[2], matching_observations[0].box[2]))
        if len(shifts) < 2:
            return 0.0

        ordered_shifts = sorted(shifts)
        middle = len(ordered_shifts) // 2
        median_shift = (
            ordered_shifts[middle]
            if len(ordered_shifts) % 2
            else (ordered_shifts[middle - 1] + ordered_shifts[middle]) / 2.0
        )
        ordered_widths = sorted(widths)
        median_width = ordered_widths[len(ordered_widths) // 2]
        tolerance = max(80.0, median_width * 1.5)
        inliers = [value for value in shifts if abs(value - median_shift) <= tolerance]
        if len(inliers) < 2:
            return 0.0
        return sum(inliers) / len(inliers)

    @staticmethod
    def _leading_identity(track: Track) -> tuple[str, int]:
        if track.confirmed:
            return track.name, len(track.identity_votes)
        counts = Counter(track.identity_votes)
        if not counts:
            return "", 0
        ranked = counts.most_common()
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return "", ranked[0][1]
        return ranked[0]

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
            # Template identities are exact, repeat quickly, and do not have
            # OCR's transient character substitutions. If a different visual
            # identity persists after the old card stopped being seen, let it
            # start its own confirmation instead of quarantining it for the
            # full low-OCR timeout. This also handles a belt frame that was
            # held for several seconds and then jumps to the next card.
            if (
                observation.raw_text.startswith("template:")
                and now - track.last_seen_at >= self.template_conflict_grace_seconds
            ):
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
            vote_intervals=deque([0.0], maxlen=self.confirmation_window),
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
        track.prediction_horizon = min(12.0, max(0.75, dt * 1.35))

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
            track.vote_intervals.append(dt)
            confirmed_vote = self._confirmed_vote(track)
            if confirmed_vote is not None:
                candidate, confirmation_mode = confirmed_vote
                # This is the only assignment to a non-empty public identity.
                # Conflicting future OCR readings therefore cannot rename it.
                track.name = candidate
                track.confirmed = True
                track.confirmation_mode = confirmation_mode
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
        if self._is_template_track(track):
            recent = matching[-self.template_attribute_confirmation_hits :]
            if (
                len(recent) < self.template_attribute_confirmation_hits
                or len({value for value, _confidence in recent}) != 1
            ):
                return
            value = recent[-1][0]
            confidences = [confidence for _value, confidence in recent]
            setattr(track, value_attribute, value)
            setattr(track, confidence_attribute, sum(confidences) / len(confidences))
            return

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

    def _confirmed_vote(self, track: Track) -> tuple[str, str] | None:
        counts = Counter(track.identity_votes)
        if not counts:
            return None
        ranked = counts.most_common()
        candidate, votes = ranked[0]
        if self._is_template_track(track):
            recent_names = list(track.identity_votes)[-self.confirmation_hits :]
            if (
                len(recent_names) == self.confirmation_hits
                and len(set(recent_names)) == 1
            ):
                return recent_names[-1], "standard"
        elif votes >= self.confirmation_hits and not (
            len(ranked) > 1 and ranked[1][1] == votes
        ):
            return candidate, "standard"

        names = list(track.identity_votes)[-self.slow_confirmation_hits :]
        confidences = list(track.vote_confidences)[-self.slow_confirmation_hits :]
        intervals = list(track.vote_intervals)[-self.slow_confirmation_hits :]
        if (
            len(names) == self.slow_confirmation_hits
            and len(set(names)) == 1
            and all(value >= self.slow_minimum_confidence for value in confidences)
            and all(
                value >= self.slow_cadence_seconds
                for value in intervals[1:]
            )
        ):
            return names[-1], "slow-cadence"
        return None

    @staticmethod
    def _should_emit_entered(track: Track) -> bool:
        if not track.confirmed or track.entered_emitted:
            return False
        if BeltTracker._is_template_track(track):
            return bool(track.family and track.rarity)
        return True

    @staticmethod
    def _is_template_track(track: Track) -> bool:
        raw_texts = tuple(track.vote_raw_texts)
        return bool(raw_texts) and all(
            raw_text.startswith("template:") for raw_text in raw_texts
        )

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
            vote_intervals=deque(track.vote_intervals, maxlen=track.vote_intervals.maxlen),
            family_votes=deque(track.family_votes, maxlen=track.family_votes.maxlen),
            rarity_votes=deque(track.rarity_votes, maxlen=track.rarity_votes.maxlen),
        )
