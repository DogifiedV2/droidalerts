from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from PySide6.QtWidgets import QApplication

from droid_alerts.config import AppConfig
from droid_alerts import scrap_overlay


class ScrapIncomeOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        scrap_overlay.hide_scrap_income_overlay()
        self.app.processEvents()

    def test_overlay_stays_visible_and_keeps_the_last_available_rate(self):
        config = AppConfig(scrap_income_overlay_enabled=True)

        scrap_overlay.update_scrap_income_overlay(config, None)
        overlay = scrap_overlay._ACTIVE_OVERLAY
        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.isVisible())
        self.assertEqual("--", overlay._rate_text)

        scrap_overlay.update_scrap_income_overlay(config, "1.4T")
        self.assertTrue(overlay.isVisible())
        self.assertEqual("1.4T", overlay._rate_text)

        scrap_overlay.update_scrap_income_overlay(config, None)
        self.assertTrue(overlay.isVisible())
        self.assertEqual("1.4T", overlay._rate_text)

    def test_position_action_opens_a_preview_in_edit_mode(self):
        config = AppConfig(scrap_income_overlay_enabled=True)

        overlay = scrap_overlay.adjust_scrap_income_overlay(config)

        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.edit_mode)
        self.assertEqual("1.4T", overlay._rate_text)


if __name__ == "__main__":
    unittest.main()
