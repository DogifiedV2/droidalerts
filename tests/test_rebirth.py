from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.classifier import Detection
from droid_alerts.config import AppConfig
from droid_alerts.notifications import alert_title, event_text
from droid_alerts.rebirth import (
    RebirthAlertTracker,
    RebirthHudDetector,
    RebirthObservation,
)


FIXTURES = BASE_DIR / "tests" / "rebirth_fixtures"


class RebirthHudDetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.detector = RebirthHudDetector()

    def test_reads_ready_and_level_22_from_ultrawide_hud(self):
        image = cv2.imread(str(FIXTURES / "rebirth_22_ready.png"))

        observation = self.detector.detect(image, screen_height=1080)

        self.assertTrue(observation.ready)
        self.assertGreater(observation.ready_score, 0.95)
        self.assertEqual(22, observation.level)
        self.assertGreater(observation.level_score, 0.75)

    def test_reads_ready_and_level_25_from_widescreen_hud(self):
        image = cv2.imread(str(FIXTURES / "rebirth_25_ready.png"))

        observation = self.detector.detect(image, screen_height=1080)

        self.assertTrue(observation.ready)
        self.assertGreater(observation.ready_score, 0.78)
        self.assertEqual(25, observation.level)
        self.assertGreater(observation.level_score, 0.75)

    def test_ready_without_level_fails_closed(self):
        image = cv2.imread(str(FIXTURES / "rebirth_22_ready.png"))
        image[:, :150] = 0

        observation = self.detector.detect(image, screen_height=1080)

        self.assertTrue(observation.ready)
        self.assertIsNone(observation.level)

    def test_no_ready_text_does_not_read_level(self):
        image = np.zeros((180, 900, 3), dtype=np.uint8)

        observation = self.detector.detect(image, screen_height=1080)

        self.assertFalse(observation.ready)
        self.assertIsNone(observation.level)


class RebirthAlertTrackerTests(unittest.TestCase):
    def test_alerts_once_per_confirmed_level_and_persists_it(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "rebirth_state.json"
            tracker = RebirthAlertTracker(state_path)
            level_22 = RebirthObservation(True, 0.95, 22, 0.85)

            self.assertIsNone(tracker.observe(level_22))
            self.assertEqual(22, tracker.observe(level_22))
            self.assertIsNone(tracker.observe(level_22))

            restarted = RebirthAlertTracker(state_path)
            self.assertIsNone(restarted.observe(level_22))
            self.assertIsNone(restarted.observe(level_22))

            level_23 = RebirthObservation(True, 0.96, 23, 0.86)
            self.assertIsNone(restarted.observe(level_23))
            self.assertEqual(23, restarted.observe(level_23))

    def test_does_not_alert_when_level_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = RebirthAlertTracker(Path(directory) / "state.json")
            unknown = RebirthObservation(True, 0.97, None, 0.0)

            self.assertIsNone(tracker.observe(unknown))
            self.assertIsNone(tracker.observe(unknown))


class RebirthIntegrationTests(unittest.TestCase):
    def test_config_round_trip(self):
        config = AppConfig(
            rebirth_ready_alert_enabled=True,
            rebirth_scan_interval_seconds=7.0,
        )

        restored = AppConfig.from_dict(config.to_dict())

        self.assertTrue(restored.rebirth_ready_alert_enabled)
        self.assertEqual(7.0, restored.rebirth_scan_interval_seconds)

    def test_notification_copy(self):
        detection = Detection(
            droid="Rebirth",
            rarity="Ready",
            row_box=(0, 0, 0, 0),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="rebirth-ready",
        )

        self.assertEqual("Rebirth Ready", event_text(detection))
        self.assertEqual("Droid Alerts Rebirth Ready", alert_title(detection))


if __name__ == "__main__":
    unittest.main()
