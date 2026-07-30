from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts import capture as capture_module
from droid_alerts import watcher
from droid_alerts.capture import MSSCapture, MonitorInfo, PixelBox
from droid_alerts.config import AppConfig
from droid_alerts.window_capture import (
    WindowDescriptor,
    WindowsGraphicsCapture,
    resolve_capture_window,
    window_capture_key,
)


def descriptor(
    hwnd: int,
    *,
    title: str,
    process: str,
    class_name: str = "UnrealWindow",
) -> WindowDescriptor:
    return WindowDescriptor(
        hwnd=hwnd,
        title=title,
        process_name=process,
        class_name=class_name,
        process_id=1000 + hwnd,
        left=0,
        top=0,
        width=2560,
        height=1440,
    )


class WindowSelectionTests(unittest.TestCase):
    def test_resolver_requires_the_persisted_process_and_prefers_exact_title(self):
        candidates = [
            descriptor(1, title="Fake Fortnite", process="other.exe"),
            descriptor(2, title="Fortnite - Lobby", process="FortniteClient-Win64-Shipping.exe"),
            descriptor(3, title="Fortnite", process="FortniteClient-Win64-Shipping.exe"),
        ]

        selected = resolve_capture_window(
            title="Fortnite",
            process_name="FortniteClient-Win64-Shipping.exe",
            class_name="UnrealWindow",
            candidates=candidates,
        )

        self.assertEqual(3, selected.hwnd)

    def test_resolver_fails_closed_instead_of_accepting_a_spoofed_title(self):
        with self.assertRaisesRegex(RuntimeError, "Could not find"):
            resolve_capture_window(
                title="Fortnite",
                process_name="FortniteClient-Win64-Shipping.exe",
                class_name="UnrealWindow",
                candidates=[descriptor(1, title="Fortnite", process="other.exe")],
            )

    def test_calibration_key_does_not_store_window_metadata(self):
        key = window_capture_key(
            title="Fortnite - Ruben",
            process_name="FortniteClient-Win64-Shipping.exe",
            class_name="UnrealWindow",
        )

        self.assertRegex(key, r"^window:[0-9a-f]{24}$")
        self.assertNotIn("ruben", key)
        self.assertNotIn("fortnite", key)
        self.assertEqual(
            key,
            window_capture_key(
                title="Fortnite - Ruben",
                process_name="FortniteClient-Win64-Shipping.exe",
                class_name="UnrealWindow",
            ),
        )

    def test_config_round_trip_preserves_window_selector(self):
        config = AppConfig.from_dict(
            {
                "capture_source": "window",
                "capture_window_title": " Fortnite ",
                "capture_window_process": "FortniteClient-Win64-Shipping.exe",
                "capture_window_class": " UnrealWindow ",
            }
        )

        self.assertEqual("window", config.capture_source)
        self.assertEqual("Fortnite", config.capture_window_title)
        self.assertEqual("UnrealWindow", config.capture_window_class)
        self.assertEqual("window", AppConfig.from_dict(config.to_dict()).capture_source)

    def test_window_source_without_selector_falls_back_to_monitor(self):
        self.assertEqual(
            "monitor",
            AppConfig.from_dict({"capture_source": "window"}).capture_source,
        )

    def test_capture_factory_routes_window_metadata_to_wgc(self):
        expected = object()
        with patch(
            "droid_alerts.window_capture.WindowsGraphicsCapture",
            return_value=expected,
        ) as window_capture:
            result = capture_module.create_capture(
                monitor_index=2,
                capture_source="window",
                window_title="Fortnite",
                window_process="Fortnite.exe",
                window_class="UnrealWindow",
            )

        self.assertIs(expected, result)
        window_capture.assert_called_once_with(
            title="Fortnite",
            process_name="Fortnite.exe",
            class_name="UnrealWindow",
            monitor_index=2,
        )

    def test_missing_monitor_capture_fails_instead_of_watching_monitor_one(self):
        fake_mss = SimpleNamespace(
            monitors=[
                {"left": 0, "top": 0, "width": 3840, "height": 2160},
                {"left": 0, "top": 0, "width": 3840, "height": 2160},
            ],
            close=Mock(),
        )

        with (
            patch("droid_alerts.capture._mss_instance", return_value=fake_mss),
            self.assertRaisesRegex(RuntimeError, "Monitor 2 is unavailable"),
        ):
            MSSCapture(monitor_index=2)

        fake_mss.close.assert_called_once()

    def test_failed_capture_open_closes_the_partially_started_backend(self):
        backend = SimpleNamespace(
            screen_size=lambda: (_ for _ in ()).throw(RuntimeError("no frame")),
            close=Mock(),
        )
        with (
            patch.object(watcher, "_create_configured_capture", return_value=backend),
            self.assertRaisesRegex(RuntimeError, "no frame"),
        ):
            watcher._open_configured_capture(AppConfig())

        backend.close.assert_called_once()

    def test_chat_watcher_waits_for_a_selected_window_to_reopen(self):
        stop_event = threading.Event()
        config = AppConfig(
            capture_source="window",
            capture_window_title="Fortnite",
            capture_window_process="Fortnite.exe",
            capture_window_class="UnrealWindow",
        )
        attempts = 0
        events = []

        def unavailable(_config):
            nonlocal attempts
            attempts += 1
            stop_event.set()
            raise RuntimeError("Fortnite is closed")

        with patch.object(watcher, "_open_configured_capture", side_effect=unavailable):
            watcher.run_watch(
                config=config,
                stop_event=stop_event,
                status_callback=events.append,
            )

        self.assertEqual(1, attempts)
        self.assertEqual("capture_error", events[0]["type"])
        self.assertEqual("watcher_stopped", events[-1]["type"])


class WindowFrameTests(unittest.TestCase):
    def make_capture(self) -> WindowsGraphicsCapture:
        capture = WindowsGraphicsCapture.__new__(WindowsGraphicsCapture)
        capture.capture_area = MonitorInfo(0, 0, 8, 6, key="window:test")
        capture._condition = threading.Condition()
        capture._capture_size = (8, 6)
        capture._requested_box = PixelBox(2, 1, 3, 2)
        capture._latest_box = None
        capture._latest_crop = None
        capture._crop_sequence = 0
        capture._returned_sequence = 0
        capture._last_crop_at = 0.0
        capture._closed = False
        capture._error = ""
        return capture

    def test_only_the_requested_chat_crop_is_copied(self):
        capture = self.make_capture()
        frame_buffer = np.arange(6 * 8 * 4, dtype=np.uint8).reshape(6, 8, 4)

        capture._on_frame(SimpleNamespace(width=8, height=6, frame_buffer=frame_buffer))
        crop = capture.grab(PixelBox(2, 1, 3, 2))

        np.testing.assert_array_equal(crop, frame_buffer[1:3, 2:5, :3])
        self.assertEqual((2, 3, 3), crop.shape)

    def test_grab_does_not_replay_a_stale_frame_when_capture_pauses(self):
        capture = self.make_capture()
        frame_buffer = np.zeros((6, 8, 4), dtype=np.uint8)
        capture._on_frame(SimpleNamespace(width=8, height=6, frame_buffer=frame_buffer))
        capture.grab(PixelBox(2, 1, 3, 2))

        with (
            patch("droid_alerts.window_capture._FRAME_WAIT_SECONDS", 0.01),
            self.assertRaisesRegex(RuntimeError, "No new Fortnite frame arrived"),
        ):
            capture.grab(PixelBox(2, 1, 3, 2))


if __name__ == "__main__":
    unittest.main()
