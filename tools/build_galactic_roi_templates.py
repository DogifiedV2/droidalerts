"""Build reviewed Galactic rarity-ROI prototypes from debug submissions.

The reviewed dataset contains far more Galactic examples than should be
shipped as individual runtime templates. This tool normalizes every real
Galactic Epic/Legendary/Mythic row, groups similar 44x230 edge ROIs, and writes
one averaged prototype per group. Averaging preserves the fixed alert text and
suppresses unrelated scenery that changes between submissions.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.classifier import fixed_rarity_roi  # noqa: E402
from droid_alerts.pipeline import Pipeline  # noqa: E402


PRIORITY_RARITIES = ("Epic", "Legendary", "Mythic")
DEFAULT_REVIEW_MANIFEST = BASE_DIR / "tests" / "data_report_manifest.json"
DEFAULT_TEMPLATE_DIR = BASE_DIR / "templates" / "rarity_rois"
DEFAULT_OUTPUT_MANIFEST = BASE_DIR / "templates" / "galactic_rarity_rois_manifest.json"
DEFAULT_CLUSTERS_PER_RARITY = 8


@dataclass(frozen=True)
class ReviewedRoi:
    submission_id: str
    install_id: str
    rarity: str
    resolution: tuple[int, int]
    app_version: str
    edge: np.ndarray


def _normalized_vectors(images: list[np.ndarray]) -> np.ndarray:
    vectors = np.stack([image.reshape(-1).astype(np.float32) for image in images])
    vectors -= vectors.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-6)


def _facility_medoids(similarity: np.ndarray, count: int) -> list[int]:
    """Choose deterministic representatives that maximize dataset coverage."""

    selected: list[int] = []
    covered = np.full(similarity.shape[0], -1.0, dtype=np.float32)
    for _ in range(min(count, similarity.shape[0])):
        gains = np.maximum(covered[:, None], similarity).sum(axis=0) - covered.sum()
        if selected:
            gains[selected] = -np.inf
        index = int(np.argmax(gains))
        selected.append(index)
        covered = np.maximum(covered, similarity[:, index])
    return selected


def cluster_prototypes(
    rows: list[ReviewedRoi],
    cluster_count: int,
) -> list[tuple[np.ndarray, ReviewedRoi, list[ReviewedRoi]]]:
    """Group related ROIs and average each group into a scenery-resistant template."""

    vectors = _normalized_vectors([row.edge for row in rows])
    similarity = vectors @ vectors.T
    medoid_indices = _facility_medoids(similarity, cluster_count)
    assignments = np.argmax(similarity[:, medoid_indices], axis=1)

    prototypes: list[tuple[np.ndarray, ReviewedRoi, list[ReviewedRoi]]] = []
    for cluster_index, medoid_index in enumerate(medoid_indices):
        members = [
            row
            for row_index, row in enumerate(rows)
            if int(assignments[row_index]) == cluster_index
        ]
        prototype = np.rint(
            np.mean(np.stack([member.edge for member in members]), axis=0)
        ).astype(np.uint8)
        prototypes.append((prototype, rows[medoid_index], members))
    return prototypes


def _load_reviewed_entries(manifest_path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    entries = [
        entry
        for entry in payload["entries"]
        if entry["droid"] == "Galactic"
        and entry["rarity"] in PRIORITY_RARITIES
        and entry["status"] == "real"
    ]
    entries.sort(key=lambda entry: (entry["rarity"], entry["submissionId"]))
    return payload, entries


def extract_reviewed_rois(
    data_root: Path,
    entries: list[dict[str, object]],
    template_root: Path,
) -> list[ReviewedRoi]:
    pipeline = Pipeline(template_root)
    rows: list[ReviewedRoi] = []
    for index, entry in enumerate(entries, start=1):
        folder = data_root / str(entry["relativePath"])
        image_path = folder / "roi.png"
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(image_path)

        width, height = (int(value) for value in entry["resolution"])
        result = pipeline.detect(
            image,
            screen_width=width,
            screen_height=height,
            keep_normalized=True,
        )
        target = ("Galactic", str(entry["rarity"]))
        matches = [
            detection
            for detection in result.detections
            if (detection.droid, detection.rarity) == target
        ]
        if not matches or result.normalized_image is None:
            detected = [(item.droid, item.rarity) for item in result.detections]
            raise RuntimeError(
                f"Reviewed target {target} was not detected in {image_path}: {detected}"
            )

        detection = max(matches, key=lambda item: item.score)
        edge = fixed_rarity_roi(
            result.normalized_image,
            detection.row_box[1],
            row_height=44,
        )
        rows.append(
            ReviewedRoi(
                submission_id=str(entry["submissionId"]),
                install_id=str(entry["installId"]),
                rarity=str(entry["rarity"]),
                resolution=(width, height),
                app_version=str(entry["appVersion"]),
                edge=edge,
            )
        )
        if index % 50 == 0:
            print(f"normalized {index}/{len(entries)} reviewed Galactic rows")
    return rows


def build_templates(
    *,
    data_root: Path,
    review_manifest: Path,
    template_dir: Path,
    output_manifest: Path,
    clusters_per_rarity: int,
) -> None:
    payload, entries = _load_reviewed_entries(review_manifest)
    if not entries:
        raise ValueError(f"No reviewed Galactic priority rows in {review_manifest}")

    rows = extract_reviewed_rois(data_root, entries, template_dir.parent)
    counts = Counter(row.rarity for row in rows)
    missing = [rarity for rarity in PRIORITY_RARITIES if not counts[rarity]]
    if missing:
        raise ValueError(f"Missing reviewed Galactic rows for: {', '.join(missing)}")

    template_dir.mkdir(parents=True, exist_ok=True)
    for old in template_dir.glob("Galactic__*.png"):
        old.unlink()

    written: list[dict[str, object]] = []
    for rarity in PRIORITY_RARITIES:
        rarity_rows = [row for row in rows if row.rarity == rarity]
        for index, (prototype, medoid, members) in enumerate(
            cluster_prototypes(rarity_rows, clusters_per_rarity),
            start=1,
        ):
            filename = f"Galactic__{rarity}__reviewed_cluster_{index:02d}.png"
            output = template_dir / filename
            if not cv2.imwrite(str(output), prototype):
                raise OSError(f"Could not write {output}")
            written.append(
                {
                    "file": filename,
                    "rarity": rarity,
                    "memberCount": len(members),
                    "medoidSubmissionId": medoid.submission_id,
                    "installCount": len({member.install_id for member in members}),
                    "resolutions": sorted(
                        {f"{member.resolution[0]}x{member.resolution[1]}" for member in members}
                    ),
                    "appVersions": sorted({member.app_version for member in members}),
                    "submissionIds": [member.submission_id for member in members],
                }
            )
            print(f"wrote {filename} from {len(members)} reviewed rows")

    try:
        review_manifest_label = str(review_manifest.resolve().relative_to(BASE_DIR))
    except ValueError:
        review_manifest_label = str(review_manifest)

    output_manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "reviewManifest": review_manifest_label,
                "dataset": payload.get("dataset"),
                "selection": {
                    "method": "greedy correlation medoids with per-cluster averaged edge ROI",
                    "roi": [180, 0, 410, 44],
                    "clustersPerRarity": clusters_per_rarity,
                    "reviewedRows": dict(sorted(counts.items())),
                },
                "templates": written,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output_manifest} ({len(written)} templates)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build averaged Galactic priority rarity-ROI templates."
    )
    parser.add_argument("--review-manifest", type=Path, default=DEFAULT_REVIEW_MANIFEST)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE_DIR)
    parser.add_argument("--output-manifest", type=Path, default=DEFAULT_OUTPUT_MANIFEST)
    parser.add_argument(
        "--clusters-per-rarity",
        type=int,
        default=DEFAULT_CLUSTERS_PER_RARITY,
    )
    args = parser.parse_args()
    if args.clusters_per_rarity < 1:
        parser.error("--clusters-per-rarity must be at least 1")

    payload = json.loads(args.review_manifest.read_text(encoding="utf-8-sig"))
    data_root = args.data_root or Path(str(payload["sourceRootHint"]))
    build_templates(
        data_root=data_root,
        review_manifest=args.review_manifest,
        template_dir=args.template_dir,
        output_manifest=args.output_manifest,
        clusters_per_rarity=args.clusters_per_rarity,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
