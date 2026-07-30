from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from droid_alerts.belt.selector import RegionSelector
from droid_alerts.capture import MonitorInfo, PixelBox


class RegionSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selector_closes_capture_and_maps_display_pixels_to_source(self):
        capture = Mock()
        capture.grab.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        selected = Mock()
        selector = RegionSelector(
            None,
            MonitorInfo(0, 0, 1920, 1080),
            selected,
            capture=capture,
            display_monitor=MonitorInfo(0, 0, 960, 540),
        )
        selector._selection = QRect(10, 20, 300, 80)

        selector.save()

        capture.close.assert_called_once_with()
        selected.assert_called_once_with(PixelBox(20, 40, 600, 160))


if __name__ == "__main__":
    unittest.main()
