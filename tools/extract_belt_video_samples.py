from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import cv2


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from droid_alerts.belt.region import DEFAULT_REGION, RelativeRegion
from droid_alerts.belt.runtime import adaptive_track_timeout
from droid_alerts.belt.sample_collection import BeltTemplateSampleCollector
from droid_alerts.belt.scale_recognition import HybridCardRecognizer
from droid_alerts.belt.tracking import BeltTracker


def _region(value: str) -> RelativeRegion:
    try:
        values = [float(item.strip()) for item in value.split(",")]
        if len(values) != 4:
            raise ValueError
        region = RelativeRegion(*values)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Region must be normalized left,top,width,height"
        ) from exc
    if not region.is_valid():
        raise argparse.ArgumentTypeError("Region is outside the source frame")
    return region


def _default_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "data" / "belt_video_review" / f"run_{stamp}"


def _crop(frame, region: RelativeRegion):
    height, width = frame.shape[:2]
    left = max(0, round(region.left * width))
    top = max(0, round(region.top * height))
    right = min(width, left + round(region.width * width))
    bottom = min(height, top + round(region.height * height))
    return frame[top:bottom, left:right]


def process_video(
    video_path: Path,
    *,
    sample_fps: float,
    region: RelativeRegion,
    collector: BeltTemplateSampleCollector,
) -> dict[str, object]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Video could not be opened: {video_path}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    if source_fps <= 0:
        source_fps = 30.0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    stride = max(1, round(source_fps / sample_fps))
    effective_fps = source_fps / stride

    recognizer = HybridCardRecognizer(
        scale_scan_interval_seconds=max(0.05, 1.0 / effective_fps),
    )
    tracker = BeltTracker(
        confirmation_hits=4,
        slow_confirmation_hits=2,
        slow_cadence_seconds=0.70,
        slow_minimum_confidence=0.90,
        timeout_seconds=adaptive_track_timeout(3.5, 1.0 / effective_fps),
        minimum_template_displacement_ratio=0.10,
    )
    counters: Counter[str] = Counter()
    collection_actions: Counter[str] = Counter()
    last_timestamp = 0.0
    started = time.perf_counter()
    frame_number = -1
    try:
        while True:
            success, frame = capture.read()
            if not success:
                break
            frame_number += 1
            if frame_number % stride:
                continue
            timestamp = frame_number / source_fps
            last_timestamp = timestamp
            belt_frame = _crop(frame, region)
            result = recognizer.analyze(
                belt_frame,
                now=timestamp,
                force_scale=True,
            )
            update = tracker.update(
                result.observations,
                timestamp,
                belt_frame.shape[1],
            )
            accepted_candidates = tuple(
                item for item in result.candidates if item.accepted
            )
            collector.observe(
                belt_frame,
                accepted_candidates,
                update.observation_track_ids,
                now=timestamp,
                frame_number=frame_number,
            )
            for collection_update in collector.process_events(update.events):
                collection_actions[collection_update.action] += 1
            counters["sampled_frames"] += 1
            counters["accepted_observations"] += len(result.observations)
            counters["entered_tracks"] += sum(
                event.kind == "entered" for event in update.events
            )
            counters["updated_tracks"] += sum(
                event.kind == "updated" for event in update.events
            )
            if counters["sampled_frames"] % max(1, round(effective_fps * 30)) == 0:
                print(
                    f"{video_path.name}: {timestamp:.0f}s, "
                    f"{counters['entered_tracks']} confirmed tracks",
                    flush=True,
                )
    finally:
        capture.release()

    final_update = tracker.predict(
        last_timestamp + tracker.timeout_seconds + 0.1,
        max(1, round(source_width * region.width)),
    )
    for collection_update in collector.process_events(final_update.events):
        collection_actions[collection_update.action] += 1
    for collection_update in collector.close():
        collection_actions[collection_update.action] += 1
    return {
        "video": str(video_path),
        "source_resolution": [source_width, source_height],
        "source_fps": source_fps,
        "source_frame_count": frame_count,
        "sample_fps": effective_fps,
        "region": {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        },
        "counts": dict(counters),
        "collection_actions": dict(collection_actions),
        "elapsed_seconds": time.perf_counter() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract temporally confirmed belt-card crops from videos for "
            "manual review. Predictions are never written as confirmed labels."
        )
    )
    parser.add_argument("videos", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--sample-fps", type=float, default=2.0)
    parser.add_argument(
        "--region",
        type=_region,
        default=DEFAULT_REGION,
        help="Normalized left,top,width,height. Defaults to the Belt Tracker region.",
    )
    arguments = parser.parse_args()
    if not 0.1 <= arguments.sample_fps <= 20.0:
        parser.error("--sample-fps must be between 0.1 and 20")
    missing = [path for path in arguments.videos if not path.is_file()]
    if missing:
        parser.error(f"Video does not exist: {missing[0]}")

    output = (arguments.output or _default_output()).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    collector = BeltTemplateSampleCollector(output)
    runs = []
    for video in arguments.videos:
        runs.append(
            process_video(
                video.expanduser().resolve(),
                sample_fps=arguments.sample_fps,
                region=arguments.region,
                collector=collector,
            )
        )
    manifest = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label_status": "template_prediction",
        "training_status": "manual_review_required",
        "split_policy": "keep_each_source_video_whole",
        "runs": runs,
        "collector": collector.status(),
    }
    manifest_path = output / "video_extraction.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Review data: {output}")
    print(f"Manifest: {manifest_path}")
    print("Do not train from detections until a human has reviewed the labels.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
