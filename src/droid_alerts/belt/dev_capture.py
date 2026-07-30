from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from queue import Full, Queue
import threading
from typing import Any, Mapping, Sequence
import zipfile

import cv2
import numpy as np

from .models import CardCandidate
from .template_recognition import identity_features


DEV_CAPTURE_SCHEMA_VERSION = 1
DEV_TRACK_TIMEOUT_SECONDS = 5.0
DEV_TRACK_MAX_CROPS = 5
DEV_TRACK_MAX_OBSERVATIONS = 80
DEV_TRACK_MAX_ACTIVE = 64
DEV_TRACK_WRITE_QUEUE_SIZE = 64
DEV_TRACK_MAX_BYTES = 100 * 1024 * 1024
DEV_CROP_PNG_COMPRESSION = 3
DEV_UNCONFIRMED_ACCEPTANCE_SPLIT_SECONDS = 5.0

_SENTINEL = object()


@dataclass
class _SavedCrop:
    frame_number: int
    captured_at: float
    image: np.ndarray
    card_box: tuple[int, int, int, int]
    art_box_in_crop: tuple[int, int, int, int]
    source_frame_shape: tuple[int, ...]
    quality_score: float
    perceptual_hash: int
    candidate: dict[str, object]


@dataclass(frozen=True)
class _AppearanceSignature:
    descriptor: np.ndarray
    perceptual_hash: int
    accepted: bool
    production_track_id: int | None


@dataclass(frozen=True)
class _PreparedCandidate:
    candidate: CardCandidate
    box: tuple[int, int, int, int]
    production_track_id: int | None
    appearance: _AppearanceSignature | None


@dataclass
class _DevTrack:
    id: int
    first_seen_at: float
    last_seen_at: float
    initial_box: tuple[int, int, int, int]
    last_box: tuple[int, int, int, int]
    minimum_center_x: float
    maximum_center_x: float
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    observations: deque[dict[str, object]] = field(
        default_factory=lambda: deque(maxlen=DEV_TRACK_MAX_OBSERVATIONS)
    )
    crops: list[_SavedCrop] = field(default_factory=list)
    production_track_ids: set[int] = field(default_factory=set)
    tracker_events: list[dict[str, object]] = field(default_factory=list)
    appearances: deque[_AppearanceSignature] = field(
        default_factory=lambda: deque(maxlen=12)
    )


@dataclass(frozen=True)
class _TrackWriteJob:
    track: _DevTrack
    finish_reason: str


class BeltDevCaptureRecorder:
    """Create review-only packages for physical card candidates.

    This recorder is deliberately separate from production alert tracking. It
    associates every card-shaped candidate, including rejected identities,
    retains several diverse source crops, and writes completed tracks on a
    background thread. Disabling Developer Mode makes every method a no-op.
    """

    def __init__(
        self,
        session_dir: str | Path | None,
        *,
        enabled: bool = True,
        track_timeout_seconds: float = DEV_TRACK_TIMEOUT_SECONDS,
        maximum_saved_crops: int = DEV_TRACK_MAX_CROPS,
        maximum_session_bytes: int = DEV_TRACK_MAX_BYTES,
    ) -> None:
        self.enabled = bool(enabled and session_dir is not None)
        self.session_dir = Path(session_dir) if session_dir is not None else None
        self.tracks_dir = (
            self.session_dir / "tracks" if self.session_dir is not None else None
        )
        self.manifest_path = (
            self.session_dir / "capture_manifest.json"
            if self.session_dir is not None
            else None
        )
        self.track_timeout_seconds = max(1.0, float(track_timeout_seconds))
        self._effective_track_timeout_seconds = self.track_timeout_seconds
        self.maximum_saved_crops = max(1, int(maximum_saved_crops))
        self.maximum_session_bytes = max(1, int(maximum_session_bytes))
        self._tracks: list[_DevTrack] = []
        self._production_track_index: dict[int, set[int]] = {}
        self._next_track_id = 1
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._session_metadata: dict[str, object] = {}
        self._write_queue: Queue[object] = Queue(
            maxsize=DEV_TRACK_WRITE_QUEUE_SIZE
        )
        self._writer: threading.Thread | None = None
        self._written_bytes = 0
        self._queued_tracks = 0
        self._written_tracks = 0
        self._dropped_tracks = 0
        self._filtered_tracks = 0
        self._dropped_crops = 0
        self._writer_errors: list[str] = []
        self._closed = False
        self._lock = threading.Lock()
        if not self.enabled:
            return
        assert self.session_dir is not None
        assert self.tracks_dir is not None
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.tracks_dir.mkdir(parents=True, exist_ok=True)
        self._writer = threading.Thread(
            target=self._writer_loop,
            name="BeltDevCaptureWriter",
            daemon=True,
        )
        self._writer.start()
        self._write_session_manifest(stopped=False)

    def set_session_metadata(self, **values: object) -> None:
        if not self.enabled or self._closed:
            return
        self._session_metadata.update(values)
        self._write_session_manifest(stopped=False)

    def observe(
        self,
        frame_bgr: np.ndarray,
        candidates: Sequence[CardCandidate],
        observation_track_ids: Mapping[int, int],
        *,
        now: float,
        frame_number: int,
        track_timeout_seconds: float | None = None,
    ) -> None:
        """Record all physical candidates from one real captured frame."""

        if (
            not self.enabled
            or self._closed
            or not isinstance(frame_bgr, np.ndarray)
            or frame_bgr.ndim != 3
            or frame_bgr.size == 0
        ):
            return
        timestamp = float(now)
        if track_timeout_seconds is None:
            self._effective_track_timeout_seconds = self.track_timeout_seconds
        else:
            self._effective_track_timeout_seconds = min(
                20.0,
                max(
                    self.track_timeout_seconds,
                    _finite_float(track_timeout_seconds),
                ),
            )
        self._expire(timestamp)

        prepared: list[_PreparedCandidate] = []
        accepted_index = 0
        for candidate in candidates:
            production_track_id: int | None = None
            if bool(getattr(candidate, "accepted", False)):
                mapped = observation_track_ids.get(accepted_index)
                accepted_index += 1
                try:
                    production_track_id = int(mapped) if mapped is not None else None
                except (TypeError, ValueError):
                    production_track_id = None
            box = _valid_card_box(candidate, frame_bgr.shape)
            if box is None:
                continue
            prepared.append(
                _PreparedCandidate(
                    candidate,
                    box,
                    production_track_id,
                    _appearance_signature(
                        frame_bgr,
                        candidate,
                        production_track_id,
                    ),
                )
            )

        prepared = _deduplicate_candidates(prepared)
        matches = self._associate(prepared, timestamp, frame_bgr.shape[1])
        matched_candidates = set(matches.values())

        for track_index, candidate_index in matches.items():
            track = self._tracks[track_index]
            prepared_candidate = prepared[candidate_index]
            self._update_track(
                track,
                frame_bgr,
                prepared_candidate,
                now=timestamp,
                frame_number=frame_number,
            )

        for candidate_index, prepared_candidate in enumerate(prepared):
            if candidate_index in matched_candidates:
                continue
            box = prepared_candidate.box
            if len(self._tracks) >= DEV_TRACK_MAX_ACTIVE:
                oldest = min(self._tracks, key=lambda item: item.last_seen_at)
                self._finish_track(oldest, "active_track_limit")
            track = _DevTrack(
                id=self._next_track_id,
                first_seen_at=timestamp,
                last_seen_at=timestamp,
                initial_box=box,
                last_box=box,
                minimum_center_x=_box_center(box)[0],
                maximum_center_x=_box_center(box)[0],
            )
            self._next_track_id += 1
            self._tracks.append(track)
            self._update_track(
                track,
                frame_bgr,
                prepared_candidate,
                now=timestamp,
                frame_number=frame_number,
                first_observation=True,
            )

    def record_tracker_event(self, event: Any, *, alerted: bool) -> None:
        """Attach the production decision to its diagnostic physical track."""

        if not self.enabled or self._closed:
            return
        try:
            production_track_id = int(event.track.id)
            kind = str(event.kind)
        except (AttributeError, TypeError, ValueError):
            return
        record = {
            "event": kind,
            "production_track_id": production_track_id,
            "name": str(getattr(event.track, "name", "") or ""),
            "family": str(getattr(event.track, "family", "") or ""),
            "rarity": str(getattr(event.track, "rarity", "") or ""),
            "confidence": _finite_float(
                getattr(event.track, "confidence", 0.0)
            ),
            "alerted": bool(alerted),
        }
        diagnostic_ids = self._production_track_index.get(
            production_track_id,
            set(),
        )
        for track in self._tracks:
            if track.id in diagnostic_ids:
                track.tracker_events.append(dict(record))

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "active_tracks": len(self._tracks),
                "queued_tracks": self._queued_tracks,
                "written_tracks": self._written_tracks,
                "dropped_tracks": self._dropped_tracks,
                "filtered_tracks": self._filtered_tracks,
                "dropped_crops": self._dropped_crops,
                "written_bytes": self._written_bytes,
                "writer_errors": list(self._writer_errors),
            }

    def close(self) -> dict[str, object]:
        if not self.enabled or self._closed:
            return self.status()
        self._closed = True
        for track in tuple(self._tracks):
            self._finish_track(track, "session_stopped")
        try:
            self._write_queue.put(_SENTINEL, timeout=2.0)
        except Full:
            with self._lock:
                self._writer_errors.append("writer_queue_did_not_close")
        if self._writer is not None:
            self._writer.join(timeout=30.0)
            if self._writer.is_alive():
                with self._lock:
                    self._writer_errors.append("writer_thread_timeout")
        self._write_session_manifest(stopped=True)
        return self.status()

    def _associate(
        self,
        candidates: Sequence[_PreparedCandidate],
        now: float,
        frame_width: int,
    ) -> dict[int, int]:
        possibilities: list[tuple[float, int, int]] = []
        for track_index, track in enumerate(self._tracks):
            last_center = _box_center(track.last_box)
            elapsed = min(
                self._effective_track_timeout_seconds,
                max(0.0, now - track.last_seen_at),
            )
            predicted_center = (
                last_center[0] + track.velocity_x * elapsed,
                last_center[1] + track.velocity_y * elapsed,
            )
            for candidate_index, prepared in enumerate(candidates):
                box = prepared.box
                production_id = prepared.production_track_id
                if (
                    production_id is not None
                    and not track.production_track_ids
                    and now - track.first_seen_at
                    > DEV_UNCONFIRMED_ACCEPTANCE_SPLIT_SECONDS
                ):
                    # Do not attach the first accepted card to a long-running
                    # stream of unrelated rejected hypotheses at the belt
                    # entrance. The old rejected stream is still retained if
                    # it showed real conveyor motion.
                    continue
                if not _appearance_matches(track, prepared.appearance):
                    continue
                center = _box_center(box)
                maximum_width = max(track.last_box[2], box[2])
                maximum_height = max(track.last_box[3], box[3])
                width_ratio = max(track.last_box[2], box[2]) / max(
                    1.0,
                    min(track.last_box[2], box[2]),
                )
                height_ratio = max(track.last_box[3], box[3]) / max(
                    1.0,
                    min(track.last_box[3], box[3]),
                )
                horizontal = abs(center[0] - predicted_center[0])
                vertical = abs(center[1] - predicted_center[1])
                if (
                    width_ratio > 1.8
                    or height_ratio > 1.8
                    or horizontal
                    > max(maximum_width * 1.8, max(24.0, frame_width * 0.24))
                    or vertical > max(12.0, maximum_height * 0.65)
                ):
                    continue
                overlap_bonus = _intersection_over_union(track.last_box, box)
                cost = (
                    horizontal / max(1.0, maximum_width)
                    + vertical / max(1.0, maximum_height)
                    + abs(math.log(width_ratio)) * 0.4
                    + abs(math.log(height_ratio)) * 0.4
                    - overlap_bonus * 0.7
                )
                if (
                    production_id is not None
                    and production_id in track.production_track_ids
                ):
                    cost -= 1.0
                elif production_id is not None and track.production_track_ids:
                    # Geometry remains the source of truth for the diagnostic
                    # track. A changed production ID can itself explain a
                    # duplicate alert, so retain it with a small penalty.
                    cost += 0.35
                possibilities.append((cost, track_index, candidate_index))

        matches: dict[int, int] = {}
        used_candidates: set[int] = set()
        for _cost, track_index, candidate_index in sorted(possibilities):
            if track_index in matches or candidate_index in used_candidates:
                continue
            matches[track_index] = candidate_index
            used_candidates.add(candidate_index)
        return matches

    def _update_track(
        self,
        track: _DevTrack,
        frame_bgr: np.ndarray,
        prepared: _PreparedCandidate,
        *,
        now: float,
        frame_number: int,
        first_observation: bool = False,
    ) -> None:
        candidate = prepared.candidate
        box = prepared.box
        production_track_id = prepared.production_track_id
        previous_center = _box_center(track.last_box)
        center = _box_center(box)
        elapsed = now - track.last_seen_at
        if not first_observation and elapsed > 1e-4:
            measured_x = (center[0] - previous_center[0]) / elapsed
            measured_y = (center[1] - previous_center[1]) / elapsed
            track.velocity_x = track.velocity_x * 0.45 + measured_x * 0.55
            track.velocity_y = track.velocity_y * 0.45 + measured_y * 0.55
        track.last_seen_at = now
        track.last_box = box
        track.minimum_center_x = min(track.minimum_center_x, center[0])
        track.maximum_center_x = max(track.maximum_center_x, center[0])
        if prepared.appearance is not None:
            track.appearances.append(prepared.appearance)
        if production_track_id is not None:
            track.production_track_ids.add(production_track_id)
            self._production_track_index.setdefault(
                production_track_id,
                set(),
            ).add(track.id)

        diagnostic = candidate_diagnostics(candidate)
        quality = _crop_quality(frame_bgr, candidate, box)
        track.observations.append(
            {
                "frame": int(frame_number),
                "captured_at_monotonic": now,
                "card_box": list(box),
                "quality_score": quality,
                "production_track_id": production_track_id,
                "candidate": diagnostic,
            }
        )
        crop = _build_crop(
            frame_bgr,
            candidate,
            box,
            frame_number=frame_number,
            captured_at=now,
            quality_score=quality,
            diagnostic=diagnostic,
        )
        if crop is not None:
            self._consider_crop(track, crop)

    def _consider_crop(self, track: _DevTrack, crop: _SavedCrop) -> None:
        nearest: tuple[int, _SavedCrop] | None = None
        for existing in track.crops:
            distance = (existing.perceptual_hash ^ crop.perceptual_hash).bit_count()
            if nearest is None or distance < nearest[0]:
                nearest = distance, existing
        if nearest is not None and nearest[0] <= 3:
            existing = nearest[1]
            old_center = _box_center(existing.card_box)
            new_center = _box_center(crop.card_box)
            position_change = abs(new_center[0] - old_center[0]) / max(
                1.0,
                crop.card_box[2],
            )
            if position_change < 0.25:
                if crop.quality_score > existing.quality_score:
                    track.crops[track.crops.index(existing)] = crop
                return
        if len(track.crops) < self.maximum_saved_crops:
            track.crops.append(crop)
            return
        weakest = min(track.crops, key=lambda item: item.quality_score)
        existing_centers = [_box_center(item.card_box)[0] for item in track.crops]
        novelty = min(
            abs(_box_center(crop.card_box)[0] - center)
            for center in existing_centers
        ) / max(1.0, crop.card_box[2])
        if (
            crop.quality_score >= weakest.quality_score + 0.02
            or (
                novelty >= 0.55
                and crop.quality_score >= weakest.quality_score - 0.08
            )
        ):
            track.crops[track.crops.index(weakest)] = crop
        else:
            with self._lock:
                self._dropped_crops += 1

    def _expire(self, now: float) -> None:
        expired = [
            track
            for track in self._tracks
            if now - track.last_seen_at > self._effective_track_timeout_seconds
        ]
        for track in expired:
            self._finish_track(track, "candidate_timeout")

    def _finish_track(self, track: _DevTrack, reason: str) -> None:
        if track not in self._tracks:
            return
        self._tracks.remove(track)
        for production_id in tuple(track.production_track_ids):
            diagnostic_ids = self._production_track_index.get(production_id)
            if diagnostic_ids is None:
                continue
            diagnostic_ids.discard(track.id)
            if not diagnostic_ids:
                self._production_track_index.pop(production_id, None)
        if not _review_worthy_track(track):
            with self._lock:
                self._filtered_tracks += 1
            return
        try:
            self._write_queue.put_nowait(_TrackWriteJob(track, str(reason)))
            with self._lock:
                self._queued_tracks += 1
        except Full:
            with self._lock:
                self._dropped_tracks += 1

    def _writer_loop(self) -> None:
        while True:
            item = self._write_queue.get()
            try:
                if item is _SENTINEL:
                    return
                if isinstance(item, _TrackWriteJob):
                    self._write_track(item)
            except Exception as exc:
                with self._lock:
                    self._writer_errors.append(str(exc)[:500])
            finally:
                self._write_queue.task_done()

    def _write_track(self, job: _TrackWriteJob) -> None:
        if self.tracks_dir is None:
            return
        track = job.track
        track_dir = self.tracks_dir / f"track_{track.id:06d}"
        track_dir.mkdir(parents=True, exist_ok=True)
        frame_records: list[dict[str, object]] = []
        for index, crop in enumerate(
            sorted(track.crops, key=lambda item: item.captured_at),
            start=1,
        ):
            success, encoded = cv2.imencode(
                ".png",
                crop.image,
                (cv2.IMWRITE_PNG_COMPRESSION, DEV_CROP_PNG_COMPRESSION),
            )
            if not success:
                continue
            payload = encoded.tobytes()
            with self._lock:
                if self._written_bytes + len(payload) > self.maximum_session_bytes:
                    self._dropped_crops += 1
                    continue
                self._written_bytes += len(payload)
            file_name = f"crop_{index:02d}_frame_{crop.frame_number:06d}.png"
            (track_dir / file_name).write_bytes(payload)
            frame_records.append(
                {
                    "frame": crop.frame_number,
                    "captured_at_monotonic": crop.captured_at,
                    "image": file_name,
                    "card_box": list(crop.card_box),
                    "art_box_in_crop": list(crop.art_box_in_crop),
                    "source_frame_shape": list(crop.source_frame_shape),
                    "quality_score": crop.quality_score,
                    "candidate": crop.candidate,
                }
            )

        observations = list(track.observations)
        predicted_names = Counter(
            str(item.get("candidate", {}).get("name", ""))
            for item in observations
            if isinstance(item.get("candidate"), dict)
            and str(item.get("candidate", {}).get("name", ""))
        )
        predicted_families = Counter(
            str(item.get("candidate", {}).get("family", ""))
            for item in observations
            if isinstance(item.get("candidate"), dict)
            and str(item.get("candidate", {}).get("family", ""))
        )
        rejection_reasons = Counter(
            str(item.get("candidate", {}).get("reason", ""))
            for item in observations
            if isinstance(item.get("candidate"), dict)
            and not bool(item.get("candidate", {}).get("accepted", False))
        )
        manifest = {
            "version": DEV_CAPTURE_SCHEMA_VERSION,
            "physical_track_id": track.id,
            "label_status": "unreviewed",
            "training_status": "never_auto_promote",
            "finish_reason": job.finish_reason,
            "first_seen_at_monotonic": track.first_seen_at,
            "last_seen_at_monotonic": track.last_seen_at,
            "duration_seconds": max(
                0.0,
                track.last_seen_at - track.first_seen_at,
            ),
            "horizontal_displacement_pixels": max(
                0.0,
                track.maximum_center_x - track.minimum_center_x,
            ),
            "production_track_ids": sorted(track.production_track_ids),
            "tracker_events": track.tracker_events,
            "summary": {
                "observation_count": len(observations),
                "saved_crop_count": len(frame_records),
                "accepted_count": sum(
                    bool(item.get("candidate", {}).get("accepted", False))
                    for item in observations
                    if isinstance(item.get("candidate"), dict)
                ),
                "predicted_names": dict(predicted_names),
                "predicted_families": dict(predicted_families),
                "rejection_reasons": dict(rejection_reasons),
            },
            "observations": observations,
            "frames": frame_records,
        }
        _write_json_atomic(track_dir / "manifest.json", manifest)
        with self._lock:
            self._written_tracks += 1

    def _write_session_manifest(self, *, stopped: bool) -> None:
        if self.manifest_path is None:
            return
        status = self.status()
        manifest = {
            "version": DEV_CAPTURE_SCHEMA_VERSION,
            "created_at": self._started_at,
            "stopped_at": (
                datetime.now(timezone.utc).isoformat() if stopped else None
            ),
            "label_status": "unreviewed",
            "training_status": "manual_review_required",
            "capture_scope": "belt_region_only",
            "metadata": self._session_metadata,
            "status": status,
        }
        try:
            _write_json_atomic(self.manifest_path, manifest)
        except OSError:
            return


def candidate_diagnostics(candidate: CardCandidate) -> dict[str, object]:
    context = candidate.context
    return {
        "name": str(candidate.canonical_name),
        "raw_text": str(candidate.raw_text),
        "identity_confidence": _finite_float(candidate.identity_confidence),
        "raw_best_similarity": _finite_float(candidate.raw_best_similarity),
        "runner_up_identity": str(candidate.runner_up_identity),
        "identity_margin": _finite_float(candidate.identity_margin),
        "name_box": list(candidate.name_box),
        "accepted": bool(candidate.accepted),
        "reason": str(candidate.reason),
        "family": str(candidate.family),
        "family_confidence": _finite_float(candidate.family_confidence),
        "family_best_similarity": _finite_float(
            candidate.family_best_similarity
        ),
        "runner_up_family": str(candidate.runner_up_family),
        "family_margin": _finite_float(candidate.family_margin),
        "rarity": str(candidate.rarity),
        "rarity_confidence": _finite_float(candidate.rarity_confidence),
        "context": {
            "art_box": list(context.art_box),
            "card_box": list(context.card_box),
            "nameplate_dark_fraction": _finite_float(
                context.nameplate_dark_fraction
            ),
            "art_standard_deviation": _finite_float(
                context.art_standard_deviation
            ),
            "art_edge_density": _finite_float(context.art_edge_density),
            "frame_line_ratio": _finite_float(context.frame_line_ratio),
            "accepted": bool(context.accepted),
            "reason": str(context.reason),
        },
    }


def latest_dev_session(root: str | Path) -> Path | None:
    directory = Path(root)
    sessions = [
        path
        for path in directory.glob("session_*")
        if path.is_dir()
    ]
    if not sessions:
        return None
    return max(sessions, key=_safe_mtime)


def export_dev_session(
    session_dir: str | Path,
    output_path: str | Path | None = None,
) -> Path:
    """Export a complete local Belt Dev session without changing its labels."""

    session = Path(session_dir).resolve()
    if not session.is_dir():
        raise ValueError(f"Belt Dev session does not exist: {session}")
    if output_path is None:
        exports = session.parent / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        output = exports / f"{session.name}.zip"
    else:
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.writestr(
                "README.txt",
                "Belt Tracker Developer Mode capture.\n"
                "Contains only the selected belt region, local detector diagnostics, "
                "and human review labels if present.\n"
                "Nothing in this archive is automatically trusted as training data.\n",
            )
            for path in sorted(session.rglob("*")):
                if (
                    not path.is_file()
                    or path.is_symlink()
                    or path == output
                    or path == temporary
                    or path.suffix.lower() == ".tmp"
                ):
                    continue
                archive.write(
                    path,
                    (Path("session") / path.relative_to(session)).as_posix(),
                )
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output


def _deduplicate_candidates(
    candidates: Sequence[_PreparedCandidate],
) -> list[_PreparedCandidate]:
    kept: list[_PreparedCandidate] = []
    ordered = sorted(
        candidates,
        key=lambda item: (
            bool(item.candidate.accepted),
            _finite_float(item.candidate.raw_best_similarity),
            _finite_float(item.candidate.identity_margin),
        ),
        reverse=True,
    )
    for item in ordered:
        if any(
            _same_physical_box(item.box, previous.box)
            for previous in kept
        ):
            continue
        kept.append(item)
    return sorted(kept, key=lambda item: _box_center(item.box)[0])


def _appearance_signature(
    frame_bgr: np.ndarray,
    candidate: CardCandidate,
    production_track_id: int | None,
) -> _AppearanceSignature | None:
    try:
        x, y, width, height = (
            int(round(float(value)))
            for value in candidate.context.art_box
        )
    except (AttributeError, TypeError, ValueError):
        return None
    frame_height, frame_width = frame_bgr.shape[:2]
    left = max(0, x)
    top = max(0, y)
    right = min(frame_width, x + width)
    bottom = min(frame_height, y + height)
    art = frame_bgr[top:bottom, left:right]
    if art.shape[0] < 8 or art.shape[1] < 8:
        return None
    try:
        descriptor = identity_features(art)
        perceptual_hash = _perceptual_hash(art)
    except (ValueError, cv2.error):
        return None
    return _AppearanceSignature(
        descriptor=descriptor,
        perceptual_hash=perceptual_hash,
        accepted=bool(candidate.accepted),
        production_track_id=production_track_id,
    )


def _appearance_matches(
    track: _DevTrack,
    current: _AppearanceSignature | None,
) -> bool:
    if current is None or not track.appearances:
        return True
    if (
        current.production_track_id is not None
        and current.production_track_id in track.production_track_ids
    ):
        return True
    references = [
        item
        for item in track.appearances
        if (
            item.accepted
            or (
                item.production_track_id is not None
                and item.production_track_id in track.production_track_ids
            )
        )
    ]
    if not references:
        references = list(track.appearances)[-5:]
    for reference in references:
        similarity = float(reference.descriptor @ current.descriptor)
        hash_distance = (
            reference.perceptual_hash ^ current.perceptual_hash
        ).bit_count()
        if (
            similarity >= 0.86
            or (similarity >= 0.78 and hash_distance <= 16)
            or hash_distance <= 10
        ):
            return True
    return False


def _valid_card_box(
    candidate: CardCandidate,
    frame_shape: tuple[int, ...],
) -> tuple[int, int, int, int] | None:
    try:
        values = tuple(int(round(float(value))) for value in candidate.context.card_box)
        if len(values) != 4:
            return None
        x, y, width, height = values
    except (AttributeError, TypeError, ValueError):
        return None
    frame_height, frame_width = frame_shape[:2]
    if (
        width < 8
        or height < 8
        or x >= frame_width
        or y >= frame_height
        or x + width <= 0
        or y + height <= 0
    ):
        return None
    return x, y, width, height


def _build_crop(
    frame_bgr: np.ndarray,
    candidate: CardCandidate,
    box: tuple[int, int, int, int],
    *,
    frame_number: int,
    captured_at: float,
    quality_score: float,
    diagnostic: dict[str, object],
) -> _SavedCrop | None:
    frame_height, frame_width = frame_bgr.shape[:2]
    x, y, width, height = box
    left = max(0, x)
    top = max(0, y)
    right = min(frame_width, x + width)
    bottom = min(frame_height, y + height)
    if right - left < 8 or bottom - top < 8:
        return None
    crop = frame_bgr[top:bottom, left:right].copy()
    art_x, art_y, art_width, art_height = candidate.context.art_box
    art_left = max(left, int(art_x))
    art_top = max(top, int(art_y))
    art_right = min(right, int(art_x + art_width))
    art_bottom = min(bottom, int(art_y + art_height))
    art_box_in_crop = (
        max(0, art_left - left),
        max(0, art_top - top),
        max(0, art_right - art_left),
        max(0, art_bottom - art_top),
    )
    return _SavedCrop(
        frame_number=int(frame_number),
        captured_at=float(captured_at),
        image=crop,
        card_box=box,
        art_box_in_crop=art_box_in_crop,
        source_frame_shape=tuple(int(value) for value in frame_bgr.shape),
        quality_score=quality_score,
        perceptual_hash=_perceptual_hash(crop),
        candidate=diagnostic,
    )


def _crop_quality(
    frame_bgr: np.ndarray,
    candidate: CardCandidate,
    box: tuple[int, int, int, int],
) -> float:
    frame_height, frame_width = frame_bgr.shape[:2]
    x, y, width, height = box
    visible_width = max(0, min(frame_width, x + width) - max(0, x))
    visible_height = max(0, min(frame_height, y + height) - max(0, y))
    completeness = (visible_width * visible_height) / max(1, width * height)
    art_x, art_y, art_width, art_height = candidate.context.art_box
    art = frame_bgr[
        max(0, art_y) : min(frame_height, art_y + art_height),
        max(0, art_x) : min(frame_width, art_x + art_width),
    ]
    sharpness = 0.0
    if art.size:
        gray = cv2.cvtColor(art, cv2.COLOR_BGR2GRAY)
        sharpness = min(
            1.0,
            math.log1p(max(0.0, float(cv2.Laplacian(gray, cv2.CV_64F).var())))
            / math.log1p(900.0),
        )
    similarity = min(1.0, max(0.0, _finite_float(candidate.raw_best_similarity)))
    margin = min(1.0, max(0.0, _finite_float(candidate.identity_margin) / 0.18))
    score = (
        completeness * 0.30
        + sharpness * 0.22
        + similarity * 0.20
        + margin * 0.13
        + (0.15 if candidate.accepted else 0.0)
    )
    return round(min(1.0, max(0.0, score)), 6)


def _perceptual_hash(image: np.ndarray) -> int:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    normalized = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
    average = float(np.mean(normalized))
    bits = normalized >= average
    value = 0
    for bit in bits.reshape(-1):
        value = (value << 1) | int(bit)
    return value


def _same_physical_box(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    if _intersection_over_union(first, second) >= 0.40:
        return True
    first_center = _box_center(first)
    second_center = _box_center(second)
    horizontal_overlap = max(
        0,
        min(first[0] + first[2], second[0] + second[2])
        - max(first[0], second[0]),
    )
    horizontal_overlap_ratio = horizontal_overlap / max(
        1,
        min(first[2], second[2]),
    )
    if (
        horizontal_overlap_ratio >= 0.62
        and abs(first_center[0] - second_center[0])
        <= max(first[2], second[2]) * 0.60
    ):
        # Multiple scale hypotheses can cover the upper and lower halves of
        # one card without enough two-dimensional IoU. Belt cards cannot
        # occupy the same horizontal slot, so retain only the best hypothesis.
        return True
    return (
        abs(first_center[0] - second_center[0])
        <= max(4.0, min(first[2], second[2]) * 0.22)
        and abs(first_center[1] - second_center[1])
        <= max(4.0, min(first[3], second[3]) * 0.35)
    )


def _review_worthy_track(track: _DevTrack) -> bool:
    observations = list(track.observations)
    if not observations:
        return False
    accepted = [
        item
        for item in observations
        if isinstance(item.get("candidate"), dict)
        and bool(item["candidate"].get("accepted", False))
    ]
    if accepted:
        return True
    if len(observations) < 2:
        return False
    movement = track.maximum_center_x - track.minimum_center_x
    required_movement = max(
        6.0,
        max(track.initial_box[2], track.last_box[2]) * 0.10,
    )
    best_similarity = max(
        (
            _finite_float(item["candidate"].get("raw_best_similarity", 0.0))
            for item in observations
            if isinstance(item.get("candidate"), dict)
        ),
        default=0.0,
    )
    return movement >= required_movement and best_similarity >= 0.72


def _intersection_over_union(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    union = first[2] * first[3] + second[2] * second[3] - intersection
    return intersection / union if union > 0 else 0.0


def _box_center(box: tuple[int, int, int, int]) -> tuple[float, float]:
    return box[0] + box[2] / 2.0, box[1] + box[3] / 2.0


def _finite_float(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _json_default(value: Any):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return str(value)


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
