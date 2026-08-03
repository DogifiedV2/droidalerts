from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts import overlay_window
from droid_alerts.ui.single_instance import SingleInstanceGuard
from PySide6.QtWidgets import QApplication


class OverlayWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_hidden_overlay_is_not_raised(self) -> None:
        widget = Mock()
        widget.isVisible.return_value = False

        overlay_window.restore_overlay_topmost(widget)

        widget.raise_.assert_not_called()

    def test_windows_native_refresh_does_not_fall_back_to_qt_raise(self) -> None:
        widget = Mock()
        widget.isVisible.return_value = True
        with (
            patch.object(overlay_window.sys, "platform", "win32"),
            patch.object(overlay_window, "_raise_windows_overlay", return_value=True),
        ):
            overlay_window.restore_overlay_topmost(widget)

        widget.raise_.assert_not_called()

    def test_failed_native_refresh_falls_back_to_nonactivating_qt_raise(self) -> None:
        widget = Mock()
        widget.isVisible.return_value = True
        with (
            patch.object(overlay_window.sys, "platform", "win32"),
            patch.object(overlay_window, "_raise_windows_overlay", return_value=False),
        ):
            overlay_window.restore_overlay_topmost(widget)

        widget.raise_.assert_called_once_with()
        widget.activateWindow.assert_not_called()

    def test_second_gui_instance_notifies_the_primary(self) -> None:
        name = f"DroidAlerts.Test.{uuid4().hex}"
        primary = SingleInstanceGuard(name)
        secondary = SingleInstanceGuard(name)
        activated = Mock()
        primary.connect_window_activation(activated)
        try:
            self.assertTrue(primary.acquire())
            self.app.processEvents()
            self.assertFalse(secondary.acquire())
            socket = Mock()
            socket.readAll.return_value = b"activate"
            primary._read(socket)
            activated.assert_called_once_with()
        finally:
            secondary.close()
            primary.close()


if __name__ == "__main__":
    unittest.main()
