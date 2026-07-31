from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog

from ..alerts import WAKE_ALARM_FILE
from ..belt.dev_capture import export_dev_session, latest_dev_session
from ..belt.dev_logging import belt_dev_dir
from ..belt.sample_collection import belt_template_samples_dir
from ..capture import list_monitors
from ..config import (
    config_dir,
    normalize_belt_scan_fps,
    sounds_dir,
    user_sounds_dir,
)
from ..telemetry import load_or_create_anonymous_install_id
from .constants import DISCORD_COMMUNITY_URL, IDENTIFY_INSTALL_URL
from .capture_controller import CaptureController
from .dashboard_controller import DashboardController
from .overlays import RegionOutline
from .runtime import ApplicationRuntime
from .state import StateObject


BOOLEAN_FIELDS = {
    "advanced_mode",
    "start_watcher_on_launch",
    "update_check_enabled",
    "wake_alarm_enabled",
    "wake_alarm_beskar_mythic",
    "wake_alarm_galactic_mythic",
    "save_alert_samples",
    "save_debug_screenshots",
    "share_debug_detections",
    "ntfy_include_attachment",
    "phone_include_attachment",
    "belt_dev_mode",
    "belt_template_collection_enabled",
    "rebirth_ready_alert_enabled",
    "scrap_alert_enabled",
    "cb23_mission_alert_enabled",
    "rebirth_alert_enabled",
    "timer_reminders_enabled",
}

FLOAT_RANGES = {
    "capture_interval_seconds": (0.05, None),
    "rebirth_scan_interval_seconds": (2.0, 30.0),
    "dedupe_seconds": (0.0, None),
    "alert_cooldown_seconds": (0.0, None),
    "popup_seconds": (0.5, None),
    "popup_scale": (0.7, 1.5),
    "popup_opacity": (0.55, 1.0),
}

INT_RANGES = {
    "validation_failures_before_calibration_prompt": (1, None),
    "retention_days": (0, None),
    "max_storage_mb": (0, None),
    "timer_reminder_seconds": (1, None),
    "timer_offset_seconds": (-3600, 3600),
    "belt_idle_scan_fps": (1, 20),
    "belt_active_scan_fps": (1, 20),
}

STRING_FIELDS = {
    "sound_file",
    "popup_position",
    "ntfy_server_url",
    "ntfy_topic",
    "ntfy_priority",
    "ntfy_tags",
    "phone_sound",
    "update_repo",
}


class SettingsController(StateObject):
    """Exposes app settings to QML."""

    storageSettingsChanged = Signal()

    def __init__(
        self,
        runtime: ApplicationRuntime,
        dashboard: DashboardController,
        capture: CaptureController | None = None,
        *,
        parent: QObject | None = None,
    ) -> None:
        self.runtime = runtime
        self.dashboard = dashboard
        self.capture = capture
        self._display_labels: list[RegionOutline] = []
        super().__init__({}, parent=parent)
        runtime.configChanged.connect(self.refresh)
        runtime.register_shutdown(self.shutdown)
        self.refresh()

    def _sound_choices(self) -> list[dict[str, str]]:
        names: set[str] = set()
        for folder in (user_sounds_dir(), sounds_dir()):
            if not folder.exists():
                continue
            names.update(
                path.name
                for path in folder.glob("*.wav")
                if path.is_file()
                and path.name.casefold() != WAKE_ALARM_FILE.casefold()
            )
        return [
            {"id": name, "label": name}
            for name in ("System beeps", *sorted(names, key=str.casefold))
        ]

    @Slot()
    def refresh(self) -> None:
        config = self.runtime.config
        values = {
            name: getattr(config, name)
            for name in BOOLEAN_FIELDS | FLOAT_RANGES.keys() | INT_RANGES.keys() | STRING_FIELDS
        }
        values["popup_position"] = config.popup_position
        values["share_debug_detections"] = (
            config.save_debug_screenshots and config.share_debug_detections
        )
        values["timer_reminders_enabled"] = (
            config.droid_timers_enabled and config.timer_reminders_enabled
        )
        self.replace_state(
            {
                "values": values,
                "advanced": config.advanced_mode,
                "soundChoices": self._sound_choices(),
                "popupPositions": [
                    {"id": "top_center", "label": "Top center"},
                    {"id": "top_left", "label": "Top left"},
                    {"id": "top_right", "label": "Top right"},
                    {"id": "bottom_left", "label": "Bottom left"},
                    {"id": "bottom_right", "label": "Bottom right"},
                ],
                "alarmActive": self.dashboard.wake_alarm.active,
            }
        )

    @Slot(str, "QVariant")
    def setValue(self, key: str, value: Any) -> None:
        try:
            if key in BOOLEAN_FIELDS:
                normalized: Any = bool(value)
                if key == "share_debug_detections" and not self.runtime.config.save_debug_screenshots:
                    normalized = False
                if key == "timer_reminders_enabled" and not self.runtime.config.droid_timers_enabled:
                    normalized = False
            elif key in FLOAT_RANGES:
                low, high = FLOAT_RANGES[key]
                normalized = max(low, float(value))
                if high is not None:
                    normalized = min(high, normalized)
            elif key in INT_RANGES:
                low, high = INT_RANGES[key]
                normalized = max(low, int(float(value)))
                if high is not None:
                    normalized = min(high, normalized)
            elif key in STRING_FIELDS:
                normalized = str(value).strip()
                if key == "popup_position" and normalized not in {
                    "top_center",
                    "top_left",
                    "top_right",
                    "bottom_left",
                    "bottom_right",
                }:
                    normalized = "top_center"
                if key == "sound_file" and normalized == "System beeps":
                    normalized = ""
                if key == "ntfy_server_url":
                    normalized = normalized.rstrip("/") or "https://ntfy.sh"
                if key == "update_repo":
                    normalized = normalized or "DogifiedV2/droidalerts"
            else:
                return
        except (TypeError, ValueError):
            self.runtime.dialogs.show_message(
                "Settings",
                f"Invalid value for {key.replace('_', ' ')}.",
                tone="danger",
            )
            self.refresh()
            return
        changes = {key: normalized}
        if key == "save_debug_screenshots" and not normalized:
            changes["share_debug_detections"] = False
        if key in {"belt_idle_scan_fps", "belt_active_scan_fps"}:
            idle = (
                normalized
                if key == "belt_idle_scan_fps"
                else self.runtime.config.belt_idle_scan_fps
            )
            active = (
                normalized
                if key == "belt_active_scan_fps"
                else self.runtime.config.belt_active_scan_fps
            )
            idle, active = normalize_belt_scan_fps(idle, active)
            changes.update(
                belt_idle_scan_fps=idle,
                belt_active_scan_fps=active,
            )
        self.runtime.update_config(**changes)
        if (
            key
            in {
                "timer_reminders_enabled",
                "timer_reminder_seconds",
                "timer_offset_seconds",
            }
            and self.runtime.config.droid_timers_enabled
        ):
            self.dashboard.setTimersEnabled(True)
        if key in {"retention_days", "max_storage_mb"}:
            self.storageSettingsChanged.emit()
        self.refresh()

    @Slot()
    def addAlertSound(self) -> None:
        source, _selected = QFileDialog.getOpenFileName(
            None,
            "Add alert sound",
            "",
            "WAV audio (*.wav)",
        )
        if not source:
            return
        path = Path(source)
        if path.suffix.lower() != ".wav":
            self.runtime.dialogs.show_message(
                "Alert Sound",
                "Droid Alerts currently supports WAV files.",
                tone="danger",
            )
            return
        try:
            folder = user_sounds_dir()
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / path.name
            if path.resolve() != target.resolve():
                shutil.copy2(path, target)
        except OSError as exc:
            self.runtime.dialogs.show_message("Alert Sound", str(exc), tone="danger")
            return
        self.runtime.update_config(sound_file=target.name)
        self.runtime.detailChanged.emit(f"Alert sound added: {target.name}")
        self.refresh()

    @Slot()
    def showPrivacy(self) -> None:
        self.runtime.dialogs.show_message(
            "Privacy Details",
            "Detection runs locally from pixels on the selected display.",
            note=(
                "Droid Alerts sends anonymous app, watcher and Belt Tracker heartbeats. "
                "Automatic telemetry never includes chat text, credentials, player or machine "
                "names, or screenshots. Debug screenshots are shared only when that separate "
                "option is enabled. Support bundles redact notification details."
            ),
        )

    @Slot()
    def identifyInstall(self) -> None:
        install_id = load_or_create_anonymous_install_id()
        self.runtime.dialogs.confirm(
            "Identify This Install",
            f"Install ID:\n{install_id}",
            note="Identification is optional and visible only to the developer.",
            link=("Open Identification Page", IDENTIFY_INSTALL_URL),
            accept_text="Copy install ID",
            callback=lambda payload: (
                self._copy_install_id(install_id) if payload is not None else None
            ),
        )

    def _copy_install_id(self, install_id: str) -> None:
        QGuiApplication.clipboard().setText(install_id)
        self.runtime.detailChanged.emit("Install ID copied")

    @Slot()
    def showFaq(self) -> None:
        if sys.platform == "win32":
            clock = (
                "Timers out of sync? Open Windows Settings, then Time & language, "
                "Date & time, and press Sync now."
            )
        elif sys.platform == "darwin":
            clock = (
                "Timers out of sync? Open System Settings, General, Date & Time, "
                "and enable automatic time."
            )
        else:
            clock = "Timers out of sync? Enable automatic time in system settings."
        self.runtime.dialogs.show_message(
            "FAQ",
            clock,
            note=(
                "For missed detections, verify the capture source and use Show Chat Region. "
                "For support, create a redacted Support Bundle in Diagnostics."
            ),
        )

    @Slot()
    def openDiscord(self) -> None:
        self.runtime.open_url(DISCORD_COMMUNITY_URL)

    @Slot(str)
    def openPath(self, name: str) -> None:
        path = {
            "config": config_dir() / "config.json",
            "belt_logs": belt_dev_dir(),
            "belt_samples": belt_template_samples_dir(),
        }.get(name)
        if path is not None:
            self.runtime.open_path(path)

    @Slot()
    def exportBeltCollection(self) -> None:
        session = latest_dev_session(belt_dev_dir())
        if session is None:
            self.runtime.dialogs.show_message(
                "Export Blueprint Collection",
                "No Blueprint Collection session has been recorded yet.",
            )
            return
        try:
            manifest = json.loads(
                (session / "capture_manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            manifest = {}
        if not isinstance(manifest, dict) or not manifest.get("stopped_at"):
            self.runtime.dialogs.show_message(
                "Export Blueprint Collection",
                "Stop Belt Tracker before exporting so every crop is included.",
            )
            return
        try:
            output = export_dev_session(session)
        except Exception as exc:
            self.runtime.dialogs.show_message(
                "Export Blueprint Collection",
                str(exc),
                tone="danger",
            )
            return
        self.runtime.dialogs.show_message(
            "Blueprint Collection Exported",
            "The latest collection is ready to share.",
            note=str(output),
        )
        self.runtime.open_path(output.parent)

    @Slot()
    def identifyDisplays(self) -> None:
        try:
            monitors = list_monitors()
        except Exception as exc:
            self.runtime.dialogs.show_message(
                "Identify Displays", str(exc), tone="danger"
            )
            return
        self._close_display_labels()
        for monitor in monitors:
            label = RegionOutline()
            label._color.setNamedColor("#39c6d8")
            width = min(420, max(280, monitor.width // 3))
            height = 150
            left = monitor.left + max(0, (monitor.width - width) // 2)
            top = monitor.top + max(0, (monitor.height - height) // 2)
            label.show_region(
                left,
                top,
                width,
                height,
                f"MONITOR {monitor.index} · {monitor.width} × {monitor.height}"
                + (" · Primary" if monitor.is_primary else ""),
            )
            self._display_labels.append(label)
        QTimer.singleShot(3000, self._close_display_labels)
        self.runtime.detailChanged.emit("Display numbers shown for 3 seconds")

    def _close_display_labels(self) -> None:
        for label in self._display_labels:
            label.close()
        self._display_labels = []

    @Slot()
    def refreshDisplayLayout(self) -> None:
        if self.capture is not None:
            self.capture.refreshDisplayGeometry(False)

    @Slot()
    def testWakeAlarm(self) -> None:
        self.dashboard.testWakeAlarm()
        self.refresh()

    @Slot()
    def stopWakeAlarm(self) -> None:
        self.dashboard.stopWakeAlarm()
        self.refresh()

    def shutdown(self) -> None:
        self._close_display_labels()
