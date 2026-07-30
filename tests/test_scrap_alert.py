from __future__ import annotations

import unittest

import cv2
import numpy as np

from droid_alerts.classifier import Detection
from droid_alerts.config import AppConfig
from droid_alerts.notifications import alert_type_id
from droid_alerts.scrap_alert import (
    CreditHudDetector,
    CreditHudObservation,
    ScrapIncomeTracker,
    ScrapVisibilityTracker,
)


class ScrapIncomeTrackerTests(unittest.TestCase):
    def test_alerts_after_thirty_seconds_without_a_change(self):
        tracker = ScrapIncomeTracker(stall_seconds=30)
        same = CreditHudObservation(True, "same", 0.95)

        self.assertFalse(tracker.observe(same, now=10))
        self.assertFalse(tracker.observe(same, now=39.9))
        self.assertAlmostEqual(29.9, tracker.unchanged_seconds(39.9))
        self.assertTrue(tracker.observe(same, now=40))
        self.assertFalse(tracker.observe(same, now=80))

    def test_change_rearms_and_missing_hud_resets_timer(self):
        tracker = ScrapIncomeTracker(stall_seconds=30)
        first = CreditHudObservation(True, "first", 0.95)
        second = CreditHudObservation(True, "second", 0.95)

        tracker.observe(first, now=0)
        self.assertFalse(tracker.observe(second, now=29))
        self.assertFalse(tracker.observe(second, now=58))
        self.assertTrue(tracker.observe(second, now=59))
        self.assertFalse(tracker.observe(CreditHudObservation(False), now=60))
        self.assertFalse(tracker.observe(second, now=90))

class ScrapVisibilityTrackerTests(unittest.TestCase):
    def test_alerts_once_after_five_minutes_without_the_icon(self):
        tracker = ScrapVisibilityTracker(inactive_seconds=300)

        self.assertFalse(tracker.observe(icon_visible=False, now=10))
        self.assertFalse(tracker.observe(icon_visible=False, now=309.9))
        self.assertTrue(tracker.observe(icon_visible=False, now=310))
        self.assertFalse(tracker.observe(icon_visible=False, now=600))

    def test_seeing_the_icon_rearms_the_inactive_alert(self):
        tracker = ScrapVisibilityTracker(inactive_seconds=300)

        tracker.observe(icon_visible=False, now=0)
        self.assertTrue(tracker.observe(icon_visible=False, now=300))
        self.assertFalse(tracker.observe(icon_visible=True, now=305))
        self.assertFalse(tracker.observe(icon_visible=False, now=310))
        self.assertTrue(tracker.observe(icon_visible=False, now=610))


class CreditHudDetectorTests(unittest.TestCase):
    def test_locates_icon_and_fingerprints_amount_without_ocr(self):
        detector = CreditHudDetector()
        screen_height, screen_width = 1080, 1920
        bottom = np.full((410, screen_width, 3), (50, 60, 70), dtype=np.uint8)
        rendered = cv2.resize(detector.template, (56, 56), interpolation=cv2.INTER_AREA)
        alpha = rendered[:, :, 3:4].astype(np.float32) / 255.0
        x, y = 42, 260
        destination = bottom[y : y + 56, x : x + 56].astype(np.float32)
        bottom[y : y + 56, x : x + 56] = (
            rendered[:, :, :3].astype(np.float32) * alpha + destination * (1.0 - alpha)
        ).astype(np.uint8)
        cv2.putText(
            bottom,
            "693.83T",
            (100, 305),
            cv2.FONT_HERSHEY_DUPLEX,
            1.2,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        observation = detector.detect(
            bottom,
            screen_height=screen_height,
            screen_width=screen_width,
        )

        self.assertTrue(observation.visible)
        self.assertTrue(observation.icon_visible)
        self.assertIsNotNone(observation.fingerprint)
        self.assertGreater(observation.icon_score, 0.9)


class ScrapAlertIntegrationTests(unittest.TestCase):
    def test_config_and_alert_type(self):
        restored = AppConfig.from_dict(AppConfig(scrap_alert_enabled=True).to_dict())
        self.assertTrue(restored.scrap_alert_enabled)

        detection = Detection(
            droid="Scrap",
            rarity="Stalled",
            row_box=(0, 0, 0, 0),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="scrap-alert",
        )
        self.assertEqual("scrap_alert", alert_type_id(detection))

        inactive = Detection(
            droid="Scrap",
            rarity="Inactive",
            row_box=(0, 0, 0, 0),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="scrap-inactive",
        )
        self.assertEqual("scrap_alert", alert_type_id(inactive))


if __name__ == "__main__":
    unittest.main()
