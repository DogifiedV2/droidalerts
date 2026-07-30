from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from droid_alerts.belt.region import DEFAULT_REGION
from droid_alerts.belt.scale_recognition import HybridCardRecognizer


def _crop(frame):
    height, width = frame.shape[:2]
    left = round(DEFAULT_REGION.left * width)
    top = round(DEFAULT_REGION.top * height)
    right = left + round(DEFAULT_REGION.width * width)
    bottom = top + round(DEFAULT_REGION.height * height)
    return frame[top:bottom, left:right]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the production Belt Tracker detector on a video."
    )
    parser.add_argument("video", type=Path)
    parser.add_argument("--scan-fps", type=float, default=8.0)
    parser.add_argument("--seconds", type=float, default=60.0)
    arguments = parser.parse_args()
    video = arguments.video.expanduser().resolve()
    if not video.is_file():
        parser.error(f"Video does not exist: {video}")
    if not 0.1 <= arguments.scan_fps <= 20.0:
        parser.error("--scan-fps must be between 0.1 and 20")

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        parser.error(f"Video could not be opened: {video}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    if source_fps <= 0:
        source_fps = 30.0
    stride = max(1, round(source_fps / arguments.scan_fps))
    recognizer = HybridCardRecognizer()
    durations: list[float] = []
    scale_durations: list[float] = []
    observation_count = 0
    scale_scans = 0
    sampled_video_seconds = 0.0
    frame_number = -1
    try:
        while True:
            success, frame = capture.read()
            if not success:
                break
            frame_number += 1
            if frame_number % stride:
                continue
            video_time = frame_number / source_fps
            if video_time > arguments.seconds:
                break
            started = time.perf_counter()
            result = recognizer.analyze(_crop(frame), now=video_time)
            duration = time.perf_counter() - started
            durations.append(duration)
            observation_count += len(result.observations)
            if result.diagnostics["scale_scan_ran"]:
                scale_scans += 1
                scale_durations.append(
                    float(
                        result.diagnostics.get("scale", {}).get(
                            "total_seconds",
                            0.0,
                        )
                    )
                )
            sampled_video_seconds = video_time
    finally:
        capture.release()

    values = np.asarray(durations, dtype=np.float64)
    scale_values = np.asarray(scale_durations, dtype=np.float64)
    report = {
        "video": str(video),
        "source_fps": source_fps,
        "requested_scan_fps": arguments.scan_fps,
        "sample_count": len(durations),
        "sampled_video_seconds": sampled_video_seconds,
        "scale_scan_count": scale_scans,
        "observation_count": observation_count,
        "scan_milliseconds": {
            "mean": float(values.mean() * 1000.0) if len(values) else 0.0,
            "p50": float(np.percentile(values, 50) * 1000.0) if len(values) else 0.0,
            "p95": float(np.percentile(values, 95) * 1000.0) if len(values) else 0.0,
            "maximum": float(values.max() * 1000.0) if len(values) else 0.0,
        },
        "scale_milliseconds": {
            "mean": float(scale_values.mean() * 1000.0) if len(scale_values) else 0.0,
            "p95": (
                float(np.percentile(scale_values, 95) * 1000.0)
                if len(scale_values)
                else 0.0
            ),
        },
        "estimated_one_core_percent": (
            float(values.sum() / sampled_video_seconds * 100.0)
            if sampled_video_seconds > 0
            else 0.0
        ),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
