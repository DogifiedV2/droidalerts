"""Compatibility imports for code that still uses ``droid_alerts.gui``."""

from __future__ import annotations

from .capture import MonitorDescriptor, format_tk_geometry
from .ui import run_gui
from .ui.app_controller import AppController
from .ui.constants import (
    ALERT_COMBOS,
    DISCORD_COMMUNITY_URL,
    STATS_URL,
    TRACKER_URL,
    WIKI_URL,
)
from .ui.history_controller import read_last_lines


WAKE_ALARM_MAX_MS = 40_000
REBIRTH_ALERT_TOOLTIP = (
    "Receive a notification when a droid you need for rebirth spawns"
)
SCRAP_ALERT_TOOLTIP = (
    "Notifies you when your credits stop increasing. Useful for when afk."
)

# Keep the old import name for integrations.
DroidAlertsApp = AppController


def fit_window_size(
    width: int,
    height: int,
    screen_width: int,
    screen_height: int,
    *,
    horizontal_margin: int,
    vertical_margin: int,
) -> tuple[int, int]:
    usable_width = max(1, int(screen_width) - horizontal_margin)
    usable_height = max(1, int(screen_height) - vertical_margin)
    return min(int(width), usable_width), min(int(height), usable_height)


def centered_window_geometry(
    width: int,
    height: int,
    *,
    parent_x: int,
    parent_y: int,
    parent_width: int,
    parent_height: int,
) -> str:
    x = int(parent_x) + (int(parent_width) - int(width)) // 2
    y = int(parent_y) + (int(parent_height) - int(height)) // 2
    return format_tk_geometry(width=int(width), height=int(height), x=x, y=y)


def clamp_dialog_position(
    x: int,
    y: int,
    width: int,
    height: int,
    monitors: list[MonitorDescriptor],
) -> tuple[int, int]:
    if not monitors:
        return int(x), int(y)
    center_x = x + width / 2
    center_y = y + height / 2

    def distance_squared(monitor: MonitorDescriptor) -> float:
        right = monitor.left + monitor.width
        bottom = monitor.top + monitor.height
        dx = max(monitor.left - center_x, 0.0, center_x - right)
        dy = max(monitor.top - center_y, 0.0, center_y - bottom)
        return dx * dx + dy * dy

    monitor = min(monitors, key=distance_squared)
    max_x = monitor.left + max(0, monitor.width - width)
    max_y = monitor.top + max(0, monitor.height - height)
    return (
        max(monitor.left, min(int(x), max_x)),
        max(monitor.top, min(int(y), max_y)),
    )


__all__ = [
    "ALERT_COMBOS",
    "DISCORD_COMMUNITY_URL",
    "DroidAlertsApp",
    "REBIRTH_ALERT_TOOLTIP",
    "SCRAP_ALERT_TOOLTIP",
    "STATS_URL",
    "TRACKER_URL",
    "WAKE_ALARM_MAX_MS",
    "WIKI_URL",
    "centered_window_geometry",
    "clamp_dialog_position",
    "fit_window_size",
    "read_last_lines",
    "run_gui",
]
