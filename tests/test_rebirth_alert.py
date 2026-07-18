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

from droid_alerts.capture import MonitorInfo, PixelBox
from droid_alerts.classifier import Detection
from droid_alerts.config import AppConfig
from droid_alerts.gui import REBIRTH_ALERT_TOOLTIP
from droid_alerts.notifications import alert_title, event_text
from droid_alerts.rebirth import (
    RebirthAlertDetector,
    RebirthMatch,
    RebirthPresenceGate,
    rebirth_region,
)
from droid_alerts import watcher


class RebirthDetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.asset = BASE_DIR / "assets" / "rebirth_alert_jawa.png"

    def test_region_mirrors_chat_y_and_extends_over_right_side(self):
        region = rebirth_region(PixelBox(0, 576, 845, 230), 2560, 1440)

        self.assertEqual(1715, region.left)
        self.assertEqual(845, region.width)
        self.assertLess(region.top, 576)
        self.assertGreaterEqual(region.bottom, 576 + 2 * 230)

    def test_detector_matches_composited_jawa(self):
        rgba = cv2.imdecode(
            np.frombuffer(self.asset.read_bytes(), dtype=np.uint8),
            cv2.IMREAD_UNCHANGED,
        )
        rendered = cv2.resize(rgba, None, fx=2 / 3, fy=2 / 3, interpolation=cv2.INTER_AREA)
        rng = np.random.default_rng(20260718)
        image = rng.integers(0, 256, (500, 900, 3), dtype=np.uint8)
        x, y = 520, 90
        alpha = rendered[:, :, 3:4].astype(np.float32) / 255.0
        target = image[y : y + rendered.shape[0], x : x + rendered.shape[1]]
        target[:] = (rendered[:, :, :3] * alpha + target * (1.0 - alpha)).astype(np.uint8)

        match = RebirthAlertDetector(self.asset).detect(
            image,
            screen_width=2560,
            screen_height=1440,
        )

        self.assertTrue(match.matched)
        self.assertGreater(match.score, 0.9)
        self.assertIsNotNone(match.box)

    def test_detector_rejects_unrelated_image(self):
        rng = np.random.default_rng(7)
        image = rng.integers(0, 256, (500, 900, 3), dtype=np.uint8)

        match = RebirthAlertDetector(self.asset).detect(
            image,
            screen_width=2560,
            screen_height=1440,
        )

        self.assertFalse(match.matched)

    def test_presence_gate_fires_once_per_appearance(self):
        gate = RebirthPresenceGate(confirm_frames=2, release_frames=2)

        self.assertFalse(gate.update(True))
        self.assertTrue(gate.update(True))
        self.assertFalse(gate.update(True))
        self.assertFalse(gate.update(False))
        self.assertFalse(gate.update(False))
        self.assertFalse(gate.update(True))
        self.assertTrue(gate.update(True))

    def test_rebirth_notification_copy_is_specific(self):
        detection = Detection(
            droid="Rebirth",
            rarity="Available",
            row_box=(0, 0, 1, 1),
            droid_score=0.9,
            rarity_score=0.9,
            rarity_margin=0.9,
            score=0.9,
            source="rebirth-alert",
        )

        self.assertEqual("A rebirth droid is available", event_text(detection))
        self.assertEqual("Droid Alerts Rebirth Alert", alert_title(detection))


class RebirthWatcherTests(unittest.TestCase):
    def test_confirmed_jawa_uses_normal_alert_channels_and_history(self):
        stop_event = threading.Event()
        status_events: list[dict[str, object]] = []
        capture = Mock()
        capture.monitor = MonitorInfo(0, 0, 2560, 1440, key="monitor-a")
        capture.screen_size.return_value = (2560, 1440)
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
            normalized_image=np.zeros((200, 800, 3), dtype=np.uint8),
            phrase_row_boxes=[],
            rejections=[],
            scale=1.0,
            scale_method="screen",
        )
        detector = Mock()
        detector.detect.return_value = RebirthMatch(
            True,
            0.93,
            (20, 30, 160, 220),
            2 / 3,
        )
        policy = Mock()
        telemetry = Mock()
        config = AppConfig(
            rebirth_alert_enabled=True,
            popup_enabled=True,
            sound_enabled=True,
            capture_interval_seconds=0.01,
        )

        with (
            patch.object(watcher, "set_dpi_awareness"),
            patch.object(watcher, "RegionResolver") as resolver_type,
            patch.object(watcher, "Pipeline", return_value=pipeline),
            patch.object(watcher, "AlertPolicy", return_value=policy),
            patch.object(watcher, "RebirthAlertDetector", return_value=detector),
            patch.object(watcher, "AnonymousTelemetryClient", return_value=telemetry),
            patch.object(watcher, "append_event") as append_event,
            patch.object(watcher, "show_popup") as show_popup,
            patch.object(watcher, "REBIRTH_SCAN_INTERVAL_SECONDS", 0.0),
        ):
            resolver_type.return_value.resolve.return_value = (
                PixelBox(0, 576, 845, 230),
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
        self.assertEqual("rebirth-alert", alert_event["source"])
        self.assertTrue(alert_event["is_priority"])
        self.assertTrue(
            any(
                event.get("type") == "alert"
                and event.get("event", {}).get("source") == "rebirth-alert"
                for event in status_events
            )
        )


class RebirthConfigTests(unittest.TestCase):
    def test_dashboard_toggle_uses_requested_tooltip(self):
        self.assertEqual(
            "Receive a notification when a droid you need for rebirth spawns",
            REBIRTH_ALERT_TOOLTIP,
        )

    def test_setting_round_trips_and_defaults_off(self):
        self.assertFalse(AppConfig.from_dict({}).rebirth_alert_enabled)
        restored = AppConfig.from_dict(AppConfig(rebirth_alert_enabled=True).to_dict())
        self.assertTrue(restored.rebirth_alert_enabled)


if __name__ == "__main__":
    unittest.main()
