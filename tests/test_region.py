from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.capture import PixelBox
from droid_alerts.region import Calibration, RegionResolver, auto_box_percent, auto_box_profile


class AutoBoxProfileTests(unittest.TestCase):
    def test_1920x1200_uses_16_10_chat_position(self):
        self.assertEqual("16:10", auto_box_profile(1920, 1200))
        self.assertEqual(PixelBox(left=0, top=534, width=634, height=192), auto_box_percent(1920, 1200))

    def test_16_9_keeps_standard_wide_position(self):
        self.assertEqual("wide", auto_box_profile(1920, 1080))
        self.assertEqual(508, auto_box_percent(1920, 1080).top)

    def test_existing_compact_and_ultrawide_profiles_are_unchanged(self):
        self.assertEqual("compact", auto_box_profile(1440, 1080))
        self.assertEqual("ultrawide", auto_box_profile(3440, 1392))


class RegionValidationTests(unittest.TestCase):
    def test_calibration_profiles_are_independent_per_display(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "droid_alerts.region.config_dir", return_value=Path(temp_dir)
        ):
            Calibration(
                mode="manual",
                ratios={"left": 0.1, "top": 0.2, "width": 0.3, "height": 0.4},
            ).save("display-a")
            Calibration(
                mode="manual",
                ratios={"left": 0.2, "top": 0.3, "width": 0.3, "height": 0.3},
            ).save("display-b")

            self.assertEqual(0.1, Calibration.load("display-a").ratios["left"])
            self.assertEqual(0.2, Calibration.load("display-b").ratios["left"])
            self.assertEqual("auto", Calibration.load("missing-display").mode)

    def test_validated_rescaled_manual_region_persists_new_signature(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "droid_alerts.region.config_dir", return_value=Path(temp_dir)
        ):
            calibration = Calibration(
                mode="manual",
                ratios={"left": 0.1, "top": 0.2, "width": 0.3, "height": 0.2},
                monitor_signature={"width": 1920, "height": 1080},
            )
            calibration.save("display")

            resolver = RegionResolver(2560, 1440, monitor_key="display")
            _box, source = resolver.resolve()
            self.assertEqual("manual(rescaled)", source)

            resolver.mark_validated()
            self.assertFalse(resolver.signature_changed)
            reloaded = RegionResolver(2560, 1440, monitor_key="display")
            self.assertEqual("manual", reloaded.resolve()[1])

    def test_failed_signature_save_remains_unvalidated(self):
        resolver = RegionResolver(2560, 1440)
        resolver.signature_changed = True
        with patch.object(resolver.calibration, "save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                resolver.mark_validated()
        self.assertTrue(resolver.signature_changed)


if __name__ == "__main__":
    unittest.main()
