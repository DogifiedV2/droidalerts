from __future__ import annotations

import sys
from typing import Any


def configure_macos_overlay(window: Any) -> Any | None:
    """Make a Tk window a click-through, non-activating fullscreen companion."""
    if sys.platform != "darwin":
        return None
    try:
        from AppKit import (
            NSApp,
            NSStatusWindowLevel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
        )

        window.update_idletasks()
        title = window.title()
        native = next((item for item in NSApp.windows() if item.title() == title), None)
        if native is None:
            return None
        behavior = (
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        native.setCollectionBehavior_(behavior)
        native.setLevel_(NSStatusWindowLevel)
        native.setIgnoresMouseEvents_(True)
        native.setHidesOnDeactivate_(False)
        native.setExcludedFromWindowsMenu_(True)
        native.orderFrontRegardless()
        return native
    except Exception:
        # Tk's normal topmost behavior remains the fallback when PyObjC is not
        # installed or a future macOS release changes the native window bridge.
        return None


def refresh_macos_overlay(native: Any | None) -> None:
    """Bring a configured overlay forward without activating Belt Tracker."""
    if native is None:
        return
    try:
        native.orderFrontRegardless()
    except Exception:
        pass
