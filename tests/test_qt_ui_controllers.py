from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from PySide6.QtCore import (
    QByteArray,
    QCoreApplication,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QUrl,
    Qt,
    Signal,
)
from PySide6.QtGui import QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from droid_alerts import __version__
from droid_alerts.belt.region import RelativeRegion
from droid_alerts.capture import MonitorDescriptor, MonitorInfo, PixelBox
from droid_alerts.config import MAX_SAFE_DELAY_SECONDS, AppConfig
from droid_alerts.limited_deals import LimitedDeal
from droid_alerts.notifications import (
    DeliveryResult,
    load_discord_destinations,
    load_discord_webhook,
    load_limited_deal_discord_webhook,
    load_ntfy_token,
    save_ntfy_token,
)
from droid_alerts.popup import (
    _caption_text,
    _centered_text_bounds,
    _title_segments,
    popup_icon_path,
)
from droid_alerts.classifier import Detection
from droid_alerts.timers import DroidTimersOverlay, timer_reminder_detection
from droid_alerts.ui.app_controller import AppController
from droid_alerts.ui.application import initial_window_geometry, qml_dir
from droid_alerts.ui.belt_controller import BeltController
from droid_alerts.ui.capture_controller import CaptureController
from droid_alerts.ui.dashboard_controller import DashboardController
from droid_alerts.ui.deals_controller import DealsController
from droid_alerts.ui.dialogs import DialogController
from droid_alerts.ui.history_controller import HistoryController
from droid_alerts.ui.runtime import ApplicationRuntime
from droid_alerts.ui.settings_controller import SettingsController
from droid_alerts.ui.state import UiDispatcher
from droid_alerts.window_capture import WindowDescriptor


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

        action = Mock()
        dialogs.rules(
            "Rules",
            "Edit rules",
            [],
            [],
            action_text="Add item",
            action_callback=action,
        )
        self.assertEqual("Add item", dialogs.state_snapshot()["actionText"])
        dialogs.action({"values": {"alert": "sound.wav"}})
        action.assert_called_once_with(
            {"values": {"alert": "sound.wav"}}
        )

        manage_action = Mock()
        dialogs.manage(
            "Discord Webhooks",
            "Manage destinations",
            [{"id": "main", "label": "Main"}],
            action_callback=manage_action,
        )
        self.assertEqual("manage", dialogs.state_snapshot()["kind"])
        self.assertEqual("Add New", dialogs.state_snapshot()["actionText"])
        self.assertEqual("Exit", dialogs.state_snapshot()["acceptText"])
        dialogs.action({"action": "modify", "id": "main"})
        manage_action.assert_called_once_with({"action": "modify", "id": "main"})
        dialogs.accept({})

    def test_dialog_controls_remain_clickable_after_wheel_scroll(self):
        dialogs = DialogController()
        accepted = Mock()
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(qml_dir()))
        engine.rootContext().setContextProperty("dialogController", dialogs)
        engine.loadData(
            QByteArray(
                b"import QtQuick 2.15\n"
                b"import QtQuick.Controls 2.15\n"
                b"import DroidAlerts.Components 1.0\n"
                b"ApplicationWindow { width: 1000; height: 650; visible: true; "
                b"DialogOverlay { anchors.fill: parent } }"
            ),
            QUrl.fromLocalFile(str(qml_dir()) + "/"),
        )
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]
        dialogs.choices(
            "Discord Message Rules",
            "Select alerts",
            [
                {"id": str(index), "label": f"Alert {index}", "selected": False}
                for index in range(40)
            ],
            multi=True,
            callback=accepted,
        )
        for _ in range(5):
            self.app.processEvents()

        def descendants(item):
            for child in item.childItems():
                yield child
                yield from descendants(child)

        items = list(descendants(window.contentItem()))
        checkboxes = [
            item
            for item in items
            if "SignalCheck" in item.metaObject().className()
            and item.property("visible")
        ]

        def click_item(item):
            position = item.mapToScene(
                QPoint(round(item.width() / 2), round(item.height() / 2))
            )
            QTest.mouseClick(
                window,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                position.toPoint(),
            )
            self.app.processEvents()

        select_all = next(
            item
            for item in items
            if "SignalButton" in item.metaObject().className()
            and item.property("text") == "Select all"
        )
        deselect_all = next(
            item
            for item in items
            if "SignalButton" in item.metaObject().className()
            and item.property("text") == "Deselect all"
        )
        click_item(select_all)
        self.assertTrue(all(item.property("checked") for item in checkboxes))
        click_item(deselect_all)
        self.assertFalse(any(item.property("checked") for item in checkboxes))

        scroll = next(
            item
            for item in items
            if "Flickable" in item.metaObject().className()
        )
        position = scroll.mapToScene(
            QPoint(round(scroll.width() / 2), round(scroll.height() / 2))
        )
        for _ in range(4):
            event = QWheelEvent(
                QPointF(position),
                QPointF(position),
                QPoint(),
                QPoint(0, -120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
            QApplication.sendEvent(window, event)
            self.app.processEvents()

        self.assertGreater(float(scroll.property("contentY")), 0.0)
        scroll_top = scroll.mapToScene(QPoint()).y()
        scroll_bottom = scroll_top + scroll.height()
        checkbox = next(
            item
            for item in items
            if "SignalCheck" in item.metaObject().className()
            and scroll_top <= item.mapToScene(QPoint()).y()
            and item.mapToScene(QPoint()).y() + item.height() <= scroll_bottom
        )
        click_item(checkbox)
        self.assertTrue(checkbox.property("checked"))

        save_button = next(
            item
            for item in items
            if "SignalButton" in item.metaObject().className()
            and item.property("visible")
            and item.property("text") == "Save"
        )
        click_item(save_button)
        accepted.assert_called_once()

        channel_accepted = Mock()
        dialogs.channel_settings(
            "Configure Discord alerts",
            "Choose connection and delivery settings.",
            [],
            [
                {"id": str(index), "label": f"Alert {index}", "selected": False}
                for index in range(3)
            ],
            callback=channel_accepted,
        )
        self.app.processEvents()
        items = list(descendants(window.contentItem()))
        checkboxes = [
            item
            for item in items
            if "SignalCheck" in item.metaObject().className()
            and item.property("visible")
        ]
        select_all = next(
            item
            for item in items
            if "SignalButton" in item.metaObject().className()
            and item.property("visible")
            and item.property("text") == "Select all"
        )
        click_item(select_all)
        self.assertTrue(all(item.property("checked") for item in checkboxes))
        save_button = next(
            item
            for item in items
            if "SignalButton" in item.metaObject().className()
            and item.property("visible")
            and item.property("text") == "Save"
        )
        click_item(save_button)
        self.assertEqual(
            ["0", "1", "2"],
            channel_accepted.call_args.args[0]["selected"],
        )

        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_rule_search_is_cleared_when_another_dialog_opens(self):
        dialogs = DialogController()
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(qml_dir()))
        engine.rootContext().setContextProperty("dialogController", dialogs)
        engine.loadData(
            QByteArray(
                b"import QtQuick 2.15\n"
                b"import QtQuick.Controls 2.15\n"
                b"import DroidAlerts.Components 1.0\n"
                b"ApplicationWindow { width: 800; height: 600; visible: true; "
                b"DialogOverlay { anchors.fill: parent } }"
            ),
            QUrl.fromLocalFile(str(qml_dir()) + "/"),
        )
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]

        def descendants(item):
            for child in item.childItems():
                yield child
                yield from descendants(child)

        dialogs.rules("Belt Tracker", "Choose targets", [], [])
        for _ in range(3):
            self.app.processEvents()
        search = next(
            item
            for item in descendants(window.contentItem())
            if "SignalField" in item.metaObject().className()
            and item.property("visible")
        )
        search.setProperty("text", "belt")
        self.assertEqual("belt", search.property("text"))

        dialogs.cancel()
        dialogs.rules("Discord Webhooks", "Choose destinations", [], [])
        for _ in range(3):
            self.app.processEvents()
        self.assertEqual("", search.property("text"))

        dialogs.cancel()
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

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

    def test_main_window_geometry_never_exceeds_the_available_display(self):
        available = QRect(0, 0, 800, 600)
        self.assertTrue(available.contains(initial_window_geometry(available)))

    def test_runtime_reports_save_failures_and_keeps_a_retry_pending(self):
        with (
            patch(
                "droid_alerts.ui.runtime.load_config",
                return_value=AppConfig(),
            ),
            patch("droid_alerts.ui.runtime.AnonymousAppTelemetryClient"),
            patch(
                "droid_alerts.ui.runtime.save_config",
                side_effect=OSError("disk full"),
            ),
        ):
            runtime = ApplicationRuntime()
            details: list[str] = []
            runtime.detailChanged.connect(details.append)
            runtime.update_config(sound_enabled=False)
            runtime._save_timer.stop()
            runtime.save_now()

            self.assertIn("disk full", details[-1])
            self.assertTrue(runtime._config_dirty)
            self.assertTrue(runtime._save_timer.isActive())
            runtime._save_timer.stop()
            runtime._closed = True
            runtime.dispatcher.close()

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
        message = runtime.dialogs.state_snapshot()["message"]
        self.assertIn("Full port of the app", message)
        self.assertIn("full customisation for all alert types", message)
        self.assertIn("Discord link in the bottom left corner", message)

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

    def test_window_capture_uses_the_window_display_and_desktop_position(self):
        runtime = RuntimeStub(
            AppConfig(
                monitor_index=1,
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
        window = WindowDescriptor(
            hwnd=42,
            title="Fortnite",
            process_name="Fortnite.exe",
            class_name="UnrealWindow",
            process_id=100,
            left=2100,
            top=100,
            width=1600,
            height=900,
        )
        with (
            patch(
                "droid_alerts.ui.capture_controller.list_monitors",
                return_value=monitors,
            ),
            patch(
                "droid_alerts.ui.capture_controller.resolve_capture_window",
                return_value=window,
            ),
        ):
            controller = CaptureController(runtime)
            monitor = controller.current_monitor()
            source = controller.current_belt_source()

        self.assertEqual(2, monitor.index)
        self.assertEqual((2100, 100), (source.left, source.top))
        self.assertEqual((1600, 900), (source.width, source.height))

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

    def test_settings_reject_non_finite_numbers_and_cap_delays(self):
        runtime = RuntimeStub(AppConfig())
        controller = SettingsController(runtime, DashboardStub())

        controller.setValue("capture_interval_seconds", "1e309")
        self.assertEqual(0.25, runtime.config.capture_interval_seconds)
        self.assertEqual("Settings", runtime.dialogs.state_snapshot()["title"])
        runtime.dialogs.cancel()

        controller.setValue("retention_days", "1e309")
        self.assertEqual(30, runtime.config.retention_days)
        self.assertEqual("Settings", runtime.dialogs.state_snapshot()["title"])
        runtime.dialogs.cancel()

        controller.setValue(
            "capture_interval_seconds",
            str(MAX_SAFE_DELAY_SECONDS * 2),
        )
        self.assertEqual(
            MAX_SAFE_DELAY_SECONDS,
            runtime.config.capture_interval_seconds,
        )

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
        discord_timers = [
            option for option in discord_options if option["id"].startswith("timer:")
        ]
        ntfy_timers = [
            option for option in ntfy_options if option["id"].startswith("timer:")
        ]

        self.assertEqual(3, len(discord_timers))
        self.assertTrue(all(not option["selected"] for option in discord_timers))
        self.assertEqual(3, len(ntfy_timers))
        self.assertTrue(all(option["selected"] for option in ntfy_timers))
        controller.shutdown()

    def test_saving_timer_channel_choices_replaces_legacy_global_block(self):
        runtime = RuntimeStub(
            AppConfig(channel_disabled_alerts={"discord": ["timer_reminder"]})
        )
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        controller = DashboardController(runtime, capture)
        _label, options = controller._channel_alert_options("discord")
        selected = [
            option["id"]
            for option in options
            if option["id"] != "timer:mythic"
        ]

        controller._save_channel_alerts(
            "discord",
            options,
            {"selected": selected},
        )

        disabled = runtime.config.channel_disabled_alerts["discord"]
        self.assertNotIn("timer_reminder", disabled)
        self.assertEqual(["timer:mythic"], disabled)
        self.assertTrue(
            runtime.config.channel_allows_alert("discord", "timer:beskar")
        )
        self.assertFalse(
            runtime.config.channel_allows_alert("discord", "timer:mythic")
        )
        controller.shutdown()

    def test_alert_customization_options_follow_priority_alert_order(self):
        runtime = RuntimeStub(
            AppConfig(
                alert_targets=[
                    ["Diamond", "Mythic"],
                    ["Rainbow", "Mythic"],
                    ["Beskar", "Epic"],
                    ["Rainbow", "Epic"],
                ]
            )
        )
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        controller = DashboardController(runtime, capture)

        expected = [
            "chat:Rainbow:Epic",
            "chat:Rainbow:Mythic",
            "chat:Beskar:Epic",
            "chat:Diamond:Mythic",
        ]
        for channel in ("popup", "sound", "discord", "ntfy", "pushover"):
            with self.subTest(channel=channel):
                _label, options = controller._channel_alert_options(channel)
                chat_ids = [
                    option["id"]
                    for option in options
                    if option["id"].startswith("chat:")
                ]
                self.assertEqual(expected, chat_ids)

        controller.shutdown()

    def test_discord_channel_config_only_saves_main_webhook(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        controller = DashboardController(runtime, capture)
        payload = {
            "webhook": "https://discord.com/api/webhooks/1/main",
            "limited_deal_webhook": "https://discord.com/api/webhooks/2/deals",
        }

        with patch(
            "droid_alerts.ui.dashboard_controller.save_discord_webhook"
        ) as save_main:
            self.assertTrue(controller._save_discord(payload))

        save_main.assert_called_once_with(runtime.config, payload["webhook"])
        self.assertTrue(runtime.config.discord_enabled)
        controller.shutdown()

    def test_popup_and_sound_routing_applies_to_dashboard_alerts(self):
        alert_id = "chat:Beskar:Mythic"
        runtime = RuntimeStub(
            AppConfig(
                popup_enabled=True,
                sound_enabled=True,
                channel_disabled_alerts={
                    "popup": [alert_id],
                    "sound": [alert_id],
                },
            )
        )
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        controller = DashboardController(runtime, capture)
        item = controller.test_detection()

        with (
            patch.object(controller, "_show_popup") as show_popup,
            patch("droid_alerts.ui.dashboard_controller.AlertPolicy.notify") as sound,
        ):
            controller.dispatch_detection(item, source="test")

        show_popup.assert_not_called()
        sound.assert_not_called()
        controller.shutdown()

    def test_ntfy_settings_preserve_or_explicitly_remove_a_saved_token(self):
        runtime = RuntimeStub(
            AppConfig(
                ntfy_server_url="https://ntfy.example",
                ntfy_topic="private",
            )
        )
        controller = DashboardController(
            runtime,
            CaptureStub(MonitorInfo(0, 0, 1920, 1080)),
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "droid_alerts.notifications.config_dir",
            return_value=Path(directory),
        ):
            save_ntfy_token(runtime.config, "existing-secret")
            self.assertTrue(
                controller._save_ntfy(
                    {
                        "server": "https://ntfy.example",
                        "topic": "private",
                        "token": "",
                        "token_action": "keep",
                    }
                )
            )
            self.assertEqual("existing-secret", load_ntfy_token(runtime.config)[0])

            controller._save_ntfy(
                {
                    "server": "https://ntfy.example",
                    "topic": "private",
                    "token": "",
                    "token_action": "remove",
                }
            )
            self.assertIsNone(load_ntfy_token(runtime.config)[0])
        controller.shutdown()

    def test_watcher_popups_use_current_runtime_customization(self):
        runtime = RuntimeStub(
            AppConfig(popup_seconds=5.0, popup_position="top-right")
        )
        runtime.dispatcher.post = lambda callback: callback()
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        capture.create_runtime_capture = Mock()
        controller = DashboardController(runtime, capture)
        snapshot = AppConfig.from_dict(runtime.config.to_dict())
        runtime.update_config(
            popup_seconds=20.0,
            popup_position="bottom-left",
        )
        detection = Detection(
            droid="Beskar",
            rarity="Mythic",
            row_box=(0, 0, 1, 1),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="test",
        )

        def invoke_popup(**kwargs):
            kwargs["popup_callback"](detection)

        with (
            patch(
                "droid_alerts.ui.dashboard_controller.run_watch",
                side_effect=invoke_popup,
            ),
            patch.object(controller, "_show_popup") as show_popup,
        ):
            controller._watch_worker(snapshot, threading.Event())

        popup_config = show_popup.call_args.args[1]
        self.assertEqual(20.0, popup_config.popup_seconds)
        self.assertEqual("bottom-left", popup_config.popup_position)
        controller.shutdown()

    def test_dashboard_remote_delivery_retries_a_transient_failure(self):
        runtime = RuntimeStub(AppConfig(discord_enabled=True))
        controller = DashboardController(
            runtime,
            CaptureStub(MonitorInfo(0, 0, 1920, 1080)),
        )
        detection = Detection(
            droid="R2",
            rarity="Rainbow Epic",
            row_box=(0, 0, 1, 1),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="limited-deal",
        )
        results = [
            DeliveryResult("Discord", False, "connection timeout"),
            DeliveryResult("Discord", True, "Delivered"),
        ]
        outcomes = Mock()
        with (
            patch(
                "droid_alerts.ui.dashboard_controller.load_discord_webhook_for_detection",
                return_value=("https://discord.com/api/webhooks/1/main", "test"),
            ),
            patch(
                "droid_alerts.ui.dashboard_controller.send_discord_alert",
                side_effect=results,
            ) as send,
            patch(
                "droid_alerts.ui.dashboard_controller.time.sleep",
                return_value=None,
            ),
        ):
            controller.dispatch_detection(
                detection,
                source="limited_deal",
                include_local=False,
                on_complete=outcomes,
            )

        self.assertEqual(2, send.call_count)
        outcomes.assert_called_once_with({"Discord": True})
        controller.shutdown()

    def test_timer_reminder_detection_uses_readable_timer_name(self):
        detection = timer_reminder_detection("galactic", 45)

        self.assertEqual("Galactic Timer", detection.droid)
        self.assertEqual("45", detection.rarity)
        self.assertEqual("timer-reminder", detection.source)

    def test_timer_overlay_supports_multiple_independent_lead_times(self):
        reminders = []
        overlay = DroidTimersOverlay(
            monitor=MonitorInfo(0, 0, 1920, 1080),
            reminders_enabled=False,
            reminder_rules={
                "beskar": [300, 60],
                "mythic": [120],
                "galactic": [],
            },
            on_reminder=lambda kind, remaining: reminders.append((kind, remaining)),
        )
        try:
            overlay._reminders_enabled = True
            with patch(
                "droid_alerts.timers.TIMER_SCHEDULE_CLOCK.current_time_seconds",
                return_value=1_000_000,
            ):
                overlay._maybe_remind("beskar", 250)
                overlay._maybe_remind("beskar", 200)
                overlay._maybe_remind("beskar", 50)
                overlay._maybe_remind("galactic", 10)
            self.assertEqual([("beskar", 250), ("beskar", 50)], reminders)
        finally:
            overlay.close()

    def test_timer_overlay_does_not_burst_missed_reminders_when_started_late(self):
        reminders = []
        overlay = DroidTimersOverlay(
            monitor=MonitorInfo(0, 0, 1920, 1080),
            reminders_enabled=False,
            reminder_rules={"beskar": [300, 60], "mythic": [], "galactic": []},
            on_reminder=lambda kind, remaining: reminders.append((kind, remaining)),
        )
        try:
            overlay._reminders_enabled = True
            with patch(
                "droid_alerts.timers.TIMER_SCHEDULE_CLOCK.current_time_seconds",
                return_value=1_000_000,
            ):
                overlay._maybe_remind("beskar", 30)
                overlay._maybe_remind("beskar", 29)
            self.assertEqual([("beskar", 30)], reminders)
        finally:
            overlay.close()

    def test_alert_customization_controllers_save_rules_and_profiles(self):
        runtime = RuntimeStub(
            AppConfig(
                sound_enabled=True,
                wake_alarm_beskar_mythic=False,
                quiet_hours_enabled=True,
                quiet_hours_start="21:30",
                quiet_hours_muted_channels=["sound", "discord"],
                discord_alert_destinations={"belt_tracker": "Belt Team"},
            )
        )
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        dashboard = DashboardController(runtime, capture)
        settings = SettingsController(runtime, dashboard)

        settings._save_alert_sounds(
            {"values": {"belt_tracker": "belt.wav", "limited_deals": "__default__"}}
        )
        settings._save_notification_profile({"name": "AFK"})
        runtime.update_config(
            sound_enabled=False,
            wake_alarm_beskar_mythic=True,
            quiet_hours_enabled=False,
            quiet_hours_start="23:00",
            discord_alert_destinations={},
        )
        settings.activateNotificationProfile("AFK")

        self.assertEqual("belt.wav", runtime.config.alert_sound_overrides["belt_tracker"])
        self.assertTrue(runtime.config.sound_enabled)
        self.assertFalse(runtime.config.wake_alarm_beskar_mythic)
        self.assertTrue(runtime.config.quiet_hours_enabled)
        self.assertEqual("21:30", runtime.config.quiet_hours_start)
        self.assertEqual(
            ["sound", "discord"], runtime.config.quiet_hours_muted_channels
        )
        self.assertEqual(
            "Belt Team", runtime.config.discord_alert_destinations["belt_tracker"]
        )
        self.assertEqual("AFK", runtime.config.active_notification_profile)
        settings.shutdown()
        dashboard.shutdown()

    def test_notification_profile_limit_is_reported_without_invalid_active_name(self):
        profiles = {f"Profile {index:02d}": {} for index in range(20)}
        runtime = RuntimeStub(AppConfig(notification_profiles=profiles))
        dashboard = DashboardController(
            runtime,
            CaptureStub(MonitorInfo(0, 0, 1920, 1080)),
        )
        settings = SettingsController(runtime, dashboard)

        settings._save_notification_profile({"name": "New Profile"})

        self.assertNotIn("New Profile", runtime.config.notification_profiles)
        self.assertNotEqual("New Profile", runtime.config.active_notification_profile)
        self.assertIn("up to 20 profiles", runtime.dialogs.state_snapshot()["message"])
        settings.shutdown()
        dashboard.shutdown()

    def test_discord_destination_can_be_created_with_a_custom_name(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        dashboard = DashboardController(runtime, capture)
        settings = SettingsController(runtime, dashboard)

        with tempfile.TemporaryDirectory() as directory, patch(
            "droid_alerts.notifications.config_dir", return_value=Path(directory)
        ):
            draft_alert = "chat:Rainbow:Mythic"
            settings._show_add_discord_destination(
                {"values": {draft_alert: "Belt Team"}}
            )
            settings._save_discord_destination(
                {
                    "name": "Belt Team",
                    "webhook": "https://discord.com/api/webhooks/1/belt",
                }
            )
            self.assertEqual(
                "https://discord.com/api/webhooks/1/belt",
                load_discord_destinations(runtime.config)["Belt Team"],
            )
            self.assertEqual("manage", runtime.dialogs.state_snapshot()["kind"])
            self.assertEqual(
                ["Belt Team"],
                [
                    option["label"]
                    for option in runtime.dialogs.state_snapshot()["options"]
                ],
            )
            runtime.dialogs.accept({})
            route_options = runtime.dialogs.state_snapshot()["options"]
            self.assertEqual(
                "Belt Team",
                next(
                    option["value"]
                    for option in route_options
                    if option["id"] == draft_alert
                ),
            )

        settings.shutdown()
        dashboard.shutdown()

    def test_discord_webhooks_require_main_webhook_before_routes(self):
        runtime = RuntimeStub(AppConfig())
        dashboard = DashboardController(
            runtime,
            CaptureStub(MonitorInfo(0, 0, 1920, 1080)),
        )
        settings = SettingsController(runtime, dashboard)

        with tempfile.TemporaryDirectory() as directory, patch(
            "droid_alerts.notifications.config_dir", return_value=Path(directory)
        ):
            settings.configureDiscordRoutes()
            state = runtime.dialogs.state_snapshot()
            self.assertEqual("form", state["kind"])
            self.assertEqual("Set Up Discord Webhooks", state["title"])
            self.assertEqual("webhook", state["fields"][0]["id"])

            runtime.dialogs.accept({"webhook": "not-a-webhook"})
            self.assertEqual(
                "That does not look like a Discord webhook URL.",
                runtime.dialogs.state_snapshot()["message"],
            )
            runtime.dialogs.accept({})
            self.assertEqual(
                "Set Up Discord Webhooks",
                runtime.dialogs.state_snapshot()["title"],
            )

            runtime.dialogs.accept(
                {"webhook": "https://discord.com/api/webhooks/1/main"}
            )

            self.assertEqual(
                "https://discord.com/api/webhooks/1/main",
                load_discord_webhook(runtime.config)[0],
            )
            self.assertTrue(runtime.config.discord_enabled)
            state = runtime.dialogs.state_snapshot()
            self.assertEqual("rules", state["kind"])
            self.assertEqual("Discord Webhooks", state["title"])

        settings.shutdown()
        dashboard.shutdown()

    def test_discord_destination_can_be_modified_and_deleted(self):
        alert_id = "chat:Rainbow:Mythic"
        runtime = RuntimeStub(
            AppConfig(discord_alert_destinations={alert_id: "Belt Team"})
        )
        dashboard = DashboardController(
            runtime,
            CaptureStub(MonitorInfo(0, 0, 1920, 1080)),
        )
        settings = SettingsController(runtime, dashboard)
        draft = {"values": {alert_id: "Belt Team"}}

        with tempfile.TemporaryDirectory() as directory, patch(
            "droid_alerts.notifications.config_dir", return_value=Path(directory)
        ):
            settings._show_add_discord_destination(draft)
            settings._save_discord_destination(
                {
                    "name": "Belt Team",
                    "webhook": "https://discord.com/api/webhooks/1/old",
                }
            )
            runtime.dialogs.action(
                {"action": "modify", "id": "named:Belt Team"}
            )
            self.assertEqual(
                "Modify Belt Team",
                runtime.dialogs.state_snapshot()["title"],
            )
            settings._save_modified_discord_destination(
                "named:Belt Team",
                {
                    "name": "Private Alerts",
                    "webhook": "https://discord.com/api/webhooks/1/new",
                },
            )

            destinations = load_discord_destinations(runtime.config)
            self.assertNotIn("Belt Team", destinations)
            self.assertEqual(
                "https://discord.com/api/webhooks/1/new",
                destinations["Private Alerts"],
            )
            self.assertEqual(
                "Private Alerts",
                runtime.config.discord_alert_destinations[alert_id],
            )
            self.assertEqual("Private Alerts", draft["values"][alert_id])

            runtime.dialogs.action(
                {"action": "delete", "id": "named:Private Alerts"}
            )
            self.assertEqual(
                "Delete Discord Webhook",
                runtime.dialogs.state_snapshot()["title"],
            )
            runtime.dialogs.accept({})

            self.assertEqual({}, load_discord_destinations(runtime.config))
            self.assertNotIn(alert_id, runtime.config.discord_alert_destinations)
            self.assertEqual("Main", draft["values"][alert_id])
            self.assertEqual("manage", runtime.dialogs.state_snapshot()["kind"])

        settings.shutdown()
        dashboard.shutdown()

    def test_limited_deal_webhook_is_managed_from_discord_routing(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        dashboard = DashboardController(runtime, capture)
        settings = SettingsController(runtime, dashboard)

        with tempfile.TemporaryDirectory() as directory, patch(
            "droid_alerts.notifications.config_dir", return_value=Path(directory)
        ):
            draft = {"values": {"limited_deals": "Main"}}
            settings._show_manage_discord_webhooks(draft)
            self.assertEqual([], runtime.dialogs.state_snapshot()["options"])

            settings._save_discord_destination(
                {
                    "name": "Main",
                    "webhook": "https://discord.com/api/webhooks/1/main",
                }
            )
            self.assertEqual(
                "https://discord.com/api/webhooks/1/main",
                load_discord_webhook(runtime.config)[0],
            )
            self.assertEqual(
                ["Main"],
                [
                    option["label"]
                    for option in runtime.dialogs.state_snapshot()["options"]
                ],
            )

            settings._save_discord_destination(
                {
                    "name": "Limited Deals",
                    "webhook": "https://discord.com/api/webhooks/2/deals",
                }
            )

            webhook, _source = load_limited_deal_discord_webhook(runtime.config)
            self.assertEqual(
                "https://discord.com/api/webhooks/2/deals",
                webhook,
            )
            self.assertEqual(
                ["Main", "Limited Deals"],
                [
                    option["label"]
                    for option in runtime.dialogs.state_snapshot()["options"]
                ],
            )

            runtime.config.discord_alert_destinations = {
                "limited_deals": "Limited Deals",
                "belt_tracker": "Limited Deals",
            }
            draft["values"] = {
                "limited_deals": "Limited Deals",
                "belt_tracker": "Limited Deals",
            }
            settings._delete_discord_destination(
                "limited_deals",
                {},
            )
            webhook, _source = load_limited_deal_discord_webhook(runtime.config)
            self.assertIsNone(webhook)
            self.assertEqual({}, runtime.config.discord_alert_destinations)
            self.assertEqual(
                {"limited_deals": "Main", "belt_tracker": "Main"},
                draft["values"],
            )

        settings.shutdown()
        dashboard.shutdown()

    def test_sound_rules_can_be_reopened_with_unsaved_draft_values(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        dashboard = DashboardController(runtime, capture)
        settings = SettingsController(runtime, dashboard)
        draft_alert = "chat:Rainbow:Mythic"

        settings._configure_alert_sounds(
            {"values": {draft_alert: "System beeps"}}
        )

        options = runtime.dialogs.state_snapshot()["options"]
        self.assertEqual(
            "System beeps",
            next(
                option["value"]
                for option in options
                if option["id"] == draft_alert
            ),
        )
        settings.shutdown()
        dashboard.shutdown()

    def test_timer_reminder_rules_accept_clear_human_times(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        dashboard = DashboardController(runtime, capture)

        dashboard._save_timer_reminder_rules(
            {"beskar": "5m, 1m, 30s", "mythic": "1h", "galactic": ""}
        )

        self.assertEqual([300, 60, 30], runtime.config.timer_reminder_rules["beskar"])
        self.assertEqual([3600], runtime.config.timer_reminder_rules["mythic"])
        self.assertEqual([], runtime.config.timer_reminder_rules["galactic"])
        dashboard.shutdown()

    def test_quiet_hours_discord_routes_and_messages_are_editable(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        dashboard = DashboardController(runtime, capture)
        settings = SettingsController(runtime, dashboard)

        settings.setQuietChannel("sound", False)
        settings.snoozeNotifications(30)
        settings._save_quiet_bypass({"selected": ["timer:galactic"]})
        settings._save_discord_routes(
            {"values": {"belt_tracker": "Belt Room", "limited_deals": "Main"}}
        )
        settings._save_discord_message_rules(
            ["belt_tracker", "limited_deals"],
            {
                "prefix": "Blueprint found",
                "mention_type": "role",
                "mention_id": "123456789012345678",
            },
        )

        self.assertNotIn("sound", runtime.config.quiet_hours_muted_channels)
        self.assertTrue(runtime.config.snoozed_until)
        self.assertEqual(["timer:galactic"], runtime.config.quiet_hours_bypass_alerts)
        self.assertEqual("Belt Room", runtime.config.discord_alert_destinations["belt_tracker"])
        self.assertEqual(
            "Main", runtime.config.discord_alert_destinations["limited_deals"]
        )
        self.assertEqual(
            "Blueprint found",
            runtime.config.discord_message_prefixes["belt_tracker"],
        )
        self.assertEqual(
            "role",
            runtime.config.discord_mentions["belt_tracker"]["type"],
        )
        self.assertEqual(
            "Blueprint found",
            runtime.config.discord_message_prefixes["limited_deals"],
        )
        settings.shutdown()
        dashboard.shutdown()

    def test_quiet_bypass_choices_replace_legacy_global_timer_alias(self):
        hidden_id = "chat:Galactic:Rare"
        runtime = RuntimeStub(
            AppConfig(
                quiet_hours_bypass_alerts=["timer_reminder", hidden_id],
            )
        )
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        dashboard = DashboardController(runtime, capture)
        settings = SettingsController(runtime, dashboard)

        settings.configureQuietBypass()
        timer_options = [
            option
            for option in runtime.dialogs.state_snapshot()["options"]
            if option["id"].startswith("timer:")
        ]
        self.assertTrue(all(option["selected"] for option in timer_options))

        settings._save_quiet_bypass(
            {"selected": ["timer:beskar", "timer:galactic"]}
        )

        self.assertEqual(
            [hidden_id, "timer:beskar", "timer:galactic"],
            runtime.config.quiet_hours_bypass_alerts,
        )
        settings.shutdown()
        dashboard.shutdown()

    def test_hidden_alert_customizations_survive_editing_visible_alerts(self):
        hidden_id = "chat:Galactic:Mythic"
        runtime = RuntimeStub(
            AppConfig(
                alert_targets=[["Rainbow", "Epic"]],
                alert_sound_overrides={hidden_id: "hidden.wav"},
                quiet_hours_bypass_alerts=[hidden_id],
                discord_alert_destinations={hidden_id: "Hidden Team"},
            )
        )
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        dashboard = DashboardController(runtime, capture)
        settings = SettingsController(runtime, dashboard)

        settings._save_alert_sounds(
            {"values": {"chat:Rainbow:Epic": "visible.wav"}}
        )
        settings._save_quiet_bypass({"selected": []})
        settings._save_discord_routes(
            {"values": {"chat:Rainbow:Epic": "Main"}}
        )

        self.assertEqual(
            "hidden.wav", runtime.config.alert_sound_overrides[hidden_id]
        )
        self.assertIn(hidden_id, runtime.config.quiet_hours_bypass_alerts)
        self.assertEqual(
            "Hidden Team", runtime.config.discord_alert_destinations[hidden_id]
        )
        settings.shutdown()
        dashboard.shutdown()

    def test_discord_message_rules_override_all_selected_or_cancel(self):
        first = "chat:Rainbow:Epic"
        second = "chat:Beskar:Mythic"
        runtime = RuntimeStub(
            AppConfig(
                discord_message_prefixes={first: "First", second: "Second"},
                discord_mentions={
                    first: {"type": "role", "id": "123456789012345678"},
                    second: {"type": "user", "id": "223456789012345678"},
                },
            )
        )
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        dashboard = DashboardController(runtime, capture)
        settings = SettingsController(runtime, dashboard)

        settings._choose_discord_message_rule(
            {"selected": [first, second]}
        )
        fields = runtime.dialogs.state_snapshot()["fields"]
        self.assertEqual(
            ["prefix", "mention_type", "mention_id"],
            [field["id"] for field in fields],
        )
        runtime.dialogs.cancel()
        self.assertEqual(
            {first: "First", second: "Second"},
            runtime.config.discord_message_prefixes,
        )
        self.assertEqual("role", runtime.config.discord_mentions[first]["type"])
        self.assertEqual("user", runtime.config.discord_mentions[second]["type"])

        settings._save_discord_message_rules(
            [first, second],
            {
                "prefix": "Priority spawn",
                "mention_type": "role",
                "mention_id": "323456789012345678",
            },
        )
        self.assertEqual(
            {first: "Priority spawn", second: "Priority spawn"},
            runtime.config.discord_message_prefixes,
        )
        self.assertEqual("role", runtime.config.discord_mentions[first]["type"])
        self.assertEqual("role", runtime.config.discord_mentions[second]["type"])
        self.assertEqual(
            "323456789012345678",
            runtime.config.discord_mentions[second]["id"],
        )

        settings._save_discord_message_rules(
            [first, second],
            {"prefix": "", "mention_type": "", "mention_id": ""},
        )
        self.assertEqual({}, runtime.config.discord_message_prefixes)
        self.assertEqual({}, runtime.config.discord_mentions)
        settings.shutdown()
        dashboard.shutdown()

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

    def test_belt_overlay_anchors_directly_to_a_window_capture_area(self):
        runtime = RuntimeStub(
            AppConfig(capture_source="window", belt_overlay_enabled=True)
        )
        capture = CaptureStub(MonitorInfo(1920, 0, 2560, 1440, index=2))
        window_source = MonitorInfo(
            2100,
            100,
            1600,
            900,
            index=2,
            key="window:test",
        )
        capture.current_belt_source = lambda **_kwargs: window_source
        with patch(
            "droid_alerts.ui.belt_controller.load_region",
            return_value=RelativeRegion(0.0, 0.0, 1.0, 1.0),
        ):
            controller = BeltController(runtime, capture, DashboardStub())
        controller._overlay_requested = True
        controller.region = PixelBox(100, 200, 800, 260)
        overlay = Mock()
        with patch(
            "droid_alerts.ui.overlays.belt_overlay",
            return_value=overlay,
        ):
            controller._update_overlay()

        monitor, region, _tracks = overlay.show_tracks.call_args.args
        self.assertEqual(window_source, monitor)
        self.assertEqual(PixelBox(100, 200, 800, 260), region)
        controller.shutdown()

    def test_belt_start_does_not_open_window_capture_on_the_ui_thread(self):
        runtime = RuntimeStub(
            AppConfig(
                capture_source="window",
                capture_window_title="Fortnite",
                capture_window_process="FortniteClient-Win64-Shipping.exe",
                capture_window_class="UnrealWindow",
            )
        )
        monitor = MonitorInfo(0, 0, 1920, 1080, key="id:monitor")
        window = MonitorInfo(0, 0, 1920, 1080, key="window:fortnite")
        capture = CaptureStub(monitor)

        def current_belt_source(*, open_device=False):
            if open_device:
                raise RuntimeError("Fortnite did not provide a capture frame")
            return window

        capture.current_belt_source = Mock(side_effect=current_belt_source)
        context = Mock()
        process = Mock()
        process.is_alive.return_value = False
        context.Event.return_value = Mock()
        context.Queue.return_value = Mock()
        context.Process.return_value = process

        with (
            patch(
                "droid_alerts.ui.belt_controller.load_region",
                return_value=RelativeRegion(0.0, 0.0, 1.0, 1.0),
            ),
            patch(
                "droid_alerts.ui.belt_controller.multiprocessing.get_context",
                return_value=context,
            ),
            patch("droid_alerts.ui.overlays.belt_overlay"),
        ):
            controller = BeltController(runtime, capture, DashboardStub())
            capture.current_belt_source.reset_mock()

            controller.startTracking()

        process.start.assert_called_once_with()
        self.assertTrue(capture.current_belt_source.call_args_list)
        self.assertTrue(
            all(
                call.kwargs.get("open_device", False) is False
                for call in capture.current_belt_source.call_args_list
            )
        )
        self.assertEqual("Running", controller.status)
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

    def test_belt_capture_change_during_startup_is_not_reported_as_a_crash(self):
        runtime = RuntimeStub(AppConfig())
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        with (
            patch(
                "droid_alerts.ui.belt_controller.load_region",
                return_value=RelativeRegion(0.0, 0.0, 1.0, 1.0),
            ),
            patch("droid_alerts.ui.belt_controller.QTimer.singleShot") as later,
            patch("droid_alerts.ui.overlays.belt_overlay"),
        ):
            controller = BeltController(runtime, capture, DashboardStub())
            process = Mock(exitcode=-15)
            process.is_alive.side_effect = [True, False]
            controller.process = process
            controller.stop_event = Mock()
            controller._worker_ready = False

            capture.sourceChanged.emit()
            controller._poll_process()

            process.terminate.assert_called_once()
            self.assertEqual("Stopped", controller.status)
            self.assertFalse(runtime.dialogs.state_snapshot()["visible"])
            later.assert_called_once_with(150, controller.startTracking)
            controller.shutdown()

    def test_belt_region_save_failure_restores_the_main_window(self):
        runtime = RuntimeStub(AppConfig())
        runtime.main_window = Mock()
        capture = CaptureStub(MonitorInfo(0, 0, 1920, 1080))
        with (
            patch(
                "droid_alerts.ui.belt_controller.load_region",
                return_value=RelativeRegion(0.0, 0.0, 1.0, 1.0),
            ),
            patch(
                "droid_alerts.ui.belt_controller.save_region",
                side_effect=OSError("disk full"),
            ),
            patch("droid_alerts.ui.overlays.belt_overlay"),
        ):
            controller = BeltController(runtime, capture, DashboardStub())
            original_region = controller.region
            controller.selector = Mock()

            controller._region_selected(
                PixelBox(10, 20, 400, 160),
                capture.monitor,
            )

            runtime.main_window.show.assert_called_once()
            runtime.main_window.raise_.assert_called_once()
            runtime.main_window.requestActivate.assert_called_once()
            self.assertIsNone(controller.selector)
            self.assertEqual(original_region, controller.region)
            self.assertEqual("Belt Region", runtime.dialogs.state_snapshot()["title"])
            self.assertIn("disk full", runtime.dialogs.state_snapshot()["message"])
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

    def test_limited_deal_is_marked_alerted_only_after_failed_channels_retry(self):
        runtime = RuntimeStub(
            AppConfig(
                discord_enabled=True,
                limited_deal_priority_alerts=[["Rainbow", "Epic"]],
            )
        )
        dashboard = DashboardStub()
        dispatches: list[dict[str, object]] = []

        def dispatch_detection(_detection, **kwargs):
            dispatches.append(kwargs)
            kwargs["on_complete"]({"Discord": len(dispatches) > 1})

        dashboard.dispatch_detection = Mock(side_effect=dispatch_detection)
        with patch.object(DealsController, "start"):
            controller = DealsController(runtime, dashboard)
        start = datetime.now(timezone.utc).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        deal = LimitedDeal(
            starts_at=start.isoformat(timespec="milliseconds").replace(
                "+00:00",
                "Z",
            ),
            ends_at=(start + timedelta(hours=1)).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
            rarity="Rainbow",
            droid="R2",
            droid_id=17,
            droid_class="Epic",
        )
        service = Mock()
        service.was_alerted.return_value = False
        controller.service = service
        controller.current_deal = deal

        with patch("droid_alerts.ui.deals_controller.append_event_safely"):
            controller._evaluate_current()
        service.mark_alerted.assert_not_called()
        self.assertTrue(controller._alert_retry_timer.isActive())
        self.assertTrue(dispatches[0]["include_local"])

        controller._alert_retry_timer.stop()
        controller._retry_pending_alert()
        service.mark_alerted.assert_called_once_with(deal)
        self.assertFalse(dispatches[1]["include_local"])
        self.assertEqual({"Discord"}, dispatches[1]["remote_channels"])
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
        self.assertIn('text: "Discord Webhooks"', dashboard)
        self.assertNotIn("Discord Webhooks & Routing", dashboard)
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
        self.assertIn("wheel.accepted = true", dialog_overlay)
        self.assertTrue(qml.joinpath("components", "NavIcon.qml").is_file())
        self.assertTrue(qml.joinpath("components", "LinkChip.qml").is_file())


if __name__ == "__main__":
    unittest.main()
