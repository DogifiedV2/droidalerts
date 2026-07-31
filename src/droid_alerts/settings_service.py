from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from .config import AppConfig, normalize_belt_scan_fps
from .notifications import valid_ntfy_server_url, valid_ntfy_topic
from .ui_theme import normalize_theme_key


class SettingsValidationError(ValueError):
    """A user-editable setting could not be converted to its required type."""


NUMERIC_SETTING_KEYS = (
    "monitor_index", "capture_interval_seconds", "rebirth_scan_interval_seconds",
    "dedupe_seconds", "alert_cooldown_seconds",
    "validation_failures_before_calibration_prompt", "popup_seconds", "popup_scale",
    "popup_opacity", "retention_days", "max_storage_mb", "timer_reminder_seconds",
    "timer_offset_seconds", "belt_idle_scan_fps", "belt_active_scan_fps",
)


@dataclass(frozen=True)
class SettingsUpdate:
    config: AppConfig
    normalized_values: dict[str, object]
    unconfigured_channels: tuple[str, ...]


def build_settings_update(
    persisted: AppConfig,
    runtime: AppConfig,
    values: Mapping[str, object],
    alert_targets: Sequence[tuple[str, str]],
    limited_deal_priorities: Sequence[tuple[str, str]],
    *,
    configured_channels: Mapping[str, bool] | None = None,
) -> SettingsUpdate:
    """Validate GUI values without reading widgets, files, or global state."""
    config = deepcopy(persisted)
    for field in (
        "capture_source",
        "capture_window_title",
        "capture_window_process",
        "capture_window_class",
        "capture_device_name",
        "capture_device_path",
        "capture_device_vid",
        "capture_device_pid",
        "capture_device_backend",
    ):
        setattr(config, field, getattr(runtime, field))

    normalized: dict[str, object] = {}
    try:
        normalized.update(
            monitor_index=max(1, int(values["monitor_index"])),
            capture_interval_seconds=max(0.05, float(values["capture_interval_seconds"])),
            rebirth_scan_interval_seconds=min(
                30.0, max(2.0, float(values["rebirth_scan_interval_seconds"]))
            ),
            dedupe_seconds=max(0.0, float(values["dedupe_seconds"])),
            alert_cooldown_seconds=max(0.0, float(values["alert_cooldown_seconds"])),
            validation_failures_before_calibration_prompt=max(
                1, int(values["validation_failures_before_calibration_prompt"])
            ),
            popup_seconds=max(0.5, float(values["popup_seconds"])),
            popup_scale=min(1.5, max(0.7, float(values["popup_scale"]))),
            popup_opacity=min(1.0, max(0.55, float(values["popup_opacity"]))),
            retention_days=max(0, int(values["retention_days"])),
            max_storage_mb=max(0, int(values["max_storage_mb"])),
            timer_reminder_seconds=max(1, int(values["timer_reminder_seconds"])),
            timer_offset_seconds=max(-3600, min(3600, int(values["timer_offset_seconds"]))),
        )
        idle_fps, active_fps = normalize_belt_scan_fps(
            values["belt_idle_scan_fps"], values["belt_active_scan_fps"]
        )
        normalized["belt_idle_scan_fps"] = idle_fps
        normalized["belt_active_scan_fps"] = active_fps
    except (KeyError, TypeError, ValueError) as exc:
        raise SettingsValidationError(f"Invalid numeric setting: {exc}") from exc

    for field, value in normalized.items():
        setattr(config, field, value)

    boolean_fields = (
        "sound_enabled", "wake_alarm_enabled", "wake_alarm_beskar_mythic",
        "wake_alarm_galactic_mythic", "popup_enabled", "rebirth_ready_alert_enabled",
        "scrap_alert_enabled",
        "droid_timers_enabled", "save_alert_samples", "save_debug_screenshots",
        "ntfy_enabled", "discord_enabled", "phone_alerts_enabled",
        "ntfy_include_attachment", "phone_include_attachment", "update_check_enabled",
        "start_watcher_on_launch", "rebirth_alert_enabled",
        "cb23_mission_alert_enabled", "belt_overlay_enabled", "belt_dev_mode",
        "belt_template_collection_enabled", "advanced_mode",
    )
    for field in boolean_fields:
        setattr(config, field, bool(values.get(field, getattr(config, field))))
    config.share_debug_detections = config.save_debug_screenshots and bool(
        values.get("share_debug_detections", False)
    )
    config.timer_reminders_enabled = config.droid_timers_enabled and bool(
        values.get("timer_reminders_enabled", False)
    )

    popup_position = str(values.get("popup_position", "")).strip().lower().replace(" ", "_")
    if popup_position not in {"top_center", "top_left", "top_right", "bottom_left", "bottom_right"}:
        popup_position = "top_center"
    config.popup_position = popup_position
    config.ui_theme = normalize_theme_key(values.get("ui_theme", config.ui_theme))
    normalized["popup_position"] = popup_position
    normalized["ui_theme"] = config.ui_theme

    string_values = {
        "sound_file": "",
        "ntfy_server_url": "https://ntfy.sh",
        "ntfy_topic": "",
        "ntfy_priority": "5",
        "ntfy_tags": "rotating_light",
        "phone_sound": "siren",
        "update_repo": "DogifiedV2/droidalerts",
    }
    for field, fallback in string_values.items():
        value = str(values.get(field, "")).strip() or fallback
        setattr(config, field, value)

    config.alert_targets = [list(combo) for combo in alert_targets]
    config.limited_deal_priority_alerts = [list(combo) for combo in limited_deal_priorities]

    readiness = dict(configured_channels or {})
    readiness.setdefault(
        "ntfy",
        valid_ntfy_server_url(config.ntfy_server_url)
        and valid_ntfy_topic(config.ntfy_topic),
    )
    unconfigured: list[str] = []
    if config.ntfy_enabled and not readiness.get("ntfy", False):
        unconfigured.append("ntfy")
    if config.discord_enabled and not readiness.get("discord", False):
        unconfigured.append("Discord")
    if config.phone_alerts_enabled and not readiness.get("pushover", False):
        unconfigured.append("Pushover")
    return SettingsUpdate(config, normalized, tuple(unconfigured))
