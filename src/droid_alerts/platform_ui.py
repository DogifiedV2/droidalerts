from __future__ import annotations

import ctypes
import sys


def set_dpi_awareness() -> None:
    """Enable physical-pixel coordinates before the first Windows UI opens."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
