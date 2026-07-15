from __future__ import annotations

import ctypes
import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt import macos_overlay
from droid_alerts.belt import overlay as overlay_module
from droid_alerts.belt.overlay import BeltOverlay
from droid_alerts.belt.selector import RegionSelector
from droid_alerts.capture import MonitorInfo, PixelBox


class FakeBindTarget:
    def __init__(self):
        self.bindings = []

    def bind(self, sequence, callback, add=None):
        self.bindings.append((sequence, callback, add))


class RegionSelectorInputTests(unittest.TestCase):
    def test_shortcuts_are_bound_to_window_and_canvas(self):
        selector = RegionSelector.__new__(RegionSelector)
        selector.window = FakeBindTarget()
        selector.canvas = FakeBindTarget()

        selector._bind_shortcuts()

        expected = {"<Return>", "<KP_Enter>", "<KeyPress-s>", "<Escape>"}
        for target in (selector.window, selector.canvas):
            self.assertEqual(expected, {binding[0] for binding in target.bindings})
            self.assertTrue(all(binding[2] == "+" for binding in target.bindings))

    def test_save_finishes_and_reports_selected_box(self):
        selector = RegionSelector.__new__(RegionSelector)
        selector.current = (10, 20, 300, 80)
        selector._finished = False
        selector.window = Mock()
        selector.on_selected = Mock()

        result = selector._save()

        self.assertEqual("break", result)
        self.assertTrue(selector._finished)
        selector.window.destroy.assert_called_once_with()
        selector.on_selected.assert_called_once_with(PixelBox(10, 20, 300, 80))


class MacOSOverlayBehaviorTests(unittest.TestCase):
    def test_native_panel_uses_nonactivating_fullscreen_overlay_styles(self):
        appkit = Mock()
        appkit.NSWindowStyleMaskBorderless = 0
        appkit.NSWindowStyleMaskNonactivatingPanel = 128
        appkit.NSBackingStoreBuffered = 2
        appkit.NSScreenSaverWindowLevel = 1000
        appkit.NSWindowCollectionBehaviorCanJoinAllSpaces = 1
        appkit.NSWindowCollectionBehaviorStationary = 16
        appkit.NSWindowCollectionBehaviorFullScreenAuxiliary = 256
        appkit.NSWindowCollectionBehaviorCanJoinAllApplications = 262144
        appkit.NSMakeRect.side_effect = lambda x, y, width, height: (x, y, width, height)
        panel = Mock()
        appkit.NSPanel.alloc.return_value.initWithContentRect_styleMask_backing_defer_.return_value = panel
        overlay = macos_overlay._NativeMacOSOverlay.__new__(
            macos_overlay._NativeMacOSOverlay
        )
        overlay.AppKit = appkit
        overlay.primary_height = 1080.0
        overlay.behavior = macos_overlay._window_behavior(appkit)

        configured = overlay._panel(10, 20, 300, 40, "#00e5ff")

        self.assertIs(panel, configured)
        initializer = (
            appkit.NSPanel.alloc.return_value.initWithContentRect_styleMask_backing_defer_
        )
        initializer.assert_called_once_with(
            (10.0, 1020.0, 300.0, 40.0),
            128,
            2,
            False,
        )
        panel.setCollectionBehavior_.assert_called_once_with(262144 | 256 | 16 | 1)
        panel.setLevel_.assert_called_once_with(1000)
        panel.setIgnoresMouseEvents_.assert_called_once_with(True)
        panel.setHidesOnDeactivate_.assert_called_once_with(False)
        panel.orderFrontRegardless.assert_called_once_with()

    def test_belt_overlay_routes_macos_without_creating_tk_windows(self):
        native = Mock()
        monitor = MonitorInfo(left=0, top=0, width=1920, height=1080, index=1)
        region = PixelBox(100, 200, 800, 260)
        tracks = [{"id": 1, "name": "ARG", "box": [0, 0, 100, 100]}]

        with (
            patch.object(overlay_module.sys, "platform", "darwin"),
            patch.object(
                overlay_module, "MacOSOverlayController", return_value=native
            ),
            patch.object(overlay_module.tk, "Toplevel") as toplevel,
        ):
            overlay = BeltOverlay(Mock())
            overlay.configure(monitor, region)
            overlay.update_tracks(tracks)

        native.configure.assert_called_once_with(monitor, region)
        native.update_tracks.assert_called_once_with(tracks)
        toplevel.assert_not_called()


class CrossPlatformOverlayRoutingTests(unittest.TestCase):
    def test_windows_keeps_existing_tk_overlay_path(self):
        root = Mock()
        root.after.return_value = "refresh-id"
        monitor = MonitorInfo(left=0, top=0, width=1920, height=1080, index=1)
        region = PixelBox(100, 200, 800, 260)

        with (
            patch.object(overlay_module.sys, "platform", "win32"),
            patch.object(
                overlay_module.tk, "Toplevel", side_effect=lambda _root: Mock()
            ) as toplevel,
            patch.object(
                overlay_module.tk,
                "Label",
                side_effect=lambda *_args, **_kwargs: Mock(),
            ),
            patch.object(overlay_module, "_configure_windows_overlay") as configure_windows,
        ):
            overlay = BeltOverlay(root)
            overlay.configure(monitor, region)

        self.assertIsNone(overlay._macos)
        self.assertEqual(4 + 16, toplevel.call_count)
        self.assertEqual(4 + 16, configure_windows.call_count)


@unittest.skipUnless(sys.platform == "win32", "Windows overlay style test")
class WindowsOverlayTests(unittest.TestCase):
    def test_overlay_styles_real_toplevel_and_initializes_full_opacity(self):
        root = tk.Tk()
        root.withdraw()
        overlay = BeltOverlay(root)
        try:
            overlay.configure(
                MonitorInfo(left=0, top=0, width=1920, height=1080, index=1),
                PixelBox(-10_000, -10_000, 200, 100),
            )
            root.update()
            window = overlay._border[0]
            user32 = ctypes.windll.user32
            child = window.winfo_id()
            wrapper = user32.GetParent(child) or child
            style = user32.GetWindowLongW(wrapper, -20) & 0xFFFFFFFF

            self.assertNotEqual(child, wrapper)
            self.assertTrue(style & 0x00080000)  # WS_EX_LAYERED
            self.assertTrue(style & 0x00000020)  # WS_EX_TRANSPARENT
            self.assertTrue(style & 0x08000000)  # WS_EX_NOACTIVATE

            color_key = ctypes.c_uint()
            alpha = ctypes.c_ubyte()
            flags = ctypes.c_uint()
            configured = user32.GetLayeredWindowAttributes(
                wrapper,
                ctypes.byref(color_key),
                ctypes.byref(alpha),
                ctypes.byref(flags),
            )
            self.assertTrue(configured)
            self.assertEqual(255, alpha.value)
            self.assertTrue(flags.value & 0x00000002)  # LWA_ALPHA
        finally:
            overlay.close()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
