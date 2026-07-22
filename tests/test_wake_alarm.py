from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.alerts import WAKE_ALARM_FILE, WakeAlarm
from droid_alerts.config import AppConfig
from droid_alerts.gui import WAKE_ALARM_MAX_MS, DroidAlertsApp


class WakeAlarmConfigTests(unittest.TestCase):
    def test_defaults_target_only_beskar_and_galactic_mythic(self) -> None:
        config = AppConfig(wake_alarm_enabled=True)

        self.assertTrue(config.wake_alarm_matches("Beskar", "Mythic"))
        self.assertTrue(config.wake_alarm_matches("Galactic", "Mythic"))
        self.assertFalse(config.wake_alarm_matches("Rainbow", "Mythic"))
        self.assertFalse(config.wake_alarm_matches("Beskar", "Legendary"))

    def test_each_target_can_be_disabled_independently(self) -> None:
        config = AppConfig(
            wake_alarm_enabled=True,
            wake_alarm_beskar_mythic=False,
            wake_alarm_galactic_mythic=True,
        )

        self.assertFalse(config.wake_alarm_matches("Beskar", "Mythic"))
        self.assertTrue(config.wake_alarm_matches("Galactic", "Mythic"))

    def test_settings_round_trip(self) -> None:
        restored = AppConfig.from_dict(
            AppConfig(
                wake_alarm_enabled=True,
                wake_alarm_beskar_mythic=True,
                wake_alarm_galactic_mythic=False,
            ).to_dict()
        )

        self.assertTrue(restored.wake_alarm_enabled)
        self.assertTrue(restored.wake_alarm_beskar_mythic)
        self.assertFalse(restored.wake_alarm_galactic_mythic)


class WakeAlarmPlaybackTests(unittest.TestCase):
    def test_windows_alarm_loops_and_stale_test_cannot_stop_new_alarm(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            wav_path = Path(folder) / WAKE_ALARM_FILE
            wav_path.write_bytes(b"RIFF-test")
            play_sound = Mock()
            winsound = SimpleNamespace(
                PlaySound=play_sound,
                SND_FILENAME=1,
                SND_ASYNC=2,
                SND_LOOP=4,
            )
            alarm = WakeAlarm(wav_path)

            with (
                patch("droid_alerts.alerts.sys.platform", "win32"),
                patch.dict(sys.modules, {"winsound": winsound}),
            ):
                test_generation = alarm.start()
                real_generation = alarm.start()

                self.assertNotEqual(test_generation, real_generation)
                self.assertFalse(alarm.stop(test_generation))
                self.assertTrue(alarm.active)
                self.assertTrue(alarm.stop(real_generation))
                self.assertFalse(alarm.active)

            play_sound.assert_any_call(str(wav_path), 1 | 2 | 4)
            play_sound.assert_any_call(None, 0)

    def test_real_alert_upgrades_an_in_progress_three_second_test(self) -> None:
        app = object.__new__(DroidAlertsApp)
        app.root = Mock()
        app.wake_alarm = SimpleNamespace(active=True, start=Mock(return_value=2))
        app._wake_alarm_is_test = True
        app._wake_alarm_test_after_id = "after#test"
        app._wake_alarm_auto_stop_after_id = None
        app.wake_alarm_status_var = Mock()
        app.detail_var = Mock()
        app._show_wake_alarm_stop_dialog = Mock()

        DroidAlertsApp._maybe_start_wake_alarm(
            app,
            AppConfig(wake_alarm_enabled=True),
            "Galactic",
            "Mythic",
        )

        app.root.after_cancel.assert_called_once_with("after#test")
        self.assertEqual(app.root.after.call_args.args[0], WAKE_ALARM_MAX_MS)
        app.wake_alarm.start.assert_called_once_with()
        app._show_wake_alarm_stop_dialog.assert_called_once_with("Galactic Mythic")
        self.assertFalse(app._wake_alarm_is_test)

    def test_real_alarm_auto_stops_at_40_seconds(self) -> None:
        app = object.__new__(DroidAlertsApp)
        app.wake_alarm = SimpleNamespace(stop=Mock(return_value=True))
        app._wake_alarm_auto_stop_after_id = "after#alarm"
        app._wake_alarm_is_test = False
        app._close_wake_alarm_dialog = Mock()
        app.wake_alarm_status_var = Mock()
        app.detail_var = Mock()

        DroidAlertsApp._finish_wake_alarm_alert(app, 7)

        app.wake_alarm.stop.assert_called_once_with(7)
        self.assertIsNone(app._wake_alarm_auto_stop_after_id)
        app._close_wake_alarm_dialog.assert_called_once_with()
        app.wake_alarm_status_var.set.assert_called_once_with(
            "Alarm stopped after 40 seconds"
        )


if __name__ == "__main__":
    unittest.main()
