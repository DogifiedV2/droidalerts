from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from typing import Any

import cv2
import numpy as np

from .. import __version__
from ..config import data_dir
from ..image_io import write_cv_image
from .recognition import CardCandidate


MAX_SAMPLES_PER_DROID = 20
MAX_REVIEW_SAMPLES = 100
MINIMUM_LABEL_READS = 3
MINIMUM_SAMPLE_IDENTITY_CONFIDENCE = 0.90
DUPLICATE_HASH_DISTANCE = 6
STALE_APPEARANCE_SECONDS = 75.0
_INDEX_VERSION = 2


def belt_template_samples_dir() -> Path:
    """Writable, local-only blueprint collection used to build image templates."""

    return data_dir() / "belt_template_samples"


@dataclass(frozen=True)
class CollectionUpdate:
    action: str
    name: str = ""
    samples_for_droid: int = 0
    detail: str = ""


@dataclass(frozen=True)
class _StoredSample:
    name: str
    image_path: Path
    metadata_path: Path
    perceptual_hash: int
    quality_score: float


@dataclass
class _BestCrop:
    image: np.ndarray
    name: str
    family: str
    family_confidence: float
    rarity: str
    rarity_confidence: float
    identity_confidence: float
    quality_score: float
    quality_components: dict[str, float]
    perceptual_hash: int
    frame_number: int
    card_box: tuple[int, int, int, int]
    art_box_in_crop: tuple[int, int, int, int]
    source_frame_shape: tuple[int, ...]


@dataclass
class _Appearance:
    track_id: int
    first_seen_at: float
    last_seen_at: float
    names: Counter[str] = field(default_factory=Counter)
    strong_names: Counter[str] = field(default_factory=Counter)
    confidences: dict[str, list[float]] = field(default_factory=dict)
    family_votes: Counter[tuple[str, str]] = field(default_factory=Counter)
    rarity_votes: Counter[tuple[str, str]] = field(default_factory=Counter)
    confirmed_name: str = ""
    best: _BestCrop | None = None


class BeltTemplateSampleCollector:
    """Keep one strong crop per appearance and a bounded diverse set per droid.

    The Belt Tracker still owns physical tracking and temporal confirmation. This
    collector receives the track ID assigned to each accepted template candidate,
    remembers only that appearance's best complete crop, and writes it only after
    the track is confirmed. Existing samples are loaded at startup, so caps and
    duplicate checks continue to apply across app restarts.
    """

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        max_samples_per_droid: int = MAX_SAMPLES_PER_DROID,
        maximum_review_samples: int = MAX_REVIEW_SAMPLES,
        minimum_label_reads: int = MINIMUM_LABEL_READS,
        minimum_identity_confidence: float = MINIMUM_SAMPLE_IDENTITY_CONFIDENCE,
        duplicate_hash_distance: int = DUPLICATE_HASH_DISTANCE,
    ) -> None:
        self.root = Path(root) if root is not None else belt_template_samples_dir()
        # Template predictions are review material, never ground truth. Keeping
        # them outside confirmed/ prevents accidental self-training.
        self.detections_dir = self.root / "detections"
        self.review_dir = self.root / "review"
        self.event_log = self.root / "collection.jsonl"
        self.max_samples_per_droid = max(1, int(max_samples_per_droid))
        self.maximum_review_samples = max(0, int(maximum_review_samples))
        self.minimum_label_reads = max(2, int(minimum_label_reads))
        self.minimum_identity_confidence = min(
            1.0,
            max(0.0, float(minimum_identity_confidence)),
        )
        self.duplicate_hash_distance = max(0, int(duplicate_hash_distance))
        self._appearances: dict[int, _Appearance] = {}
        self._samples_by_name: dict[str, list[_StoredSample]] = {}
        self._review_samples: list[_StoredSample] = []
        self.detections_dir.mkdir(parents=True, exist_ok=True)
        self.review_dir.mkdir(parents=True, exist_ok=True)
        self._load_existing_samples()

    def status(self, update: CollectionUpdate | None = None) -> dict[str, object]:
        values: dict[str, object] = {
            "enabled": True,
            "path": self._relative_path(),
            "total_samples": self.total_samples,
            "droid_count": self.droid_count,
            "review_count": len(self._review_samples),
            "max_per_droid": self.max_samples_per_droid,
        }
        if update is not None:
            values.update(
                {
                    "action": update.action,
                    "name": update.name,
                    "samples_for_droid": update.samples_for_droid,
                    "detail": update.detail,
                }
            )
        return values

    @property
    def total_samples(self) -> int:
        return sum(len(samples) for samples in self._samples_by_name.values())

    @property
    def droid_count(self) -> int:
        return sum(bool(samples) for samples in self._samples_by_name.values())

    def observe(
        self,
        frame_bgr: np.ndarray,
        candidates: Sequence[CardCandidate],
        observation_track_ids: Mapping[int, int],
        *,
        now: float,
        frame_number: int,
    ) -> None:
        """Consider accepted candidates from one template scan.

        ``candidates`` must use the same accepted-candidate order as the
        observations passed to ``BeltTracker.update``. Missing track mappings are
        deliberately ignored; the tracker rejected or quarantined those reads.
        """

        if not isinstance(frame_bgr, np.ndarray) or frame_bgr.ndim != 3:
            return
        for source_index, candidate in enumerate(candidates):
            track_id = observation_track_ids.get(source_index)
            if track_id is None or not candidate.accepted:
                continue
            try:
                track_id = int(track_id)
            except (TypeError, ValueError):
                continue
            name = str(candidate.canonical_name).strip()
            if not name:
                continue
            appearance = self._appearances.get(track_id)
            if appearance is None:
                appearance = _Appearance(track_id, float(now), float(now))
                self._appearances[track_id] = appearance
            appearance.last_seen_at = float(now)
            appearance.names[name] += 1
            confidence = min(1.0, max(0.0, float(candidate.ocr_confidence)))
            appearance.confidences.setdefault(name, []).append(confidence)
            if confidence >= self.minimum_identity_confidence:
                appearance.strong_names[name] += 1
            family = str(candidate.family).strip()
            if family:
                appearance.family_votes[(name, family)] += 1
            rarity = str(candidate.rarity).strip()
            if rarity:
                appearance.rarity_votes[(name, rarity)] += 1

            crop = _build_best_crop(frame_bgr, candidate, frame_number=frame_number)
            if crop is None or confidence < self.minimum_identity_confidence:
                continue
            if appearance.best is None or crop.quality_score > appearance.best.quality_score:
                appearance.best = crop

    def process_events(self, events: Sequence[Any]) -> list[CollectionUpdate]:
        updates: list[CollectionUpdate] = []
        for event in events:
            try:
                kind = str(event.kind)
                track_id = int(event.track.id)
                name = str(event.track.name).strip()
            except (AttributeError, TypeError, ValueError):
                continue
            appearance = self._appearances.get(track_id)
            if kind == "entered":
                if appearance is None:
                    appearance = _Appearance(track_id, 0.0, 0.0)
                    self._appearances[track_id] = appearance
                appearance.confirmed_name = name
            elif kind == "exited" and appearance is not None:
                update = self._finalize(appearance, reason="track_exited")
                self._appearances.pop(track_id, None)
                if update is not None:
                    updates.append(update)
        return updates

    def expire(self, now: float) -> list[CollectionUpdate]:
        """Bound memory if an unconfirmed tracker path never emits an exit event."""

        updates: list[CollectionUpdate] = []
        stale_ids = [
            track_id
            for track_id, appearance in self._appearances.items()
            if float(now) - appearance.last_seen_at > STALE_APPEARANCE_SECONDS
        ]
        for track_id in stale_ids:
            appearance = self._appearances.pop(track_id)
            if appearance.confirmed_name:
                update = self._finalize(appearance, reason="collector_timeout")
                if update is not None:
                    updates.append(update)
        return updates

    def close(self) -> list[CollectionUpdate]:
        updates: list[CollectionUpdate] = []
        for appearance in tuple(self._appearances.values()):
            if appearance.confirmed_name:
                update = self._finalize(appearance, reason="tracker_stopped")
                if update is not None:
                    updates.append(update)
        self._appearances.clear()
        return updates

    def _finalize(self, appearance: _Appearance, *, reason: str) -> CollectionUpdate | None:
        best = appearance.best
        if best is None:
            return None
        confirmed_name = appearance.confirmed_name
        names = set(appearance.names)
        label_is_safe = (
            bool(confirmed_name)
            and names == {confirmed_name}
            and appearance.strong_names[confirmed_name] >= self.minimum_label_reads
            and best.name == confirmed_name
        )
        try:
            if label_is_safe:
                family = _majority_attribute(appearance.family_votes, confirmed_name) or best.family
                rarity = _majority_attribute(appearance.rarity_votes, confirmed_name) or best.rarity
                metadata = self._sample_metadata(
                    appearance,
                    best,
                    name=confirmed_name,
                    family=family,
                    rarity=rarity,
                    finalization_reason=reason,
                )
                return self._store_detection(best, metadata)

            review_reason = _review_reason(
                appearance,
                best,
                minimum_label_reads=self.minimum_label_reads,
            )
            if confirmed_name and self.maximum_review_samples:
                metadata = self._sample_metadata(
                    appearance,
                    best,
                    name=confirmed_name or best.name,
                    family=best.family,
                    rarity=best.rarity,
                    finalization_reason=reason,
                )
                metadata["review_reason"] = review_reason
                return self._store_review(best, metadata)
        except Exception as exc:
            # Collection is optional development work. A disk/codec/metadata
            # failure must never take the live Belt Tracker down overnight.
            return CollectionUpdate("error", confirmed_name or best.name, detail=str(exc))
        return None

    def _store_detection(
        self,
        best: _BestCrop,
        metadata: dict[str, object],
    ) -> CollectionUpdate:
        name = str(metadata["name"])
        existing = self._samples_by_name.setdefault(name, [])
        nearest = _nearest_sample(existing, best.perceptual_hash)
        replacement: _StoredSample | None = None
        action = "saved"

        if nearest is not None and nearest[0] <= self.duplicate_hash_distance:
            similar = nearest[1]
            if best.quality_score <= similar.quality_score + 0.01:
                return CollectionUpdate("duplicate", name, len(existing))
            replacement = similar
            action = "replaced"
        elif len(existing) >= self.max_samples_per_droid:
            weakest = min(existing, key=lambda sample: sample.quality_score)
            novelty = nearest[0] if nearest is not None else 64
            is_better = best.quality_score >= weakest.quality_score + 0.015
            adds_diversity = novelty >= 18 and best.quality_score >= weakest.quality_score - 0.04
            if not (is_better or adds_diversity):
                return CollectionUpdate("capped", name, len(existing))
            replacement = weakest
            action = "replaced"

        record = self._write_sample(self.detections_dir / _slug(name), best, metadata)
        if replacement is not None:
            _delete_sample(replacement)
            existing.remove(replacement)
        existing.append(record)
        existing.sort(key=lambda sample: (-sample.quality_score, sample.image_path.name))
        self._log_collection_event(action, metadata, record)
        return CollectionUpdate(action, name, len(existing))

    def _store_review(
        self,
        best: _BestCrop,
        metadata: dict[str, object],
    ) -> CollectionUpdate:
        name = str(metadata.get("name") or best.name)
        nearest = _nearest_sample(self._review_samples, best.perceptual_hash)
        if nearest is not None and nearest[0] <= self.duplicate_hash_distance:
            return CollectionUpdate("review_duplicate", name, detail=str(metadata["review_reason"]))
        replacement: _StoredSample | None = None
        if len(self._review_samples) >= self.maximum_review_samples:
            replacement = min(self._review_samples, key=lambda sample: sample.quality_score)
            if best.quality_score <= replacement.quality_score:
                return CollectionUpdate("review_capped", name, detail=str(metadata["review_reason"]))
        record = self._write_sample(self.review_dir, best, metadata)
        if replacement is not None:
            _delete_sample(replacement)
            self._review_samples.remove(replacement)
        self._review_samples.append(record)
        self._log_collection_event("reviewed", metadata, record)
        return CollectionUpdate("reviewed", name, detail=str(metadata["review_reason"]))

    def _write_sample(
        self,
        folder: Path,
        best: _BestCrop,
        metadata: dict[str, object],
    ) -> _StoredSample:
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        sample_id = f"{stamp}_t{int(metadata['track_id']):05d}_f{best.frame_number:06d}"
        image_path = folder / f"{sample_id}.png"
        metadata_path = folder / f"{sample_id}.json"
        temporary_image = folder / f".{sample_id}.tmp.png"
        temporary_metadata = folder / f".{sample_id}.tmp.json"
        metadata = {
            "index_version": _INDEX_VERSION,
            "sample_id": sample_id,
            "image_file": image_path.name,
            **metadata,
        }
        try:
            write_cv_image(
                temporary_image,
                best.image,
                (cv2.IMWRITE_PNG_COMPRESSION, 3),
            )
            temporary_image.replace(image_path)
            temporary_metadata.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary_metadata.replace(metadata_path)
        except Exception:
            for path in (temporary_image, temporary_metadata, image_path, metadata_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        return _StoredSample(
            name=str(metadata["name"]),
            image_path=image_path,
            metadata_path=metadata_path,
            perceptual_hash=int(str(metadata["perceptual_hash"]), 16),
            quality_score=float(metadata["quality_score"]),
        )

    def _sample_metadata(
        self,
        appearance: _Appearance,
        best: _BestCrop,
        *,
        name: str,
        family: str,
        rarity: str,
        finalization_reason: str,
    ) -> dict[str, object]:
        confidences = appearance.confidences.get(name, [])
        return {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "app_version": __version__,
            "detector": "templates",
            "label_source": "template_prediction",
            "name": name,
            "detected_name": name,
            "family": family,
            "detected_family": family,
            "family_confidence": best.family_confidence,
            "rarity": rarity,
            "detected_rarity": rarity,
            "rarity_confidence": best.rarity_confidence,
            "track_id": appearance.track_id,
            "frame_number": best.frame_number,
            "first_seen_at": appearance.first_seen_at,
            "last_seen_at": appearance.last_seen_at,
            "finalization_reason": finalization_reason,
            "label_reads": int(appearance.names[name]),
            "strong_label_reads": int(appearance.strong_names[name]),
            "observed_names": dict(sorted(appearance.names.items())),
            "minimum_identity_confidence": min(confidences) if confidences else 0.0,
            "average_identity_confidence": (
                sum(confidences) / len(confidences) if confidences else 0.0
            ),
            "best_identity_confidence": best.identity_confidence,
            "quality_score": best.quality_score,
            "quality_components": best.quality_components,
            "perceptual_hash": f"{best.perceptual_hash:016x}",
            "card_box": list(best.card_box),
            "art_box_in_crop": list(best.art_box_in_crop),
            "source_frame_shape": list(best.source_frame_shape),
            "crop_shape": list(best.image.shape),
        }

    def _load_existing_samples(self) -> None:
        for metadata_path in self.detections_dir.glob("*/*.json"):
            record = _load_sample(metadata_path)
            if record is not None:
                self._samples_by_name.setdefault(record.name, []).append(record)
        for samples in self._samples_by_name.values():
            samples.sort(key=lambda sample: (-sample.quality_score, sample.image_path.name))
        for metadata_path in self.review_dir.glob("**/*.json"):
            record = _load_sample(metadata_path)
            if record is not None:
                self._review_samples.append(record)

    def _log_collection_event(
        self,
        action: str,
        metadata: Mapping[str, object],
        record: _StoredSample,
    ) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "name": record.name,
            "family": metadata.get("family", ""),
            "rarity": metadata.get("rarity", ""),
            "quality_score": record.quality_score,
            "image": record.image_path.relative_to(self.root).as_posix(),
        }
        try:
            with self.event_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _relative_path(self) -> str:
        try:
            return self.root.relative_to(data_dir()).as_posix()
        except ValueError:
            return str(self.root)


def _build_best_crop(
    frame_bgr: np.ndarray,
    candidate: CardCandidate,
    *,
    frame_number: int,
) -> _BestCrop | None:
    frame_height, frame_width = frame_bgr.shape[:2]
    x, y, name_width, name_height = (int(value) for value in candidate.name_box)
    if name_height <= 0:
        return None
    # The selected belt region already defines the complete vertical card area.
    # Use its height as the card-scale reference so long names rendered with a
    # smaller font do not produce vertically clipped crops. The recognizer's
    # context box and a name-relative anchor establish the horizontal center.
    context_x, _context_y, context_width, _context_height = candidate.context.card_box
    context_center_x = context_x + context_width / 2.0
    center_x = max(context_center_x, x + 2.6 * name_height)
    card_width = max(
        round(frame_height * 0.90),
        context_width + round(2.0 * name_height),
    )
    left = round(center_x - card_width / 2.0)
    top = 0
    right = left + card_width
    bottom = frame_height
    art_left = left + round(card_width * 0.18)
    art_top = max(round(frame_height * 0.08), round(y - frame_height * 0.58))
    art_right = right - round(card_width * 0.18)
    art_bottom = round(y - 0.1 * name_height)
    # A crop touching an edge may be missing pixels even when its identity can
    # still be matched. Those partial cards are unsuitable review examples.
    if left < 0 or right > frame_width:
        return None
    if (
        right - left < 24
        or bottom - top < 24
        or art_left < left
        or art_top < top
        or art_right > right
        or art_bottom <= art_top
    ):
        return None
    image = frame_bgr[top:bottom, left:right].copy()
    art = frame_bgr[art_top:art_bottom, art_left:art_right]
    if image.size == 0 or art.size == 0:
        return None

    gray = cv2.cvtColor(art, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharpness_score = min(1.0, math.log1p(max(0.0, sharpness)) / math.log1p(1200.0))
    center_x = (left + right) / 2.0
    centrality = max(0.0, 1.0 - abs(center_x - frame_width / 2.0) / max(1.0, frame_width / 2.0))
    resolution_score = min(1.0, math.sqrt(float(image.shape[0] * image.shape[1])) / 190.0)
    context_score = (
        min(1.0, candidate.context.art_standard_deviation / 64.0)
        + min(1.0, candidate.context.art_edge_density / 0.10)
        + min(1.0, candidate.context.frame_line_ratio)
    ) / 3.0
    confidence = min(1.0, max(0.0, float(candidate.ocr_confidence)))
    quality_components = {
        "identity_confidence": confidence,
        "centrality": centrality,
        "sharpness": sharpness_score,
        "resolution": resolution_score,
        "card_context": context_score,
    }
    quality_score = (
        confidence * 0.35
        + centrality * 0.20
        + sharpness_score * 0.20
        + resolution_score * 0.10
        + context_score * 0.15
    )
    return _BestCrop(
        image=image,
        name=str(candidate.canonical_name),
        family=str(candidate.family),
        family_confidence=float(candidate.family_confidence),
        rarity=str(candidate.rarity),
        rarity_confidence=float(candidate.rarity_confidence),
        identity_confidence=confidence,
        quality_score=quality_score,
        quality_components=quality_components,
        perceptual_hash=_difference_hash(art),
        frame_number=int(frame_number),
        card_box=(left, top, right - left, bottom - top),
        art_box_in_crop=(
            art_left - left,
            art_top - top,
            art_right - art_left,
            art_bottom - art_top,
        ),
        source_frame_shape=tuple(int(value) for value in frame_bgr.shape),
    )


def _difference_hash(image_bgr: np.ndarray) -> int:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    comparisons = resized[:, 1:] > resized[:, :-1]
    value = 0
    for bit in comparisons.reshape(-1):
        value = (value << 1) | int(bool(bit))
    return value


def _nearest_sample(
    samples: Sequence[_StoredSample],
    perceptual_hash: int,
) -> tuple[int, _StoredSample] | None:
    if not samples:
        return None
    return min(
        ((_hamming_distance(sample.perceptual_hash, perceptual_hash), sample) for sample in samples),
        key=lambda item: (item[0], -item[1].quality_score, item[1].image_path.name),
    )


def _hamming_distance(first: int, second: int) -> int:
    return (int(first) ^ int(second)).bit_count()


def _majority_attribute(votes: Counter[tuple[str, str]], name: str) -> str:
    matching = Counter({value: count for (vote_name, value), count in votes.items() if vote_name == name})
    if not matching:
        return ""
    ranked = matching.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return ""
    return ranked[0][0]


def _review_reason(
    appearance: _Appearance,
    best: _BestCrop,
    *,
    minimum_label_reads: int,
) -> str:
    if len(appearance.names) > 1:
        return "conflicting_track_names"
    if appearance.confirmed_name and best.name != appearance.confirmed_name:
        return "best_crop_name_mismatch"
    if (
        appearance.confirmed_name
        and appearance.strong_names[appearance.confirmed_name] < minimum_label_reads
    ):
        return "insufficient_high_confidence_reads"
    return "unsafe_label_consensus"


def _load_sample(metadata_path: Path) -> _StoredSample | None:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            return None
        image_path = metadata_path.parent / str(metadata["image_file"])
        if not image_path.is_file():
            return None
        name = str(metadata["name"]).strip()
        perceptual_hash = int(str(metadata["perceptual_hash"]), 16)
        quality_score = float(metadata["quality_score"])
        if not name or not math.isfinite(quality_score):
            return None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return _StoredSample(name, image_path, metadata_path, perceptual_hash, quality_score)


def _delete_sample(sample: _StoredSample) -> None:
    for path in (sample.image_path, sample.metadata_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return slug or "unknown"
