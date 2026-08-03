from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QWidget


TOPMOST_REFRESH_INTERVAL_MS = 500


def _raise_windows_overlay(widget: QWidget) -> bool:
    """Restore HWND_TOPMOST without activating the overlay."""
    try:
        hwnd = int(widget.winId())
        # HWND_TOPMOST plus SWP_NOACTIVATE keeps the game focused while repairing
        # z-order changes made by fullscreen/borderless applications.
        flags = 0x0001 | 0x0002 | 0x0010 | 0x0200 | 0x0400
        set_window_pos = ctypes.windll.user32.SetWindowPos
        set_window_pos.argtypes = (
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        )
        set_window_pos.restype = wintypes.BOOL
        return bool(
            set_window_pos(
                wintypes.HWND(hwnd),
                wintypes.HWND(-1),  # HWND_TOPMOST
                0,
                0,
                0,
                0,
                flags,
            )
        )
    except Exception:
        return False


def _raise_macos_overlay(widget: QWidget) -> bool:
    """Place a Qt overlay in normal and native-fullscreen macOS spaces."""
    try:
        import AppKit
        import objc

        view = objc.objc_object(c_void_p=int(widget.winId()))
        window = view.window()
        if window is None:
            return False
        behavior = int(window.collectionBehavior())
        behavior |= (
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        can_join_all_apps = getattr(
            AppKit, "NSWindowCollectionBehaviorCanJoinAllApplications", None
        )
        if can_join_all_apps is not None:
            behavior |= can_join_all_apps
        window.setCollectionBehavior_(behavior)
        window.setHidesOnDeactivate_(False)
        window.setLevel_(AppKit.NSScreenSaverWindowLevel)
        window.orderFrontRegardless()
        return True
    except Exception:
        return False


def restore_overlay_topmost(widget: QWidget) -> None:
    """Reassert an overlay's z-order without requesting keyboard focus."""
    if not widget.isVisible():
        return
    if sys.platform == "win32" and _raise_windows_overlay(widget):
        return
    if sys.platform == "darwin" and _raise_macos_overlay(widget):
        return
    widget.raise_()


class OverlayTopmostGuard(QObject):
    """Periodically repair topmost state lost during focus/fullscreen changes."""

    def __init__(self, widget: QWidget) -> None:
        super().__init__(widget)
        self._widget = widget
        self._timer = QTimer(self)
        self._timer.setInterval(TOPMOST_REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def refresh(self) -> None:
        restore_overlay_topmost(self._widget)

    def stop(self) -> None:
        self._timer.stop()


__all__ = [
    "OverlayTopmostGuard",
    "TOPMOST_REFRESH_INTERVAL_MS",
    "restore_overlay_topmost",
]
