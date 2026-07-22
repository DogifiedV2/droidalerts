from __future__ import annotations

import sys
import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.capture import PixelBox
from droid_alerts.region import auto_box_percent, auto_box_profile


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


if __name__ == "__main__":
    unittest.main()
