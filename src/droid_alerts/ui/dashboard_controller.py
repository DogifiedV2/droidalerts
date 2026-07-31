from __future__ import annotations

import threading
import time
from datetime import datetime
from functools import partial
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..alert_customization import alert_id_aliases
from ..alerts import AlertPolicy, WakeAlarm
from ..alert_delivery import (
    build_delivery_event,
    execute_alert_delivery,
    persist_delivery_event,
)
from ..classifier import Detection
from ..config import AppConfig
from ..notifications import (
    discord_webhook_configured,
    enabled_alert_deliveries,
    alert_type_id,
    event_text,
    load_discord_webhook,
    load_discord_webhook_for_detection,
    load_ntfy_token,
    load_phone_alert_credentials,
    ntfy_configured,
    phone_alerts_configured,
    save_discord_webhook,
    save_ntfy_token,
    save_phone_credentials,
    send_discord_alert,
    send_ntfy_alert,
    send_phone_alert,
    valid_discord_webhook_url,
    valid_ntfy_server_url,
    valid_ntfy_topic,
)
from ..popup import popup_icon_path, show_popup
from ..timers import (
    DISPLAY_TIMER_ORDER,
    TIMER_COLORS,
    TIMER_PERIOD_SECONDS,
    adjust_droid_timers,
    format_countdown,
    hide_droid_timers,
    seconds_until_next,
    show_droid_timers,
    timer_reminder_detection,
)
from ..watcher import run_watch
from .capture_controller import CaptureController
from .constants import ALERT_COMBOS
from .runtime import ApplicationRuntime
from .state import StateObject


class DashboardController(StateObject):
    """Runs chat monitoring and dashboard actions."""

    statusChanged = Signal(str)
    historyChanged = Signal()

    def __init__(
        self,
        runtime: ApplicationRuntime,
        capture: CaptureController,
        *,
        parent: QObject | None = None,
    ) -> None:
        self.runtime = runtime
        self.capture = capture
        self.watch_thread: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self._watch_started: float | None = None
        self._monitoring_seconds = 0.0
        self._status = "Stopped"
        self._channel_status: dict[str, str] = {
            "Popup": "",
            "Sound": "",
            "Discord": "",
            "ntfy": "",
            "Pushover": "",
        }
        self._scans = 0
        self._alerts = 0
        self._last_alert = "No priority alerts this session"
        self._restart_after_capture_change = False
        self.wake_alarm = WakeAlarm()
        self._alarm_timer = QTimer()
        self._alarm_timer.setSingleShot(True)
        self._alarm_timer.timeout.connect(self.stopWakeAlarm)
        super().__init__({}, parent=parent)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        runtime.configChanged.connect(self.refresh)
        capture.sourceChanged.connect(self._capture_changed)
        capture.displayGeometryChanged.connect(self.refreshForDisplayGeometry)
        runtime.register_shutdown(self.shutdown)
        self.refresh()

    @property
    def status(self) -> str:
        return self._status

    def is_watching(self) -> bool:
        return self.watch_thread is not None and self.watch_thread.is_alive()

    def _uptime(self) -> float:
        seconds = self._monitoring_seconds
        if self._watch_started is not None:
            seconds += time.monotonic() - self._watch_started
        return seconds

    @staticmethod
    def _timer_rows(config: AppConfig) -> list[dict[str, Any]]:
        offset = int(config.timer_offset_seconds)
        rows: list[dict[str, Any]] = []
        for timer_id in DISPLAY_TIMER_ORDER:
            seconds = seconds_until_next(timer_id, offset_seconds=offset)
            period = TIMER_PERIOD_SECONDS[timer_id]
            target = datetime.now().timestamp() + seconds
            rows.append(
                {
                    "id": timer_id,
                    "label": timer_id.title(),
                    "countdown": format_countdown(seconds),
                    "progress": max(0.0, min(1.0, seconds / period)),
                    "color": TIMER_COLORS[timer_id],
                    "hot": seconds <= 60,
                    "target": datetime.fromtimestamp(target).strftime("Spawns at %H:%M"),
                }
            )
        return rows

    def _priority_rows(self) -> list[dict[str, str]]:
        return [
            {
                "id": f"{droid}|{rarity}",
                "droid": droid,
                "rarity": rarity,
                "label": f"{droid} · {rarity}",
                "tone": droid.lower() if droid != "Galactic" else "galactic",
            }
            for droid, rarity in ALERT_COMBOS
            if (droid, rarity) in self.runtime.config.targets
        ]

    def _channels(self) -> list[dict[str, Any]]:
        config = self.runtime.config
        return [
            {
                "id": "popup",
                "label": "Popup",
                "enabled": config.popup_enabled,
                "configured": True,
                "detail": self._channel_status["Popup"] or "On-screen card on this device",
            },
            {
                "id": "sound",
                "label": "Sound",
                "enabled": config.sound_enabled,
                "configured": True,
                "detail": self._channel_status["Sound"]
                or (config.sound_file or "Built-in alert sound"),
            },
            {
                "id": "discord",
                "label": "Discord",
                "enabled": config.discord_enabled,
                "configured": discord_webhook_configured(config),
                "detail": self._channel_status["Discord"]
                or ("Webhook connected" if discord_webhook_configured(config) else "Not set up"),
            },
            {
                "id": "ntfy",
                "label": "ntfy",
                "enabled": config.ntfy_enabled,
                "configured": ntfy_configured(config),
                "detail": self._channel_status["ntfy"]
                or (f"Topic · {config.ntfy_topic}" if ntfy_configured(config) else "Not set up"),
            },
            {
                "id": "pushover",
                "label": "Pushover",
                "enabled": config.phone_alerts_enabled,
                "configured": phone_alerts_configured(config),
                "detail": self._channel_status["Pushover"]
                or ("Credentials connected" if phone_alerts_configured(config) else "Not set up"),
            },
        ]

    @Slot()
    def refresh(self) -> None:
        config = self.runtime.config
        uptime = self._uptime()
        watching = self.is_watching()
        self.replace_state(
            {
                "watching": watching,
                "status": self._status,
                "statusTone": {
                    "Running": "good",
                    "Warning": "warning",
                    "Error": "danger",
                }.get(self._status, "muted"),
                "watchButton": "Stop Watching" if watching else "Start Watching",
                "watcherTitle": "Watching for alerts" if watching else "Not watching",
                "watcherDetail": (
                    self._state.get("watcherDetail")
                    if watching and self._state.get("watcherDetail")
                    else self.capture.ready_text()
                ),
                "sourceLabel": self.capture.source_label(),
                "priorities": self._priority_rows(),
                "priorityCount": len(config.targets),
                "channels": self._channels(),
                "timers": self._timer_rows(config),
                "timersEnabled": config.droid_timers_enabled,
                "timerReminders": config.timer_reminders_enabled,
                "scans": self._scans,
                "alerts": self._alerts,
                "uptime": (
                    f"{int(uptime // 3600)}h {int(uptime % 3600 // 60):02d}m"
                    if uptime >= 3600
                    else f"{int(uptime // 60)}m"
                ),
                "lastAlert": self._last_alert,
                "alarmActive": self.wake_alarm.active,
            }
        )

    @Slot()
    def toggleWatching(self) -> None:
        self.stopWatcher() if self.is_watching() else self.startWatcher()

    @Slot()
    def startWatcher(self) -> None:
        if self.is_watching():
            return
        # Freeze the current in-memory settings for this watcher run. This
        # includes changes still inside the save debounce.
        config = AppConfig.from_dict(self.runtime.config.to_dict())
        if config.capture_source == "device":
            try:
                self.capture.ensure_device_session(config)
            except Exception as exc:
                self._set_status("Error")
                self.update_state(
                    watcherTitle="Capture device unavailable",
                    watcherDetail=str(exc),
                )
                self.runtime.dialogs.show_message("Watcher", str(exc), tone="danger")
                return
        self.stop_event = threading.Event()
        self._watch_started = time.monotonic()
        thread = threading.Thread(
            target=self._watch_worker,
            args=(config, self.stop_event),
            name="DroidAlertsWatcher",
            daemon=True,
        )
        self.watch_thread = thread
        thread.start()
        self._set_status("Running")
        self.update_state(
            watcherTitle="Starting screen capture…",
            watcherDetail=f"Preparing {self.capture.source_label(config)}",
        )
        self.runtime.detailChanged.emit("Watcher started")

    def _watch_worker(self, config: AppConfig, stop_event: threading.Event) -> None:
        thread = threading.current_thread()
        try:
            run_watch(
                debug=config.save_debug_screenshots,
                config=config,
                stop_event=stop_event,
                popup_parent=None,
                popup_callback=lambda detection: self.runtime.dispatcher.post(
                    lambda value=detection: self._show_popup(
                        value,
                        AppConfig.from_dict(self.runtime.config.to_dict()),
                    )
                ),
                status_callback=lambda event: self.runtime.dispatcher.post(
                    lambda value=event: self._handle_status(value)
                ),
                capture_factory=self.capture.create_runtime_capture,
                local_sound_allowed=lambda: not self.wake_alarm.active,
            )
        except Exception as exc:
            self.runtime.dispatcher.post(
                lambda error=exc, owner=thread: self._watcher_finished(error, owner)
            )
        else:
            self.runtime.dispatcher.post(
                lambda owner=thread: self._watcher_finished(None, owner)
            )

    def _show_popup(self, detection: Detection, config: AppConfig) -> None:
        show_popup(
            detection,
            config.popup_seconds,
            icon_path=popup_icon_path(config, detection),
            monitor=self.capture.current_monitor(),
            position=config.popup_position,
            scale=config.popup_scale,
            opacity=config.popup_opacity,
        )

    def _handle_status(self, event: dict[str, object]) -> None:
        kind = str(event.get("type") or "")
        if kind in {"watcher_ready", "config_reloaded"}:
            label = str(event.get("capture_label") or "").strip() or self.capture.source_label()
            width, height = event.get("screen_width", "?"), event.get("screen_height", "?")
            source = event.get("region_source", "automatic")
            self._set_status("Running")
            self.update_state(
                watcherTitle="Watching for alerts",
                watcherDetail=f"{label} · {width} × {height} · Region: {source}",
            )
        elif kind == "scan":
            self._scans += 1
        elif kind in {"detection", "alert"}:
            row = event.get("event")
            if isinstance(row, dict) and kind == "alert":
                self._alerts += 1
                label = f"{row.get('rarity', '')} {row.get('droid', '')}".strip()
                self._last_alert = (
                    f"{label} · {self._display_timestamp(str(row.get('ts', '')))}"
                )
                wake_alert_id = (
                    f"chat:{row.get('droid', '')}:{row.get('rarity', '')}"
                )
                if self.runtime.config.channel_allows_alert("sound", wake_alert_id):
                    self._maybe_start_wake_alarm(row.get("droid"), row.get("rarity"))
            self.historyChanged.emit()
        elif kind == "delivery":
            result = event.get("result")
            if isinstance(result, dict):
                label = str(result.get("channel") or "")
                label = "Pushover" if label.lower() in {"phone", "pushover"} else label
                if label in self._channel_status:
                    success = bool(result.get("success"))
                    detail = str(result.get("detail") or "")
                    self._channel_status[label] = (
                        "Delivered just now" if success else f"Failed · {detail[:70]}"
                    )
            self.historyChanged.emit()
        elif kind in {"capture_error", "rebirth_error", "hud_error", "log_error"}:
            message = str(event.get("message") or "Unknown watcher error")
            self._set_status("Warning")
            self.update_state(watcherDetail=f"{message} Retrying automatically.")
        elif kind == "sound_error":
            message = str(event.get("message") or "Unknown sound error")
            self._channel_status["Sound"] = f"Failed · {message[:70]}"
        self.refresh()

    @staticmethod
    def _display_timestamp(value: str) -> str:
        text = value.strip()
        if len(text) >= 15 and text[8] == "_":
            return f"{text[9:11]}:{text[11:13]}:{text[13:15]}"
        return text or "just now"

    def _watcher_finished(
        self,
        error: BaseException | None,
        owner: threading.Thread,
    ) -> None:
        if self.watch_thread is not owner:
            return
        restart = self._restart_after_capture_change and error is None
        self._restart_after_capture_change = False
        if self._watch_started is not None:
            self._monitoring_seconds += time.monotonic() - self._watch_started
        self._watch_started = None
        self.watch_thread = None
        self.stop_event = None
        if error is None:
            self._set_status("Stopped")
            self.update_state(
                watcherTitle="Not watching",
                watcherDetail=self.capture.ready_text(),
            )
            self.runtime.detailChanged.emit("Watcher stopped")
        else:
            self._set_status("Error")
            self.update_state(
                watcherTitle="Monitoring stopped unexpectedly",
                watcherDetail=str(error),
            )
            self.runtime.dialogs.show_message("Watcher", str(error), tone="danger")
        self.refresh()
        if restart:
            QTimer.singleShot(150, self.startWatcher)

    @Slot()
    def stopWatcher(self) -> None:
        if self.stop_event is None:
            return
        self.stop_event.set()
        self.update_state(watchButton="Stopping…")
        self.runtime.detailChanged.emit("Stopping watcher…")

    def _capture_changed(self) -> None:
        if self.is_watching():
            self._restart_after_capture_change = True
            self.stopWatcher()
            self.runtime.detailChanged.emit(
                "Capture source changed; restarting watcher"
            )
        self.refresh()

    @Slot(bool)
    def refreshForDisplayGeometry(self, automatic: bool) -> None:
        if self.runtime.config.droid_timers_enabled:
            hide_droid_timers()
            show_droid_timers(
                self.runtime.config,
                monitor=self.capture.current_monitor(),
                on_reminder=self.handleTimerReminder,
            )
        if self.is_watching():
            self._restart_after_capture_change = True
            self.stopWatcher()
        self.runtime.detailChanged.emit(
            "Display geometry changed; layout refreshed automatically"
            if automatic
            else "Display geometry refreshed"
        )
        self.refresh()

    def _set_status(self, status: str) -> None:
        if self._status == status:
            return
        self._status = status
        self.statusChanged.emit(status)

    @Slot()
    def choosePriorities(self) -> None:
        selected = self.runtime.config.targets
        self.runtime.dialogs.choices(
            "Priority Alerts",
            "Choose the chat spawns that should trigger alerts.",
            [
                {
                    "id": f"{droid}|{rarity}",
                    "label": f"{droid} · {rarity}",
                    "detail": "",
                    "selected": (droid, rarity) in selected,
                }
                for droid, rarity in ALERT_COMBOS
            ],
            multi=True,
            callback=self._save_priorities,
        )

    def _save_priorities(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        selected = {
            str(value) for value in payload.get("selected", []) if str(value)
        }
        targets = [
            [droid, rarity]
            for droid, rarity in ALERT_COMBOS
            if f"{droid}|{rarity}" in selected
        ]
        self.runtime.update_config(alert_targets=targets)
        self.refresh()

    @Slot(str, bool)
    def setChannelEnabled(self, channel: str, enabled: bool) -> None:
        field = {
            "popup": "popup_enabled",
            "sound": "sound_enabled",
            "discord": "discord_enabled",
            "ntfy": "ntfy_enabled",
            "pushover": "phone_alerts_enabled",
        }.get(channel)
        if field is None:
            return
        configured = {
            "discord": discord_webhook_configured,
            "ntfy": ntfy_configured,
            "pushover": phone_alerts_configured,
        }.get(channel)
        if enabled and configured is not None and not configured(self.runtime.config):
            self.configureChannel(channel)
            return
        self.runtime.update_config(**{field: enabled})
        self.refresh()

    @Slot(str)
    def configureChannel(self, channel: str) -> None:
        _label, options = self._channel_alert_options(channel)
        if channel in {"popup", "sound"}:
            label = "Popup" if channel == "popup" else "Sound"
            self.runtime.dialogs.channel_settings(
                f"Configure {label} alerts",
                f"Choose which alerts may use the {label.lower()} channel.",
                [],
                options,
                accept_text="Save",
                callback=lambda payload: self._save_local_channel_settings(
                    channel, options, payload
                ),
            )
        elif channel == "discord":
            try:
                current, _ = load_discord_webhook(self.runtime.config)
            except Exception:
                current = ""
            self.runtime.dialogs.channel_settings(
                "Configure Discord alerts",
                "Set the main webhook and choose which alerts may use Discord.",
                [
                    {
                        "id": "webhook",
                        "label": "Main webhook URL",
                        "value": current or "",
                        "password": False,
                    }
                ],
                options,
                accept_text="Save",
                callback=lambda payload: self._save_channel_settings(
                    channel, options, payload
                ),
            )
        elif channel == "ntfy":
            try:
                existing_token, _ = load_ntfy_token(self.runtime.config)
            except Exception:
                existing_token = None
            self.runtime.dialogs.channel_settings(
                "Configure ntfy alerts",
                "Subscribe to a hard-to-guess topic in the ntfy app, then enter it here.",
                [
                    {
                        "id": "topic",
                        "label": "Topic",
                        "value": self.runtime.config.ntfy_topic,
                        "password": False,
                    },
                    {
                        "id": "server",
                        "label": "Server",
                        "value": self.runtime.config.ntfy_server_url,
                        "password": False,
                    },
                    {
                        "id": "token_action",
                        "label": "Access token",
                        "value": "keep",
                        "choices": [
                            {
                                "id": "keep",
                                "label": (
                                    "Keep saved token"
                                    if existing_token
                                    else "No saved token"
                                ),
                            },
                            {"id": "remove", "label": "Remove saved token"},
                        ],
                    },
                    {
                        "id": "token",
                        "label": "New access token (optional)",
                        "value": "",
                        "password": True,
                    },
                ],
                options,
                accept_text="Save",
                callback=lambda payload: self._save_channel_settings(
                    channel, options, payload
                ),
            )
        elif channel == "pushover":
            try:
                existing, _ = load_phone_alert_credentials(self.runtime.config)
            except Exception:
                existing = None
            self.runtime.dialogs.channel_settings(
                "Configure Pushover alerts",
                "Enter the User Key and application API token from pushover.net.",
                [
                    {
                        "id": "user",
                        "label": "User Key",
                        "value": (existing or {}).get("user", ""),
                        "password": False,
                    },
                    {
                        "id": "token",
                        "label": "API Token",
                        "value": (existing or {}).get("token", ""),
                        "password": True,
                    },
                ],
                options,
                link=("Open pushover.net", "https://pushover.net"),
                accept_text="Save",
                callback=lambda payload: self._save_channel_settings(
                    channel, options, payload
                ),
            )

    def _save_local_channel_settings(
        self,
        channel: str,
        options: list[dict[str, Any]],
        payload: dict[str, Any] | None,
    ) -> None:
        if payload is None:
            return
        self._save_channel_alerts(channel, options, payload)
        self.refresh()

    def _save_discord(self, payload: dict[str, Any] | None) -> bool:
        if not payload:
            return False
        value = str(payload.get("webhook") or "").strip().lstrip("\ufeff")
        if not valid_discord_webhook_url(value):
            self.runtime.dialogs.show_message(
                "Discord",
                "That does not look like a Discord webhook URL.",
                tone="danger",
            )
            return False
        save_discord_webhook(self.runtime.config, value)
        self.runtime.update_config(discord_enabled=True)
        self.refresh()
        return True

    def _save_ntfy(self, payload: dict[str, Any] | None) -> bool:
        if not payload:
            return False
        topic = str(payload.get("topic") or "").strip()
        server = str(payload.get("server") or "").strip().rstrip("/") or "https://ntfy.sh"
        if not valid_ntfy_server_url(server) or not valid_ntfy_topic(topic):
            self.runtime.dialogs.show_message(
                "ntfy",
                "Use a valid http(s) server and a topic containing only letters, numbers, hyphens or underscores.",
                tone="danger",
            )
            return False
        token = str(payload.get("token") or "").strip()
        token_action = str(payload.get("token_action") or "keep").casefold()
        if token:
            save_ntfy_token(self.runtime.config, token)
        elif token_action == "remove":
            save_ntfy_token(self.runtime.config, "")
        self.runtime.update_config(
            ntfy_server_url=server,
            ntfy_topic=topic,
            ntfy_enabled=True,
        )
        self.refresh()
        return True

    def _save_pushover(self, payload: dict[str, Any] | None) -> bool:
        if not payload:
            return False
        user = str(payload.get("user") or "").strip()
        token = str(payload.get("token") or "").strip()
        if not user or not token:
            self.runtime.dialogs.show_message(
                "Pushover",
                "Both the User Key and API Token are required.",
                tone="danger",
            )
            return False
        save_phone_credentials(self.runtime.config, token, user)
        self.runtime.update_config(phone_alerts_enabled=True)
        self.refresh()
        return True

    def _channel_alert_options(
        self,
        channel: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        label = {"discord": "Discord", "ntfy": "ntfy", "pushover": "Pushover"}.get(
            channel, channel
        )
        config = self.runtime.config
        disabled = set(config.channel_disabled_alerts.get(channel, []))
        options = [
            {
                "id": f"chat:{droid}:{rarity}",
                "label": f"{droid} · {rarity}",
                "selected": f"chat:{droid}:{rarity}" not in disabled,
            }
            for droid, rarity in ALERT_COMBOS
            if (droid, rarity) in config.targets
        ]
        extras = (
            ("rebirth_ready_alert_enabled", "rebirth_ready", "Rebirth Ready"),
            ("scrap_alert_enabled", "scrap_alert", "Scrap Alert"),
            ("rebirth_alert_enabled", "rebirth_available", "Rebirth Alert"),
            ("cb23_mission_alert_enabled", "cb23_mission", "CB23 Mission"),
        )
        for field, alert_id, title in extras:
            if getattr(config, field):
                options.append(
                    {
                        "id": alert_id,
                        "label": title,
                        "selected": alert_id not in disabled,
                    }
                )
        if config.belt_target_tiers:
            options.append(
                {
                    "id": "belt_tracker",
                    "label": "Belt Tracker alerts",
                    "selected": "belt_tracker" not in disabled,
                }
            )
        if config.limited_deal_priority_alerts or config.limited_deal_target_tiers:
            options.append(
                {
                    "id": "limited_deals",
                    "label": "Limited Deal alerts",
                    "selected": "limited_deals" not in disabled,
                }
            )
        if channel != "sound":
            for kind in DISPLAY_TIMER_ORDER:
                timer_id = f"timer:{kind}"
                options.append(
                    {
                        "id": timer_id,
                        "label": f"{kind.title()} Timer Reminder",
                        "selected": (
                            timer_id not in disabled
                            and "timer_reminder" not in disabled
                        ),
                    }
                )
        return label, options

    def _save_channel_settings(
        self,
        channel: str,
        options: list[dict[str, Any]],
        payload: dict[str, Any] | None,
    ) -> None:
        if payload is None:
            return
        save_credentials = {
            "discord": self._save_discord,
            "ntfy": self._save_ntfy,
            "pushover": self._save_pushover,
        }.get(channel)
        if save_credentials is None or not save_credentials(payload):
            return
        self._save_channel_alerts(channel, options, payload)
        self.refresh()

    def _save_channel_alerts(
        self,
        channel: str,
        options: list[dict[str, Any]],
        payload: dict[str, Any] | None,
    ) -> None:
        if payload is None:
            return
        enabled = {str(value) for value in payload.get("selected", [])}
        current_ids = {str(option["id"]) for option in options}
        previous = set(self.runtime.config.channel_disabled_alerts.get(channel, []))
        legacy_ids = {
            alias
            for alert_id in current_ids
            for alias in alert_id_aliases(alert_id)
        }
        disabled = (previous - current_ids - legacy_ids) | (current_ids - enabled)
        mapping = dict(self.runtime.config.channel_disabled_alerts)
        if disabled:
            mapping[channel] = sorted(disabled)
        else:
            mapping.pop(channel, None)
        self.runtime.update_config(channel_disabled_alerts=mapping)

    @staticmethod
    def test_detection() -> Detection:
        return Detection(
            droid="Beskar",
            rarity="Mythic",
            row_box=(0, 0, 480, 44),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="gui-test",
            shape_score=1.0,
        )

    @Slot(str)
    def testChannel(self, channel: str) -> None:
        config = self.runtime.config
        detection = self.test_detection()
        label = {
            "popup": "Popup",
            "sound": "Sound",
            "discord": "Discord",
            "ntfy": "ntfy",
            "pushover": "Pushover",
        }.get(channel, channel)
        if channel == "popup":
            self._show_popup(detection, config)
            self._channel_status[label] = "Previewed just now"
            self.refresh()
            return
        if channel == "sound":
            policy = AlertPolicy(config)
            policy.sound_enabled = True
            self._channel_status[label] = "Playing…"
            self.refresh()
            self.runtime.run_background(
                lambda: policy.notify(detection),
                lambda _result, error: self._channel_test_done(label, error),
                name="DroidAlertsTestSound",
            )
            return
        try:
            if channel == "discord":
                webhook, _ = load_discord_webhook_for_detection(
                    config,
                    detection,
                )
                if not webhook:
                    raise ValueError("Set up Discord first")
                work = partial(
                    send_discord_alert,
                    webhook,
                    detection,
                    config=config,
                )
            elif channel == "ntfy":
                if not ntfy_configured(config):
                    raise ValueError("Set up ntfy first")
                work = partial(
                    send_ntfy_alert,
                    config,
                    detection,
                    attachment_path=None,
                )
            elif channel == "pushover":
                credentials, _ = load_phone_alert_credentials(config)
                if not credentials:
                    raise ValueError("Set up Pushover first")
                work = partial(
                    send_phone_alert,
                    credentials,
                    detection,
                    sound=config.phone_sound,
                    attachment_path=None,
                )
            else:
                return
        except Exception as exc:
            self._channel_status[label] = f"Failed · {exc}"
            self.refresh()
            return
        self._channel_status[label] = "Sending…"
        self.refresh()
        self.runtime.run_background(
            work,
            lambda result, error: self._channel_test_done(
                label,
                error
                or (
                    None
                    if bool(getattr(result, "success", True))
                    else RuntimeError(str(getattr(result, "message", "Failed")))
                ),
            ),
            name=f"DroidAlertsTest{label}",
        )

    def _channel_test_done(self, label: str, error: BaseException | None) -> None:
        if error is None:
            self._channel_status[label] = "Test delivered just now"
            self.runtime.detailChanged.emit(f"{label} test completed")
        else:
            self._channel_status[label] = f"Failed · {str(error)[:70]}"
            self.runtime.detailChanged.emit(f"{label} test failed")
        self.refresh()

    @Slot()
    def testAllAlerts(self) -> None:
        enabled = [
            row["id"] for row in self._channels() if bool(row["enabled"])
        ]
        if not enabled:
            self.runtime.detailChanged.emit("No alert channels are enabled")
            return
        for channel in enabled:
            self.testChannel(channel)
        self.runtime.detailChanged.emit(
            f"Testing {', '.join(enabled)} · {event_text(self.test_detection())}"
        )

    def dispatch_detection(
        self,
        detection: Detection,
        *,
        source: str,
        rarity: str = "",
        extra_fields: dict[str, object] | None = None,
        include_sound: bool = True,
        include_local: bool = True,
        remote_channels: set[str] | None = None,
        on_complete: Callable[[dict[str, bool]], None] | None = None,
    ) -> None:
        """Send a Belt or Limited Deal alert through the enabled channels."""
        config = AppConfig.from_dict(self.runtime.config.to_dict())
        alert_id = alert_type_id(detection)
        if include_local and config.channel_allows_alert("sound", alert_id):
            self._maybe_start_wake_alarm(detection.droid, detection.rarity)
        if (
            include_local
            and include_sound
            and config.sound_enabled
            and config.channel_allows_alert("sound", alert_id)
            and not self.wake_alarm.active
        ):
            self.runtime.run_background(
                lambda: AlertPolicy(config).notify(detection),
                lambda _value, error: self._dispatch_sound_done(error),
                name=f"DroidAlerts{source.title()}Sound",
            )
        if (
            include_local
            and config.popup_enabled
            and config.channel_allows_alert("popup", alert_id)
        ):
            self._show_popup(detection, config)

        requested_channels = (
            {str(channel).casefold() for channel in remote_channels}
            if remote_channels is not None
            else None
        )
        expected_channels: list[str] = []
        for label, enabled, channel in (
            ("Discord", config.discord_enabled, "discord"),
            ("ntfy", config.ntfy_enabled, "ntfy"),
            ("Pushover", config.phone_alerts_enabled, "pushover"),
        ):
            if (
                enabled
                and config.channel_allows_alert(channel, alert_id)
                and (
                    requested_channels is None
                    or label.casefold() in requested_channels
                )
            ):
                expected_channels.append(label)

        webhook = None
        if "Discord" in expected_channels:
            try:
                webhook, _ = load_discord_webhook_for_detection(
                    config,
                    detection,
                )
            except Exception as exc:
                self._channel_status["Discord"] = f"Failed · {str(exc)[:70]}"
        credentials = None
        if "Pushover" in expected_channels:
            try:
                credentials, _ = load_phone_alert_credentials(config)
            except Exception as exc:
                self._channel_status["Pushover"] = f"Failed · {str(exc)[:70]}"
        deliveries = enabled_alert_deliveries(
            config,
            detection,
            webhook_url=webhook,
            phone_credentials=credentials,
            ntfy_ready=ntfy_configured(config),
            attachment_path=None,
            discord_sender=send_discord_alert,
            ntfy_sender=send_ntfy_alert,
            phone_sender=send_phone_alert,
        )
        deliveries = [
            delivery
            for delivery in deliveries
            if delivery.label in expected_channels
        ]
        outcomes = {label: False for label in expected_channels}
        remaining = {"count": len(deliveries)}

        def delivery_finished(item, execution, error) -> None:
            self._delivery_done(
                item.label,
                execution,
                error,
                detection,
                source,
                rarity,
                extra_fields or {},
            )
            outcomes[item.label] = bool(
                error is None
                and execution is not None
                and execution.result.success
            )
            remaining["count"] -= 1
            if remaining["count"] == 0 and on_complete is not None:
                on_complete(dict(outcomes))

        if not deliveries and on_complete is not None:
            on_complete(dict(outcomes))
        for delivery in deliveries:
            self._channel_status[delivery.label] = "Sending…"
            self.runtime.run_background(
                lambda item=delivery: execute_alert_delivery(
                    item,
                    wait_before_retry=lambda seconds: (
                        time.sleep(seconds) or False
                    ),
                ),
                lambda execution, error, item=delivery: delivery_finished(
                    item,
                    execution,
                    error,
                ),
                name=f"DroidAlerts{source.title()}{delivery.label}",
            )
        self.refresh()

    def _dispatch_sound_done(self, error: BaseException | None) -> None:
        self._channel_status["Sound"] = (
            "Played just now" if error is None else f"Failed · {str(error)[:70]}"
        )
        self.refresh()

    def _delivery_done(
        self,
        label: str,
        execution,
        error: BaseException | None,
        detection: Detection,
        source: str,
        rarity: str,
        extra_fields: dict[str, object],
    ) -> None:
        if error is not None or execution is None:
            self._channel_status[label] = f"Failed · {str(error)[:70]}"
            self.refresh()
            return
        event = build_delivery_event(
            execution,
            {"droid": detection.droid, "score": detection.score},
            channel=label,
            source=source,
            rarity=rarity,
            extra_fields=extra_fields,
        )
        persist_delivery_event(
            event,
            on_error=lambda exc: print(f"[LOG] Failed to write delivery: {exc}"),
        )
        success = bool(event.get("success"))
        detail = str(event.get("detail") or "")
        self._channel_status[label] = (
            "Delivered just now" if success else f"Failed · {detail[:70]}"
        )
        self.historyChanged.emit()
        self.refresh()

    @Slot(bool)
    def setTimersEnabled(self, enabled: bool) -> None:
        self.runtime.update_config(
            droid_timers_enabled=enabled,
            timer_reminders_enabled=(
                self.runtime.config.timer_reminders_enabled if enabled else False
            ),
        )
        if enabled:
            show_droid_timers(
                self.runtime.config,
                monitor=self.capture.current_monitor(),
                on_reminder=self.handleTimerReminder,
            )
        else:
            hide_droid_timers()
        self.refresh()

    @Slot(bool)
    def setTimerRemindersEnabled(self, enabled: bool) -> None:
        self.runtime.update_config(
            timer_reminders_enabled=(
                self.runtime.config.droid_timers_enabled and enabled
            )
        )
        if self.runtime.config.droid_timers_enabled:
            show_droid_timers(
                self.runtime.config,
                monitor=self.capture.current_monitor(),
                on_reminder=self.handleTimerReminder,
            )
        self.refresh()

    @Slot()
    def configureTimerReminders(self) -> None:
        rules = self.runtime.config.timer_reminder_rules
        self.runtime.dialogs.form(
            "Timer Reminder Rules",
            "Choose when to alert before each timer reaches zero. Use times such as 5m, 1m or 30s, separated by commas. Leave a timer blank to disable its reminders.",
            [
                {
                    "id": kind,
                    "label": f"{kind.title()} alerts before spawn",
                    "value": ", ".join(
                        self._format_reminder_offset(value)
                        for value in rules.get(kind, [])
                    ),
                    "password": False,
                }
                for kind in DISPLAY_TIMER_ORDER
            ],
            callback=self._save_timer_reminder_rules,
        )

    @staticmethod
    def _format_reminder_offset(seconds: int) -> str:
        value = max(1, int(seconds))
        if value % 3600 == 0:
            return f"{value // 3600}h"
        if value % 60 == 0:
            return f"{value // 60}m"
        return f"{value}s"

    @staticmethod
    def _parse_reminder_offset(value: str) -> int:
        text = value.strip().casefold()
        if not text:
            raise ValueError("Empty reminder time")
        multiplier = 1
        if text[-1:] in {"s", "m", "h"}:
            unit = text[-1]
            text = text[:-1].strip()
            multiplier = {"s": 1, "m": 60, "h": 3600}[unit]
        seconds = int(text) * multiplier
        if seconds <= 0:
            raise ValueError("Reminder time must be positive")
        return min(86_400, seconds)

    def _save_timer_reminder_rules(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        rules: dict[str, list[int]] = {}
        try:
            for kind in DISPLAY_TIMER_ORDER:
                text = str(payload.get(kind) or "").strip()
                values = (
                    []
                    if not text
                    else [
                        self._parse_reminder_offset(part)
                        for part in text.split(",")
                        if part.strip()
                    ]
                )
                rules[kind] = sorted(set(values), reverse=True)[:8]
        except ValueError:
            self.runtime.dialogs.show_message(
                "Timer Reminder Rules",
                "Use times like 5m, 1m or 30s, separated by commas.",
                tone="danger",
            )
            return
        self.runtime.update_config(timer_reminder_rules=rules)
        if self.runtime.config.droid_timers_enabled:
            show_droid_timers(
                self.runtime.config,
                monitor=self.capture.current_monitor(),
                on_reminder=self.handleTimerReminder,
            )
        self.refresh()

    @Slot(str, int)
    def handleTimerReminder(self, kind: str, remaining: int) -> None:
        detection = timer_reminder_detection(kind, remaining)
        self.dispatch_detection(
            detection,
            source="timer-reminder",
            rarity="Timer",
            extra_fields={
                "timer_kind": str(kind).lower(),
                "timer_remaining_seconds": max(0, int(remaining)),
            },
            include_sound=False,
        )

    @Slot()
    def adjustTimers(self) -> None:
        adjust_droid_timers(
            self.runtime.config,
            on_reminder=self.handleTimerReminder,
        )

    def _maybe_start_wake_alarm(self, droid: object, rarity: object) -> None:
        config = self.runtime.config
        if not config.wake_alarm_matches(str(droid or ""), str(rarity or "")):
            return
        try:
            self.wake_alarm.start()
        except Exception as exc:
            self.runtime.detailChanged.emit(f"Wake-up alarm failed: {exc}")
            return
        self._alarm_timer.start(40_000)
        self.runtime.dialogs.confirm(
            "WAKE UP — MYTHIC DETECTED",
            f"{droid} {rarity} was detected.",
            tone="danger",
            note="The alarm stops after 40 seconds or when you press Stop.",
            accept_text="Stop alarm",
            cancel_text="",
            callback=lambda _payload: self.stopWakeAlarm(),
        )
        self.refresh()

    @Slot()
    def testWakeAlarm(self) -> None:
        try:
            generation = self.wake_alarm.start()
        except Exception as exc:
            self.runtime.dialogs.show_message("Wake-Up Alarm", str(exc), tone="danger")
            return
        QTimer.singleShot(3000, lambda: self.wake_alarm.stop(generation))
        self.runtime.detailChanged.emit("Testing wake-up alarm for 3 seconds")
        self.refresh()

    @Slot()
    def stopWakeAlarm(self) -> None:
        self._alarm_timer.stop()
        if self.wake_alarm.stop():
            self.runtime.detailChanged.emit("Wake-up alarm stopped")
        self.refresh()

    def shutdown(self) -> None:
        self._restart_after_capture_change = False
        if self.stop_event is not None:
            self.stop_event.set()
        self.wake_alarm.stop()
        self._timer.stop()
