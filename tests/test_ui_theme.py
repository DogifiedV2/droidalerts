from __future__ import annotations

import sys
import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.config import AppConfig
from droid_alerts.ui_theme import (
    APP_THEMES,
    DEFAULT_THEME_KEY,
    normalize_theme_key,
    theme_label,
)


def relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]

    def linearize(channel: float) -> float:
        if channel <= 0.04045:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linearize(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted(
        (relative_luminance(first), relative_luminance(second)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


class UiThemeTests(unittest.TestCase):
    def test_historical_theme_keys_migrate_to_signal_dark(self):
        self.assertEqual(DEFAULT_THEME_KEY, normalize_theme_key("Midnight"))
        self.assertEqual(DEFAULT_THEME_KEY, normalize_theme_key("Daylight"))
        self.assertEqual(DEFAULT_THEME_KEY, normalize_theme_key("High Contrast"))
        self.assertEqual(DEFAULT_THEME_KEY, normalize_theme_key("not-a-theme"))
        self.assertEqual("Signal dark", theme_label("not-a-theme"))

    def test_old_theme_value_round_trips_as_signal_dark(self):
        config = AppConfig.from_dict({"ui_theme": "Daylight"})

        self.assertEqual(DEFAULT_THEME_KEY, config.ui_theme)
        self.assertEqual(DEFAULT_THEME_KEY, config.to_dict()["ui_theme"])
        self.assertEqual(
            DEFAULT_THEME_KEY,
            AppConfig.from_dict(config.to_dict()).ui_theme,
        )

    def test_primary_text_meets_normal_text_contrast(self):
        for theme in APP_THEMES:
            with self.subTest(theme=theme.key):
                self.assertGreaterEqual(
                    contrast_ratio(theme.colors["fg"], theme.colors["bg"]),
                    4.5,
                )
                self.assertGreaterEqual(
                    contrast_ratio(theme.sidebar_fg, theme.sidebar_bg),
                    4.5,
                )

if __name__ == "__main__":
    unittest.main()
