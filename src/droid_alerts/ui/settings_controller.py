from __future__ import annotations

import json
import shutil
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog

from ..alert_customization import (
    ALERT_CHANNELS,
    MAX_NOTIFICATION_PROFILES,
    PROFILE_FIELDS,
    alert_id_aliases,
    normalize_clock,
    normalize_discord_alert_destinations,
    normalize_discord_mentions,
    normalize_discord_message_prefixes,
    normalize_notification_profiles,
)
from ..alerts import WAKE_ALARM_FILE
from ..belt.dev_capture import export_dev_session, latest_dev_session
from ..belt.dev_logging import belt_dev_dir
from ..belt.sample_collection import belt_template_samples_dir
from ..capture import list_monitors
from ..config import (
    AppConfig,
    config_dir,
    normalize_belt_scan_fps,
    sounds_dir,
    user_sounds_dir,
)
from ..notifications import (
    discord_destination_names,
    load_discord_destinations,
    load_discord_webhook,
    load_limited_deal_discord_webhook,
    save_discord_destinations,
    save_discord_webhook,
    save_limited_deal_discord_webhook,
    valid_discord_webhook_url,
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
    "quiet_hours_enabled",
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
    "quiet_hours_start",
    "quiet_hours_end",
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
        self._discord_route_draft: dict[str, Any] | None = None
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
        profile_names = list(config.notification_profiles)
        active_profile = (
            config.active_notification_profile
            if config.active_notification_profile in config.notification_profiles
            else ""
        )
        ordered_profiles = (
            [active_profile] + [name for name in profile_names if name != active_profile]
            if active_profile
            else profile_names
        )
        snoozed_until = config.snoozed_until
        try:
            snoozed = datetime.fromisoformat(snoozed_until) if snoozed_until else None
        except ValueError:
            snoozed = None
        if snoozed is not None and snoozed.tzinfo is None:
            snoozed = snoozed.astimezone()
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
                "profileChoices": [
                    {"id": name, "label": name} for name in ordered_profiles
                ],
                "activeProfile": active_profile or "No active profile",
                "quietChannels": {
                    channel: channel in config.quiet_hours_muted_channels
                    for channel in ALERT_CHANNELS
                },
                "snoozeStatus": (
                    f"Snoozed until {snoozed.astimezone().strftime('%H:%M')}"
                    if snoozed is not None
                    and snoozed > datetime.now().astimezone(snoozed.tzinfo)
                    else "Not snoozed"
                ),
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
                if key == "quiet_hours_start":
                    normalized = normalize_clock(normalized, "23:00")
                if key == "quiet_hours_end":
                    normalized = normalize_clock(normalized, "08:00")
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
        self._add_alert_sound(reopen_rules=False)

    def _add_alert_sound(
        self,
        *,
        reopen_rules: bool,
        draft_values: dict[str, Any] | None = None,
    ) -> None:
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
        if not reopen_rules:
            self.runtime.update_config(sound_file=target.name)
        self.runtime.detailChanged.emit(f"Alert sound added: {target.name}")
        self.refresh()
        if reopen_rules:
            self._configure_alert_sounds(draft_values)

    def _alert_options(self, channel: str = "discord") -> list[dict[str, Any]]:
        _label, options = self.dashboard._channel_alert_options(channel)
        return options

    @Slot()
    def configureAlertSounds(self) -> None:
        self._configure_alert_sounds(None)

    def _configure_alert_sounds(
        self,
        draft_values: dict[str, Any] | None,
    ) -> None:
        overrides = self.runtime.config.alert_sound_overrides
        draft = (
            draft_values.get("values", {})
            if isinstance(draft_values, dict)
            else {}
        )
        self.runtime.dialogs.rules(
            "Sounds by Alert",
            "Choose a sound for each alert, or keep the global default.",
            [
                {
                    "id": option["id"],
                    "label": option["label"],
                    "value": draft.get(
                        str(option["id"]),
                        overrides.get(str(option["id"]), "__default__"),
                    ),
                }
                for option in self._alert_options("sound")
            ],
            [
                {"id": "__default__", "label": "Global default"},
                *self._sound_choices(),
            ],
            action_text="Add WAV",
            action_callback=lambda payload: self._add_alert_sound(
                reopen_rules=True,
                draft_values=payload,
            ),
            callback=self._save_alert_sounds,
        )

    def _save_alert_sounds(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        values = payload.get("values", {})
        if not isinstance(values, dict):
            return
        overrides = dict(self.runtime.config.alert_sound_overrides)
        for alert_id, sound in values.items():
            alert_id = str(alert_id)
            sound = str(sound)
            if sound == "__default__":
                overrides.pop(alert_id, None)
            else:
                overrides[alert_id] = sound
        self.runtime.update_config(alert_sound_overrides=overrides)
        self.refresh()

    @Slot()
    def saveNotificationProfile(self) -> None:
        self.runtime.dialogs.form(
            "Save Notification Profile",
            "Save the current channels, routing, sounds, popup, alarm and timer settings.",
            [
                {
                    "id": "name",
                    "label": "Profile name",
                    "value": self.runtime.config.active_notification_profile,
                    "password": False,
                }
            ],
            callback=self._save_notification_profile,
        )

    def _save_notification_profile(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        name = " ".join(str(payload.get("name") or "").split())[:40]
        if not name:
            self.runtime.dialogs.show_message(
                "Notification Profile",
                "Enter a profile name.",
                tone="danger",
            )
            return
        profiles = deepcopy(self.runtime.config.notification_profiles)
        if name not in profiles and len(profiles) >= MAX_NOTIFICATION_PROFILES:
            self.runtime.dialogs.show_message(
                "Notification Profile",
                f"You can save up to {MAX_NOTIFICATION_PROFILES} profiles. "
                "Delete one before creating another.",
                tone="danger",
            )
            return
        profiles[name] = {
            field: deepcopy(getattr(self.runtime.config, field))
            for field in PROFILE_FIELDS
        }
        normalized_profiles = normalize_notification_profiles(profiles)
        self.runtime.update_config(
            notification_profiles=normalized_profiles,
            active_notification_profile=name,
        )
        self.refresh()

    @Slot(str)
    def activateNotificationProfile(self, name: str) -> None:
        values = self.runtime.config.notification_profiles.get(name)
        if not isinstance(values, dict):
            return
        merged = self.runtime.config.to_dict()
        merged.update(deepcopy(values))
        normalized = AppConfig.from_dict(merged)
        changes = {
            field: deepcopy(getattr(normalized, field))
            for field in PROFILE_FIELDS
        }
        changes["active_notification_profile"] = name
        self.runtime.update_config(**changes)
        self.dashboard.setTimersEnabled(self.runtime.config.droid_timers_enabled)
        self.refresh()

    @Slot()
    def deleteNotificationProfile(self) -> None:
        name = self.runtime.config.active_notification_profile
        if not name:
            return
        profiles = dict(self.runtime.config.notification_profiles)
        profiles.pop(name, None)
        self.runtime.update_config(
            notification_profiles=profiles,
            active_notification_profile="",
        )
        self.refresh()

    @Slot(str, bool)
    def setQuietChannel(self, channel: str, muted: bool) -> None:
        channel = channel.casefold()
        if channel not in ALERT_CHANNELS:
            return
        channels = set(self.runtime.config.quiet_hours_muted_channels)
        if muted:
            channels.add(channel)
        else:
            channels.discard(channel)
        self.runtime.update_config(
            quiet_hours_muted_channels=[
                value for value in ALERT_CHANNELS if value in channels
            ]
        )
        self.refresh()

    @Slot(int)
    def snoozeNotifications(self, minutes: int) -> None:
        if minutes <= 0:
            self.runtime.update_config(snoozed_until="")
        else:
            until = datetime.now().astimezone() + timedelta(
                minutes=min(24 * 60, int(minutes))
            )
            self.runtime.update_config(
                snoozed_until=until.isoformat(timespec="seconds")
            )
        self.refresh()

    @Slot()
    def configureQuietBypass(self) -> None:
        bypass = set(self.runtime.config.quiet_hours_bypass_alerts)
        self.runtime.dialogs.choices(
            "Quiet Hours Bypass",
            "Selected alerts can still notify during quiet hours and snooze.",
            [
                {
                    "id": option["id"],
                    "label": option["label"],
                    "selected": (
                        str(option["id"]) in bypass
                        or any(
                            alias in bypass
                            for alias in alert_id_aliases(str(option["id"]))
                        )
                    ),
                }
                for option in self._alert_options()
            ],
            multi=True,
            callback=self._save_quiet_bypass,
        )

    def _save_quiet_bypass(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        visible_ids = {
            str(option["id"])
            for option in self._alert_options()
        }
        legacy_ids = {
            alias
            for alert_id in visible_ids
            for alias in alert_id_aliases(alert_id)
        }
        selected = {str(value) for value in payload.get("selected", [])}
        bypass = (
            set(self.runtime.config.quiet_hours_bypass_alerts)
            - visible_ids
            - legacy_ids
        ) | (selected & visible_ids)
        self.runtime.update_config(
            quiet_hours_bypass_alerts=sorted(bypass)
        )
        self.refresh()

    @Slot()
    def configureDiscordRoutes(self) -> None:
        self._configure_discord_routes(None)

    def _configure_discord_routes(
        self,
        draft_values: dict[str, Any] | None,
    ) -> None:
        try:
            destinations = discord_destination_names(self.runtime.config)
        except Exception as exc:
            self.runtime.dialogs.show_message("Discord Routing", str(exc), tone="danger")
            return
        routes = self.runtime.config.discord_alert_destinations
        draft = (
            draft_values.get("values", {})
            if isinstance(draft_values, dict)
            else {}
        )
        has_limited_destination = "Limited Deals" in destinations
        self.runtime.dialogs.rules(
            "Discord Routing",
            "Optional: use different webhooks for different alerts.",
            [
                {
                    "id": option["id"],
                    "label": option["label"],
                    "value": draft.get(
                        str(option["id"]),
                        routes.get(
                            str(option["id"]),
                            (
                                "Limited Deals"
                                if str(option["id"]) == "limited_deals"
                                and has_limited_destination
                                else "Main"
                            ),
                        ),
                    ),
                }
                for option in self._alert_options()
            ],
            [{"id": name, "label": name} for name in destinations],
            note="Create a named webhook, then assign that name to one or more alerts.",
            action_text="Manage webhooks",
            action_callback=self._show_manage_discord_webhooks,
            callback=self._save_discord_routes,
        )

    def _show_manage_discord_webhooks(self, draft: dict[str, Any]) -> None:
        self._discord_route_draft = draft
        try:
            main_webhook, _ = load_discord_webhook(self.runtime.config)
            limited_webhook, _ = load_limited_deal_discord_webhook(
                self.runtime.config
            )
        except Exception as exc:
            self.runtime.dialogs.show_message(
                "Discord Webhooks",
                str(exc),
                tone="danger",
                callback=lambda _payload: self._configure_discord_routes(draft),
            )
            return
        self.runtime.dialogs.choices(
            "Discord Webhooks",
            "Choose a webhook to configure, or add a named webhook.",
            [
                {
                    "id": "main",
                    "label": "Main webhook",
                    "detail": "Configured" if main_webhook else "Not configured",
                    "selected": False,
                },
                {
                    "id": "limited_deals",
                    "label": "Limited Deals webhook",
                    "detail": "Configured" if limited_webhook else "Optional",
                    "selected": False,
                },
                {
                    "id": "add_named",
                    "label": "Add named webhook",
                    "detail": "Create another destination for selected alerts",
                    "selected": False,
                },
            ],
            accept_text="Continue",
            callback=self._choose_discord_webhook_action,
        )

    def _choose_discord_webhook_action(
        self,
        payload: dict[str, Any] | None,
    ) -> None:
        draft = self._discord_route_draft or {}
        if payload is None:
            self._configure_discord_routes(draft)
            return
        selected = str(payload.get("selected") or "")
        if selected == "add_named":
            self._show_add_discord_destination(draft)
        elif selected in {"main", "limited_deals"}:
            self._show_builtin_discord_webhook(selected, draft)
        else:
            self._show_manage_discord_webhooks(draft)

    def _show_builtin_discord_webhook(
        self,
        destination: str,
        draft: dict[str, Any],
    ) -> None:
        self._discord_route_draft = draft
        try:
            if destination == "main":
                current, _ = load_discord_webhook(self.runtime.config)
                title = "Main Discord Webhook"
                message = "Set the default webhook used by Discord alerts."
                note = "The Main webhook is required when an alert has no other destination."
            else:
                current, _ = load_limited_deal_discord_webhook(
                    self.runtime.config
                )
                title = "Limited Deals Webhook"
                message = "Optionally send Limited Deal alerts to a separate webhook."
                note = "Leave the URL blank and save to remove this dedicated webhook."
        except Exception as exc:
            self.runtime.dialogs.show_message(
                "Discord Webhooks",
                str(exc),
                tone="danger",
                callback=lambda _payload: self._show_manage_discord_webhooks(draft),
            )
            return
        self.runtime.dialogs.form(
            title,
            message,
            [
                {
                    "id": "webhook",
                    "label": "Webhook URL",
                    "value": current or "",
                    "password": True,
                }
            ],
            note=note,
            callback=lambda values: self._save_builtin_discord_webhook(
                destination,
                values,
            ),
        )

    def _save_builtin_discord_webhook(
        self,
        destination: str,
        payload: dict[str, Any] | None,
    ) -> None:
        draft = self._discord_route_draft or {}
        if payload is None:
            self._show_manage_discord_webhooks(draft)
            return
        webhook = str(payload.get("webhook") or "").strip().lstrip("\ufeff")
        if destination == "main" and not webhook:
            self.runtime.dialogs.show_message(
                "Discord Webhooks",
                "Enter the Main Discord webhook URL.",
                tone="danger",
                callback=lambda _payload: self._show_builtin_discord_webhook(
                    destination,
                    draft,
                ),
            )
            return
        if webhook and not valid_discord_webhook_url(webhook):
            self.runtime.dialogs.show_message(
                "Discord Webhooks",
                "That does not look like a Discord webhook URL.",
                tone="danger",
                callback=lambda _payload: self._show_builtin_discord_webhook(
                    destination,
                    draft,
                ),
            )
            return
        try:
            if destination == "main":
                save_discord_webhook(self.runtime.config, webhook)
                self.runtime.update_config(discord_enabled=True)
                detail = "Main Discord webhook saved"
            else:
                save_limited_deal_discord_webhook(self.runtime.config, webhook)
                detail = (
                    "Limited Deals webhook saved"
                    if webhook
                    else "Limited Deals webhook removed"
                )
        except OSError as exc:
            self.runtime.dialogs.show_message(
                "Discord Webhooks",
                str(exc),
                tone="danger",
                callback=lambda _payload: self._show_builtin_discord_webhook(
                    destination,
                    draft,
                ),
            )
            return

        if destination == "limited_deals":
            values = draft.get("values")
            if isinstance(values, dict):
                if webhook:
                    if (
                        "limited_deals"
                        not in self.runtime.config.discord_alert_destinations
                    ):
                        values["limited_deals"] = "Limited Deals"
                else:
                    for alert_id, assigned in list(values.items()):
                        if str(assigned).casefold() == "limited deals":
                            values[alert_id] = "Main"
            if not webhook:
                routes = {
                    alert_id: assigned
                    for alert_id, assigned in (
                        self.runtime.config.discord_alert_destinations.items()
                    )
                    if assigned.casefold() != "limited deals"
                }
                self.runtime.update_config(discord_alert_destinations=routes)

        self.runtime.detailChanged.emit(detail)
        self._configure_discord_routes(draft)

    def _show_add_discord_destination(self, draft: dict[str, Any]) -> None:
        self._discord_route_draft = draft
        self.runtime.dialogs.form(
            "Add Discord Webhook",
            "Give this webhook a clear name. You can assign it to alerts next.",
            [
                {
                    "id": "name",
                    "label": "Webhook name",
                    "value": "",
                    "password": False,
                },
                {
                    "id": "webhook",
                    "label": "Webhook URL",
                    "value": "",
                    "password": True,
                },
            ],
            callback=self._save_discord_destination,
        )

    def _save_discord_destination(self, payload: dict[str, Any] | None) -> None:
        draft = self._discord_route_draft
        self._discord_route_draft = None
        if payload is None:
            self._configure_discord_routes(draft)
            return
        name = " ".join(str(payload.get("name") or "").split())[:64]
        webhook = str(payload.get("webhook") or "").strip().lstrip("\ufeff")
        if not name or name.casefold() in {"main", "limited deals"}:
            self.runtime.dialogs.show_message(
                "Discord Routing",
                "Enter a unique name other than Main or Limited Deals.",
                tone="danger",
                callback=lambda _payload: self._configure_discord_routes(draft),
            )
            return
        if not valid_discord_webhook_url(webhook):
            self.runtime.dialogs.show_message(
                "Discord Routing",
                "That does not look like a Discord webhook URL.",
                tone="danger",
                callback=lambda _payload: self._configure_discord_routes(draft),
            )
            return
        try:
            destinations = load_discord_destinations(self.runtime.config)
            destinations = {
                existing_name: url
                for existing_name, url in destinations.items()
                if existing_name.casefold() != name.casefold()
            }
            destinations[name] = webhook
            save_discord_destinations(self.runtime.config, destinations)
        except (OSError, ValueError) as exc:
            self.runtime.dialogs.show_message(
                "Discord Routing",
                str(exc),
                tone="danger",
                callback=lambda _payload: self._configure_discord_routes(draft),
            )
            return
        self.runtime.detailChanged.emit(f"Discord webhook added: {name}")
        self._configure_discord_routes(draft)

    def _save_discord_routes(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        values = payload.get("values", {})
        values = normalize_discord_alert_destinations(values)
        routes = dict(self.runtime.config.discord_alert_destinations)
        for alert_id, destination in values.items():
            if destination.casefold() == "main" and alert_id != "limited_deals":
                routes.pop(alert_id, None)
            else:
                routes[alert_id] = destination
        self.runtime.update_config(discord_alert_destinations=routes)
        self.refresh()

    @Slot()
    def configureDiscordMessages(self) -> None:
        self.runtime.dialogs.choices(
            "Discord Message Rules",
            "Select one or more alerts to edit together.",
            [
                {
                    "id": option["id"],
                    "label": option["label"],
                    "selected": False,
                }
                for option in self._alert_options()
            ],
            multi=True,
            accept_text="Edit",
            callback=self._choose_discord_message_rule,
        )

    def _choose_discord_message_rule(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        selected = payload.get("selected", [])
        if isinstance(selected, str):
            selected = [selected]
        alert_ids = [str(alert_id) for alert_id in selected if str(alert_id)]
        if not alert_ids:
            return
        prefixes = {
            self.runtime.config.discord_message_prefixes.get(alert_id, "")
            for alert_id in alert_ids
        }
        mentions = {
            (
                self.runtime.config.discord_mentions.get(alert_id, {}).get("type", ""),
                self.runtime.config.discord_mentions.get(alert_id, {}).get("id", ""),
            )
            for alert_id in alert_ids
        }
        shared_prefix = len(prefixes) == 1
        shared_mention = len(mentions) == 1
        prefix = prefixes.pop() if shared_prefix else ""
        mention_type, mention_id = mentions.pop() if shared_mention else ("", "")
        self.runtime.dialogs.form(
            "Discord Message Rules",
            f"Choose which settings to change for {len(alert_ids)} selected alert(s). Existing mixed values are kept unless you replace or remove them.",
            [
                {
                    "id": "prefix_action",
                    "label": "Prefix change",
                    "value": "set" if shared_prefix else "keep",
                    "choices": [
                        {"id": "keep", "label": "Keep each current prefix"},
                        {"id": "set", "label": "Use prefix below"},
                        {"id": "clear", "label": "Remove prefix"},
                    ],
                },
                {
                    "id": "prefix",
                    "label": "Message prefix (optional)",
                    "value": prefix,
                    "password": False,
                },
                {
                    "id": "mention_action",
                    "label": "Mention change",
                    "value": "set" if shared_mention and mention_type else "keep",
                    "choices": [
                        {"id": "keep", "label": "Keep each current mention"},
                        {"id": "set", "label": "Use mention below"},
                        {"id": "clear", "label": "Remove mention"},
                    ],
                },
                {
                    "id": "mention_type",
                    "label": "Who should Discord mention?",
                    "value": mention_type,
                    "choices": [
                        {"id": "", "label": "No mention"},
                        {"id": "user", "label": "User"},
                        {"id": "role", "label": "Role"},
                    ],
                },
                {
                    "id": "mention_id",
                    "label": "User or role ID",
                    "value": mention_id,
                    "password": False,
                },
            ],
            callback=lambda values: self._save_discord_message_rules(alert_ids, values),
        )

    def _save_discord_message_rules(
        self,
        alert_ids: list[str],
        payload: dict[str, Any] | None,
    ) -> None:
        if payload is None:
            return
        prefix_action = str(
            payload.get(
                "prefix_action",
                "set" if "prefix" in payload else "keep",
            )
        ).casefold()
        mention_action = str(
            payload.get(
                "mention_action",
                "set"
                if "mention_type" in payload or "mention_id" in payload
                else "keep",
            )
        ).casefold()
        mention_type = str(payload.get("mention_type") or "").strip().casefold()
        mention_id = str(payload.get("mention_id") or "").strip()
        if prefix_action not in {"keep", "set", "clear"} or mention_action not in {
            "keep",
            "set",
            "clear",
        }:
            return
        if mention_action == "set":
            if (
                mention_type not in {"user", "role"}
                or not mention_id.isdigit()
                or not 15 <= len(mention_id) <= 22
            ):
                self.runtime.dialogs.show_message(
                    "Discord Message Rules",
                    "Choose User or Role and enter its numeric Discord ID.",
                    tone="danger",
                )
                return
        prefixes = dict(self.runtime.config.discord_message_prefixes)
        prefix = " ".join(str(payload.get("prefix") or "").split())[:160]
        mentions = dict(self.runtime.config.discord_mentions)
        for alert_id in alert_ids:
            if prefix_action == "set":
                if prefix:
                    prefixes[alert_id] = prefix
                else:
                    prefixes.pop(alert_id, None)
            elif prefix_action == "clear":
                prefixes.pop(alert_id, None)
            if mention_action == "set":
                mentions[alert_id] = {"type": mention_type, "id": mention_id}
            elif mention_action == "clear":
                mentions.pop(alert_id, None)
        self.runtime.update_config(
            discord_message_prefixes=normalize_discord_message_prefixes(prefixes),
            discord_mentions=normalize_discord_mentions(mentions),
        )
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
