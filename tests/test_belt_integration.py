from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.region import RelativeRegion, load_region, save_region
from droid_alerts.capture import MonitorDescriptor, PixelBox
from droid_alerts.config import AppConfig
from droid_alerts.gui import DroidAlertsApp


class FakeVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeButton:
    def __init__(self):
        self.options = {}

    def configure(self, **values):
        self.options.update(values)


class AliveThread:
    @staticmethod
    def is_alive():
        return True


class FakeOverlay:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class BeltConfigAndRegionTests(unittest.TestCase):
    def test_config_round_trip_normalizes_targets_and_preserves_overlay(self):
        config = AppConfig.from_dict(
            {
                "belt_overlay_enabled": False,
                "belt_target_names": [" r2 ", "GONK", "R2", 123],
            }
        )

        self.assertFalse(config.belt_overlay_enabled)
        self.assertEqual(["R2", "GONK"], config.belt_target_names)
        self.assertEqual(["R2", "GONK"], AppConfig.from_dict(config.to_dict()).belt_target_names)

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


class IndependentLifecycleTests(unittest.TestCase):
    @staticmethod
    def fake_app():
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.stop_event = threading.Event()
        app.belt_stop_event = threading.Event()
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
        self.assertTrue(app.belt_overlay.closed)

    def test_stopping_chat_watcher_does_not_stop_belt(self):
        app = self.fake_app()

        DroidAlertsApp.stop_watcher(app)

        self.assertTrue(app.stop_event.is_set())
        self.assertFalse(app.belt_stop_event.is_set())
        self.assertEqual("manual", app._watch_stop_reason)

    def test_programmatic_monitor_change_restarts_only_belt_tracker(self):
        app = self.fake_app()
        app.belt_thread = AliveThread()
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

    def test_shutdown_blocks_a_pending_belt_restart(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app._shutting_down = True

        DroidAlertsApp.start_belt_tracking(app)

        self.assertFalse(hasattr(app, "belt_thread"))


if __name__ == "__main__":
    unittest.main()
