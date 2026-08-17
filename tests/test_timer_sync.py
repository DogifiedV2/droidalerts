from __future__ import annotations

import sys
import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.timer_sync import DEFAULT_TIMER_SCHEDULES, SyncedTimerSchedule
from droid_alerts.alert_customization import normalize_timer_reminder_rules
from droid_alerts.timers import (
    BASE_HEIGHT,
    DISPLAY_TIMER_ORDER,
    EDIT_BAR_HEIGHT,
    MAX_SCALE,
    MIN_SCALE,
    TIMER_COLORS,
    TIMER_PERIOD_SECONDS,
    _edit_bar_row_bounds,
    next_timer_refresh_delay_ms,
)


def payload(server_time_ms: int) -> dict[str, object]:
    return {
        "serverTimeMs": server_time_ms,
        "scheduleVersion": 1,
        "refreshAfterSeconds": 600,
        "schedules": {
            kind: {"intervalSeconds": interval, "offsetSeconds": offset}
            for kind, (interval, offset) in DEFAULT_TIMER_SCHEDULES.items()
        },
        "nextSpawns": {},
    }


class SyncedTimerScheduleTests(unittest.TestCase):
    def test_current_spawn_timer_lineup_and_fallback_schedule(self):
        self.assertEqual(("galactic", "stellar", "mythic"), DISPLAY_TIMER_ORDER)
        self.assertEqual((30 * 60, 15 * 60), DEFAULT_TIMER_SCHEDULES["galactic"])
        self.assertEqual((60 * 60, 0), DEFAULT_TIMER_SCHEDULES["stellar"])
        self.assertEqual(30 * 60, TIMER_PERIOD_SECONDS["galactic"])
        self.assertEqual(60 * 60, TIMER_PERIOD_SECONDS["stellar"])
        self.assertEqual("#ffe14d", TIMER_COLORS["stellar"])

        schedule = SyncedTimerSchedule(wall_clock=lambda: 10.0)
        self.assertEqual(890, schedule.seconds_until_next("galactic"))
        self.assertEqual(3590, schedule.seconds_until_next("stellar"))
        self.assertEqual(3290, schedule.seconds_until_next("mythic"))

    def test_legacy_beskar_reminder_rules_move_to_stellar(self):
        rules = normalize_timer_reminder_rules(
            {"beskar": [300, 60], "galactic": [120], "mythic": []}
        )

        self.assertEqual(
            {"galactic": [120], "stellar": [300, 60], "mythic": []},
            rules,
        )

    def test_timer_controls_fit_and_refresh_just_after_each_second(self):
        for scale in (MIN_SCALE, 1.0, MAX_SCALE):
            card_top = int(BASE_HEIGHT * scale)
            first_y1, first_y2, reset_y1, reset_y2 = _edit_bar_row_bounds(
                card_top,
                scale,
            )
            window_bottom = card_top + int(EDIT_BAR_HEIGHT * scale)
            self.assertTrue(
                card_top
                <= first_y1
                < first_y2
                < reset_y1
                < reset_y2
                < window_bottom
            )

        self.assertEqual(758, next_timer_refresh_delay_ms(now_seconds=100.25))
        self.assertLessEqual(
            8,
            next_timer_refresh_delay_ms(now_seconds=100.999),
        )
        self.assertLessEqual(
            next_timer_refresh_delay_ms(now_seconds=100.999),
            10,
        )

    def test_server_time_corrects_a_bad_local_clock_and_uses_monotonic_elapsed_time(self):
        monotonic = [50.0]
        schedule = SyncedTimerSchedule(
            wall_clock=lambda: 1_000.0,
            monotonic_clock=lambda: monotonic[0],
        )
        refresh_after = schedule.apply_server_payload(
            payload(7_200_000),
            sent_wall=999.8,
            received_wall=1_000.2,
            received_monotonic=50.0,
        )

        self.assertEqual(600, refresh_after)
        self.assertTrue(schedule.synchronized)
        self.assertEqual(1, schedule.schedule_version)
        self.assertAlmostEqual(7_200.2, schedule.current_time_seconds(), places=6)
        self.assertEqual(900, schedule.seconds_until_next("beskar"))

        monotonic[0] += 30
        self.assertAlmostEqual(7_230.2, schedule.current_time_seconds(), places=6)
        self.assertEqual(870, schedule.seconds_until_next("beskar"))

    def test_server_can_change_spawn_anchors_without_an_app_update(self):
        custom = payload(7_200_000)
        custom["schedules"]["galactic"] = {
            "intervalSeconds": 3600,
            "offsetSeconds": 30 * 60,
        }
        schedule = SyncedTimerSchedule(monotonic_clock=lambda: 10.0)
        schedule.apply_server_payload(
            custom,
            sent_wall=100.0,
            received_wall=100.0,
            received_monotonic=10.0,
        )
        self.assertEqual(1800, schedule.seconds_until_next("galactic"))

    def test_invalid_schedule_is_rejected_without_replacing_the_fallback(self):
        invalid = payload(7_200_000)
        invalid["schedules"]["beskar"] = {
            "intervalSeconds": 0,
            "offsetSeconds": 0,
        }
        schedule = SyncedTimerSchedule(wall_clock=lambda: 7_200.0)
        with self.assertRaises(ValueError):
            schedule.apply_server_payload(
                invalid,
                sent_wall=100.0,
                received_wall=100.0,
            )
        self.assertFalse(schedule.synchronized)
        self.assertEqual(900, schedule.seconds_until_next("beskar"))


if __name__ == "__main__":
    unittest.main()
