from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_THEME_KEY = "signal_dark"


@dataclass(frozen=True)
class AppTheme:
    key: str
    label: str
    colors: dict[str, str]
    sidebar_bg: str
    sidebar_fg: str
    sidebar_muted: str
    sidebar_hover: str
    sidebar_active: str
    muted_fg: str
    subtle_bg: str


SIGNAL_DARK = AppTheme(
    key=DEFAULT_THEME_KEY,
    label="Signal dark",
    colors={
        "primary": "#39c6d8",
        "secondary": "#667483",
        "success": "#36c98f",
        "info": "#5aa9ff",
        "warning": "#f4b942",
        "danger": "#ef6672",
        "light": "#dce7ef",
        "dark": "#080d13",
        "bg": "#0e151d",
        "fg": "#e9f1f7",
        "selectbg": "#184b56",
        "selectfg": "#f7fdff",
        "border": "#243140",
        "inputfg": "#edf5fa",
        "inputbg": "#182330",
        "active": "#121b25",
    },
    sidebar_bg="#080d13",
    sidebar_fg="#e9f1f7",
    sidebar_muted="#8ba0ae",
    sidebar_hover="#121b25",
    sidebar_active="#183a43",
    muted_fg="#8ba0ae",
    subtle_bg="#121b25",
)

APP_THEMES: tuple[AppTheme, ...] = (SIGNAL_DARK,)


def normalize_theme_key(_value: Any) -> str:
    """Map old theme names to the current theme."""
    return DEFAULT_THEME_KEY


def theme_for(_value: Any) -> AppTheme:
    return SIGNAL_DARK


def theme_label(_value: Any) -> str:
    return SIGNAL_DARK.label


def theme_labels() -> tuple[str, ...]:
    return (SIGNAL_DARK.label,)


def register_app_themes(_style: Any) -> None:
    """Kept for callers from the old Tk interface."""


def apply_app_theme(
    _style: Any,
    value: Any,
    *,
    bootstrap: bool = False,
    font_family: str = "Segoe UI",
) -> AppTheme:
    del bootstrap, font_family
    return theme_for(value)


__all__ = [
    "APP_THEMES",
    "AppTheme",
    "DEFAULT_THEME_KEY",
    "SIGNAL_DARK",
    "apply_app_theme",
    "normalize_theme_key",
    "register_app_themes",
    "theme_for",
    "theme_label",
    "theme_labels",
]
