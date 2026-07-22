from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))
sys.path.insert(0, str(BASE_DIR / "tests"))
CB23_MISSION_TOAST_FIXTURE = BASE_DIR / "tests" / "cb23_mission_toast.png"

from droid_alerts.cb23_mission import (  # noqa: E402
    CB23MissionDetector,
    CB23MissionGate,
    CB23MissionMatch,
    cb23_mission_region,
)
from droid_alerts.classifier import Detection  # noqa: E402
from droid_alerts.capture import MonitorInfo, PixelBox  # noqa: E402
from droid_alerts.config import AppConfig, assets_dir  # noqa: E402
from droid_alerts.normalize import scale_from_screen  # noqa: E402
from droid_alerts.notifications import alert_title, alert_type_id, event_text  # noqa: E402
from droid_alerts.popup import _caption_text  # noqa: E402
from droid_alerts import watcher  # noqa: E402
from resolution_matrix import RESOLUTION_CASES, place_inside, resize_for_screen  # noqa: E402


def _synthetic_frame(
    *,
    with_mission_toast: bool = True,
    with_cb23: bool = True,
) -> np.ndarray:
    frame = np.full((720, 1280, 3), (75, 58, 44), dtype=np.uint8)
    if with_mission_toast:
        cv2.rectangle(frame, (80, 290), (390, 405), (8, 8, 8), -1)
        cv2.rectangle(frame, (80, 290), (390, 405), (0, 225, 255), 5)
        cv2.putText(
            frame,
            "MISSION COMPLETE!",
            (165, 335),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (255, 255, 255),
            2,
        )
        cv2.putText(
            frame,
            "REWARD READY!",
            (165, 375),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 225, 255),
            2,
        )

    if not with_cb23:
        return frame

    portrait = cv2.imread(
        str(assets_dir() / "cb23_mission.png"),
        cv2.IMREAD_UNCHANGED,
    )
    assert portrait is not None and portrait.shape[2] == 4
    height = 75
    width = round(portrait.shape[1] * height / portrait.shape[0])
    portrait = cv2.resize(portrait, (width, height), interpolation=cv2.INTER_AREA)
    alpha = portrait[:, :, 3:4].astype(np.float32) / 255.0
    x, y = 95, 307
    frame[y : y + height, x : x + width] = (
        portrait[:, :, :3] * alpha
        + frame[y : y + height, x : x + width] * (1.0 - alpha)
    ).astype(np.uint8)
    return frame


def _reported_mission_band(screen_width: int, screen_height: int) -> np.ndarray:
    """Place the supplied toast at the target HUD scale without stretching it."""

    toast = cv2.imread(str(CB23_MISSION_TOAST_FIXTURE), cv2.IMREAD_COLOR)
    assert toast is not None
    reference_width, reference_height = 1814, 1020
    target_region = cb23_mission_region(screen_width, screen_height)
    band = np.zeros((target_region.height, target_region.width, 3), dtype=np.uint8)
    reference_scale = scale_from_screen(reference_height, reference_width)
    target_scale = scale_from_screen(screen_height, screen_width)
    scaled_toast = resize_for_screen(
        toast,
        source_scale=reference_scale,
        target_scale=target_scale,
    )
    toast_y = round(520 * target_scale / reference_scale)
    place_inside(scaled_toast, band, x=0, y=toast_y)
    return band


def _synthetic_mission_band(
    screen_width: int,
    screen_height: int,
    *,
    with_mission_toast: bool,
    with_cb23: bool,
) -> np.ndarray:
    source = _synthetic_frame(
        with_mission_toast=with_mission_toast,
        with_cb23=with_cb23,
    )
    scaled = resize_for_screen(
        source,
        source_scale=scale_from_screen(720, 1280),
        target_scale=scale_from_screen(screen_height, screen_width),
    )
    region = cb23_mission_region(screen_width, screen_height)
    band = np.zeros((region.height, region.width, 3), dtype=np.uint8)
    place_inside(scaled, band, x=0, y=0)
    return band


class CB23MissionDetectorTests(unittest.TestCase):
    def test_region_scans_full_height_of_left_side(self) -> None:
        region = cb23_mission_region(1920, 1080)
        self.assertEqual(
            (0, 0, 1056, 1080),
            (region.left, region.top, region.width, region.height),
        )

    def test_detects_cb23_inside_mission_toast(self) -> None:
        frame = _synthetic_frame()
        region = cb23_mission_region(frame.shape[1], frame.shape[0])
        match = CB23MissionDetector().detect(
            frame[:, : region.width],
            screen_width=frame.shape[1],
            screen_height=frame.shape[0],
        )
        self.assertTrue(match.matched)
        self.assertGreater(match.score, 0.90)
        self.assertGreaterEqual(match.mission_score, 1.0)

    def test_detects_reported_mission_toast_across_resolutions(self) -> None:
        detector = CB23MissionDetector()
        for case in RESOLUTION_CASES:
            with self.subTest(resolution=f"{case.width}x{case.height}"):
                match = detector.detect(
                    _reported_mission_band(case.width, case.height),
                    screen_width=case.width,
                    screen_height=case.height,
                )
                self.assertTrue(
                    match.matched,
                    f"score={match.score:.4f}, mission_score={match.mission_score:.4f}",
                )

    def test_mission_negatives_are_rejected_across_resolutions(self) -> None:
        detector = CB23MissionDetector()
        for case in RESOLUTION_CASES:
            for with_mission_toast, with_cb23, label in (
                (False, True, "portrait-only"),
                (True, False, "toast-only"),
            ):
                with self.subTest(resolution=f"{case.width}x{case.height}", case=label):
                    match = detector.detect(
                        _synthetic_mission_band(
                            case.width,
                            case.height,
                            with_mission_toast=with_mission_toast,
                            with_cb23=with_cb23,
                        ),
                        screen_width=case.width,
                        screen_height=case.height,
                    )
                    self.assertFalse(match.matched)

    def test_portrait_without_mission_toast_is_rejected(self) -> None:
        frame = _synthetic_frame(with_mission_toast=False)
        match = CB23MissionDetector().detect(
            frame[:, :704],
            screen_width=1280,
            screen_height=720,
        )
        self.assertFalse(match.matched)
        self.assertEqual(0.0, match.mission_score)

    def test_mission_toast_without_cb23_is_rejected(self) -> None:
        frame = _synthetic_frame(with_cb23=False)
        match = CB23MissionDetector().detect(
            frame[:, :704],
            screen_width=1280,
            screen_height=720,
        )
        self.assertFalse(match.matched)

    def test_gate_requires_two_frames_and_rearms(self) -> None:
        gate = CB23MissionGate()
        self.assertFalse(gate.update(True))
        self.assertTrue(gate.update(True))
        self.assertFalse(gate.update(True))
        self.assertFalse(gate.update(False))
        self.assertFalse(gate.update(False))
        self.assertFalse(gate.update(True))
        self.assertTrue(gate.update(True))

    def test_config_and_notification_identity(self) -> None:
        restored = AppConfig.from_dict(
            AppConfig(cb23_mission_alert_enabled=True).to_dict()
        )
        self.assertTrue(restored.cb23_mission_alert_enabled)
        detection = Detection(
            droid="CB23",
            rarity="Mission",
            row_box=(0, 0, 1, 1),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="cb23-mission",
        )
        self.assertEqual("cb23_mission", alert_type_id(detection))
        self.assertEqual("CB23 Mission", event_text(detection))
        self.assertEqual("Droid Alerts CB23 Mission", alert_title(detection))
        self.assertEqual("MISSION READY", _caption_text(detection))


class CB23MissionWatcherTests(unittest.TestCase):
    def test_two_confirmed_scans_use_priority_alert_flow(self) -> None:
        stop_event = threading.Event()
        status_events: list[dict[str, object]] = []
        capture = Mock()
        capture.monitor = MonitorInfo(0, 0, 1280, 720, key="monitor-a")
        capture.screen_size.return_value = (1280, 720)
        grabs = 0

        def grab(box: PixelBox):
            nonlocal grabs
            grabs += 1
            if grabs >= 4:
                stop_event.set()
            return np.zeros((box.height, box.width, 3), dtype=np.uint8)

        capture.grab.side_effect = grab
        pipeline = Mock()
        pipeline.detect.return_value = SimpleNamespace(
            detections=[],
            normalized_image=np.zeros((120, 420, 3), dtype=np.uint8),
            phrase_row_boxes=[],
            rejections=[],
            scale=1.0,
            scale_method="screen",
        )
        detector = Mock()
        detector.detect.return_value = CB23MissionMatch(
            True,
            0.98,
            1.5,
            (30, 250, 75, 325),
            0.15,
        )
        policy = Mock()
        telemetry = Mock()
        config = AppConfig(
            cb23_mission_alert_enabled=True,
            popup_enabled=True,
            sound_enabled=True,
            capture_interval_seconds=0.01,
        )

        with (
            patch.object(watcher, "set_dpi_awareness"),
            patch.object(watcher, "RegionResolver") as resolver_type,
            patch.object(watcher, "Pipeline", return_value=pipeline),
            patch.object(watcher, "AlertPolicy", return_value=policy),
            patch.object(watcher, "CB23MissionDetector", return_value=detector),
            patch.object(watcher, "AnonymousTelemetryClient", return_value=telemetry),
            patch.object(watcher, "append_event") as append_event,
            patch.object(watcher, "show_popup") as show_popup,
            patch.object(watcher, "CB23_MISSION_SCAN_INTERVAL_SECONDS", 0.0),
        ):
            resolver_type.return_value.resolve.return_value = (
                PixelBox(0, 338, 422, 115),
                "auto",
            )
            watcher.run_watch(
                config=config,
                stop_event=stop_event,
                status_callback=status_events.append,
                capture_factory=lambda _config: capture,
            )

        policy.notify.assert_called_once()
        show_popup.assert_called_once()
        alert_event = next(
            call.args[0]
            for call in append_event.call_args_list
            if call.args[0].get("event_type") == "alert"
        )
        self.assertEqual("cb23-mission", alert_event["source"])
        self.assertEqual("CB23", alert_event["droid"])
        self.assertTrue(alert_event["is_priority"])
        self.assertTrue(
            any(
                event.get("type") == "alert"
                and event.get("event", {}).get("source") == "cb23-mission"
                for event in status_events
            )
        )


if __name__ == "__main__":
    unittest.main()
