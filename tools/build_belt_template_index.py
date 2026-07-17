"""Compile confirmed Belt Tracker crops into one compact runtime index."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.names import DROID_NAMES  # noqa: E402
from droid_alerts.belt.template_recognition import (  # noqa: E402
    BeltTemplateIndex,
    FAMILY_HISTOGRAM_WEIGHT,
    FAMILY_WORD_WEIGHT,
    INDEX_FILE,
    INDEX_VERSION,
    family_features,
    identity_features,
)


FAMILY_ORDER = ("Default", "Gold", "Diamond", "Rainbow", "Beskar")


@dataclass(frozen=True)
class SourceSample:
    name: str
    family: str
    image_file: Path
    quality: float
    card_width_ratio: float
    art_left_ratio: float
    art_top_ratio: float
    art_width_ratio: float
    art_height_ratio: float
    identity_hog: np.ndarray
    family_histogram: np.ndarray
    family_word: np.ndarray


def _read_image(path: Path) -> np.ndarray:
    encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError(f"Unreadable template image: {path}")
    return image


def _load_samples(
    confirmed_dir: Path,
    *,
    require_every_droid: bool = True,
) -> list[SourceSample]:
    samples: list[SourceSample] = []
    for metadata_path in sorted(confirmed_dir.glob("*/*.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            name = str(metadata["name"])
            family = str(metadata["family"])
            image_path = metadata_path.with_suffix(".png")
            image = _read_image(image_path)
            art_x, art_y, art_width, art_height = (
                int(value) for value in metadata["art_box_in_crop"]
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid confirmed sample metadata: {metadata_path}") from exc

        if name not in DROID_NAMES:
            raise ValueError(f"Unknown droid name {name!r}: {metadata_path}")
        if family not in FAMILY_ORDER:
            raise ValueError(f"Unknown card family {family!r}: {metadata_path}")
        image_height, image_width = image.shape[:2]
        art = image[art_y : art_y + art_height, art_x : art_x + art_width]
        if art.size == 0 or art.shape[0] != art_height or art.shape[1] != art_width:
            raise ValueError(f"Artwork crop falls outside its image: {metadata_path}")

        identity_hog = identity_features(art)
        family_histogram, family_word = family_features(image)
        samples.append(
            SourceSample(
                name=name,
                family=family,
                image_file=image_path,
                quality=float(metadata.get("quality_score", 0.0)),
                card_width_ratio=image_width / image_height,
                art_left_ratio=art_x / image_height,
                art_top_ratio=art_y / image_height,
                art_width_ratio=art_width / image_height,
                art_height_ratio=art_height / image_height,
                identity_hog=identity_hog,
                family_histogram=family_histogram,
                family_word=family_word,
            )
        )

    if not samples:
        raise ValueError(f"No confirmed template metadata found under {confirmed_dir}")
    missing = [name for name in DROID_NAMES if not any(item.name == name for item in samples)]
    if require_every_droid and missing:
        raise ValueError(f"Template library is missing {len(missing)} droids: {', '.join(missing)}")
    return samples


def _diverse_sample_indices(samples: list[SourceSample], maximum: int) -> list[int]:
    if len(samples) <= maximum:
        return list(range(len(samples)))
    hog = np.stack([item.identity_hog for item in samples])
    similarities = hog @ hog.T

    # Start with the medoid rather than an arbitrary timestamp. Subsequent
    # samples maximize distance from the selected set, retaining the useful
    # animation/angle variation without carrying every near-duplicate crop.
    chosen = [int(np.argmax(similarities.mean(axis=1)))]
    minimum_distance = 1.0 - similarities[chosen[0]]
    while len(chosen) < maximum:
        next_index = int(np.argmax(minimum_distance))
        chosen.append(next_index)
        minimum_distance = np.minimum(
            minimum_distance,
            1.0 - similarities[next_index],
        )
    return chosen


def build_index(
    confirmed_dir: Path,
    output_path: Path,
    *,
    templates_per_droid: int = 8,
) -> dict[str, object]:
    samples = _load_samples(confirmed_dir)
    templates_per_droid = max(1, int(templates_per_droid))

    identity_hog: list[np.ndarray] = []
    identity_offsets = [0]
    selected_counts: dict[str, int] = {}
    for name in DROID_NAMES:
        named = [item for item in samples if item.name == name]
        selected = _diverse_sample_indices(named, templates_per_droid)
        for index in selected:
            identity_hog.append(named[index].identity_hog)
        selected_counts[name] = len(selected)
        identity_offsets.append(len(identity_hog))

    family_histograms: list[np.ndarray] = []
    family_words: list[np.ndarray] = []
    family_offsets = [0]
    family_counts: dict[str, int] = {}
    for family in FAMILY_ORDER:
        matching = [item for item in samples if item.family == family]
        family_histograms.extend(item.family_histogram for item in matching)
        family_words.extend(item.family_word for item in matching)
        family_counts[family] = len(matching)
        family_offsets.append(len(family_histograms))

    ratios = {
        "card_width_ratio": float(np.median([item.card_width_ratio for item in samples])),
        "art_left_ratio": float(np.median([item.art_left_ratio for item in samples])),
        "art_top_ratio": float(np.median([item.art_top_ratio for item in samples])),
        "art_width_ratio": float(np.median([item.art_width_ratio for item in samples])),
        "art_height_ratio": float(np.median([item.art_height_ratio for item in samples])),
    }
    manifest = {
        "version": INDEX_VERSION,
        "confirmed_samples": len(samples),
        "droid_count": len(DROID_NAMES),
        "identity_templates": len(identity_hog),
        "templates_per_droid": templates_per_droid,
        "selected_counts": selected_counts,
        "family_counts": family_counts,
        "identity_descriptor": "48x48-hog",
        "family_weights": [FAMILY_HISTOGRAM_WEIGHT, FAMILY_WORD_WEIGHT],
        "geometry": ratios,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary_path.open("wb") as handle:
        np.savez_compressed(
            handle,
            version=np.asarray([INDEX_VERSION], dtype=np.int32),
            identity_hog=np.stack(identity_hog).astype(np.float32),
            identity_names=np.asarray(DROID_NAMES),
            identity_name_offsets=np.asarray(identity_offsets, dtype=np.int32),
            family_histograms=np.stack(family_histograms).astype(np.float32),
            family_words=np.stack(family_words).astype(np.float32),
            family_labels=np.asarray(FAMILY_ORDER),
            family_offsets=np.asarray(family_offsets, dtype=np.int32),
            manifest_json=np.asarray([json.dumps(manifest, sort_keys=True)]),
            **{key: np.asarray(value, dtype=np.float32) for key, value in ratios.items()},
        )
    temporary_path.replace(output_path)
    return manifest


def _append_unique_vectors(
    existing: np.ndarray,
    additions: list[np.ndarray],
    *,
    duplicate_similarity: float = 0.999,
) -> tuple[np.ndarray, int]:
    rows = [np.asarray(row, dtype=np.float32) for row in existing]
    added = 0
    for addition in additions:
        vector = np.asarray(addition, dtype=np.float32)
        if rows and max(float(row @ vector) for row in rows) >= duplicate_similarity:
            continue
        rows.append(vector)
        added += 1
    return np.stack(rows).astype(np.float32), added


def _append_unique_family_vectors(
    existing_histograms: np.ndarray,
    existing_words: np.ndarray,
    additions: list[SourceSample],
    *,
    duplicate_similarity: float = 0.999,
) -> tuple[np.ndarray, np.ndarray, int]:
    histograms = [np.asarray(row, dtype=np.float32) for row in existing_histograms]
    words = [np.asarray(row, dtype=np.float32) for row in existing_words]
    added = 0
    for sample in additions:
        duplicate = any(
            float(old_histogram @ sample.family_histogram) >= duplicate_similarity
            and (
                float(old_word @ sample.family_word) >= duplicate_similarity
                or (
                    float(np.linalg.norm(old_word)) <= 1e-6
                    and float(np.linalg.norm(sample.family_word)) <= 1e-6
                )
            )
            for old_histogram, old_word in zip(histograms, words, strict=True)
        )
        if duplicate:
            continue
        histograms.append(sample.family_histogram)
        words.append(sample.family_word)
        added += 1
    return (
        np.stack(histograms).astype(np.float32),
        np.stack(words).astype(np.float32),
        added,
    )


def _load_manifest(index_path: Path) -> dict[str, object]:
    try:
        with np.load(index_path, allow_pickle=False) as archive:
            raw = str(np.asarray(archive["manifest_json"]).reshape(-1)[0])
        manifest = json.loads(raw)
        return manifest if isinstance(manifest, dict) else {}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def augment_index(
    confirmed_dir: Path,
    base_index_path: Path,
    output_path: Path,
    *,
    templates_per_droid: int = 8,
) -> dict[str, object]:
    """Add manually confirmed samples without discarding the bundled library."""

    samples = _load_samples(confirmed_dir, require_every_droid=False)
    base = BeltTemplateIndex.load(base_index_path)
    templates_per_droid = max(1, int(templates_per_droid))

    identity_hog: list[np.ndarray] = []
    identity_offsets = [0]
    added_identity_counts: dict[str, int] = {}
    for name_index, name in enumerate(base.identity_names):
        start = int(base.identity_name_offsets[name_index])
        end = int(base.identity_name_offsets[name_index + 1])
        existing = base.identity_hog[start:end]
        named = [item for item in samples if item.name == name]
        selected = _diverse_sample_indices(named, templates_per_droid)
        combined, added = _append_unique_vectors(
            existing,
            [named[index].identity_hog for index in selected],
        )
        identity_hog.extend(combined)
        identity_offsets.append(len(identity_hog))
        added_identity_counts[name] = added

    family_histograms: list[np.ndarray] = []
    family_words: list[np.ndarray] = []
    family_offsets = [0]
    added_family_counts: dict[str, int] = {}
    for family_index, family in enumerate(base.family_labels):
        start = int(base.family_offsets[family_index])
        end = int(base.family_offsets[family_index + 1])
        histograms, words, added = _append_unique_family_vectors(
            base.family_histograms[start:end],
            base.family_words[start:end],
            [item for item in samples if item.family == family],
        )
        family_histograms.extend(histograms)
        family_words.extend(words)
        family_offsets.append(len(family_histograms))
        added_family_counts[family] = added

    manifest = _load_manifest(base_index_path)
    previous_confirmed = int(manifest.get("confirmed_samples", 0) or 0)
    previous_selected = manifest.get("selected_counts", {})
    if not isinstance(previous_selected, dict):
        previous_selected = {}
    previous_family = manifest.get("family_counts", {})
    if not isinstance(previous_family, dict):
        previous_family = {}
    selected_counts = {
        name: int(previous_selected.get(name, 0) or 0) + added_identity_counts[name]
        for name in base.identity_names
    }
    family_counts = {
        family: int(previous_family.get(family, 0) or 0) + added_family_counts[family]
        for family in base.family_labels
    }
    added_sample_count = max(
        sum(added_identity_counts.values()),
        sum(added_family_counts.values()),
    )
    manifest.update(
        {
            "version": INDEX_VERSION,
            "confirmed_samples": previous_confirmed + added_sample_count,
            "droid_count": len(DROID_NAMES),
            "identity_templates": len(identity_hog),
            "templates_per_droid": templates_per_droid,
            "selected_counts": selected_counts,
            "family_counts": family_counts,
            "augmentation_source_samples": len(samples),
            "augmentation_samples": added_sample_count,
            "added_identity_templates": sum(added_identity_counts.values()),
            "added_family_templates": sum(added_family_counts.values()),
            "augmentation_identity_counts": added_identity_counts,
            "augmentation_family_counts": added_family_counts,
            "geometry": {
                "card_width_ratio": base.card_width_ratio,
                "art_left_ratio": base.art_left_ratio,
                "art_top_ratio": base.art_top_ratio,
                "art_width_ratio": base.art_width_ratio,
                "art_height_ratio": base.art_height_ratio,
            },
        }
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary_path.open("wb") as handle:
        np.savez_compressed(
            handle,
            version=np.asarray([INDEX_VERSION], dtype=np.int32),
            identity_hog=np.stack(identity_hog).astype(np.float32),
            identity_names=np.asarray(base.identity_names),
            identity_name_offsets=np.asarray(identity_offsets, dtype=np.int32),
            family_histograms=np.stack(family_histograms).astype(np.float32),
            family_words=np.stack(family_words).astype(np.float32),
            family_labels=np.asarray(base.family_labels),
            family_offsets=np.asarray(family_offsets, dtype=np.int32),
            manifest_json=np.asarray([json.dumps(manifest, sort_keys=True)]),
            card_width_ratio=np.asarray(base.card_width_ratio, dtype=np.float32),
            art_left_ratio=np.asarray(base.art_left_ratio, dtype=np.float32),
            art_top_ratio=np.asarray(base.art_top_ratio, dtype=np.float32),
            art_width_ratio=np.asarray(base.art_width_ratio, dtype=np.float32),
            art_height_ratio=np.asarray(base.art_height_ratio, dtype=np.float32),
        )
    temporary_path.replace(output_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "confirmed_dir",
        nargs="?",
        type=Path,
        default=BASE_DIR / "data" / "belt_template_samples" / "confirmed",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE_DIR / "templates" / INDEX_FILE,
    )
    parser.add_argument(
        "--base-index",
        type=Path,
        help="Augment this complete index with the confirmed samples instead of rebuilding it.",
    )
    parser.add_argument("--templates-per-droid", type=int, default=8)
    args = parser.parse_args()
    if args.base_index is not None:
        manifest = augment_index(
            args.confirmed_dir,
            args.base_index,
            args.output,
            templates_per_droid=args.templates_per_droid,
        )
    else:
        manifest = build_index(
            args.confirmed_dir,
            args.output,
            templates_per_droid=args.templates_per_droid,
        )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"wrote {args.output} ({args.output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
