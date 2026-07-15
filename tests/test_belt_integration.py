from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.events import log_track_event
from droid_alerts.belt.region import RelativeRegion, load_region, save_region
from droid_alerts.capture import MonitorDescriptor, MonitorInfo, PixelBox
from droid_alerts.config import AppConfig
from droid_alerts.gui import DroidAlertsApp
from droid_alerts.notifications import alert_title, event_text
from droid_alerts.classifier import Detection
from droid_alerts.popup import _title_segments


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeButton:
    def __init__(self):
        self.options = {}

    def configure(self, **values):
        self.options.update(values)


class AliveProcess:
    @staticmethod
    def is_alive():
        return True


class FakeOverlay:
    def __init__(self):
        self.closed = False
        self.configured = None
        self.tracks = None

    def close(self):
        self.closed = True

    def configure(self, monitor, region):
        self.configured = (monitor, region)

    def update_tracks(self, tracks):
        self.tracks = tracks


class FakeRoot:
    def __init__(self, state="normal"):
        self.window_state = state
        self.events = []

    def state(self, value=None):
        if value is not None:
            self.window_state = value
            self.events.append(f"state:{value}")
        return self.window_state

    def iconify(self):
        self.events.append("iconify")
        self.window_state = "iconic"

    def update_idletasks(self):
        self.events.append("update_idletasks")

    def after(self, delay, callback):
        self.events.append(f"after:{delay}")
        callback()
        return "after-id"

    def deiconify(self):
        self.events.append("deiconify")
        self.window_state = "normal"

    def lift(self):
        self.events.append("lift")


class ImmediateThread:
    def __init__(self, *, target, args=(), kwargs=None, **_options):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


class BeltConfigAndRegionTests(unittest.TestCase):
    def test_config_round_trip_normalizes_targets_and_preserves_overlay(self):
        config = AppConfig.from_dict(
            {
                "belt_overlay_enabled": False,
                "belt_cpu_warning_confirmed": True,
                "belt_region_guide_confirmed": True,
                "belt_target_names": [" r2 ", "GONK", "R2", 123],
            }
        )

        self.assertFalse(config.belt_overlay_enabled)
        self.assertTrue(config.belt_cpu_warning_confirmed)
        self.assertTrue(config.belt_region_guide_confirmed)
        self.assertEqual(["R2", "GONK"], config.belt_target_names)
        restored = AppConfig.from_dict(config.to_dict())
        self.assertEqual(["R2", "GONK"], restored.belt_target_names)
        self.assertTrue(restored.belt_cpu_warning_confirmed)
        self.assertTrue(restored.belt_region_guide_confirmed)

    def test_belt_events_are_written_to_shared_history(self):
        track = SimpleNamespace(
            id=7,
            name="R2",
            confidence=0.93,
            raw_text="R2",
            family="Diamond",
            family_confidence=0.94,
            rarity="Common",
            rarity_confidence=0.91,
            box=(10.0, 20.0, 30.0, 40.0),
        )
        event = SimpleNamespace(kind="entered", track=track)

        with patch("droid_alerts.belt.events.append_event") as append:
            record = log_track_event(event, alerted=True)

        self.assertEqual("belt_entered", record["event_type"])
        self.assertEqual("belt_tracker", record["source"])
        self.assertEqual("Diamond Common", record["rarity"])
        self.assertEqual("Diamond", record["card_family"])
        self.assertEqual("Common", record["card_rarity"])
        self.assertEqual(0.91, record["rarity_confidence"])
        self.assertTrue(record["alerted"])
        append.assert_called_once_with(record)

    def test_regions_are_saved_independently_for_each_monitor(self):
        first = MonitorDescriptor(
            index=1, left=0, top=0, width=1920, height=1080, unique_id="first"
        )
        second = MonitorDescriptor(
            index=2, left=1920, top=0, width=2560, height=1440, unique_id="second"
        )
        first_region = RelativeRegion(0.1, 0.2, 0.7, 0.6)
        second_region = RelativeRegion(0.05, 0.1, 0.8, 0.75)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regions.json"
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                save_region(first, first_region)
                save_region(second, second_region)
                self.assertEqual(first_region, load_region(first))
                self.assertEqual(second_region, load_region(second))

    def test_relative_region_scales_with_the_same_monitor(self):
        original = MonitorDescriptor(
            index=1, left=0, top=0, width=1920, height=1080, unique_id="game"
        )
        resized = MonitorDescriptor(
            index=2, left=0, top=0, width=2560, height=1440, unique_id="game"
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regions.json"
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                save_region(
                    original,
                    RelativeRegion.from_pixels(PixelBox(192, 108, 960, 540), original),
                )
                loaded = load_region(resized)

        self.assertIsNotNone(loaded)
        self.assertEqual(PixelBox(256, 144, 1280, 720), loaded.to_pixels(resized))

    def test_legacy_name_strip_region_is_not_reused(self):
        monitor = MonitorDescriptor(index=1, left=0, top=0, width=1728, height=1117)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regions.json"
            path.write_text(
                json.dumps(
                    {
                        monitor.key: {
                            "left": 0.01,
                            "top": 0.27,
                            "width": 0.98,
                            "height": 0.29,
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                self.assertIsNone(load_region(monitor))


class BeltUiWindowsTests(unittest.TestCase):
    @staticmethod
    def fake_overlay_app():
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.belt_process = None
        app.belt_region = PixelBox(100, 200, 800, 300)
        app._belt_visible_tracks = []
        app.config = AppConfig(
            belt_cpu_warning_confirmed=True,
            belt_region_guide_confirmed=True,
        )
        app.setting_vars = {"belt_overlay_enabled": FakeVar(True)}
        app.belt_overlay = FakeOverlay()
        app._current_monitor_info = lambda: MonitorInfo(
            left=0,
            top=0,
            width=1920,
            height=1080,
            index=1,
            key="game",
        )
        return app

    def test_enabled_overlay_previews_region_before_tracking(self):
        app = self.fake_overlay_app()

        DroidAlertsApp._configure_belt_overlay(app)

        self.assertEqual(app.belt_region, app.belt_overlay.configured[1])
        self.assertEqual([], app.belt_overlay.tracks)

    def test_region_selection_minimizes_before_capture_and_restores_on_cancel(self):
        app = self.fake_overlay_app()
        app.root = FakeRoot(state="zoomed")
        app.belt_selector = None
        app._belt_selector_root_state = None
        app.belt_detail_var = FakeVar()
        monitor = app._current_monitor_info()
        app._current_monitor_info = lambda: monitor
        states_during_capture = []

        def create_selector(*_args, **_kwargs):
            states_during_capture.append(app.root.window_state)
            return object()

        with patch("droid_alerts.gui.BeltRegionSelector", side_effect=create_selector) as selector:
            DroidAlertsApp.select_belt_region(app)

        self.assertEqual(["iconic"], states_during_capture)
        self.assertLess(app.root.events.index("iconify"), app.root.events.index("after:300"))
        cancel = selector.call_args.kwargs["on_cancelled"]
        cancel()
        self.assertEqual("zoomed", app.root.window_state)
        self.assertIn("deiconify", app.root.events)

    def test_first_belt_tab_open_requires_cpu_warning_confirmation(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        config = AppConfig(belt_cpu_warning_confirmed=False)
        app.config = config
        app._setup_dialog = Mock(return_value={})

        with (
            patch("droid_alerts.gui.load_config", return_value=config),
            patch("droid_alerts.gui.save_config") as save,
        ):
            DroidAlertsApp._show_belt_cpu_warning_if_needed(app)

        self.assertTrue(config.belt_cpu_warning_confirmed)
        save.assert_called_once_with(config)
        call = app._setup_dialog.call_args
        self.assertEqual("Confirm", call.kwargs["ok_text"])
        self.assertEqual(
            "The belt tracker uses more CPU power than the normal chat alerts do.",
            call.kwargs["intro"],
        )

    def test_first_region_selection_guide_uses_bundled_example(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        config = AppConfig(belt_region_guide_confirmed=False)
        app.config = config
        app._setup_dialog = Mock(return_value={})

        with (
            patch("droid_alerts.gui.load_config", return_value=config),
            patch("droid_alerts.gui.save_config") as save,
        ):
            confirmed = DroidAlertsApp._confirm_belt_region_guide_if_needed(app)

        self.assertTrue(confirmed)
        self.assertTrue(config.belt_region_guide_confirmed)
        save.assert_called_once_with(config)
        call = app._setup_dialog.call_args
        self.assertEqual("belt_region_guide.png", call.kwargs["image_path"].name)
        self.assertIn("cards in the background are not included", call.kwargs["intro"])

    def test_closing_first_time_notices_does_not_acknowledge_them(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        config = AppConfig(
            belt_cpu_warning_confirmed=False,
            belt_region_guide_confirmed=False,
        )
        app.config = config
        app._setup_dialog = Mock(return_value=None)

        with patch("droid_alerts.gui.save_config") as save:
            DroidAlertsApp._show_belt_cpu_warning_if_needed(app)
            region_confirmed = DroidAlertsApp._confirm_belt_region_guide_if_needed(app)

        self.assertFalse(region_confirmed)
        self.assertFalse(config.belt_cpu_warning_confirmed)
        self.assertFalse(config.belt_region_guide_confirmed)
        save.assert_not_called()

    def test_first_launch_intro_keeps_dashboard_selected(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.notebook = Mock()
        app._setup_dialog = Mock(side_effect=(None, None))
        app._set_var = Mock()
        app.show_droid_timers = Mock()
        app.hide_droid_timers = Mock()
        app.prompt_notification_setup_if_needed = Mock()
        config = AppConfig(intro_shown=False, notification_setup_prompted=False)

        with (
            patch("droid_alerts.gui.load_config", return_value=config),
            patch("droid_alerts.gui.save_config"),
        ):
            DroidAlertsApp.run_first_time_intro(app)

        app.notebook.select.assert_not_called()


class IndependentLifecycleTests(unittest.TestCase):
    @staticmethod
    def fake_app():
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.stop_event = threading.Event()
        app.belt_stop_event = threading.Event()
        app.belt_process = None
        app._belt_worker_ready = True
        app._watch_stop_reason = ""
        app._belt_stop_reason = ""
        app.watch_button = FakeButton()
        app.belt_watch_button = FakeButton()
        app.belt_overlay = FakeOverlay()
        app.detail_var = FakeVar()
        app.belt_status_var = FakeVar()
        app.belt_last_scan_var = FakeVar()
        return app

    def test_stopping_belt_does_not_stop_chat_watcher(self):
        app = self.fake_app()

        DroidAlertsApp.stop_belt_tracking(app)

        self.assertTrue(app.belt_stop_event.is_set())
        self.assertFalse(app.stop_event.is_set())
        self.assertEqual("manual", app._belt_stop_reason)
        self.assertFalse(app.belt_overlay.closed)

    def test_stopping_chat_watcher_does_not_stop_belt(self):
        app = self.fake_app()

        DroidAlertsApp.stop_watcher(app)

        self.assertTrue(app.stop_event.is_set())
        self.assertFalse(app.belt_stop_event.is_set())
        self.assertEqual("manual", app._watch_stop_reason)

    def test_programmatic_monitor_change_restarts_only_belt_tracker(self):
        app = self.fake_app()
        app.belt_process = AliveProcess()
        app._belt_restart_after_stop = False
        app._load_belt_region = lambda: setattr(app, "belt_region_reloaded", True)

        DroidAlertsApp._on_belt_monitor_changed(app)

        self.assertTrue(app.belt_stop_event.is_set())
        self.assertFalse(app.stop_event.is_set())
        self.assertTrue(app._belt_restart_after_stop)
        self.assertTrue(app.belt_region_reloaded)

    def test_header_reports_running_when_only_belt_is_active(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app._watcher_header_state = "Stopped"
        app._belt_header_state = "Running"
        app.status_var = FakeVar()
        app._apply_watcher_status_style = lambda _state: None

        DroidAlertsApp._refresh_header_status(app)

        self.assertEqual("Running", app.status_var.value)

    def test_belt_loading_state_uses_requested_copy(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.belt_status_var = FakeVar()
        app.belt_detail_var = FakeVar()

        DroidAlertsApp._set_belt_loading_state(app)

        self.assertEqual("Loading Belt Tracker", app.belt_status_var.value)
        self.assertEqual("This can take a little bit", app.belt_detail_var.value)

    def test_shutdown_blocks_a_pending_belt_restart(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app._shutting_down = True

        DroidAlertsApp.start_belt_tracking(app)

        self.assertFalse(hasattr(app, "belt_process"))


class BeltAlertDeliveryTests(unittest.TestCase):
    def test_every_confirmed_belt_entry_is_counted_even_when_not_an_alert_target(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.belt_telemetry = Mock()
        app.refresh_logs = Mock()

        DroidAlertsApp._handle_belt_status(
            app,
            {
                "type": "track_event",
                "record": {
                    "event": "entered",
                    "droid": "GONK",
                    "alerted": False,
                },
            },
        )

        app.belt_telemetry.record_sighting.assert_called_once_with("GONK")
        app.refresh_logs.assert_called_once_with(update_detail=False)

    def test_belt_faq_explains_region_selection_and_overlay_check(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app._setup_dialog = Mock()

        DroidAlertsApp.show_belt_faq(app)

        app._setup_dialog.assert_called_once()
        _title = app._setup_dialog.call_args.args[0]
        steps = app._setup_dialog.call_args.kwargs["steps"]
        self.assertEqual("Belt Tracker Guide", _title)
        self.assertTrue(any("click and drag" in step for step in steps))
        self.assertTrue(any("Press Enter" in step for step in steps))
        self.assertTrue(any("Show belt overlay" in step for step in steps))

    def test_selected_belt_droid_uses_every_enabled_alert_channel(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.root = object()
        app.channel_status_vars = {
            label: FakeVar() for label in ("Popup", "Sound", "Discord", "ntfy", "Pushover")
        }
        app._current_monitor_info = lambda: None
        app._post_to_ui = lambda callback: callback()
        app.refresh_logs = lambda **_kwargs: None

        config = AppConfig(
            belt_target_names=["R2"],
            popup_enabled=True,
            sound_enabled=True,
            discord_enabled=True,
            ntfy_enabled=True,
            phone_alerts_enabled=True,
        )
        policy = SimpleNamespace(notify=Mock())
        delivered = SimpleNamespace(success=True, message="Delivered")

        with (
            patch("droid_alerts.gui.load_config", return_value=config),
            patch("droid_alerts.gui.AlertPolicy", return_value=policy),
            patch("droid_alerts.gui.show_popup") as popup,
            patch(
                "droid_alerts.gui.load_discord_webhook",
                return_value=("https://example.test", "test"),
            ),
            patch("droid_alerts.gui.ntfy_configured", return_value=True),
            patch(
                "droid_alerts.gui.load_phone_alert_credentials",
                return_value=({"token": "t", "user": "u"}, "test"),
            ),
            patch("droid_alerts.gui.send_discord_alert", return_value=delivered) as discord,
            patch("droid_alerts.gui.send_ntfy_alert", return_value=delivered) as ntfy,
            patch("droid_alerts.gui.send_phone_alert", return_value=delivered) as phone,
            patch("droid_alerts.gui.append_event") as append,
            patch("droid_alerts.gui.threading.Thread", ImmediateThread),
        ):
            DroidAlertsApp._send_belt_alert(
                app,
                {
                    "droid": "R2",
                    "rarity": "Diamond Common",
                    "confidence": 0.93,
                    "rarity_confidence": 0.97,
                    "alerted": True,
                },
            )

        policy.notify.assert_called_once()
        popup.assert_called_once()
        discord.assert_called_once()
        ntfy.assert_called_once()
        phone.assert_called_once()
        popup_detection = popup.call_args.args[0]
        self.assertEqual("Diamond Common", popup_detection.rarity)
        self.assertEqual("belt-tracker", popup_detection.source)
        self.assertEqual(3, append.call_count)
        self.assertTrue(
            all(call.args[0]["source"] == "belt_tracker" for call in append.call_args_list)
        )

    def test_unselected_belt_droid_sends_nothing(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        with (
            patch("droid_alerts.gui.load_config", return_value=AppConfig(belt_target_names=[])),
            patch("droid_alerts.gui.show_popup") as popup,
        ):
            DroidAlertsApp._send_belt_alert(
                app,
                {"droid": "R2", "confidence": 0.93, "alerted": False},
            )

        popup.assert_not_called()

    def test_actual_rarity_keeps_belt_alert_wording_and_title(self):
        detection = Detection(
            droid="BAL-CORE",
            rarity="Beskar Rare",
            row_box=(0, 0, 0, 0),
            droid_score=0.99,
            rarity_score=0.92,
            rarity_margin=0.92,
            score=0.99,
            source="belt-tracker",
        )

        self.assertEqual("Beskar Rare BAL-CORE blueprint is on the belt", event_text(detection))
        self.assertEqual("Droid Alerts Belt Tracker", alert_title(detection))
        self.assertEqual(
            [
                ("BESKAR ", "#e8eaf0"),
                ("RARE ", "#3fd9ff"),
                ("BAL-CORE", "#ffffff"),
            ],
            _title_segments(detection),
        )


if __name__ == "__main__":
    unittest.main()
