from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtWidgets import QApplication

from droid_alerts import __version__
from droid_alerts.belt.region import RelativeRegion
from droid_alerts.capture import MonitorDescriptor, MonitorInfo, PixelBox
from droid_alerts.config import AppConfig
from droid_alerts.limited_deals import LimitedDeal
from droid_alerts.popup import (
    _caption_text,
    _centered_text_bounds,
    _title_segments,
    popup_icon_path,
)
from droid_alerts.classifier import Detection
from droid_alerts.timers import DroidTimersOverlay, timer_reminder_detection
from droid_alerts.ui.app_controller import AppController
from droid_alerts.ui.application import initial_window_geometry
from droid_alerts.ui.belt_controller import BeltController
from droid_alerts.ui.capture_controller import CaptureController
from droid_alerts.ui.dashboard_controller import DashboardController
from droid_alerts.ui.deals_controller import DealsController
from droid_alerts.ui.dialogs import DialogController
from droid_alerts.ui.history_controller import HistoryController
from droid_alerts.ui.settings_controller import SettingsController
from droid_alerts.ui.state import UiDispatcher


class RuntimeStub(QObject):
    configChanged = Signal()
    detailChanged = Signal(str)

    def __init__(self, config: AppConfig | None = None):
        super().__init__()
        self.config = config or AppConfig()
        self.dialogs = DialogController()
        self.dispatcher = UiDispatcher()
        self.shutdown_callbacks = []
        self.opened_urls = []

    def update_config(self, **changes):
        changes.pop("announce", None)
        changes.pop("save", None)
        for key, value in changes.items():
            setattr(self.config, key, value)
        self.configChanged.emit()

    def register_shutdown(self, callback):
        self.shutdown_callbacks.append(callback)

    def run_background(self, work, done, *, name):
        del name
        try:
            value = work()
        except BaseException as exc:
            done(None, exc)
        else:
            done(value, None)
        return Mock()

    def close_device_capture(self, *, force=False):
        del force

    def open_url(self, url):
        self.opened_urls.append(url)

    @staticmethod
    def open_path(_path):
        return True


class DashboardStub(QObject):
    statusChanged = Signal(str)

    def __init__(self):
        super().__init__()
        self.status = "Stopped"
        self.watching = False
        self.wake_alarm = Mock(active=False)

    def is_watching(self):
        return self.watching

    def toggleWatching(self):
        self.watching = not self.watching

    def setTimersEnabled(self, _enabled):
        pass

    def configureChannel(self, _channel):
        pass


class BeltStub(QObject):
    statusChanged = Signal(str)

    def __init__(self):
        super().__init__()
        self.status = "Stopped"


class CaptureStub(QObject):
    sourceChanged = Signal()
    displayGeometryChanged = Signal(bool)

    def __init__(self, monitor: MonitorInfo):
        super().__init__()
        self.monitor = monitor

    def current_monitor(self):
        return self.monitor

    def current_belt_source(self, *, open_device=False):
        del open_device
        return self.monitor

    @staticmethod
    def source_label(_config=None):
        return "Monitor 1"

    @staticmethod
    def ready_text():
        return "Ready"


class QtUiControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_payload_and_cancel_callbacks_are_preserved(self):
        dialogs = DialogController()
        accepted = Mock()
        dialogs.form(
            "Setup",
            "Enter values",
            [{"id": "topic", "label": "Topic"}],
            callback=accepted,
        )
        self.assertTrue(dialogs.state_snapshot()["visible"])
        self.assertEqual("form", dialogs.state_snapshot()["kind"])
        dialogs.accept({"topic": "droids"})
        accepted.assert_called_once_with({"topic": "droids"})

        cancelled = Mock()
        dialogs.confirm("Confirm", "Continue?", callback=cancelled)
        dialogs.cancel()
        cancelled.assert_called_once_with(None)

    def test_channel_dialog_combines_connection_fields_and_alert_choices(self):
        dialogs = DialogController()
        accepted = Mock()
        dialogs.channel_settings(
            "Configure Discord alerts",
            "Choose connection and delivery settings.",
            [{"id": "webhook", "label": "Webhook URL"}],
            [{"id": "chat:Beskar:Mythic", "label": "Beskar · Mythic"}],
            callback=accepted,
        )

        state = dialogs.state_snapshot()
        self.assertEqual("channel", state["kind"])
        self.assertEqual("Alerts", state["eyebrow"])
        self.assertEqual("bell", state["icon"])
        dialogs.accept(
            {
                "webhook": "https://discord.com/api/webhooks/1/token",
                "selected": ["chat:Beskar:Mythic"],
            }
        )
        accepted.assert_called_once()

    def test_priority_popup_uses_matching_gonk_and_card_centered_text(self):
        for family in ("Diamond", "Rainbow", "Beskar", "Galactic"):
            with self.subTest(family=family):
                detection = Detection(
                    droid=family,
                    rarity="Mythic",
                    row_box=(0, 0, 480, 44),
                    droid_score=1.0,
                    rarity_score=1.0,
                    rarity_margin=1.0,
                    score=1.0,
                    source="test",
                )
                self.assertEqual(
                    BASE_DIR
                    / "assets"
                    / f"priority_gonk_{family.casefold()}.png",
                    popup_icon_path(AppConfig(), detection),
                )
        left, right = _centered_text_bounds(
            4,
            556,
            icon_right=92,
            scale=1.0,
        )
        self.assertEqual(280, (left + right) // 2)
        self.assertEqual(280 - left, right - 280)

    def test_main_window_geometry_fits_and_centers_on_smaller_displays(self):
        self.assertEqual(
            QRect(16, 20, 1334, 688),
            initial_window_geometry(QRect(0, 0, 1366, 728)),
        )

    def test_shell_page_selection_status_and_links(self):
        runtime = RuntimeStub()
        dashboard = DashboardStub()
        belt = BeltStub()
        controller = AppController(runtime, dashboard, belt)

        controller.selectPageNumber(4)
        self.assertEqual("history", controller.state_snapshot()["page"])
        dashboard.status = "Running"
        dashboard.statusChanged.emit("Running")
        self.assertEqual("Running", controller.state_snapshot()["status"])
        controller.openLink("wiki")
        self.assertEqual(["https://gonk.tools/wiki"], runtime.opened_urls)

    def test_shell_emits_page_changes_for_page_specific_setup(self):
        runtime = RuntimeStub()
        controller = AppController(runtime, DashboardStub(), BeltStub())
        changed = Mock()
        controller.pageChanged.connect(changed)

        controller.selectPage("belt")
        controller.selectPage("belt")

        changed.assert_called_once_with("belt")

    def test_existing_install_without_version_marker_gets_update_notes(self):
        runtime = RuntimeStub(
            AppConfig(
                intro_shown=True,
                notification_setup_prompted=True,
                last_seen_version="",
            )
        )
        controller = AppController(runtime, DashboardStub(), BeltStub())

        controller.startupPrompts()

        self.assertEqual(__version__, runtime.config.last_seen_version)
        self.assertEqual("What's new", runtime.dialogs.state_snapshot()["title"])

    def test_fresh_install_records_version_then_starts_intro(self):
        runtime = RuntimeStub(
            AppConfig(
                intro_shown=False,
                notification_setup_prompted=False,
                last_seen_version="",
            )
        )
        controller = AppController(runtime, DashboardStub(), BeltStub())

        controller.startupPrompts()

        self.assertEqual(__version__, runtime.config.last_seen_version)
        self.assertEqual(
            "Before you start",
            runtime.dialogs.state_snapshot()["title"],
        )
        self.assertEqual(
            "Okay",
            runtime.dialogs.state_snapshot()["acceptText"],
        )
        self.assertEqual("", runtime.dialogs.state_snapshot()["cancelText"])
        self.assertEqual("message", runtime.dialogs.state_snapshot()["kind"])

    def test_capture_controller_selects_monitor_without_stale_window_metadata(self):
        runtime = RuntimeStub(
            AppConfig(
                capture_source="window",
                capture_window_title="Fortnite",
                capture_window_process="Fortnite.exe",
                capture_window_class="UnrealWindow",
            )
        )
        monitors = [
            MonitorDescriptor(1, 0, 0, 1920, 1080, is_primary=True),
            MonitorDescriptor(2, 1920, 0, 2560, 1440),
        ]
        with patch(
            "droid_alerts.ui.capture_controller.list_monitors",
            return_value=monitors,
        ):
            controller = CaptureController(runtime)
            controller.selectMonitor(monitors[1].key)

        self.assertEqual("monitor", runtime.config.capture_source)
        self.assertEqual(2, runtime.config.monitor_index)
        self.assertEqual("", runtime.config.capture_window_title)

    def test_capture_controller_keeps_temporarily_missing_monitor_selected(self):
        runtime = RuntimeStub(AppConfig(monitor_index=2))
        monitor = MonitorDescriptor(1, 0, 0, 1920, 1080, is_primary=True)
        with patch(
            "droid_alerts.ui.capture_controller.list_monitors",
            return_value=[monitor],
        ):
            controller = CaptureController(runtime)
            state = controller.state_snapshot()

        self.assertEqual(2, runtime.config.monitor_index)
        self.assertEqual("unavailable:2", state["monitorKey"])
        self.assertEqual(
            "Monitor 2 (temporarily unavailable)",
            state["monitors"][-1]["label"],
        )

    def test_capture_controller_detects_display_geometry_changes(self):
        runtime = RuntimeStub()
        monitors = [
            [MonitorDescriptor(1, 0, 0, 1920, 1080, is_primary=True)]
        ]
        with patch(
            "droid_alerts.ui.capture_controller.list_monitors",
            side_effect=lambda: monitors[0],
        ):
            controller = CaptureController(runtime)
            changed = Mock()
            controller.displayGeometryChanged.connect(changed)
            monitors[0] = [
                MonitorDescriptor(1, 0, 0, 2560, 1440, is_primary=True)
            ]
            controller._poll_display_geometry()

        changed.assert_called_once_with(True)
        self.assertEqual(
            2560,
            controller.state_snapshot()["monitors"][0]["width"],
        )

    def test_capture_controller_uses_window_dimensions_for_belt_regions(self):
        runtime = RuntimeStub(
            AppConfig(
                capture_source="window",
                capture_window_title="Fortnite",
                capture_window_process="Fortnite.exe",
                capture_window_class="UnrealWindow",
            )
        )
        monitor = MonitorDescriptor(1, 0, 0, 2560, 1440, is_primary=True)
        area = MonitorInfo(0, 0, 1920, 1080, key="window:test")
        capture = Mock(
            screen_size=Mock(return_value=(1920, 1080)),
            capture_area=area,
        )
        with (
            patch(
                "droid_alerts.ui.capture_controller.list_monitors",
                return_value=[monitor],
            ),
            patch(
                "droid_alerts.ui.capture_controller.create_configured_capture",
                return_value=capture,
            ),
        ):
            controller = CaptureController(runtime)
            source = controller.current_belt_source(open_device=True)

        self.assertEqual((1920, 1080), (source.width, source.height))
        self.assertEqual("window:test", source.key)
        capture.close.assert_called_once_with()

    def test_settings_are_clamped_and_sound_choices_are_structured(self):
        runtime = RuntimeStub(AppConfig())
        dashboard = DashboardStub()
        with tempfile.TemporaryDirectory() as folder:
            sounds = Path(folder)
            sounds.joinpath("custom.wav").write_bytes(b"RIFF")
            with (
                patch(
                    "droid_alerts.ui.settings_controller.user_sounds_dir",
                    return_value=sounds,
                ),
                patch(
                    "droid_alerts.ui.settings_controller.sounds_dir",
                    return_value=sounds,
                ),
            ):
                controller = SettingsController(runtime, dashboard)
                controller.setValue("popup_scale", 99)
                controller.setValue("sound_file", "System beeps")
                controller.setValue("belt_idle_scan_fps", 20)
                choices = controller.state_snapshot()["soundChoices"]

        self.assertEqual(1.5, runtime.config.popup_scale)
        self.assertEqual("", runtime.config.sound_file)
        self.assertEqual(8, runtime.config.belt_idle_scan_fps)
        self.assertEqual(8, runtime.config.belt_active_scan_fps)
        self.assertIn({"id": "custom.wav", "label": "custom.wav"}, choices)
        self.assertNotIn("ui_theme", controller.state_snapshot()["values"])

    def test_dashboard_timer_toggle_controls_the_qt_overlay(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        controller = DashboardController(runtime, capture)
        with patch(
            "droid_alerts.ui.dashboard_controller.show_droid_timers"
        ) as show:
            controller.setTimersEnabled(True)
        self.assertTrue(runtime.config.droid_timers_enabled)
        show.assert_called_once_with(
            runtime.config,
            monitor=capture.monitor,
            on_reminder=controller.handleTimerReminder,
        )
        controller.shutdown()

    def test_dashboard_timer_reminder_requires_the_overlay(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        controller = DashboardController(runtime, capture)

        controller.setTimerRemindersEnabled(True)
        self.assertFalse(runtime.config.timer_reminders_enabled)

        with patch("droid_alerts.ui.dashboard_controller.show_droid_timers"):
            controller.setTimersEnabled(True)
            controller.setTimerRemindersEnabled(True)
        self.assertTrue(runtime.config.timer_reminders_enabled)

        with patch("droid_alerts.ui.dashboard_controller.hide_droid_timers"):
            controller.setTimersEnabled(False)
        self.assertFalse(runtime.config.timer_reminders_enabled)
        controller.shutdown()

    def test_timer_reminder_is_a_popup_and_channel_alert_without_sound(self):
        runtime = RuntimeStub(AppConfig(droid_timers_enabled=True))
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        controller = DashboardController(runtime, capture)

        with patch.object(controller, "dispatch_detection") as dispatch:
            controller.handleTimerReminder("mythic", 60)

        detection = dispatch.call_args.args[0]
        self.assertEqual("Mythic Timer", detection.droid)
        self.assertEqual("timer-reminder", detection.source)
        self.assertEqual("TIMER REMINDER", _caption_text(detection))
        self.assertEqual([("MYTHIC TIMER", "#ff3fa8")], _title_segments(detection))
        self.assertFalse(dispatch.call_args.kwargs["include_sound"])
        controller.shutdown()

    def test_remote_channel_configs_include_timer_reminders(self):
        runtime = RuntimeStub(
            AppConfig(channel_disabled_alerts={"discord": ["timer_reminder"]})
        )
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        controller = DashboardController(runtime, capture)

        _label, discord_options = controller._channel_alert_options("discord")
        _label, ntfy_options = controller._channel_alert_options("ntfy")
        discord_timer = next(
            option for option in discord_options if option["id"] == "timer_reminder"
        )
        ntfy_timer = next(
            option for option in ntfy_options if option["id"] == "timer_reminder"
        )

        self.assertFalse(discord_timer["selected"])
        self.assertTrue(ntfy_timer["selected"])
        controller.shutdown()

    def test_discord_config_saves_optional_limited_deal_webhook(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        controller = DashboardController(runtime, capture)
        payload = {
            "webhook": "https://discord.com/api/webhooks/1/main",
            "limited_deal_webhook": "https://discord.com/api/webhooks/2/deals",
        }

        with (
            patch(
                "droid_alerts.ui.dashboard_controller.save_discord_webhook"
            ) as save_main,
            patch(
                "droid_alerts.ui.dashboard_controller."
                "save_limited_deal_discord_webhook"
            ) as save_limited_deals,
        ):
            self.assertTrue(controller._save_discord(payload))

        save_main.assert_called_once_with(runtime.config, payload["webhook"])
        save_limited_deals.assert_called_once_with(
            runtime.config,
            payload["limited_deal_webhook"],
        )
        self.assertTrue(runtime.config.discord_enabled)
        controller.shutdown()

    def test_timer_reminder_detection_uses_readable_timer_name(self):
        detection = timer_reminder_detection("galactic", 45)

        self.assertEqual("Galactic Timer", detection.droid)
        self.assertEqual("45", detection.rarity)
        self.assertEqual("timer-reminder", detection.source)

    def test_timer_refresh_never_raises_or_reactivates_its_window(self):
        class TimerOverlayProbe(DroidTimersOverlay):
            def __init__(self):
                self.raise_count = 0
                super().__init__(monitor=MonitorInfo(0, 0, 1920, 1080))

            def raise_(self):
                self.raise_count += 1

        overlay = TimerOverlayProbe()
        try:
            self.assertEqual(0, overlay.raise_count)
            overlay._tick()
            self.assertEqual(0, overlay.raise_count)
            self.assertTrue(overlay._timer.isActive())
        finally:
            overlay.close()

    def test_dashboard_capture_change_restarts_an_active_watcher(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        controller = DashboardController(runtime, capture)
        worker = Mock()
        worker.is_alive.return_value = True
        controller.watch_thread = worker
        controller.stop_event = Mock()
        with patch(
            "droid_alerts.ui.dashboard_controller.QTimer.singleShot"
        ) as later:
            capture.sourceChanged.emit()
            controller.stop_event.set.assert_called_once()
            controller._watcher_finished(None, worker)

        later.assert_called_once_with(150, controller.startWatcher)
        controller.shutdown()

    def test_belt_overlay_scales_device_coordinates_to_the_display(self):
        runtime = RuntimeStub(AppConfig(belt_overlay_enabled=True))
        capture = CaptureStub(MonitorInfo(0, 0, 960, 540))
        dashboard = DashboardStub()
        with patch(
            "droid_alerts.ui.belt_controller.load_region",
            return_value=RelativeRegion(0.0, 0.0, 1.0, 1.0),
        ):
            controller = BeltController(runtime, capture, dashboard)
        controller.process = Mock()
        controller._overlay_requested = True
        controller.region = PixelBox(100, 200, 800, 260)
        controller._visible_tracks = [
            {"id": 1, "name": "R2", "box": [20, 40, 100, 80]}
        ]
        capture.current_belt_source = lambda **_kwargs: MonitorInfo(
            0, 0, 1920, 1080
        )
        overlay = Mock()
        with patch(
            "droid_alerts.ui.overlays.belt_overlay",
            return_value=overlay,
        ):
            controller._update_overlay()

        monitor, region, tracks = overlay.show_tracks.call_args.args
        self.assertEqual(capture.monitor, monitor)
        self.assertEqual(PixelBox(50, 100, 400, 130), region)
        self.assertEqual([10, 20, 50, 40], tracks[0]["box"])
        controller.process = None
        controller.shutdown()

    def test_belt_page_preview_and_cpu_notice_are_acknowledged_explicitly(self):
        runtime = RuntimeStub(AppConfig(belt_cpu_warning_confirmed=False))
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        with patch(
            "droid_alerts.ui.belt_controller.load_region",
            return_value=RelativeRegion(0.0, 0.0, 1.0, 1.0),
        ):
            controller = BeltController(runtime, capture, DashboardStub())
        controller.region = PixelBox(20, 30, 800, 260)
        overlay = Mock()
        with patch(
            "droid_alerts.ui.overlays.belt_overlay",
            return_value=overlay,
        ):
            controller.pageOpened()
        overlay.show_tracks.assert_called_once()
        self.assertEqual(
            "Belt Tracker CPU Usage",
            runtime.dialogs.state_snapshot()["title"],
        )
        runtime.dialogs.cancel()
        self.assertFalse(runtime.config.belt_cpu_warning_confirmed)
        controller.pageOpened()
        runtime.dialogs.accept({})
        self.assertTrue(runtime.config.belt_cpu_warning_confirmed)
        controller.shutdown()

    def test_belt_capture_change_restarts_only_after_worker_stops(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        with patch(
            "droid_alerts.ui.belt_controller.load_region",
            return_value=RelativeRegion(0.0, 0.0, 1.0, 1.0),
        ):
            controller = BeltController(runtime, capture, DashboardStub())
            process = Mock()
            process.is_alive.return_value = True
            controller.process = process
            controller.stop_event = Mock()
            controller._worker_ready = True
            with (
                patch(
                    "droid_alerts.ui.belt_controller.QTimer.singleShot"
                ) as later,
                patch("droid_alerts.ui.overlays.belt_overlay"),
            ):
                capture.sourceChanged.emit()
                controller.stop_event.set.assert_called_once()
                controller._worker_finished(None, process)

        later.assert_called_once_with(150, controller.startTracking)
        controller.shutdown()

    def test_limited_deal_rules_update_without_starting_network_service(self):
        runtime = RuntimeStub(AppConfig())
        dashboard = DashboardStub()
        with patch.object(DealsController, "start"):
            controller = DealsController(runtime, dashboard)
        self.assertEqual("", controller.state_snapshot()["sidebarLabel"])
        self.assertEqual(
            [
                "Rainbow Epic",
                "Rainbow Legendary",
                "Rainbow Mythic",
                "Beskar Epic",
                "Beskar Legendary",
                "Beskar Mythic",
                "Galactic Epic",
                "Galactic Legendary",
                "Galactic Mythic",
                "Diamond Mythic",
            ],
            [row["label"] for row in controller.state_snapshot()["priorityRows"]],
        )
        controller.setPriority("Rainbow|Epic", True)
        self.assertIn(
            ["Rainbow", "Epic"],
            runtime.config.limited_deal_priority_alerts,
        )
        controller.shutdown()

    def test_limited_deal_sidebar_label_uses_rarity_and_droid_name(self):
        runtime = RuntimeStub(AppConfig())
        with patch.object(DealsController, "start"):
            controller = DealsController(runtime, DashboardStub())
        controller.current_deal = LimitedDeal(
            starts_at="2026-07-30T12:00:00.000Z",
            ends_at="2026-07-30T13:00:00.000Z",
            rarity="Diamond",
            droid="Mecha Droid",
            droid_id=47,
            droid_class="Legendary",
        )

        controller.refresh()

        self.assertEqual(
            "Diamond Mecha Droid",
            controller.state_snapshot()["sidebarLabel"],
        )
        self.assertEqual(
            "Diamond Mecha Droid",
            controller.state_snapshot()["offer"],
        )
        controller.shutdown()

    def test_limited_deal_rule_picker_excludes_default_and_gold(self):
        runtime = RuntimeStub(
            AppConfig.from_dict(
                {"limited_deal_target_tiers": {"7": "Default", "8": "Gold"}}
            )
        )
        with patch.object(DealsController, "start"):
            controller = DealsController(runtime, DashboardStub())

        controller.chooseTargets()

        choices = runtime.dialogs.state_snapshot()["choices"]
        options = runtime.dialogs.state_snapshot()["options"]
        self.assertEqual(
            ["", "Diamond", "Rainbow", "Beskar", "Galactic"],
            [choice["id"] for choice in choices],
        )
        self.assertNotIn("Rare", {option["detail"] for option in options})
        self.assertEqual({}, runtime.config.limited_deal_target_tiers)
        controller.shutdown()

    def test_limited_deal_intro_is_remembered_only_after_acceptance(self):
        runtime = RuntimeStub(AppConfig(limited_deals_intro_shown=False))
        with patch.object(DealsController, "start"):
            controller = DealsController(runtime, DashboardStub())

        controller.pageOpened()
        runtime.dialogs.cancel()
        self.assertFalse(runtime.config.limited_deals_intro_shown)
        controller.pageOpened()
        runtime.dialogs.accept({})
        self.assertTrue(runtime.config.limited_deals_intro_shown)
        controller.shutdown()

    def test_history_filters_rows_and_ignores_non_objects(self):
        runtime = RuntimeStub()
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            logs.joinpath("events.jsonl").write_text(
                "\n".join(
                    (
                        json.dumps(
                            {
                                "ts": "20260729_120000",
                                "event_type": "alert",
                                "droid": "Beskar",
                                "rarity": "Mythic",
                                "alerted": True,
                            }
                        ),
                        json.dumps(
                            {
                                "event_type": "delivery",
                                "channel": "Discord",
                                "success": False,
                                "detail": "offline",
                            }
                        ),
                        "[]",
                    )
                ),
                encoding="utf-8",
            )
            with patch(
                "droid_alerts.ui.history_controller.logs_dir",
                return_value=logs,
            ):
                controller = HistoryController(runtime)
                self.assertEqual(2, len(controller.state_snapshot()["rows"]))
                controller.setFilter("failures")
                rows = controller.state_snapshot()["rows"]
                self.assertEqual(1, len(rows))
                self.assertEqual("Failed", rows[0]["status"])
                controller.shutdown()

    def test_qml_shell_declares_every_page_and_no_theme_picker(self):
        qml = BASE_DIR / "src" / "droid_alerts" / "ui" / "qml"
        main = qml.joinpath("Main.qml").read_text(encoding="utf-8")
        for page in (
            "DashboardPage",
            "BeltPage",
            "DealsPage",
            "HistoryPage",
            "DiagnosticsPage",
            "SettingsPage",
        ):
            self.assertIn(page, main)
        settings = qml.joinpath("pages", "SettingsPage.qml").read_text(
            encoding="utf-8"
        )
        dashboard = qml.joinpath("pages", "DashboardPage.qml").read_text(
            encoding="utf-8"
        )
        diagnostics = qml.joinpath("pages", "DiagnosticsPage.qml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("ui_theme", settings)
        self.assertNotIn('title: "Interface"', settings)
        self.assertIn('valueRoleName: "key"', dashboard)
        self.assertIn('text: "Configure"', dashboard)
        self.assertNotIn('text: "Configure…"', dashboard)
        self.assertNotIn('text: "Window…"', dashboard)
        self.assertNotIn('text: "Device…"', dashboard)
        self.assertNotIn('text: "Position…"', dashboard)
        self.assertNotIn('text: "Add WAV…"', settings)
        self.assertIn(
            "ToolTip.text: captureController.state.windowCaptureUnavailableReason",
            dashboard,
        )
        self.assertIn(
            "ToolTip.text: captureController.state.deviceCaptureUnavailableReason",
            dashboard,
        )
        self.assertIn("toastText.implicitWidth + 60", main)
        self.assertNotIn('text: "Alerts"', dashboard)
        self.assertIn("id: regionNudgeGrid", diagnostics)
        self.assertIn("Layout.alignment: Qt.AlignHCenter", diagnostics)
        dialog_overlay = qml.joinpath("components", "DialogOverlay.qml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("esc ·", dialog_overlay)
        self.assertNotIn("↩", dialog_overlay)
        self.assertTrue(qml.joinpath("components", "NavIcon.qml").is_file())
        self.assertTrue(qml.joinpath("components", "LinkChip.qml").is_file())


if __name__ == "__main__":
    unittest.main()
