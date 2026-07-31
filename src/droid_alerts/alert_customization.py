from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


ALERT_CHANNELS = ("popup", "sound", "discord", "ntfy", "pushover")
TIMER_REMINDER_KINDS = ("beskar", "mythic", "galactic")


def alert_type_id(detection: object) -> str:
    """Return the stable configuration ID for a detected alert."""

    source = str(getattr(detection, "source", "") or "")
    if source == "rebirth-alert":
        return "rebirth_available"
    if source == "rebirth-ready":
        return "rebirth_ready"
    if source == "cb23-mission":
        return "cb23_mission"
    if source in {"scrap-alert", "scrap-inactive"}:
        return "scrap_alert"
    if source == "belt-tracker":
        return "belt_tracker"
    if source == "limited-deal":
        return "limited_deals"
    if source == "timer-reminder":
        kind = str(getattr(detection, "droid", "") or "").split(" ", 1)[0].casefold()
        if kind in TIMER_REMINDER_KINDS:
            return f"timer:{kind}"
        return "timer_reminder"
    return (
        f"chat:{getattr(detection, 'droid', '')}:"
        f"{getattr(detection, 'rarity', '')}"
    )


def alert_id_aliases(alert_id: str) -> tuple[str, ...]:
    if alert_id.startswith("timer:"):
        return ("timer_reminder",)
    return ()


def normalize_channel_disabled_alerts(raw: object) -> dict[str, list[str]]:
    if not isinstance(raw, Mapping):
        return {}
    normalized: dict[str, list[str]] = {}
    for raw_channel, raw_ids in raw.items():
        channel = str(raw_channel).casefold()
        if channel not in ALERT_CHANNELS or not isinstance(raw_ids, Sequence) or isinstance(
            raw_ids, (str, bytes)
        ):
            continue
        values = sorted(
            {
                str(alert_id).strip()[:128]
                for alert_id in raw_ids
                if str(alert_id).strip()
            }
        )
        if values:
            normalized[channel] = values
    return normalized


def normalize_alert_sound_overrides(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(alert_id).strip()[:128]: str(filename).strip()[:255]
        for alert_id, filename in raw.items()
        if str(alert_id).strip() and str(filename).strip()
    }


def normalize_timer_reminder_rules(
    raw: object,
    *,
    fallback_seconds: int = 60,
) -> dict[str, list[int]]:
    fallback = max(1, min(86_400, int(fallback_seconds)))
    if not isinstance(raw, Mapping):
        return {kind: [fallback] for kind in TIMER_REMINDER_KINDS}
    normalized: dict[str, list[int]] = {}
    for kind in TIMER_REMINDER_KINDS:
        values = raw.get(kind, [])
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            values = []
        leads: set[int] = set()
        for value in values:
            try:
                leads.add(max(1, min(86_400, int(value))))
            except (TypeError, ValueError):
                continue
        normalized[kind] = sorted(leads, reverse=True)[:8]
    return normalized


def normalize_discord_alert_destinations(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(alert_id).strip()[:128]: str(destination).strip()[:64]
        for alert_id, destination in raw.items()
        if str(alert_id).strip() and str(destination).strip()
    }


def normalize_discord_message_prefixes(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(alert_id).strip()[:128]: " ".join(str(prefix).split())[:160]
        for alert_id, prefix in raw.items()
        if str(alert_id).strip() and " ".join(str(prefix).split())
    }


def normalize_discord_mentions(raw: object) -> dict[str, dict[str, str]]:
    if not isinstance(raw, Mapping):
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for alert_id, value in raw.items():
        key = str(alert_id).strip()[:128]
        if not key or not isinstance(value, Mapping):
            continue
        mention_type = str(value.get("type") or "").casefold()
        snowflake = str(value.get("id") or "").strip()
        if mention_type in {"user", "role"} and snowflake.isdigit() and 15 <= len(snowflake) <= 22:
            normalized[key] = {"type": mention_type, "id": snowflake}
    return normalized


PROFILE_FIELDS = (
    "popup_enabled",
    "sound_enabled",
    "discord_enabled",
    "ntfy_enabled",
    "phone_alerts_enabled",
    "channel_disabled_alerts",
    "sound_file",
    "alert_sound_overrides",
    "popup_seconds",
    "popup_position",
    "popup_custom_position",
    "popup_center_x",
    "popup_top_y",
    "popup_scale",
    "popup_opacity",
    "wake_alarm_enabled",
    "wake_alarm_beskar_mythic",
    "wake_alarm_galactic_mythic",
    "quiet_hours_enabled",
    "quiet_hours_start",
    "quiet_hours_end",
    "quiet_hours_muted_channels",
    "quiet_hours_bypass_alerts",
    "discord_alert_destinations",
    "discord_message_prefixes",
    "discord_mentions",
    "droid_timers_enabled",
    "timer_reminders_enabled",
    "timer_reminder_rules",
    "timer_offset_seconds",
)
MAX_NOTIFICATION_PROFILES = 20


def normalize_notification_profiles(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return {}
    profiles: dict[str, dict[str, Any]] = {}
    for raw_name, raw_values in raw.items():
        name = " ".join(str(raw_name).split())[:40]
        if not name or not isinstance(raw_values, Mapping):
            continue
        values = {
            field: raw_values[field]
            for field in PROFILE_FIELDS
            if field in raw_values
        }
        profiles[name] = values
        if len(profiles) >= MAX_NOTIFICATION_PROFILES:
            break
    return profiles


def normalize_clock(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError):
        return fallback
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return fallback
    return f"{hour:02d}:{minute:02d}"


def normalize_iso_datetime(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat(timespec="seconds")


def quiet_period_active(config: object, now: datetime | None = None) -> bool:
    current = now or datetime.now().astimezone()
    snoozed_until = normalize_iso_datetime(getattr(config, "snoozed_until", ""))
    if snoozed_until:
        until = datetime.fromisoformat(snoozed_until)
        if current.astimezone(until.tzinfo) < until:
            return True
    if not bool(getattr(config, "quiet_hours_enabled", False)):
        return False
    start_text = normalize_clock(getattr(config, "quiet_hours_start", "23:00"), "23:00")
    end_text = normalize_clock(getattr(config, "quiet_hours_end", "08:00"), "08:00")
    start = tuple(int(part) for part in start_text.split(":"))
    end = tuple(int(part) for part in end_text.split(":"))
    current_value = (current.hour, current.minute)
    if start == end:
        return True
    if start < end:
        return start <= current_value < end
    return current_value >= start or current_value < end
