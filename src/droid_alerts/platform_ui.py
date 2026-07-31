from __future__ import annotations

import ctypes
import sys


WINDOWS_APP_USER_MODEL_ID = "DroidAlerts.Desktop"


def set_windows_app_identity() -> None:
    """Give source launches their own Windows taskbar identity."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_USER_MODEL_ID
        )
    except Exception:
        # A missing shell API should not prevent the application from opening.
        pass


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
