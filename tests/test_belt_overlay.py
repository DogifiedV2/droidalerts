from __future__ import annotations

import ctypes
import sys
import tkinter as tk
import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.overlay import BeltOverlay
from droid_alerts.capture import MonitorInfo, PixelBox


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
