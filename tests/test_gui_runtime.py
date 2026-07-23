from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from queue import SimpleQueue
from types import SimpleNamespace
from unittest.mock import Mock, patch

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.classifier import Detection
from droid_alerts.config import AppConfig
from droid_alerts.gui import DroidAlertsApp


def detection() -> Detection:
    return Detection("Beskar", "Mythic", (0, 0, 10, 10), 1, 1, 1, 1, "test", 1)


class GuiQueueTests(unittest.TestCase):
    def make_app(self) -> DroidAlertsApp:
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.root = Mock()
        app._ui_queue = SimpleQueue()
        app._ui_queue_closed = False
        app._ui_poll_after_id = None
        return app

    def test_worker_post_only_enqueues_without_calling_tk(self):
        app = self.make_app()
        called = []
        thread = threading.Thread(target=lambda: app._post_to_ui(lambda: called.append(1)))
        thread.start()
        thread.join()
        app.root.after.assert_not_called()
        self.assertEqual([], called)
        app._drain_ui_queue()
        self.assertEqual([1], called)
        app.root.after.assert_called_once()

    def test_drain_is_fifo_and_isolates_callback_failure(self):
        app = self.make_app()
        called = []
        app._post_to_ui(lambda: called.append("first"))
        app._post_to_ui(lambda: (_ for _ in ()).throw(RuntimeError("bad callback")))
        app._post_to_ui(lambda: called.append("last"))
        app._drain_ui_queue()
        self.assertEqual(["first", "last"], called)

    def test_closed_queue_drops_late_callbacks(self):
        app = self.make_app()
        app._ui_queue_closed = True
        app._post_to_ui(lambda: self.fail("late callback ran"))
        app._drain_ui_queue()
        app.root.after.assert_not_called()

    def test_watcher_popup_is_queued_before_tk_popup_runs(self):
        app = self.make_app()
        app.wake_alarm = SimpleNamespace(active=False)
        app._current_monitor_info = Mock(return_value=None)
        app._watcher_finished = Mock()
        config = AppConfig(popup_enabled=True)

        def fake_watch(**kwargs):
            kwargs["popup_callback"](detection())

        with patch("droid_alerts.gui.run_watch", side_effect=fake_watch), patch(
            "droid_alerts.gui.show_popup"
        ) as popup:
            app._watch_thread(config, threading.Event())
            popup.assert_not_called()
            app._drain_ui_queue()
            popup.assert_called_once()

    def test_gui_alert_sound_starts_without_waiting_for_beep(self):
        app = self.make_app()
        app.wake_alarm = SimpleNamespace(active=False)
        app._maybe_start_wake_alarm = Mock()
        app.channel_status_vars = {"Sound": Mock()}
        app.detail_var = Mock()
        config = AppConfig(sound_enabled=True, popup_enabled=False)

        def slow_notify(_detection):
            time.sleep(0.44)

        with patch("droid_alerts.gui.AlertPolicy") as policy:
            policy.return_value.notify.side_effect = slow_notify
            started = time.perf_counter()
            app._dispatch_gui_alert(
                config,
                detection(),
                delivery_source="limited_deal",
                delivery_rarity="Mythic",
                sound_error_prefix="sound failed",
                popup_error_prefix=None,
                thread_name_prefix="Test",
            )
            elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.15)

    def test_gui_delivery_keeps_the_original_single_attempt_behavior(self):
        app = self.make_app()
        delivery = Mock(label="Discord")

        with (
            patch("droid_alerts.gui.execute_alert_delivery") as execute,
            patch("droid_alerts.gui.build_delivery_event", return_value={"success": False}),
            patch("droid_alerts.gui.persist_delivery_event"),
        ):
            app._deliver_gui_alert(
                delivery,
                detection(),
                "limited_deal",
                "Mythic",
                {},
            )

        execute.assert_called_once()
        self.assertEqual(1, execute.call_args.kwargs["max_attempts"])


class SchedulerRoot:
    def __init__(self):
        self.now = 0
        self._next_id = 0
        self.callbacks: dict[str, tuple[int, object]] = {}

    def after(self, delay_ms, callback):
        self._next_id += 1
        callback_id = f"after-{self._next_id}"
        self.callbacks[callback_id] = (self.now + int(delay_ms), callback)
        return callback_id

    def after_cancel(self, callback_id):
        self.callbacks.pop(callback_id, None)

    def advance(self, delay_ms):
        target = self.now + int(delay_ms)
        while True:
            due = [
                (when, callback_id, callback)
                for callback_id, (when, callback) in self.callbacks.items()
                if when <= target
            ]
            if not due:
                break
            when, callback_id, callback = min(due)
            self.now = when
            self.callbacks.pop(callback_id, None)
            callback()
        self.now = target


class ValueVar:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value


class AutosaveRegressionTests(unittest.TestCase):
    def test_normalized_belt_rates_do_not_reschedule_autosave(self):
        interpreter = tk.Tcl()
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.root = SchedulerRoot()
        app._autosave_after_id = None
        app._autosave_ready = False
        app._loading_settings = False
        app._shutting_down = False
        app.setting_vars = {
            "belt_idle_scan_fps": tk.IntVar(interpreter, value=30),
            "belt_active_scan_fps": tk.IntVar(interpreter, value=2),
        }
        app.alert_vars = {}
        app.limited_deal_priority_vars = {}
        app.detail_var = ValueVar()
        app.config = AppConfig()
        app.droid_timers = None
        app._last_cleanup_at = 0.0
        app._evaluate_current_limited_deal = Mock()
        app._refresh_storage_status = Mock()
        app.refresh_channel_statuses = Mock()

        defaults = AppConfig()

        def value(key):
            if key in app.setting_vars:
                return app.setting_vars[key].get()
            return getattr(defaults, key)

        app._value = value
        DroidAlertsApp._wire_auto_save(app)

        with (
            patch("droid_alerts.gui.load_config", return_value=AppConfig()),
            patch("droid_alerts.gui.save_config") as save_config,
        ):
            app.setting_vars["belt_idle_scan_fps"].set(31)
            app.root.advance(600)
            self.assertEqual(2, app.setting_vars["belt_idle_scan_fps"].get())
            self.assertEqual(2, app.setting_vars["belt_active_scan_fps"].get())
            self.assertIsNone(app._autosave_after_id)
            self.assertEqual(1, save_config.call_count)

            app.root.advance(1200)

        self.assertEqual(1, save_config.call_count)
        self.assertEqual({}, app.root.callbacks)


if __name__ == "__main__":
    unittest.main()
