"""Evaluate an exported debug-detection batch against reviewed ground truth.

Every submission folder is expected to contain ``metadata.json`` and
``roi.png``. The preferred JSON manifest labels each submission as ``real``,
``false``, or ``uncertain``. The legacy false-only TSV format remains supported
for older exports.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))
DEFAULT_GROUND_TRUTH = BASE_DIR / "tests" / "data_report_manifest.json"

from droid_alerts.config import Thresholds, templates_dir  # noqa: E402
from droid_alerts.pipeline import Pipeline  # noqa: E402


def load_false_ids(report_path: Path) -> dict[str, dict[str, str]]:
    with report_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    false_rows: dict[str, dict[str, str]] = {}
    for row in rows:
        submission_id = Path(row["path"]).name.split("_", 1)[0]
        false_rows[submission_id] = row
    return false_rows


def load_ground_truth(manifest_path: Path) -> dict[str, dict[str, object]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"Ground-truth manifest has no entries list: {manifest_path}")

    truth: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid ground-truth entry in {manifest_path}")
        submission_id = str(entry.get("submissionId", ""))
        status = str(entry.get("status", ""))
        if not submission_id or status not in {"real", "false", "uncertain"}:
            raise ValueError(f"Invalid ground-truth entry in {manifest_path}: {entry}")
        if submission_id in truth:
            raise ValueError(f"Duplicate submission ID in {manifest_path}: {submission_id}")
        truth[submission_id] = entry
    return truth


def submission_dirs(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.glob("*/*/metadata.json"))


def evaluate(
    root: Path,
    *,
    ground_truth: Path | None = None,
    false_report: Path | None = None,
    verbose: bool = False,
) -> int:
    if ground_truth is not None:
        truth = load_ground_truth(ground_truth)
        false_rows: dict[str, dict[str, str]] = {}
    elif false_report is not None:
        false_rows = load_false_ids(false_report)
        truth = {}
    else:
        raise ValueError("A ground-truth manifest or false report is required")
    submissions = submission_dirs(root)
    pipeline = Pipeline(templates_dir(), Thresholds())

    positive_total = positive_passed = 0
    negative_total = negative_passed = 0
    uncertain_total = skipped_total = 0
    versions: Counter[str] = Counter()
    failures: list[str] = []
    started = time.perf_counter()

    for index, folder in enumerate(submissions, start=1):
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        target = (metadata["detection"]["droid"], metadata["detection"]["rarity"])
        submission_id = str(metadata["id"])
        versions[str(metadata.get("appVersion", "?"))] += 1

        if truth:
            entry = truth.get(submission_id)
            if entry is None:
                skipped_total += 1
                continue
            manifest_target = (str(entry["droid"]), str(entry["rarity"]))
            if manifest_target != target:
                failures.append(
                    f"manifest/metadata target mismatch for {folder}: "
                    f"manifest={manifest_target}, metadata={target}"
                )
                continue
            status = str(entry["status"])
            if status == "uncertain":
                uncertain_total += 1
                continue
            is_false = status == "false"
        else:
            is_false = submission_id in false_rows

        image = cv2.imread(str(folder / "roi.png"), cv2.IMREAD_COLOR)
        if image is None:
            failures.append(f"unreadable roi: {folder / 'roi.png'}")
            continue

        resolution = metadata["metadata"]["resolution"]
        result = pipeline.detect(
            image,
            screen_width=int(resolution["width"]),
            screen_height=int(resolution["height"]),
        )
        detected = [(detection.droid, detection.rarity) for detection in result.detections]

        if is_false:
            negative_total += 1
            passed = target not in detected
            negative_passed += int(passed)
            expected = f"must not detect {target}"
        else:
            positive_total += 1
            passed = target in detected
            positive_passed += int(passed)
            expected = f"must detect {target}"

        if verbose or not passed:
            status = "PASS" if passed else "FAIL"
            reason = false_rows.get(submission_id, {}).get("reason", "")
            suffix = f" reason={reason}" if reason else ""
            print(
                f"[{status}] {index:03d}/{len(submissions)} {folder.name}: "
                f"detected={detected}; {expected}{suffix}"
            )
        if not passed:
            failures.append(f"{folder}: detected={detected}; {expected}")

    elapsed = time.perf_counter() - started
    print("\n=== Debug batch ===")
    print(f"versions: {dict(versions)}")
    print(f"valid detections: {positive_passed}/{positive_total}")
    print(f"false detections rejected: {negative_passed}/{negative_total}")
    if uncertain_total:
        print(f"uncertain detections skipped: {uncertain_total}")
    if skipped_total:
        print(f"out-of-scope submissions skipped: {skipped_total}")
    print(f"elapsed: {elapsed:.2f}s")
    if failures:
        print("failures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a debug-detection export batch.")
    parser.add_argument("root", type=Path, help="Folder containing install/submission directories.")
    parser.add_argument(
        "--false-report",
        type=Path,
        help="Legacy TSV listing false submission paths.",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        help=f"Reviewed JSON manifest (defaults to {DEFAULT_GROUND_TRUTH}).",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.ground_truth and args.false_report:
        parser.error("--ground-truth and --false-report are mutually exclusive")
    if args.false_report:
        return evaluate(args.root, false_report=args.false_report, verbose=args.verbose)
    manifest = args.ground_truth or DEFAULT_GROUND_TRUTH
    return evaluate(args.root, ground_truth=manifest, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
