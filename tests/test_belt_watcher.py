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

from droid_alerts.belt.matching import NameMatch
from droid_alerts.belt.models import CardCandidate, CardContext, DroidObservation
from droid_alerts.belt.runtime import (
    AdaptiveScanScheduler,
    adaptive_track_timeout,
    build_tracks_payload,
)
from droid_alerts.belt.watcher import (
    TEMPLATE_MINIMUM_DISPLACEMENT_RATIO,
    adaptive_template_interval,
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

    def screen_size(self):
        return self.frame.shape[1], self.frame.shape[0]

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


class ClosedCapture:
    def __init__(self):
        self.closed = False

    def grab(self, _box):
        raise RuntimeError("The selected window was closed.")

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


def template_result(*, observations=(), candidates=(), card_window_count=0):
    return SimpleNamespace(
        observations=list(observations),
        candidates=tuple(candidates),
        diagnostics={
            "detector": "templates",
            "card_window_count": card_window_count,
        },
    )


class RecordingQueue:
    def __init__(self):
        self.events = []

    def put(self, event):
        self.events.append(event)


class BeltRuntimeTests(unittest.TestCase):
    def test_scheduler_backs_off_after_empty_threshold_and_resets(self):
        scheduler = AdaptiveScanScheduler(4, 8)
        timing = None
        for index in range(12):
            timing = scheduler.record(
                card_window_count=0,
                captured_at=float(index),
                completed_at=float(index) + 0.05,
            )
        self.assertEqual(0.25, timing["next_scan_interval_seconds"])
        timing = scheduler.record(card_window_count=1, captured_at=20.0, completed_at=20.05)
        self.assertEqual(0.125, timing["next_scan_interval_seconds"])
        self.assertEqual(0, timing["empty_candidate_scans"])

    def test_track_payload_rounds_geometry_and_preserves_attributes(self):
        track = SimpleNamespace(
            id=3,
            name="R2",
            family="Gold",
            rarity="Common",
            box=(1.4, 2.6, 10.2, 20.8),
            confidence=0.93,
        )
        self.assertEqual(
            [{
                "id": 3,
                "name": "R2",
                "family": "Gold",
                "rarity": "Common",
                "box": (1, 3, 10, 21),
                "confidence": 0.93,
            }],
            build_tracks_payload([track]),
        )

    def test_track_timeout_expands_for_old_cpu_capture_cadence(self):
        self.assertEqual(3.5, adaptive_track_timeout(3.5, None))
        self.assertEqual(3.5, adaptive_track_timeout(3.5, 0.5))
        self.assertAlmostEqual(25.0 / 3.0, adaptive_track_timeout(3.5, 10.0 / 3.0))
        self.assertEqual(20.0, adaptive_track_timeout(3.5, 30.0))


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

    def test_process_entry_forwards_dashboard_window_selection(self):
        status_queue = RecordingQueue()
        received = {}

        def fake_watcher(*_args, **kwargs):
            received.update(kwargs)

        with patch("droid_alerts.belt.worker.run_belt_watcher", side_effect=fake_watcher):
            run_belt_worker_process(
                1,
                PixelBox(0, 0, 900, 520),
                {},
                threading.Event(),
                status_queue,
                capture_source="window",
                window_title="Fortnite",
                window_process="Fortnite.exe",
                window_class="UnrealWindow",
            )

        self.assertEqual("window", received["capture_source"])
        self.assertEqual("Fortnite", received["window_title"])
        self.assertEqual("Fortnite.exe", received["window_process"])
        self.assertEqual("UnrealWindow", received["window_class"])

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


class DelayedFamilyTracker:
    def update(self, _observations, _now, _frame_width):
        unknown = SimpleNamespace(id=1, name="R2", family="", confidence=0.93)
        gold = SimpleNamespace(id=1, name="R2", family="Gold", confidence=0.93)
        return SimpleNamespace(
            events=[
                SimpleNamespace(kind="entered", track=unknown),
                SimpleNamespace(kind="updated", track=gold),
                SimpleNamespace(kind="updated", track=gold),
            ],
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


def belt_track(track_id=1, *, left=10.0):
    return SimpleNamespace(
        id=track_id,
        name="R2",
        family="Gold",
        rarity="Common",
        confidence=0.93,
        family_confidence=0.92,
        rarity_confidence=0.91,
        confirmation_mode="normal",
        raw_text="template:R2",
        box=(left, 20.0, 180.0, 260.0),
    )


class ScriptedTracker:
    def __init__(self, predictions, *, events=()):
        self.predictions = list(predictions)
        self.events = list(events)
        self.timeout_seconds = 3.5

    def update(self, _observations, _now, _frame_width):
        return SimpleNamespace(events=list(self.events), tracks=[])

    def predict(self, _now, _frame_width):
        if len(self.predictions) > 1:
            tracks = self.predictions.pop(0)
        else:
            tracks = self.predictions[0]
        return SimpleNamespace(events=[], tracks=list(tracks))

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
    def _run_payload_sequence(self, predictions, *, loops=4):
        frame, _ = card_frame()
        stop_event = LoopLimitedStopEvent(loops)
        tracker = ScriptedTracker(predictions)
        recognizer = MagicMock()
        recognizer.analyze.return_value = template_result()
        events = []
        with (
            patch(
                "droid_alerts.belt.watcher.create_capture",
                return_value=CountingCapture(frame),
            ),
            patch("droid_alerts.belt.watcher.BeltTracker", return_value=tracker),
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
                return_value=recognizer,
            ),
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                status_callback=events.append,
            )
        return [event["tracks"] for event in events if event["type"] == "tracks"]

    def test_unchanged_empty_tracks_are_published_once(self):
        self.assertEqual([[]], self._run_payload_sequence([[], [], [], []]))

    def test_visible_tracks_publish_one_clearing_empty_payload(self):
        visible = [belt_track()]
        payloads = self._run_payload_sequence([visible, [], [], []])

        self.assertEqual(2, len(payloads))
        self.assertEqual(1, len(payloads[0]))
        self.assertEqual([], payloads[1])

    def test_moving_tracks_continue_to_publish_changed_boxes(self):
        payloads = self._run_payload_sequence(
            [[belt_track(left=10.0)], [belt_track(left=25.0)], [belt_track(left=25.0)]]
        )

        self.assertEqual(2, len(payloads))
        self.assertNotEqual(payloads[0][0]["box"], payloads[1][0]["box"])

    def test_exited_alert_id_is_released_for_reuse(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        track = belt_track()
        tracker = ScriptedTracker(
            [[]],
            events=(
                SimpleNamespace(kind="entered", track=track),
                SimpleNamespace(kind="exited", track=track),
                SimpleNamespace(kind="entered", track=track),
            ),
        )
        recognizer = MagicMock()
        recognizer.analyze.return_value = template_result()
        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch("droid_alerts.belt.watcher.BeltTracker", return_value=tracker),
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
                return_value=recognizer,
            ),
            patch(
                "droid_alerts.belt.watcher.log_track_event",
                side_effect=lambda event, *, alerted: {
                    "event": event.kind,
                    "alerted": alerted,
                },
            ) as log_event,
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                target_tiers={"R2": "Gold"},
                stop_event=stop_event,
            )

        self.assertEqual(
            [True, False, True],
            [call.kwargs["alerted"] for call in log_event.call_args_list],
        )

    def test_reacquired_track_cannot_alert_again_on_later_family_update(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        track = belt_track()
        tracker = ScriptedTracker(
            [[]],
            events=(
                SimpleNamespace(kind="entered", track=track),
                SimpleNamespace(kind="exited", track=track),
                SimpleNamespace(kind="reacquired", track=track),
                SimpleNamespace(kind="updated", track=track),
            ),
        )
        recognizer = MagicMock()
        recognizer.analyze.return_value = template_result()
        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch("droid_alerts.belt.watcher.BeltTracker", return_value=tracker),
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
                return_value=recognizer,
            ),
            patch(
                "droid_alerts.belt.watcher.log_track_event",
                side_effect=lambda event, *, alerted: {
                    "event": event.kind,
                    "alerted": alerted,
                },
            ) as log_event,
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                target_tiers={"R2": "Gold"},
                stop_event=stop_event,
            )

        self.assertEqual(
            [True, False, False, False],
            [call.kwargs["alerted"] for call in log_event.call_args_list],
        )

    def test_log_failure_does_not_stop_track_event_delivery(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        track = belt_track()
        tracker = ScriptedTracker(
            [[]],
            events=(SimpleNamespace(kind="entered", track=track),),
        )
        recognizer = MagicMock()
        recognizer.analyze.return_value = template_result()
        events = []
        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch("droid_alerts.belt.watcher.BeltTracker", return_value=tracker),
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
                return_value=recognizer,
            ),
            patch(
                "droid_alerts.logging_io.append_event",
                side_effect=OSError("disk full"),
            ),
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                target_tiers={"R2": "Gold"},
                stop_event=stop_event,
                status_callback=events.append,
            )

        self.assertTrue(capture.closed)
        self.assertIn("track_event", [event["type"] for event in events])
        self.assertEqual("stopped", events[-1]["type"])

    def test_empty_template_scans_back_off_to_four_fps(self):
        self.assertAlmostEqual(1.0 / 8.0, adaptive_template_interval(0))
        self.assertAlmostEqual(1.0 / 8.0, adaptive_template_interval(11))
        self.assertEqual(0.25, adaptive_template_interval(12))
        self.assertEqual(0.25, adaptive_template_interval(100))

    def test_overlay_prediction_loops_do_not_capture_unused_frames(self):
        frame, _ = card_frame()
        stop_event = LoopLimitedStopEvent(5)
        capture = CountingCapture(frame)
        recognizer = MagicMock()
        recognizer.analyze.return_value = template_result()

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
                return_value=recognizer,
            ),
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
            )

        self.assertEqual(1, capture.grabs)
        self.assertTrue(capture.closed)

    def test_template_result_is_predicted_forward_to_completion_time(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        tracker = LatencyRecordingTracker()
        recognizer = MagicMock()
        recognizer.analyze.return_value = template_result()
        events = []

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch(
                "droid_alerts.belt.watcher.BeltTracker",
                return_value=tracker,
            ) as tracker_factory,
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
                return_value=recognizer,
            ),
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
            )

        self.assertEqual(10.1, tracker.update_now)
        self.assertEqual(10.9, tracker.predict_now)
        self.assertEqual(
            TEMPLATE_MINIMUM_DISPLACEMENT_RATIO,
            tracker_factory.call_args.kwargs["minimum_template_displacement_ratio"],
        )
        scan = next(event for event in events if event["type"] == "scan")
        self.assertAlmostEqual(0.8, scan["scan_seconds"])
        self.assertAlmostEqual(1.25, scan["scan_throughput_fps"])
        self.assertEqual(3.5, scan["track_timeout_seconds"])

    def test_watcher_uses_independent_mss_and_reports_safe_counts(self):
        frame, name_box = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        observation = DroidObservation(
            NameMatch("R2", 1.0, "template:R2"),
            0.98,
            name_box,
        )
        candidate = SimpleNamespace(accepted=True, canonical_name="R2")
        recognizer = MagicMock()
        recognizer.analyze.return_value = template_result(
            observations=(observation,),
            candidates=(candidate,),
            card_window_count=1,
        )
        events = []

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture) as factory,
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
                return_value=recognizer,
            ),
        ):
            run_belt_watcher(
                2,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                status_callback=events.append,
            )

        factory.assert_called_once_with(monitor_index=2, prefer_dxcam=False)
        scan = next(event for event in events if event["type"] == "scan")
        recognizer.analyze.assert_called_once()
        self.assertEqual(0, scan["raw_count"])
        self.assertEqual(1, scan["candidate_count"])
        self.assertEqual(1, scan["accepted_count"])
        self.assertTrue(capture.closed)
        self.assertEqual("stopped", events[-1]["type"])

    def test_window_capture_reconnects_after_fortnite_restarts(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        closed_capture = ClosedCapture()
        replacement = OneFrameCapture(frame, stop_event)
        recognizer = MagicMock()
        recognizer.analyze.return_value = template_result()
        events = []

        with (
            patch(
                "droid_alerts.belt.watcher.create_capture",
                side_effect=(closed_capture, replacement),
            ) as factory,
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
                return_value=recognizer,
            ),
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                status_callback=events.append,
                capture_source="window",
                window_title="Fortnite",
                window_process="Fortnite.exe",
                window_class="UnrealWindow",
            )

        expected_call = {
            "monitor_index": 1,
            "prefer_dxcam": False,
            "capture_source": "window",
            "window_title": "Fortnite",
            "window_process": "Fortnite.exe",
            "window_class": "UnrealWindow",
        }
        self.assertEqual(2, factory.call_count)
        self.assertEqual(expected_call, factory.call_args_list[0].kwargs)
        self.assertEqual(expected_call, factory.call_args_list[1].kwargs)
        self.assertTrue(closed_capture.closed)
        self.assertTrue(replacement.closed)
        self.assertIn("capture_error", [event["type"] for event in events])
        self.assertIn("capture_reconnected", [event["type"] for event in events])

    def test_normal_watcher_uses_hybrid_templates_without_starting_ocr(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        recognizer = MagicMock()
        recognizer.analyze.return_value = SimpleNamespace(
            observations=[],
            candidates=(),
            diagnostics={"detector": "templates", "card_window_count": 0},
        )
        events = []

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
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
        self.assertEqual("hybrid-v2", ready["detector"])
        self.assertEqual("hybrid-v2", scan["detector"])

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
        observation = DroidObservation(
            NameMatch("R2", 1.0, "template:R2"),
            0.99,
            name_box,
        )
        candidate = SimpleNamespace(accepted=True, canonical_name="R2")
        recognizer = MagicMock()
        recognizer.analyze.return_value = template_result(
            observations=(observation,),
            candidates=(candidate,),
            card_window_count=1,
        )

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
                return_value=recognizer,
            ),
            patch(
                "droid_alerts.belt.watcher.BeltTemplateSampleCollector",
                return_value=collector,
            ) as collector_factory,
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                collect_template_samples=True,
            )

        collector_factory.assert_called_once_with()
        self.assertEqual({0: 1}, collector.observe.call_args.args[2])
        self.assertEqual("R2", collector.observe.call_args.args[1][0].canonical_name)
        collector.close.assert_called_once_with()

    def test_dev_mode_saves_accepted_crops_and_manual_detector_snapshot(self):
        frame, name_box = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        observation = DroidObservation(
            NameMatch("R2", 1.0, "template:R2"),
            0.99,
            (80, 80, 180, 260),
        )
        context = CardContext(
            art_box=(105, 100, 120, 140),
            card_box=(80, 80, 180, 260),
            nameplate_dark_fraction=0.85,
            art_standard_deviation=45.0,
            art_edge_density=0.10,
            frame_line_ratio=0.50,
            accepted=True,
            reason="accepted_template",
        )
        candidate = CardCandidate(
            canonical_name="R2",
            raw_text="template:R2",
            identity_confidence=0.98,
            name_box=name_box,
            context=context,
            accepted=True,
            reason="accepted_template",
            family="Gold",
            family_confidence=0.92,
            rarity="Epic",
            rarity_confidence=1.0,
            raw_best_similarity=0.92,
            runner_up_identity="R4",
            identity_margin=0.09,
        )
        rejected_context = CardContext(
            art_box=(325, 100, 120, 140),
            card_box=(300, 80, 180, 260),
            nameplate_dark_fraction=0.85,
            art_standard_deviation=45.0,
            art_edge_density=0.10,
            frame_line_ratio=0.50,
            accepted=True,
            reason="accepted_template",
        )
        rejected_candidate = CardCandidate(
            canonical_name="R4",
            raw_text="template:R4",
            identity_confidence=0.72,
            name_box=(320, 280, 90, 25),
            context=rejected_context,
            accepted=False,
            reason="ambiguous_template_identity",
            family="Rainbow",
            family_confidence=0.81,
            rarity="Legendary",
            rarity_confidence=1.0,
            raw_best_similarity=0.78,
            runner_up_identity="R2",
            identity_margin=0.02,
        )
        recognizer = MagicMock()
        recognizer.analyze.return_value = template_result(
            observations=(observation,),
            candidates=(candidate, rejected_candidate),
            card_window_count=2,
        )
        dev_logger = MagicMock()
        dev_logger.enabled = True
        dev_logger.session_dir = Path("/tmp/belt_dev_test")
        dev_logger.relative_path.return_value = "belt_dev/session_test"
        dev_logger.consume_issue_request.return_value = ""
        dev_logger.save_frame.return_value = ""
        dev_logger.save_manual_capture.return_value = (
            "belt_dev/session_test/manual_captures/capture.png"
        )
        dev_logger.last_saved_reason = ""
        recorder = MagicMock()
        recorder.close.return_value = {
            "enabled": True,
            "written_tracks": 1,
        }
        events = []

        with (
            patch(
                "droid_alerts.belt.watcher.create_capture",
                return_value=capture,
            ),
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
                return_value=recognizer,
            ) as recognizer_factory,
            patch(
                "droid_alerts.belt.watcher.BeltDevLogger",
                return_value=dev_logger,
            ),
            patch(
                "droid_alerts.belt.watcher.BeltDevCaptureRecorder",
                return_value=recorder,
            ) as recorder_factory,
            patch(
                "droid_alerts.belt.watcher._belt_capture_hotkey",
                return_value=lambda: True,
            ),
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                stop_event=stop_event,
                status_callback=events.append,
                dev_mode=True,
            )

        recorder_factory.assert_called_once_with(
            dev_logger.session_dir,
            enabled=True,
        )
        recognizer_factory.assert_called_once_with()
        recorder.set_session_metadata.assert_called_once()
        self.assertEqual(
            [frame.shape[1], frame.shape[0]],
            recorder.set_session_metadata.call_args.kwargs[
                "source_resolution"
            ],
        )
        recorder.observe.assert_called_once()
        self.assertEqual((candidate,), recorder.observe.call_args.args[1])
        self.assertEqual({0: 1}, recorder.observe.call_args.args[2])
        dev_logger.save_manual_capture.assert_called_once()
        detector_snapshot = (
            dev_logger.save_manual_capture.call_args.kwargs["detector_snapshot"]
        )
        self.assertEqual(["R2"], detector_snapshot["detected_names"])
        self.assertEqual(
            "R2",
            detector_snapshot["accepted_detections"][0]["detected_name"],
        )
        self.assertEqual(
            "R4",
            detector_snapshot["rejected_candidates"][0]["best_guess_name"],
        )
        recorder.close.assert_called_once_with()
        self.assertIn("manual_capture", [event["type"] for event in events])
        self.assertIn("dev_capture", [event["type"] for event in events])

    def test_template_index_failure_is_reported(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        events = []

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
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
            recognizer = MagicMock()
            recognizer.analyze.return_value = template_result()
            with (
                patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
                patch(
                    "droid_alerts.belt.watcher.HybridCardRecognizer",
                    return_value=recognizer,
                ),
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
                )

            self.assertEqual(expected_alerted, log_event.call_args.kwargs["alerted"])

    def test_delayed_family_can_alert_once_without_duplicate_sighting(self):
        frame, _ = card_frame()
        stop_event = threading.Event()
        capture = OneFrameCapture(frame, stop_event)
        recognizer = MagicMock()
        recognizer.analyze.return_value = template_result()

        with (
            patch("droid_alerts.belt.watcher.create_capture", return_value=capture),
            patch(
                "droid_alerts.belt.watcher.HybridCardRecognizer",
                return_value=recognizer,
            ),
            patch(
                "droid_alerts.belt.watcher.BeltTracker",
                return_value=DelayedFamilyTracker(),
            ),
            patch(
                "droid_alerts.belt.watcher.log_track_event",
                side_effect=lambda event, *, alerted: {
                    "event": event.kind,
                    "droid": "R2",
                    "alerted": alerted,
                },
            ) as log_event,
        ):
            run_belt_watcher(
                1,
                PixelBox(0, 0, frame.shape[1], frame.shape[0]),
                target_tiers={"R2": "Gold"},
                stop_event=stop_event,
            )

        self.assertEqual(
            [False, True, False],
            [call.kwargs["alerted"] for call in log_event.call_args_list],
        )


if __name__ == "__main__":
    unittest.main()
