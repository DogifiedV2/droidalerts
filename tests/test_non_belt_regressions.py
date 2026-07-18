from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts import alerts, config as config_module
from droid_alerts import gui, maintenance, region, row_finder, telemetry, watcher
from droid_alerts.capture import MonitorDescriptor, MonitorInfo, PixelBox, format_monitor_label
from droid_alerts.classifier import Detection
from droid_alerts.config import AppConfig, Thresholds
from droid_alerts.gui import DroidAlertsApp, fit_window_size
from droid_alerts.notifications import phone_alerts_configured
from droid_alerts.pipeline import Pipeline


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeTree:
    def __init__(self):
        self.items = {"old": {}}
        self._next_id = 0

    def get_children(self):
        return tuple(self.items)

    def delete(self, item):
        self.items.pop(item, None)

    def insert(self, _parent, _where, **values):
        self._next_id += 1
        item = f"row-{self._next_id}"
        self.items[item] = values
        return item


def priority_detection() -> Detection:
    return Detection(
        droid="Rainbow",
        rarity="Epic",
        row_box=(0, 0, 800, 44),
        droid_score=0.95,
        rarity_score=0.95,
        rarity_margin=0.5,
        score=0.95,
        source="test",
    )


class OptimizationRegressionTests(unittest.TestCase):
    def test_empty_screen_fast_path_defers_candidate_discovery(self):
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.thresholds = Thresholds()
        pipeline.detector = Mock()
        image = np.zeros((300, 2560, 3), dtype=np.uint8)

        with (
            patch("droid_alerts.pipeline.find_candidate_rows") as find_rows,
            patch("droid_alerts.pipeline.band_has_phrase_evidence", return_value=False),
        ):
            result = pipeline.detect(image, screen_height=1440, screen_width=2560)

        find_rows.assert_not_called()
        self.assertEqual([], result.detections)
        self.assertEqual("no-phrase-evidence", result.meta["skipped"])

    def test_storage_summary_scans_the_data_tree_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locations = {
                "logs": root / "logs",
                "samples": root / "alert_samples",
                "debug": root / "debug",
                "belt_dev": root / "belt_dev",
            }
            sizes = {"logs": 11, "samples": 13, "debug": 17, "belt_dev": 19}
            for key, folder in locations.items():
                folder.mkdir(parents=True)
                (folder / "nested").mkdir()
                (folder / "nested" / "payload.bin").write_bytes(b"x" * sizes[key])
            (root / "other.bin").write_bytes(b"x" * 23)

            with (
                patch.object(maintenance, "data_dir", return_value=root),
                patch.object(maintenance, "logs_dir", return_value=locations["logs"]),
                patch.object(maintenance, "alert_samples_dir", return_value=locations["samples"]),
                patch.object(maintenance, "debug_dir", return_value=locations["debug"]),
                patch.object(
                    maintenance,
                    "directory_size",
                    side_effect=AssertionError("storage_summary rescanned a subtree"),
                ),
            ):
                summary = maintenance.storage_summary()

        self.assertEqual({**sizes, "total": sum(sizes.values()) + 23}, summary)


class RuntimeResilienceTests(unittest.TestCase):
    def test_config_recovers_from_non_object_thresholds_using_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / config_module.CONFIG_FILE
            path.write_text(json.dumps({"thresholds": []}), encoding="utf-8")
            path.with_suffix(path.suffix + ".bak").write_text(
                json.dumps({"monitor_index": 7}), encoding="utf-8"
            )
            with patch.object(config_module, "config_dir", return_value=root):
                loaded = config_module.load_config()

        self.assertEqual(7, loaded.monitor_index)

    def test_calibration_load_and_save_tolerate_valid_non_object_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            path.write_text("[]", encoding="utf-8")
            with patch.object(region, "calibration_path", return_value=path):
                loaded = region.Calibration.load("monitor-a")
                region.Calibration().save("monitor-a")

            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual("auto", loaded.mode)
        self.assertIn("monitor-a", saved["profiles"])

    def test_runtime_screenshot_writes_support_unicode_paths(self):
        image = np.full((44, 800, 3), 127, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "測試_é_🚀"
            output.mkdir()
            with (
                patch.object(watcher, "alert_samples_dir", return_value=output),
                patch.object(watcher, "timestamp", return_value="20260715_120000"),
                patch("droid_alerts.classifier.draw_detections", return_value=image),
            ):
                raw_path, marked_path = watcher._save_sample(image, object(), "Rainbow Epic")

            raw = cv2.imdecode(np.frombuffer(raw_path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
            marked = cv2.imdecode(
                np.frombuffer(marked_path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
            )

        self.assertEqual(image.shape, raw.shape)
        self.assertEqual(image.shape, marked.shape)

    def test_event_log_failure_does_not_block_alert_channels(self):
        stop_event = threading.Event()
        status_events: list[dict[str, object]] = []
        capture = Mock()
        capture.monitor = MonitorInfo(0, 0, 2560, 1440, key="monitor-a")
        capture.screen_size.return_value = (2560, 1440)

        def grab(_box):
            stop_event.set()
            return np.zeros((80, 800, 3), dtype=np.uint8)

        capture.grab.side_effect = grab
        result = SimpleNamespace(
            detections=[priority_detection()],
            normalized_image=np.zeros((80, 800, 3), dtype=np.uint8),
            phrase_row_boxes=[],
            rejections=[],
            scale=1.0,
            scale_method="screen",
        )
        pipeline = Mock()
        pipeline.detect.return_value = result
        policy = Mock()
        policy.should_alert.return_value = True
        telemetry_client = Mock()
        config = AppConfig(
            alert_targets=[["Rainbow", "Epic"]],
            sound_enabled=True,
            popup_enabled=True,
            capture_interval_seconds=0.01,
        )

        with (
            patch.object(watcher, "set_dpi_awareness"),
            patch.object(watcher, "create_capture", return_value=capture),
            patch.object(watcher, "RegionResolver") as resolver_type,
            patch.object(watcher, "Pipeline", return_value=pipeline),
            patch.object(watcher, "AlertPolicy", return_value=policy),
            patch.object(watcher, "AnonymousTelemetryClient", return_value=telemetry_client),
            patch.object(watcher, "append_event", side_effect=OSError("disk full")),
            patch.object(watcher, "show_popup") as show_popup,
        ):
            resolver_type.return_value.resolve.return_value = (PixelBox(0, 0, 800, 80), "auto")
            watcher.run_watch(
                config=config,
                stop_event=stop_event,
                status_callback=status_events.append,
            )

        policy.notify.assert_called_once()
        show_popup.assert_called_once()
        self.assertTrue(any(event.get("type") == "log_error" for event in status_events))

    def test_corrupt_phone_credentials_are_reported_as_unconfigured(self):
        config = AppConfig()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phone_alerts.json"
            path.write_text("{broken", encoding="utf-8")
            with patch("droid_alerts.notifications.phone_credentials_path", return_value=path):
                self.assertFalse(phone_alerts_configured(config))

    def test_phone_test_handles_corrupt_credentials(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.channel_status_vars = {"Pushover": FakeVar()}
        app.detail_var = FakeVar()
        with patch.object(gui, "load_phone_alert_credentials", side_effect=ValueError("bad JSON")):
            sent = app._send_channel_test_with_config(
                "pushover", AppConfig(), priority_detection()
            )

        self.assertFalse(sent)
        self.assertIn("credentials", app.channel_status_vars["Pushover"].get().lower())


class GuiRegressionTests(unittest.TestCase):
    def test_preferred_window_size_is_capped_to_the_usable_screen(self):
        self.assertEqual(
            (1400, 1040),
            fit_window_size(
                1400,
                1040,
                3440,
                1440,
                horizontal_margin=80,
                vertical_margin=140,
            ),
        )
        self.assertEqual(
            (1286, 628),
            fit_window_size(
                1400,
                1040,
                1366,
                768,
                horizontal_margin=80,
                vertical_margin=140,
            ),
        )

    def test_generic_monitor_name_is_omitted_from_picker_label(self):
        monitor = MonitorDescriptor(
            1,
            0,
            0,
            1920,
            1080,
            is_primary=True,
            name="Generic PnP Monitor",
        )

        self.assertEqual("Monitor 1: 1920 × 1080 (Primary)", format_monitor_label(monitor))

    def test_empty_history_does_not_replace_detail_status(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.logs_tree = FakeTree()
        app.history_rows_by_item = {}
        app.history_summary_var = FakeVar()
        app.detail_var = FakeVar("Settings loaded")
        app._log_file_signature = None
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(gui, "logs_dir", return_value=Path(directory)):
                app.refresh_logs()

        self.assertEqual("No history yet", app.history_summary_var.get())
        self.assertEqual("Settings loaded", app.detail_var.get())

    def test_history_skips_valid_json_values_that_are_not_objects(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.logs_tree = FakeTree()
        app.history_rows_by_item = {}
        app.history_summary_var = FakeVar()
        app.detail_var = FakeVar()
        app.history_filter_var = FakeVar("All")
        app.history_search_var = FakeVar("")
        app._log_file_signature = None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(
                "\n".join(
                    (
                        json.dumps(
                            {
                                "event_type": "alert",
                                "droid": "Rainbow",
                                "rarity": "Epic",
                                "alerted": True,
                            }
                        ),
                        "[]",
                        '"not an event"',
                    )
                ),
                encoding="utf-8",
            )
            with patch.object(gui, "logs_dir", return_value=Path(directory)):
                app.refresh_logs()

        self.assertEqual(1, len(app.history_rows_by_item))
        self.assertIn("1 shown", app.history_summary_var.get())

    def test_history_refresh_is_rescheduled_after_an_unexpected_error(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app._shutting_down = False
        app.refresh_logs = Mock(side_effect=RuntimeError("bad history row"))
        app._schedule_log_refresh = Mock()

        app._auto_refresh_logs()

        app._schedule_log_refresh.assert_called_once()

    def test_region_nudge_saves_to_the_monitor_that_owns_the_overlay(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.region_box = PixelBox(10, 20, 200, 100)
        app.region_screen_size = (1920, 1080)
        app.region_monitor_offset = (-1920, 0)
        app.region_monitor_key = "old-monitor"
        app.region_source = "manual"
        app.region_status_var = FakeVar()
        app.destroy_region_overlay_windows = Mock()
        app.show_region_overlay = Mock()
        app.set_region_controls_visible = Mock()
        app._current_monitor_info = Mock(
            return_value=MonitorInfo(0, 0, 1920, 1080, key="new-monitor")
        )

        with patch.object(gui, "Calibration") as calibration_type:
            app.nudge_region(10, 0)

        calibration_type.return_value.save.assert_called_once_with("old-monitor")

    def test_dialog_position_can_remain_on_a_negative_secondary_monitor(self):
        clamp = getattr(gui, "clamp_dialog_position", None)
        self.assertTrue(callable(clamp), "dialog positioning helper is missing")
        monitors = [
            MonitorDescriptor(1, 0, 0, 1920, 1080, is_primary=True),
            MonitorDescriptor(2, -1920, 0, 1920, 1080),
        ]

        self.assertEqual((-1500, 200), clamp(-1500, 200, 500, 300, monitors))


class LowPriorityRegressionTests(unittest.TestCase):
    def test_non_windows_sound_uses_an_available_desktop_player(self):
        policy = alerts.AlertPolicy(AppConfig(sound_enabled=True))
        process = Mock()

        def find_player(name):
            return "/usr/bin/canberra-gtk-play" if name == "canberra-gtk-play" else None

        with (
            patch.object(alerts.sys, "platform", "linux"),
            patch.object(alerts, "_alert_wav", return_value=None),
            patch.object(alerts.shutil, "which", side_effect=find_player),
            patch.object(alerts.subprocess, "Popen", return_value=process) as popen,
        ):
            self.assertTrue(policy.notify(priority_detection()))

        self.assertEqual(
            ["/usr/bin/canberra-gtk-play", "-i", "dialog-warning"],
            popen.call_args.args[0],
        )

    def test_sound_test_reports_player_errors_instead_of_claiming_success(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.channel_status_vars = {"Sound": FakeVar()}
        app.detail_var = FakeVar()
        policy = Mock()
        policy.notify.side_effect = RuntimeError("no audio player")
        with patch.object(gui, "AlertPolicy", return_value=policy):
            played = app._send_channel_test_with_config(
                "sound", AppConfig(), priority_detection()
            )

        self.assertFalse(played)
        self.assertEqual("Failed to play", app.channel_status_vars["Sound"].get())

    def test_threshold_reload_updates_pipeline_and_detector(self):
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.thresholds = Thresholds()
        pipeline.detector = SimpleNamespace(rarity_threshold=0.35, droid_threshold=0.15)
        updated = Thresholds(
            rarity_threshold=0.61,
            droid_threshold=0.42,
            scale_min=0.7,
            scale_max=1.8,
        )

        apply_thresholds = getattr(pipeline, "apply_thresholds", None)
        self.assertTrue(callable(apply_thresholds), "Pipeline.apply_thresholds is missing")
        apply_thresholds(updated)

        self.assertIs(updated, pipeline.thresholds)
        self.assertEqual(0.61, pipeline.detector.rarity_threshold)
        self.assertEqual(0.42, pipeline.detector.droid_threshold)

    def test_projection_candidate_end_is_exclusive(self):
        mask = np.zeros((12, 1000), dtype=np.uint8)
        mask[1:5, :] = 1  # Four rows terminated by an inactive row.
        mask[7:12, :] = 1  # Five rows ending at the image boundary.
        with patch.object(row_finder.cv2, "GaussianBlur", side_effect=lambda values, *_a, **_k: values):
            candidates = row_finder.projection_candidates(mask)

        self.assertEqual(
            [(1, 5, 4), (7, 12, 5)],
            [(row.y0, row.y1, row.height) for row in candidates],
        )

    def test_install_id_creation_is_thread_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "anonymous_install_id.txt"
            read_barrier = threading.Barrier(2)
            start_barrier = threading.Barrier(3)
            original_read_text = Path.read_text
            results: list[str] = []
            errors: list[BaseException] = []

            def synchronized_read(candidate: Path, *args, **kwargs):
                existed = candidate.exists()
                try:
                    read_barrier.wait(timeout=0.25)
                except threading.BrokenBarrierError:
                    pass
                if not existed:
                    raise FileNotFoundError(candidate)
                return original_read_text(candidate, *args, **kwargs)

            def create_id():
                try:
                    start_barrier.wait(timeout=1.0)
                    results.append(telemetry.load_or_create_anonymous_install_id())
                except BaseException as exc:  # Captured so failures surface in the test thread.
                    errors.append(exc)

            with (
                patch.object(telemetry, "anonymous_install_id_path", return_value=path),
                patch.object(Path, "read_text", synchronized_read),
            ):
                threads = [threading.Thread(target=create_id) for _ in range(2)]
                for thread in threads:
                    thread.start()
                start_barrier.wait(timeout=1.0)
                for thread in threads:
                    thread.join(timeout=2.0)

        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        self.assertEqual(1, len(set(results)))


if __name__ == "__main__":
    unittest.main()
