from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.ocr import TextObservation
from droid_alerts.belt.watcher import run_belt_watcher
from droid_alerts.capture import PixelBox


class OneFrameCapture:
    def __init__(self, frame, stop_event):
        self.frame = frame
        self.stop_event = stop_event
        self.closed = False

    def grab(self, _box):
        self.stop_event.set()
        return self.frame.copy()

    def close(self):
        self.closed = True


class StaticOcr:
    def __init__(self, observations):
        self.observations = observations
        self.frame_shapes = []

    def read(self, frame):
        self.frame_shapes.append(frame.shape)
        return list(self.observations)


def card_frame():
    frame = np.full((520, 900, 3), (105, 115, 125), dtype=np.uint8)
    x, y, width, height = (130, 270, 55, 30)
    card_width = max(width + 2 * height, 6 * height)
    x1 = round(x - 1.2 * height)
    y1 = round(y - 5.0 * height)
    x2 = x1 + card_width
    y2 = round(y + 1.5 * height)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (75, 95, 125), -1)
    for index in range(12):
        cv2.circle(
            frame,
            (x1 + 12 + index * 11, y1 + 15 + (index * 17) % 90),
            5 + index % 4,
            (30 + index * 13, 180 - index * 7, 60 + index * 11),
            -1,
        )
    cv2.rectangle(frame, (x1, y - 6), (x2, y2), (12, 12, 15), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (235, 235, 235), 3)
    return frame, (x, y, width, height)


class WatcherTests(unittest.TestCase):
    def test_watcher_uses_independent_mss_and_reports_safe_counts(self):
        frame, name_box = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        ocr = StaticOcr(
            [
                TextObservation("1.10K", 0.99, (430, 55, 95, 26)),
                TextObservation("R2", 0.98, name_box),
            ]
        )
        events = []

        with patch("droid_alerts.belt.watcher.create_capture", return_value=capture) as factory:
            run_belt_watcher(
                2,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                status_callback=events.append,
                ocr_engine=ocr,
            )

        factory.assert_called_once_with(monitor_index=2, prefer_dxcam=False)
        scan = next(event for event in events if event["type"] == "scan")
        self.assertEqual(frame.shape, ocr.frame_shapes[0])
        self.assertEqual(2, scan["raw_count"])
        self.assertEqual(1, scan["candidate_count"])
        self.assertEqual(1, scan["accepted_count"])
        self.assertTrue(capture.closed)
        self.assertEqual("stopped", events[-1]["type"])

    def test_rapidocr_start_failure_does_not_fall_back_to_templates(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        events = []

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch("droid_alerts.belt.watcher.RapidOcrEngine", side_effect=RuntimeError("missing model")),
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                status_callback=events.append,
            )

        self.assertIn("Belt OCR could not start: missing model", events[0]["message"])
        self.assertTrue(capture.closed)
        self.assertNotIn("ready", [event["type"] for event in events])
        self.assertEqual("stopped", events[-1]["type"])

    def test_capture_start_failure_is_reported(self):
        events = []
        with patch(
            "droid_alerts.belt.watcher.create_capture",
            side_effect=RuntimeError("permission denied"),
        ) as factory:
            run_belt_watcher(
                3,
                PixelBox(0, 0, 900, 520),
                stop_event=threading.Event(),
                status_callback=events.append,
            )

        factory.assert_called_once_with(monitor_index=3, prefer_dxcam=False)
        self.assertEqual("error", events[0]["type"])
        self.assertIn("Screen capture could not start", events[0]["message"])
        self.assertEqual("stopped", events[-1]["type"])


if __name__ == "__main__":
    unittest.main()
