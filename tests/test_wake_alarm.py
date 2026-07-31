from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.alerts import WAKE_ALARM_FILE, WakeAlarm, _alert_wav
from droid_alerts.config import AppConfig


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


class AlertSoundSelectionTests(unittest.TestCase):
    def test_system_beeps_do_not_fall_back_to_an_added_wav(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sounds = Path(folder)
            sounds.joinpath("custom.wav").write_bytes(b"RIFF-test")
            with (
                patch("droid_alerts.alerts.user_sounds_dir", return_value=sounds),
                patch("droid_alerts.alerts.sounds_dir", return_value=sounds),
            ):
                self.assertIsNone(_alert_wav(""))
                self.assertIsNone(_alert_wav("System beeps"))

    def test_missing_selected_sound_does_not_play_a_different_wav(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sounds = Path(folder)
            sounds.joinpath("other.wav").write_bytes(b"RIFF-test")
            with (
                patch("droid_alerts.alerts.user_sounds_dir", return_value=sounds),
                patch("droid_alerts.alerts.sounds_dir", return_value=sounds),
            ):
                self.assertIsNone(_alert_wav("missing.wav"))



if __name__ == "__main__":
    unittest.main()
