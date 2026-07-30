from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))
sys.path.insert(0, str(BASE_DIR / "tests"))

from droid_alerts.config import AppConfig
from droid_alerts.normalize import scale_from_screen
from droid_alerts.rebirth import (
    RebirthAlertTracker,
    RebirthHudDetector,
    RebirthObservation,
)
from resolution_matrix import RESOLUTION_CASES, resize_for_screen


FIXTURES = BASE_DIR / "tests" / "rebirth_fixtures"


def _rebirth_hud_at_resolution(
    image: np.ndarray,
    screen_width: int,
    screen_height: int,
) -> np.ndarray:
    """Scale a real 1080p HUD crop and reproduce narrower-display bars."""

    scaled = resize_for_screen(
        image,
        source_scale=scale_from_screen(1080, 1920),
        target_scale=scale_from_screen(screen_height, screen_width),
    )
    bottom_bar = max(0, round((screen_height - screen_width * 9 / 16) / 2))
    if bottom_bar:
        scaled = cv2.copyMakeBorder(
            scaled,
            0,
            bottom_bar,
            0,
            0,
            cv2.BORDER_CONSTANT,
            value=0,
        )
    return scaled


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

    def test_reads_level_25_at_1280x720(self):
        image = cv2.imread(str(FIXTURES / "rebirth_25_ready.png"))
        image = cv2.resize(image, None, fx=2 / 3, fy=2 / 3, interpolation=cv2.INTER_AREA)

        observation = self.detector.detect(image, screen_height=720, screen_width=1280)

        self.assertTrue(observation.ready)
        self.assertEqual(25, observation.level)

    def test_reads_level_25_in_1920x1200_letterboxed_viewport(self):
        image = cv2.imread(str(FIXTURES / "rebirth_25_ready.png"))
        # The HUD retains its 1080p pixel size and the centered viewport adds a
        # 60-pixel black bar at the physical bottom of a 1200-pixel display.
        image = cv2.copyMakeBorder(image, 0, 60, 0, 0, cv2.BORDER_CONSTANT, value=0)

        observation = self.detector.detect(image, screen_height=1200, screen_width=1920)

        self.assertTrue(observation.ready)
        self.assertEqual(25, observation.level)

    def test_reads_ready_and_level_across_shared_resolution_matrix(self):
        for filename, expected_level in (
            ("rebirth_22_ready.png", 22),
            ("rebirth_25_ready.png", 25),
        ):
            source = cv2.imread(str(FIXTURES / filename))
            self.assertIsNotNone(source)
            for case in RESOLUTION_CASES:
                with self.subTest(
                    fixture=filename,
                    resolution=f"{case.width}x{case.height}",
                ):
                    image = _rebirth_hud_at_resolution(
                        source,
                        case.width,
                        case.height,
                    )
                    observation = self.detector.detect(
                        image,
                        screen_height=case.height,
                        screen_width=case.width,
                    )

                    self.assertTrue(
                        observation.ready,
                        f"ready_score={observation.ready_score:.4f}",
                    )
                    self.assertEqual(expected_level, observation.level)

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

            not_ready = RebirthObservation(False, 0.1, None, 0.0)
            for _ in range(3):
                self.assertIsNone(restarted.observe(not_ready))

            level_23 = RebirthObservation(True, 0.96, 23, 0.86)
            self.assertIsNone(restarted.observe(level_23))
            self.assertEqual(23, restarted.observe(level_23))

    def test_level_ocr_jitter_cannot_repeat_alert_during_same_ready_period(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = RebirthAlertTracker(Path(directory) / "state.json")
            level_25 = RebirthObservation(True, 0.95, 25, 0.82)

            self.assertIsNone(tracker.observe(level_25))
            self.assertEqual(25, tracker.observe(level_25))
            for level in (2, 2, 0, 0, 25, 25, 2, 2):
                self.assertIsNone(
                    tracker.observe(RebirthObservation(True, 0.95, level, 0.72))
                )

    def test_transient_ready_miss_does_not_rearm_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = RebirthAlertTracker(Path(directory) / "state.json")
            level_25 = RebirthObservation(True, 0.95, 25, 0.82)
            not_ready = RebirthObservation(False, 0.1, None, 0.0)

            tracker.observe(level_25)
            self.assertEqual(25, tracker.observe(level_25))
            self.assertIsNone(tracker.observe(not_ready))
            self.assertIsNone(tracker.observe(level_25))
            self.assertIsNone(tracker.observe(level_25))

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

if __name__ == "__main__":
    unittest.main()
