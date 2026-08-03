from __future__ import annotations

import unittest

import cv2
import numpy as np

from droid_alerts.classifier import Detection
from droid_alerts.config import AppConfig
from droid_alerts.notifications import alert_type_id
from droid_alerts.normalize import scale_from_screen
from droid_alerts.scrap_alert import (
    CreditHudDetector,
    CreditHudObservation,
    ScrapIncomeTracker,
    ScrapRateTracker,
    ScrapVisibilityTracker,
    format_credit_rate,
    parse_credit_amount,
    _read_credit_amount,
)
from tests.resolution_matrix import RESOLUTION_CASES


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


class ScrapRateTrackerTests(unittest.TestCase):
    def test_reports_on_each_new_increase_without_waiting_ten_seconds(self):
        tracker = ScrapRateTracker(confirmation_seconds=10)

        self.assertIsNone(tracker.observe(1.0e12, now=0))
        self.assertEqual(1.2e12, tracker.observe(1.1e12, now=5))
        self.assertEqual(1.2e12, tracker.observe(1.2e12, now=10))

    def test_flat_or_missing_value_keeps_the_last_numeric_baseline(self):
        tracker = ScrapRateTracker(confirmation_seconds=10)
        tracker.observe(100, now=0)
        self.assertEqual(120, tracker.observe(110, now=5))
        self.assertEqual(60, tracker.observe(110, now=10))
        self.assertIsNone(tracker.observe(None, now=20))
        self.assertEqual(48, tracker.observe(120, now=25))

    def test_unchanged_readings_reduce_a_stale_burst_rate(self):
        tracker = ScrapRateTracker(window_seconds=300)
        tracker.observe(100e9, now=0, amount_text="100.00B")

        self.assertEqual(
            600e9,
            tracker.observe(110e9, now=1, amount_text="110.00B"),
        )
        self.assertEqual(
            60e9,
            tracker.observe(110e9, now=10, amount_text="110.00B"),
        )

    def test_pauses_after_thirty_flat_seconds_and_restarts_without_idle_time(self):
        tracker = ScrapRateTracker(window_seconds=300, idle_seconds=30)
        tracker.observe(100e9, now=0, amount_text="100.00B")
        self.assertEqual(120e9, tracker.observe(110e9, now=5, amount_text="110.00B"))

        self.assertEqual(0.0, tracker.observe(110e9, now=35, amount_text="110.00B"))
        self.assertEqual("income_paused", tracker.last_status)
        self.assertEqual(0.0, tracker.observe(110e9, now=60, amount_text="110.00B"))
        self.assertEqual("idle", tracker.last_status)

        self.assertEqual(0.0, tracker.observe(120e9, now=65, amount_text="120.00B"))
        self.assertEqual("income_resumed", tracker.last_status)
        self.assertEqual(120e9, tracker.observe(130e9, now=70, amount_text="130.00B"))
        self.assertEqual("rate_updated", tracker.last_status)

    def test_missing_suffix_does_not_create_an_orders_of_magnitude_spike(self):
        tracker = ScrapRateTracker(window_seconds=300)
        tracker.observe(33.8e9, now=0, amount_text="33.8B")
        normal_rate = tracker.observe(33.9e9, now=5, amount_text="33.9B")

        self.assertEqual(1.2e9, normal_rate)
        self.assertIsNone(tracker.observe(4.60, now=10, amount_text="4.60"))
        self.assertEqual("missing_suffix_rejected", tracker.last_status)
        recovered_rate = tracker.observe(34.0e9, now=15, amount_text="34.0B")
        self.assertEqual(800e6, recovered_rate)
        self.assertEqual("rate_updated", tracker.last_status)

    def test_single_large_ocr_outlier_is_not_displayed(self):
        tracker = ScrapRateTracker(window_seconds=300)
        readings = [20.0, 20.1, 20.2, 20.3, 40.0]
        rate = None
        for index, amount in enumerate(readings):
            rate = tracker.observe(
                amount * 1e9,
                now=index * 5,
                amount_text=f"{amount}B",
            )

        self.assertIsNone(rate)

    def test_sustained_large_jump_starts_a_new_baseline_without_counting_the_jump(self):
        tracker = ScrapRateTracker(window_seconds=300)
        for index, amount in enumerate((10.0, 10.1, 10.2, 10.3)):
            tracker.observe(amount * 1e9, now=index * 5, amount_text=f"{amount}B")

        self.assertIsNone(tracker.observe(20.0e9, now=20, amount_text="20.0B"))
        rate = tracker.observe(20.1e9, now=25, amount_text="20.1B")

        self.assertEqual(1.2e9, rate)

    def test_compact_amount_parsing_and_rate_formatting(self):
        self.assertEqual(1.4e12, parse_credit_amount("1.4T"))
        self.assertEqual(693.83e12, parse_credit_amount("693.83t"))
        self.assertIsNone(parse_credit_amount("not credits"))
        self.assertEqual("1.4T", format_credit_rate(1.4e12))


class CreditHudDetectorTests(unittest.TestCase):
    def test_reads_the_heavy_italic_in_game_credit_font(self):
        fixture = cv2.imread("tests/credit_amount_fixture.png")

        text, value = _read_credit_amount(fixture)

        self.assertEqual("4.63T", text)
        self.assertEqual(4.63e12, value)

    def test_reads_every_two_second_sample_from_reference_video(self):
        # Cropped from the supplied 1080p capture at two-second intervals.
        # Keeping only the amount region makes this regression fixture small
        # and avoids depending on a local video path in normal test runs.
        seconds = list(range(0, 41, 2)) + [54, 56, 58]
        expected = [
            "476.72B",
            "498.33B",
            "519.94B",
            "541.55B",
            "563.15B",
            "584.76B",
            "627.98B",
            "649.58B",
            "671.19B",
            "692.80B",
            "714.41B",
            "757.62B",
            "779.23B",
            "844.06B",
            "865.66B",
            "887.27B",
            "887.27B",
            "887.27B",
            "887.27B",
            "887.27B",
            "887.27B",
            "1.01T",
            "1.10T",
            "1.10T",
        ]
        row_height = 73
        fixture = cv2.imread("tests/credit_amount_video_samples.png")
        self.assertIsNotNone(fixture)
        self.assertEqual(row_height * len(expected), fixture.shape[0])

        tracker = ScrapRateTracker(window_seconds=300)
        rate = None
        for index, (second, expected_text) in enumerate(zip(seconds, expected)):
            row = fixture[index * row_height : (index + 1) * row_height]
            text, value = _read_credit_amount(row)
            self.assertEqual(expected_text, text, f"wrong OCR at {second}s")
            updated_rate = tracker.observe(value, now=second, amount_text=text)
            if updated_rate is not None:
                rate = updated_rate

        self.assertIsNotNone(rate)
        self.assertAlmostEqual((1.10e12 - 476.72e9) * 60.0 / 58.0, rate)

    def test_reads_credit_hud_across_shared_resolution_matrix(self):
        source = cv2.imread("tests/credit_hud_1080p_fixture.png")
        self.assertIsNotNone(source)
        source_scale = scale_from_screen(1080, 1920)
        detector = CreditHudDetector()

        for case in RESOLUTION_CASES:
            with self.subTest(resolution=f"{case.width}x{case.height}"):
                target_scale = scale_from_screen(case.height, case.width)
                ratio = target_scale / source_scale
                scaled = cv2.resize(
                    source,
                    None,
                    fx=ratio,
                    fy=ratio,
                    interpolation=cv2.INTER_AREA if ratio < 1.0 else cv2.INTER_CUBIC,
                )
                bottom_height = case.height - int(round(case.height * 0.62))
                bottom = np.full(
                    (bottom_height, case.width, 3),
                    (48, 36, 48),
                    dtype=np.uint8,
                )
                copy_height = min(bottom.shape[0], scaled.shape[0])
                copy_width = min(bottom.shape[1], scaled.shape[1])
                bottom[-copy_height:, :copy_width] = scaled[-copy_height:, :copy_width]

                observation = detector.detect(
                    bottom,
                    screen_height=case.height,
                    screen_width=case.width,
                )

                self.assertEqual("2.44T", observation.amount_text)
                self.assertEqual(2.44e12, observation.amount_value)
                self.assertGreaterEqual(observation.icon_score, 0.90)

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
        self.assertEqual("693.83T", observation.amount_text)
        self.assertEqual(693.83e12, observation.amount_value)


class ScrapAlertIntegrationTests(unittest.TestCase):
    def test_config_and_alert_type(self):
        restored = AppConfig.from_dict(
            AppConfig(
                scrap_alert_enabled=True,
                scrap_income_overlay_enabled=True,
                scrap_income_overlay_scale=1.3,
            ).to_dict()
        )
        self.assertTrue(restored.scrap_alert_enabled)
        self.assertTrue(restored.scrap_income_overlay_enabled)
        self.assertEqual(1.3, restored.scrap_income_overlay_scale)

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
