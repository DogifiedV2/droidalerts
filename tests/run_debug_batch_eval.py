"""Evaluate an exported debug-detection batch against its false report.

Every submission folder is expected to contain ``metadata.json`` and
``roi.png``. Submissions listed in the TSV are negative examples for their
reported combo; every other submission is a positive example for the combo in
its metadata.
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


def submission_dirs(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.glob("*/*/metadata.json"))


def evaluate(root: Path, false_report: Path, *, verbose: bool = False) -> int:
    false_rows = load_false_ids(false_report)
    submissions = submission_dirs(root)
    pipeline = Pipeline(templates_dir(), Thresholds())

    positive_total = positive_passed = 0
    negative_total = negative_passed = 0
    versions: Counter[str] = Counter()
    failures: list[str] = []
    started = time.perf_counter()

    for index, folder in enumerate(submissions, start=1):
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
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
        target = (metadata["detection"]["droid"], metadata["detection"]["rarity"])
        detected = [(detection.droid, detection.rarity) for detection in result.detections]
        submission_id = str(metadata["id"])
        is_false = submission_id in false_rows
        versions[str(metadata.get("appVersion", "?"))] += 1

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
        help="TSV listing false submission paths (defaults to ROOT/false_detections.tsv).",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    report = args.false_report or args.root / "false_detections.tsv"
    return evaluate(args.root, report, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
