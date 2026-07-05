"""Fixture evaluation harness for ToolV2.

Two modes per fixture:
  - band: image goes straight to the scale-normalizing pipeline
    (pre-cropped ROI dumps, training crops, real annotated captures);
  - fullscreen: percent auto-box region detection runs first, then the
    cropped band goes through the pipeline.

Labeled fixtures are scored (TP/FP/FN per droid+rarity combo, priority
combos highlighted). Unlabeled fixtures get a review dump under
tests/results/unlabeled_review/ for the user to confirm labels.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from toolv2.classifier import PRIORITY_ALERTS, draw_detections  # noqa: E402
from toolv2.config import Thresholds, templates_dir  # noqa: E402
from toolv2.overlay_cleanup import clean_overlay  # noqa: E402
from toolv2.pipeline import Pipeline  # noqa: E402
from toolv2.region import auto_box_percent  # noqa: E402

FIXTURES_DIR = BASE_DIR / "tests" / "fixtures"
RESULTS_DIR = BASE_DIR / "tests" / "results"

# The single Beskar Mythic sample is a template-source crop; no real full-row
# capture exists yet. Flagged in every report rather than silently passing.
KNOWN_GAPS = ["Beskar Mythic: no real (non-template-source) full-row capture yet — please supply one when available."]


def load_manifest() -> dict:
    return json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8-sig"))["fixtures"]


def evaluate_fixture(pipeline: Pipeline, path: Path, spec: dict) -> dict:
    image = cv2.imread(str(path))
    if image is None:
        return {"error": f"unreadable image {path}"}
    if spec.get("clean_overlay"):
        image = clean_overlay(image)

    mode = spec.get("mode", "band")
    if mode == "fullscreen":
        h, w = image.shape[:2]
        box = auto_box_percent(w, h)
        band = image[box.top : box.bottom, box.left : box.right]
        result = pipeline.detect(band, screen_height=h, keep_normalized=True)
        region = {"left": box.left, "top": box.top, "width": box.width, "height": box.height}
    else:
        result = pipeline.detect(image, known_scale=spec.get("scale"), keep_normalized=True)
        region = None

    detected = [(d.droid, d.rarity) for d in result.detections]
    record: dict = {
        "mode": mode,
        "scale": result.scale,
        "scale_method": result.scale_method,
        "candidate_rows": result.candidate_rows,
        "region": region,
        "detections": [d.to_dict() for d in result.detections],
        "detected_combos": detected,
    }

    expected = spec.get("rows")
    if expected is not None:
        expected_pairs = [tuple(row) for row in expected]
        exp_counter = Counter(expected_pairs)
        det_counter = Counter(detected)
        tp = det_counter & exp_counter
        fp = det_counter - exp_counter
        fn = exp_counter - det_counter
        record.update(
            {
                "labeled": True,
                "tp": {f"{d} {r}": n for (d, r), n in tp.items()},
                "fp": {f"{d} {r}": n for (d, r), n in fp.items()},
                "fn": {f"{d} {r}": n for (d, r), n in fn.items()},
                "pass": not fp and not fn,
            }
        )
    else:
        record["labeled"] = False

    record["_normalized_image"] = result.normalized_image
    record["_detections_obj"] = result.detections
    return record


def main(*, verbose: bool = False, dump_unlabeled: bool = False) -> int:
    manifest = load_manifest()
    pipeline = Pipeline(templates_dir(), Thresholds())
    stamp = time.strftime("%Y%m%d_%H%M%S")
    review_dir = RESULTS_DIR / "unlabeled_review"

    per_combo: dict[str, Counter] = {"tp": Counter(), "fp": Counter(), "fn": Counter()}
    results: dict[str, dict] = {}
    passed = failed = 0

    for rel_path, spec in manifest.items():
        path = FIXTURES_DIR / rel_path
        if not path.exists():
            results[rel_path] = {"error": "missing"}
            continue
        record = evaluate_fixture(pipeline, path, spec)
        normalized = record.pop("_normalized_image", None)
        detections = record.pop("_detections_obj", [])

        if record.get("labeled"):
            if record["pass"]:
                passed += 1
                status = "PASS"
            else:
                failed += 1
                status = "FAIL"
            for bucket in ("tp", "fp", "fn"):
                for combo, n in record[bucket].items():
                    per_combo[bucket][combo] += n
            print(f"[{status}] {rel_path}: detected={record['detected_combos']} expected={spec['rows']}")
            if status == "FAIL" and normalized is not None:
                fail_dir = RESULTS_DIR / "failures"
                fail_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(fail_dir / f"{Path(rel_path).stem}_det.png"), draw_detections(normalized, detections))
        else:
            print(f"[----] {rel_path}: detected={record['detected_combos']} (unlabeled)")
            if dump_unlabeled and normalized is not None:
                review_dir.mkdir(parents=True, exist_ok=True)
                out_name = Path(rel_path).stem
                cv2.imwrite(str(review_dir / f"{out_name}_norm.png"), normalized)
                cv2.imwrite(str(review_dir / f"{out_name}_det.png"), draw_detections(normalized, detections))

        if verbose:
            for det in record["detections"]:
                print(
                    f"    {det['droid']} {det['rarity']} score={det['score']:.2f} "
                    f"rarity_score={det['rarity_score']:.2f} margin={det['rarity_margin']:.2f} "
                    f"droid_score={det['droid_score']:.2f} alert={det['should_alert']} src={det['source']}"
                )
        results[rel_path] = record

    priority_lines = []
    print("\n=== Aggregate (labeled fixtures) ===")
    print(f"fixtures: {passed} passed, {failed} failed")
    all_combos = sorted(set(per_combo["tp"]) | set(per_combo["fp"]) | set(per_combo["fn"]))
    for combo in all_combos:
        droid, rarity = combo.split(" ", 1)
        tag = " *PRIORITY*" if (droid, rarity) in PRIORITY_ALERTS else ""
        line = (
            f"  {combo}: TP={per_combo['tp'][combo]} FP={per_combo['fp'][combo]} "
            f"FN={per_combo['fn'][combo]}{tag}"
        )
        print(line)
        if tag:
            priority_lines.append(line.strip())
    for gap in KNOWN_GAPS:
        print(f"  [known gap] {gap}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp": stamp,
        "passed": passed,
        "failed": failed,
        "per_combo": {k: dict(v) for k, v in per_combo.items()},
        "priority_summary": priority_lines,
        "known_gaps": KNOWN_GAPS,
        "fixtures": results,
    }
    report_path = RESULTS_DIR / f"eval_report_{stamp}.json"
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    md_lines = [
        f"# ToolV2 eval report {stamp}",
        "",
        f"Labeled fixtures: **{passed} passed / {failed} failed**",
        "",
        "| combo | TP | FP | FN | priority |",
        "|---|---|---|---|---|",
    ]
    for combo in all_combos:
        droid, rarity = combo.split(" ", 1)
        md_lines.append(
            f"| {combo} | {per_combo['tp'][combo]} | {per_combo['fp'][combo]} | "
            f"{per_combo['fn'][combo]} | {'YES' if (droid, rarity) in PRIORITY_ALERTS else ''} |"
        )
    md_lines += ["", "## Known gaps", *[f"- {g}" for g in KNOWN_GAPS]]
    (RESULTS_DIR / f"eval_report_{stamp}.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"\nreport: {report_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dump-unlabeled", action="store_true")
    args = parser.parse_args()
    raise SystemExit(main(verbose=args.verbose, dump_unlabeled=args.dump_unlabeled))
