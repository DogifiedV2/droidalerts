from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.ocr import TextObservation
from droid_alerts.belt.watcher import (
    OCR_INTERVAL_SECONDS,
    adaptive_ocr_interval,
    adaptive_template_interval,
    adaptive_track_timeout,
    run_belt_watcher,
)
from droid_alerts.belt.worker import run_belt_worker_process
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


class CountingCapture:
    def __init__(self, frame):
        self.frame = frame
        self.grabs = 0
        self.closed = False

    def grab(self, _box):
        self.grabs += 1
        return self.frame.copy()

    def close(self):
        self.closed = True


class LoopLimitedStopEvent:
    def __init__(self, loops):
        self.loops = loops
        self.waits = 0

    def is_set(self):
        return self.waits >= self.loops

    def wait(self, _timeout):
        self.waits += 1
        return self.is_set()


class StaticOcr:
    def __init__(self, observations):
        self.observations = observations
        self.frame_shapes = []

    def read(self, frame):
        self.frame_shapes.append(frame.shape)
        return list(self.observations)


class RecordingQueue:
    def __init__(self):
        self.events = []

    def put(self, event):
        self.events.append(event)


class WorkerProcessEntryTests(unittest.TestCase):
    def test_process_entry_forwards_watcher_status(self):
        status_queue = RecordingQueue()
        received = {}

        def fake_watcher(*_args, status_callback, **kwargs):
            received.update(kwargs)
            status_callback({"type": "ready"})

        with patch("droid_alerts.belt.worker.run_belt_watcher", side_effect=fake_watcher):
            run_belt_worker_process(
                1,
                PixelBox(0, 0, 900, 520),
                {"R2": "Gold"},
                threading.Event(),
                status_queue,
                True,
                True,
            )

        self.assertEqual([{"type": "ready"}], status_queue.events)
        self.assertTrue(received["dev_mode"])
        self.assertTrue(received["collect_template_samples"])
        self.assertEqual({"R2": "Gold"}, received["target_tiers"])

    def test_process_entry_reports_uncaught_failure(self):
        status_queue = RecordingQueue()
        with patch(
            "droid_alerts.belt.worker.run_belt_watcher",
            side_effect=RuntimeError("worker exploded"),
        ):
            run_belt_worker_process(
                1,
                PixelBox(0, 0, 900, 520),
                {},
                threading.Event(),
                status_queue,
            )

        self.assertEqual("error", status_queue.events[0]["type"])
        self.assertIn("worker exploded", status_queue.events[0]["message"])


class EnteringTracker:
    def __init__(self, family):
        self.family = family

    def update(self, _observations, _now, _frame_width):
        track = SimpleNamespace(id=1, name="R2", family=self.family, confidence=0.93)
        return SimpleNamespace(
            events=[SimpleNamespace(kind="entered", track=track)],
            tracks=[],
        )

    def predict(self, _now, _frame_width):
        return SimpleNamespace(events=[], tracks=[])


class LatencyRecordingTracker:
    def __init__(self):
        self.update_now = None
        self.predict_now = None

    def update(self, _observations, now, _frame_width):
        self.update_now = now
        return SimpleNamespace(events=[], tracks=[])

    def predict(self, now, _frame_width):
        self.predict_now = now
        return SimpleNamespace(events=[], tracks=[])


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
    def test_empty_candidate_scans_back_off_without_becoming_unresponsive(self):
        self.assertEqual(OCR_INTERVAL_SECONDS, adaptive_ocr_interval(0))
        self.assertEqual(OCR_INTERVAL_SECONDS, adaptive_ocr_interval(2))
        self.assertEqual(0.5, adaptive_ocr_interval(3))
        self.assertEqual(1.0, adaptive_ocr_interval(4))
        self.assertEqual(1.0, adaptive_ocr_interval(100))

    def test_empty_template_scans_back_off_to_four_fps(self):
        self.assertAlmostEqual(1.0 / 8.0, adaptive_template_interval(0))
        self.assertAlmostEqual(1.0 / 8.0, adaptive_template_interval(11))
        self.assertEqual(0.25, adaptive_template_interval(12))
        self.assertEqual(0.25, adaptive_template_interval(100))

    def test_track_timeout_expands_for_slow_ocr_and_is_capped(self):
        self.assertEqual(3.5, adaptive_track_timeout(0.5))
        self.assertEqual(13.0, adaptive_track_timeout(5.0))
        self.assertEqual(60.0, adaptive_track_timeout(999.0))

    def test_overlay_prediction_loops_do_not_capture_unused_frames(self):
        frame, _ = card_frame()
        stop_event = LoopLimitedStopEvent(5)
        capture = CountingCapture(frame)

        with patch("droid_alerts.belt.watcher.create_capture", return_value=capture):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                ocr_engine=StaticOcr([]),
            )

        self.assertEqual(1, capture.grabs)
        self.assertTrue(capture.closed)

    def test_ocr_result_is_predicted_forward_to_completion_time(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        tracker = LatencyRecordingTracker()
        events = []

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch("droid_alerts.belt.watcher.BeltTracker", return_value=tracker),
            patch(
                "droid_alerts.belt.watcher.time.monotonic",
                side_effect=(10.0, 10.1, 10.9, 11.0),
            ),
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                status_callback=events.append,
                ocr_engine=StaticOcr([]),
            )

        self.assertEqual(10.1, tracker.update_now)
        self.assertEqual(10.9, tracker.predict_now)
        scan = next(event for event in events if event["type"] == "scan")
        self.assertAlmostEqual(0.8, scan["ocr_seconds"])
        self.assertAlmostEqual(1.25, scan["ocr_fps"])
        self.assertEqual(3.5, scan["track_timeout_seconds"])

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

    def test_normal_watcher_uses_templates_without_starting_ocr(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        recognizer = MagicMock()
        recognizer.analyze.return_value = SimpleNamespace(
            observations=[],
            candidates=(),
            text_observations=(),
            diagnostics={"detector": "templates", "card_window_count": 0},
        )
        events = []

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch(
                "droid_alerts.belt.watcher.TemplateCardRecognizer",
                return_value=recognizer,
            ) as template_factory,
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                status_callback=events.append,
            )

        template_factory.assert_called_once_with()
        recognizer.analyze.assert_called_once()
        ready = next(event for event in events if event["type"] == "ready")
        scan = next(event for event in events if event["type"] == "scan")
        self.assertEqual("templates", ready["detector"])
        self.assertEqual("templates", scan["detector"])

    def test_optional_template_collector_receives_tracked_accepted_candidates(self):
        frame, name_box = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        collector = MagicMock()
        collector.status.return_value = {
            "enabled": True,
            "path": "belt_template_samples",
            "total_samples": 0,
            "droid_count": 0,
            "review_count": 0,
            "max_per_droid": 20,
        }
        collector.process_events.return_value = []
        collector.expire.return_value = []
        collector.close.return_value = []

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch(
                "droid_alerts.belt.watcher.BeltTemplateSampleCollector",
                return_value=collector,
            ) as collector_factory,
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                ocr_engine=StaticOcr([TextObservation("R2", 0.99, name_box)]),
                collect_template_samples=True,
            )

        collector_factory.assert_called_once_with()
        self.assertEqual({0: 1}, collector.observe.call_args.args[2])
        self.assertEqual("R2", collector.observe.call_args.args[1][0].canonical_name)
        collector.close.assert_called_once_with()

    def test_template_index_failure_is_reported(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        events = []

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch(
                "droid_alerts.belt.watcher.TemplateCardRecognizer",
                side_effect=RuntimeError("missing index"),
            ),
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                status_callback=events.append,
            )

        self.assertTrue(capture.closed)
        self.assertIn("Belt template recognition could not start", events[0]["message"])
        self.assertIn("missing index", events[0]["message"])
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

    def test_per_droid_minimum_tier_controls_alerts_without_filtering_recognition(self):
        frame, _ = card_frame()

        cases = (
            ({}, "Default", False),
            ({"R2": "Default"}, "", True),
            ({"R2": "Gold"}, "Default", False),
            ({"R2": "Gold"}, "Gold", True),
            ({"R2": "Diamond"}, "Rainbow", True),
            ({"R2": "Rainbow"}, "Diamond", False),
            ({"R2": "Rainbow"}, "Beskar", True),
        )
        for targets, detected_family, expected_alerted in cases:
            stop_event = threading.Event()
            capture = OneFrameCapture(frame, stop_event)
            ocr = StaticOcr([])
            with (
                patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
                patch(
                    "droid_alerts.belt.watcher.BeltTracker",
                    return_value=EnteringTracker(detected_family),
                ),
                patch(
                    "droid_alerts.belt.watcher.log_track_event",
                    return_value={"droid": "R2", "alerted": expected_alerted},
                ) as log_event,
            ):
                run_belt_watcher(
                    1,
                    PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                    target_tiers=targets,
                    stop_event=stop_event,
                    ocr_engine=ocr,
                )

            self.assertEqual(expected_alerted, log_event.call_args.kwargs["alerted"])


if __name__ == "__main__":
    unittest.main()
