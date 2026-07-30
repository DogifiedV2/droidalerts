"""Replay manually counted belt videos and compare physical-card recall."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import cv2


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from droid_alerts.belt.region import DEFAULT_REGION, RelativeRegion  # noqa: E402
from droid_alerts.belt.runtime import adaptive_track_timeout  # noqa: E402
from droid_alerts.belt.scale_recognition import HybridCardRecognizer  # noqa: E402
from droid_alerts.belt.template_recognition import INDEX_FILE  # noqa: E402
from droid_alerts.belt.tracking import BeltTracker  # noqa: E402


DEFAULT_GROUND_TRUTH = ROOT / "tests" / "belt_video_ground_truth.json"
DEFAULT_INDEX = ROOT / "templates" / INDEX_FILE


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


def _crop(frame, region: RelativeRegion):
    height, width = frame.shape[:2]
    left = max(0, round(region.left * width))
    top = max(0, round(region.top * height))
    right = min(width, left + round(region.width * width))
    bottom = min(height, top + round(region.height * height))
    return frame[top:bottom, left:right]


def _event_dict(event, timestamp: float) -> dict[str, object]:
    track = event.track
    return {
        "event_at": round(timestamp, 4),
        "kind": event.kind,
        "track_id": int(track.id),
        "name": str(track.name),
        "family": str(track.family),
        "rarity": str(track.rarity),
        "confidence": round(float(track.confidence), 4),
        "family_confidence": round(float(track.family_confidence), 4),
        "rarity_confidence": round(float(track.rarity_confidence), 4),
        "confirmation_mode": str(track.confirmation_mode),
        "hits": int(track.hits),
        "box": [round(float(value), 2) for value in track.box],
    }


def run_video(
    video_path: Path,
    *,
    index_path: Path,
    sample_fps: float,
    region: RelativeRegion = DEFAULT_REGION,
    progress: bool = True,
) -> dict[str, object]:
    """Run the production recognizer and tracker at a deterministic cadence."""

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
        index_path=index_path,
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
    events: list[dict[str, object]] = []
    last_timestamp = 0.0
    frame_number = -1
    sampled_frames = 0
    accepted_observations = 0
    started = time.perf_counter()
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
            events.extend(_event_dict(event, timestamp) for event in update.events)
            sampled_frames += 1
            accepted_observations += len(result.observations)
            if (
                progress
                and sampled_frames % max(1, round(effective_fps * 30)) == 0
            ):
                entered = sum(item["kind"] == "entered" for item in events)
                print(
                    f"{video_path.name}: {timestamp:.0f}s, "
                    f"{entered} entry events",
                    flush=True,
                )
    finally:
        capture.release()

    final_timestamp = last_timestamp + tracker.timeout_seconds + 0.1
    final_update = tracker.predict(
        final_timestamp,
        max(1, round(source_width * region.width)),
    )
    events.extend(
        _event_dict(event, final_timestamp) for event in final_update.events
    )
    return {
        "video": str(video_path),
        "source_resolution": [source_width, source_height],
        "source_fps": source_fps,
        "source_frame_count": frame_count,
        "sample_fps": effective_fps,
        "sampled_frames": sampled_frames,
        "accepted_observations": accepted_observations,
        "tracker_timeout_seconds": tracker.timeout_seconds,
        "recognizer": recognizer.detector_name,
        "events": events,
        "elapsed_seconds": time.perf_counter() - started,
    }


def _ordered_matches(
    expected: list[dict[str, object]],
    actual: list[dict[str, object]],
    *,
    minimum_delay: float,
    maximum_delay: float,
) -> list[tuple[int, int]]:
    """Sequence-match one identity while preserving physical-card order."""

    rows = len(expected)
    columns = len(actual)
    states: list[list[tuple[int, float, int] | None]] = [
        [None] * (columns + 1) for _ in range(rows + 1)
    ]
    previous: list[list[tuple[int, int, str] | None]] = [
        [None] * (columns + 1) for _ in range(rows + 1)
    ]
    states[0][0] = (0, 0.0, 0)

    def consider(
        row: int,
        column: int,
        candidate: tuple[int, float, int],
        origin: tuple[int, int, str],
    ) -> None:
        current = states[row][column]
        candidate_rank = (-candidate[0], candidate[1], candidate[2])
        current_rank = (
            (-current[0], current[1], current[2])
            if current is not None
            else None
        )
        if current_rank is None or candidate_rank < current_rank:
            states[row][column] = candidate
            previous[row][column] = origin

    for row in range(rows + 1):
        for column in range(columns + 1):
            state = states[row][column]
            if state is None:
                continue
            matches, total_error, skips = state
            if row < rows:
                consider(
                    row + 1,
                    column,
                    (matches, total_error, skips + 1),
                    (row, column, "expected"),
                )
            if column < columns:
                consider(
                    row,
                    column + 1,
                    (matches, total_error, skips + 1),
                    (row, column, "actual"),
                )
            if row < rows and column < columns:
                delay = float(actual[column]["event_at"]) - float(
                    expected[row]["time"]
                )
                if minimum_delay <= delay <= maximum_delay:
                    expected_family = str(expected[row].get("family", ""))
                    actual_family = str(
                        actual[column].get(
                            "final_family",
                            actual[column].get("family", ""),
                        )
                    )
                    family_penalty = (
                        100.0
                        if (
                            expected_family
                            and actual_family
                            and expected_family != actual_family
                        )
                        else 0.0
                    )
                    consider(
                        row + 1,
                        column + 1,
                        (
                            matches + 1,
                            total_error + abs(delay) + family_penalty,
                            skips,
                        ),
                        (row, column, "match"),
                    )

    pairs: list[tuple[int, int]] = []
    row, column = rows, columns
    while row or column:
        origin = previous[row][column]
        if origin is None:
            break
        previous_row, previous_column, action = origin
        if action == "match":
            pairs.append((previous_row, previous_column))
        row, column = previous_row, previous_column
    return list(reversed(pairs))


def compare_run(
    expected: list[dict[str, object]],
    events: list[dict[str, object]],
    *,
    minimum_delay: float = -2.0,
    maximum_delay: float = 12.0,
) -> dict[str, object]:
    """Compare entry events to manually counted physical blueprints."""

    entered = [item.copy() for item in events if item["kind"] == "entered"]
    final_attributes: dict[int, tuple[str, str]] = {}
    for event in events:
        family = str(event.get("family", ""))
        rarity = str(event.get("rarity", ""))
        track_id = int(event["track_id"])
        old_family, old_rarity = final_attributes.get(track_id, ("", ""))
        final_attributes[track_id] = (
            family or old_family,
            rarity or old_rarity,
        )
    for event in entered:
        family, rarity = final_attributes[int(event["track_id"])]
        event["final_family"] = family
        event["final_rarity"] = rarity

    expected_by_name: dict[str, list[tuple[int, dict[str, object]]]] = {}
    actual_by_name: dict[str, list[tuple[int, dict[str, object]]]] = {}
    for index, item in enumerate(expected):
        expected_by_name.setdefault(str(item["name"]), []).append((index, item))
    for index, item in enumerate(entered):
        actual_by_name.setdefault(str(item["name"]), []).append((index, item))

    matched_pairs: list[tuple[int, int]] = []
    for name in sorted(set(expected_by_name) | set(actual_by_name)):
        expected_items = expected_by_name.get(name, [])
        actual_items = actual_by_name.get(name, [])
        local_pairs = _ordered_matches(
            [item for _index, item in expected_items],
            [item for _index, item in actual_items],
            minimum_delay=minimum_delay,
            maximum_delay=maximum_delay,
        )
        matched_pairs.extend(
            (
                expected_items[expected_index][0],
                actual_items[actual_index][0],
            )
            for expected_index, actual_index in local_pairs
        )
    matched_pairs.sort()
    matched_expected = {item[0] for item in matched_pairs}
    matched_actual = {item[1] for item in matched_pairs}

    family_correct = 0
    family_errors: list[dict[str, object]] = []
    family_unknown: list[dict[str, object]] = []
    for expected_index, actual_index in matched_pairs:
        expected_item = expected[expected_index]
        expected_family = str(expected_item.get("family", ""))
        if not expected_family:
            continue
        actual_item = entered[actual_index]
        actual_family = str(actual_item.get("final_family", ""))
        comparison = {
            "time": expected_item["time"],
            "name": expected_item["name"],
            "expected_family": expected_family,
            "actual_family": actual_family,
            "event_at": actual_item["event_at"],
        }
        if not actual_family:
            family_unknown.append(comparison)
        elif actual_family != expected_family:
            family_errors.append(comparison)
        else:
            family_correct += 1

    missing = [
        item for index, item in enumerate(expected) if index not in matched_expected
    ]
    unexpected = [
        item for index, item in enumerate(entered) if index not in matched_actual
    ]
    classified_families = family_correct + len(family_errors)
    expected_family_matches = (
        family_correct + len(family_errors) + len(family_unknown)
    )
    reacquired = [item for item in events if item["kind"] == "reacquired"]
    matched_count = len(matched_pairs)
    return {
        "manual_physical_blueprints": len(expected),
        "entered_alert_events": len(entered),
        "matched_physical_blueprints": matched_count,
        "missed_physical_blueprints": len(missing),
        "unexpected_or_duplicate_entries": len(unexpected),
        "camera_jump_reacquisitions_suppressed": len(reacquired),
        "identity_recall": matched_count / len(expected) if expected else 1.0,
        "entry_precision": matched_count / len(entered) if entered else 1.0,
        "family_coverage": (
            classified_families / expected_family_matches
            if expected_family_matches
            else 1.0
        ),
        "family_accuracy_when_classified": (
            family_correct / classified_families
            if classified_families
            else 1.0
        ),
        "manual_name_counts": dict(
            Counter(str(item["name"]) for item in expected).most_common()
        ),
        "entered_name_counts": dict(
            Counter(str(item["name"]) for item in entered).most_common()
        ),
        "missing": missing,
        "unexpected_entries": unexpected,
        "family_errors": family_errors,
        "family_unknown": family_unknown,
        "reacquired": reacquired,
    }


def _load_ground_truth(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Ground truth could not be read: {path}") from exc
    videos = data.get("videos") if isinstance(data, dict) else None
    if not isinstance(videos, dict):
        raise RuntimeError("Ground truth has no videos object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("videos", nargs="+", type=Path)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=DEFAULT_GROUND_TRUTH,
    )
    parser.add_argument("--sample-fps", type=float, default=2.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--region",
        type=_region,
        default=DEFAULT_REGION,
        help="Normalized left,top,width,height. Defaults to the Belt Tracker region.",
    )
    parser.add_argument("--quiet", action="store_true")
    arguments = parser.parse_args()
    if not 0.1 <= arguments.sample_fps <= 20.0:
        parser.error("--sample-fps must be between 0.1 and 20")
    missing = [path for path in arguments.videos if not path.is_file()]
    if missing:
        parser.error(f"Video does not exist: {missing[0]}")
    if not arguments.index.is_file():
        parser.error(f"Template index does not exist: {arguments.index}")

    ground_truth = _load_ground_truth(arguments.ground_truth)
    ground_truth_videos = ground_truth["videos"]
    assert isinstance(ground_truth_videos, dict)
    runs = []
    for source_path in arguments.videos:
        video_path = source_path.expanduser().resolve()
        expected_video = ground_truth_videos.get(video_path.name)
        if not isinstance(expected_video, dict):
            parser.error(f"No manual ground truth for {video_path.name}")
        expected_events = expected_video.get("events")
        if not isinstance(expected_events, list):
            parser.error(f"Ground truth events are invalid for {video_path.name}")
        run = run_video(
            video_path,
            index_path=arguments.index.expanduser().resolve(),
            sample_fps=arguments.sample_fps,
            region=arguments.region,
            progress=not arguments.quiet,
        )
        run["comparison"] = compare_run(expected_events, run["events"])
        runs.append(run)

    index_path = arguments.index.expanduser().resolve()
    report = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ground_truth": str(arguments.ground_truth.expanduser().resolve()),
        "ground_truth_version": ground_truth.get("version"),
        "index": str(index_path),
        "index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
        "evaluation_note": (
            "These source videos contributed reviewed templates, so this is a "
            "training replay regression, not an independent validation split."
        ),
        "runs": runs,
    }
    if arguments.output is not None:
        output = arguments.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {output}")
    for run in runs:
        comparison = run["comparison"]
        assert isinstance(comparison, dict)
        print(
            f"{Path(str(run['video'])).name}: "
            f"{comparison['matched_physical_blueprints']}/"
            f"{comparison['manual_physical_blueprints']} physical BPs, "
            f"{comparison['missed_physical_blueprints']} missed, "
            f"{comparison['unexpected_or_duplicate_entries']} unexpected, "
            f"{comparison['camera_jump_reacquisitions_suppressed']} "
            "reacquired without alert"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
