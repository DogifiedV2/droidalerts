from __future__ import annotations

import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.config import AppConfig
from droid_alerts.settings_service import SettingsValidationError, build_settings_update


def values(**overrides):
    result = {
        "monitor_index": 0,
        "capture_interval_seconds": 0,
        "rebirth_scan_interval_seconds": 99,
        "dedupe_seconds": -1,
        "alert_cooldown_seconds": -2,
        "validation_failures_before_calibration_prompt": 0,
        "popup_seconds": 0,
        "popup_scale": 9,
        "popup_opacity": 0,
        "retention_days": -5,
        "max_storage_mb": -6,
        "timer_reminder_seconds": 0,
        "timer_offset_seconds": 9999,
        "belt_idle_scan_fps": 20,
        "belt_active_scan_fps": 3,
        "popup_position": "not real",
        "ui_theme": "not real",
        "ntfy_server_url": "",
        "ntfy_topic": "",
        "ntfy_priority": "",
        "ntfy_tags": "",
        "phone_sound": "",
        "update_repo": "",
        "sound_file": "",
    }
    result.update(overrides)
    return result


class SettingsServiceTests(unittest.TestCase):
    def test_clamps_numeric_values_and_normalizes_related_settings(self):
        update = build_settings_update(AppConfig(), AppConfig(), values(), [], [])
        config = update.config
        self.assertEqual(1, config.monitor_index)
        self.assertEqual(0.05, config.capture_interval_seconds)
        self.assertEqual(30.0, config.rebirth_scan_interval_seconds)
        self.assertEqual(0.0, config.dedupe_seconds)
        self.assertEqual(0.0, config.alert_cooldown_seconds)
        self.assertEqual(1, config.validation_failures_before_calibration_prompt)
        self.assertEqual(0.5, config.popup_seconds)
        self.assertEqual(1.5, config.popup_scale)
        self.assertEqual(0.55, config.popup_opacity)
        self.assertEqual((0, 0, 1, 3600), (
            config.retention_days, config.max_storage_mb,
            config.timer_reminder_seconds, config.timer_offset_seconds,
        ))
        self.assertLessEqual(config.belt_idle_scan_fps, config.belt_active_scan_fps)
        self.assertEqual("top_center", config.popup_position)
        self.assertEqual("signal_dark", config.ui_theme)

    def test_preserves_runtime_capture_metadata(self):
        persisted = AppConfig(capture_source="monitor", monitor_index=1)
        runtime = AppConfig(
            capture_source="device",
            capture_device_name="Capture Card",
            capture_device_path="device-1",
            capture_device_vid=12,
            capture_device_pid=34,
            capture_device_backend=700,
        )
        config = build_settings_update(persisted, runtime, values(), [], []).config
        self.assertEqual("device", config.capture_source)
        self.assertEqual("Capture Card", config.capture_device_name)
        self.assertEqual("device-1", config.capture_device_path)
        self.assertEqual((12, 34, 700), (
            config.capture_device_vid, config.capture_device_pid, config.capture_device_backend,
        ))

    def test_reports_enabled_unconfigured_channels(self):
        update = build_settings_update(
            AppConfig(), AppConfig(),
            values(ntfy_enabled=True, discord_enabled=True, phone_alerts_enabled=True),
            [], [],
            configured_channels={"discord": False, "pushover": True},
        )
        self.assertEqual(("ntfy", "Discord"), update.unconfigured_channels)

    def test_rejects_nonempty_but_invalid_ntfy_settings(self):
        update = build_settings_update(
            AppConfig(),
            AppConfig(),
            values(
                ntfy_enabled=True,
                ntfy_server_url="not-a-url",
                ntfy_topic="spaces are invalid",
            ),
            [],
            [],
        )

        self.assertEqual(("ntfy",), update.unconfigured_channels)

    def test_invalid_numeric_value_raises_typed_error_without_mutating_input(self):
        persisted = AppConfig(monitor_index=7)
        with self.assertRaises(SettingsValidationError):
            build_settings_update(
                persisted, AppConfig(), values(capture_interval_seconds="bad"), [], []
            )
        self.assertEqual(7, persisted.monitor_index)


if __name__ == "__main__":
    unittest.main()
