from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .alert_customization import alert_id_aliases, alert_type_id
from .classifier import Detection
from .config import AppConfig, config_dir
from .network import certifi_ssl_context


APP_NAME = "Droid Alerts"
UA_NAME = "DroidAlerts"
USER_AGENT = f"{UA_NAME}/{__version__}"


@dataclass(frozen=True)
class DeliveryResult:
    channel: str
    success: bool
    message: str


@dataclass(frozen=True)
class AlertDelivery:
    """One enabled remote-channel delivery for an alert."""

    label: str
    target: Callable[..., DeliveryResult]
    args: tuple[object, ...]
    kwargs: dict[str, object]


def _delivery(channel: str, success: bool, message: str) -> DeliveryResult:
    return DeliveryResult(channel=channel, success=success, message=message)


def _is_belt_detection(detection: Detection) -> bool:
    return detection.source == "belt-tracker" or detection.rarity == "Belt"


def _is_limited_deal_detection(detection: Detection) -> bool:
    return detection.source == "limited-deal"


def _is_rebirth_available_detection(detection: Detection) -> bool:
    return detection.source == "rebirth-alert"


def _is_rebirth_ready_detection(detection: Detection) -> bool:
    return detection.source == "rebirth-ready"


def _is_cb23_mission_detection(detection: Detection) -> bool:
    return detection.source == "cb23-mission"


def _is_scrap_alert_detection(detection: Detection) -> bool:
    return detection.source in {"scrap-alert", "scrap-inactive"}


def _is_timer_reminder_detection(detection: Detection) -> bool:
    return detection.source == "timer-reminder"


def _is_rebirth_detection(detection: Detection) -> bool:
    return _is_rebirth_available_detection(detection) or _is_rebirth_ready_detection(detection)


def event_text(detection: Detection) -> str:
    if _is_scrap_alert_detection(detection):
        return (
            "Scrap inactive for 5+ min. Possibly kicked from the lobby."
            if detection.source == "scrap-inactive"
            else "Your income is no longer increasing"
        )
    if _is_cb23_mission_detection(detection):
        return "CB23 Mission"
    if _is_timer_reminder_detection(detection):
        return f"{detection.droid} in {detection.rarity} seconds"
    if _is_rebirth_detection(detection):
        return (
            "A rebirth droid is available"
            if _is_rebirth_available_detection(detection)
            else "Rebirth Ready"
        )
    if _is_limited_deal_detection(detection):
        return f"Limited Deal: {detection.rarity} {detection.droid}"
    if _is_belt_detection(detection):
        rarity = "" if detection.rarity == "Belt" else f"{detection.rarity} "
        return f"{rarity}{detection.droid} blueprint is on the belt"
    return f"{detection.droid} Droid ({detection.rarity})"


def alert_title(detection: Detection) -> str:
    if _is_scrap_alert_detection(detection):
        return "Droid Alerts Scrap Alert"
    if _is_cb23_mission_detection(detection):
        return "Droid Alerts CB23 Mission"
    if _is_timer_reminder_detection(detection):
        return "Droid Alerts Timer Reminder"
    if _is_rebirth_detection(detection):
        return (
            "Droid Alerts Rebirth Alert"
            if _is_rebirth_available_detection(detection)
            else "Droid Alerts Rebirth Ready"
        )
    if _is_limited_deal_detection(detection):
        return "Droid Alerts Limited Deal"
    if _is_belt_detection(detection):
        return "Droid Alerts Belt Tracker"
    return "Droid Alerts Priority Spawn"


def discord_webhook_path(config: AppConfig) -> Path:
    return config_dir() / config.discord_webhook_file


def limited_deal_discord_webhook_path(config: AppConfig) -> Path:
    return config_dir() / config.limited_deal_discord_webhook_file


def discord_destinations_path(config: AppConfig) -> Path:
    return config_dir() / config.discord_destinations_file


def phone_credentials_path(config: AppConfig) -> Path:
    return config_dir() / config.phone_credentials_file


def ntfy_token_path(config: AppConfig) -> Path:
    return config_dir() / config.ntfy_token_file


def valid_discord_webhook_url(value: str) -> bool:
    text = value.strip().lstrip("\ufeff")
    return text.startswith(
        ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
    )


def save_discord_webhook(config: AppConfig, webhook_url: str) -> Path:
    path = discord_webhook_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(webhook_url.strip().lstrip("\ufeff") + "\n", encoding="utf-8")
    return path


def save_limited_deal_discord_webhook(
    config: AppConfig,
    webhook_url: str,
) -> Path:
    path = limited_deal_discord_webhook_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = webhook_url.strip().lstrip("\ufeff")
    if value:
        path.write_text(value + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()
    return path


def save_discord_destinations(
    config: AppConfig,
    destinations: dict[str, str],
) -> Path:
    path = discord_destinations_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {
        " ".join(str(name).split())[:64]: str(url).strip().lstrip("\ufeff")
        for name, url in destinations.items()
        if " ".join(str(name).split())
        and valid_discord_webhook_url(str(url))
    }
    if cleaned:
        path.write_text(
            json.dumps(cleaned, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif path.exists():
        path.unlink()
    return path


def load_discord_destinations(config: AppConfig) -> dict[str, str]:
    path = discord_destinations_path(config)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("Discord destinations must contain a JSON object")
    return {
        " ".join(str(name).split())[:64]: str(url).strip().lstrip("\ufeff")
        for name, url in raw.items()
        if " ".join(str(name).split())
        and valid_discord_webhook_url(str(url))
    }


def discord_destination_names(config: AppConfig) -> tuple[str, ...]:
    names = ["Main"]
    limited_url, _ = load_limited_deal_discord_webhook(config)
    if limited_url:
        names.append("Limited Deals")
    names.extend(
        name
        for name in sorted(load_discord_destinations(config), key=str.casefold)
        if name.casefold() not in {"main", "limited deals"}
    )
    return tuple(names)


def load_discord_webhook(config: AppConfig) -> tuple[str | None, str | Path | None]:
    env_url = os.environ.get(config.discord_env_var, "").strip().lstrip("\ufeff")
    if env_url:
        return env_url, "environment"

    path = discord_webhook_path(config)
    if not path.exists():
        return None, None
    value = path.read_text(encoding="utf-8-sig").strip().lstrip("\ufeff")
    if not value or value.lower().startswith("disabled"):
        return None, path
    return value, path


def load_limited_deal_discord_webhook(
    config: AppConfig,
) -> tuple[str | None, str | Path | None]:
    env_url = os.environ.get(
        config.limited_deal_discord_env_var,
        "",
    ).strip().lstrip("\ufeff")
    if env_url:
        return env_url, "environment"

    path = limited_deal_discord_webhook_path(config)
    if not path.exists():
        return None, None
    value = path.read_text(encoding="utf-8-sig").strip().lstrip("\ufeff")
    if not value or value.lower().startswith("disabled"):
        return None, path
    return value, path


def load_discord_webhook_for_detection(
    config: AppConfig,
    detection: Detection,
    *,
    fallback_url: str | None = None,
) -> tuple[str | None, str | Path | None]:
    """Load the routed webhook, with current main/deal fallbacks preserved."""

    alert_id = alert_type_id(detection)
    destination = config.discord_alert_destinations.get(alert_id, "")
    if not destination:
        destination = next(
            (
                config.discord_alert_destinations[alias]
                for alias in alert_id_aliases(alert_id)
                if alias in config.discord_alert_destinations
            ),
            "",
        )
    if destination.casefold() == "main":
        if fallback_url:
            return fallback_url, "default webhook"
        return load_discord_webhook(config)
    elif destination.casefold() == "limited deals":
        dedicated_url, dedicated_source = load_limited_deal_discord_webhook(config)
        if dedicated_url:
            return dedicated_url, dedicated_source
    elif destination:
        custom = load_discord_destinations(config)
        for name, url in custom.items():
            if name.casefold() == destination.casefold():
                return url, discord_destinations_path(config)
    if _is_limited_deal_detection(detection) and not destination:
        dedicated_url, dedicated_source = load_limited_deal_discord_webhook(config)
        if dedicated_url:
            return dedicated_url, dedicated_source
    if fallback_url:
        return fallback_url, "default webhook"
    return load_discord_webhook(config)


def discord_webhook_configured(config: AppConfig) -> bool:
    webhook_url, _source = load_discord_webhook(config)
    return bool(webhook_url)


def discord_color(detection: Detection) -> int:
    if _is_scrap_alert_detection(detection):
        return 0xE7A72F
    if _is_cb23_mission_detection(detection):
        return 0xF04444
    if _is_timer_reminder_detection(detection):
        return {
            "Beskar Timer": 0xC9CDD9,
            "Mythic Timer": 0xFF3FA8,
            "Galactic Timer": 0x9200E0,
        }.get(detection.droid, 0x39C6D8)
    if _is_rebirth_detection(detection):
        return 0xFFB11B if _is_rebirth_available_detection(detection) else 0x20F070
    if _is_limited_deal_detection(detection):
        family = detection.rarity.split(" ", 1)[0]
        return {
            "Gold": 0xFFD34D,
            "Diamond": 0x3FD9FF,
            "Rainbow": 0xFF33CC,
            "Beskar": 0xC9CDD9,
            "Galactic": 0x9200E0,
        }.get(family, 0x9B59B6)
    if _is_belt_detection(detection):
        family = detection.rarity.split(" ", 1)[0]
        return {
            "Gold": 0xFFD34D,
            "Diamond": 0x3FD9FF,
            "Beskar": 0xC9CDD9,
            "Rainbow": 0xFF33CC,
            "Galactic": 0x9200E0,
        }.get(family, 0x9B59B6)
    if detection.droid == "Rainbow":
        return 0xFF33CC
    if detection.droid == "Galactic":
        return 0x9200E0
    if detection.rarity == "Mythic":
        return 0xFF3366
    if detection.rarity == "Legendary":
        return 0xFFD700
    return 0x9B59B6


def discord_message_content(
    config: AppConfig | None,
    detection: Detection,
) -> tuple[str, dict[str, object]]:
    label = event_text(detection)
    if config is None:
        return f"**{label}**", {"parse": []}
    alert_id = alert_type_id(detection)
    configured_ids = (alert_id, *alert_id_aliases(alert_id))
    prefix = next(
        (
            config.discord_message_prefixes[value]
            for value in configured_ids
            if value in config.discord_message_prefixes
        ),
        "",
    ).strip()
    mention = next(
        (
            config.discord_mentions[value]
            for value in configured_ids
            if value in config.discord_mentions
        ),
        {},
    )
    mention_type = str(mention.get("type") or "")
    snowflake = str(mention.get("id") or "")
    allowed: dict[str, object] = {"parse": []}
    mention_text = ""
    valid_snowflake = snowflake.isdigit() and 15 <= len(snowflake) <= 22
    if mention_type == "role" and valid_snowflake:
        mention_text = f"<@&{snowflake}>"
        allowed["roles"] = [snowflake]
    elif mention_type == "user" and valid_snowflake:
        mention_text = f"<@{snowflake}>"
        allowed["users"] = [snowflake]
    parts = [part for part in (mention_text, prefix, f"**{label}**") if part]
    return " ".join(parts), allowed


def post_discord(
    webhook_url: str,
    detection: Detection,
    *,
    config: AppConfig | None = None,
) -> None:
    label = event_text(detection)
    content, allowed_mentions = discord_message_content(config, detection)
    payload = json.dumps(
        {
            "username": "Droid Tycoon Alerts",
            "allowed_mentions": allowed_mentions,
            "content": content,
            "embeds": [
                {
                    "title": (
                        (
                            "Rebirth Alert"
                            if _is_rebirth_available_detection(detection)
                            else "Rebirth Ready"
                        )
                        if _is_rebirth_detection(detection)
                        else (
                            "CB23 Mission"
                            if _is_cb23_mission_detection(detection)
                            else (
                                "Timer Reminder"
                                if _is_timer_reminder_detection(detection)
                                else (
                                    "Limited Deal Alert"
                                    if _is_limited_deal_detection(detection)
                                    else (
                                        "Belt Tracker Alert"
                                        if _is_belt_detection(detection)
                                        else "Droid Tycoon Priority Spawn"
                                    )
                                )
                            )
                        )
                    ),
                    "description": f"**{label}**",
                    "color": discord_color(detection),
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(
        request,
        timeout=8,
        context=certifi_ssl_context(),
    ) as response:
        if response.status not in (200, 204):
            print(f"[DISCORD] Unexpected response: HTTP {response.status}")
        else:
            print("[DISCORD] Priority alert sent.")


def send_discord_alert(
    webhook_url: str,
    detection: Detection,
    *,
    config: AppConfig | None = None,
) -> DeliveryResult:
    try:
        post_discord(webhook_url, detection, config=config)
        return _delivery("Discord", True, "Delivered")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        print(f"[DISCORD] HTTP {exc.code}: {body}")
        return _delivery("Discord", False, f"HTTP {exc.code}: {body}")
    except Exception as exc:
        print(f"[DISCORD] Failed to send alert: {exc}")
        return _delivery("Discord", False, str(exc))


def normalize_ntfy_server_url(value: str) -> str:
    text = value.strip().rstrip("/")
    if not text:
        return "https://ntfy.sh"
    return text


def valid_ntfy_server_url(value: str) -> bool:
    text = normalize_ntfy_server_url(value)
    parsed = urllib.parse.urlparse(text)
    return parsed.scheme in {"https", "http"} and bool(parsed.netloc)


def valid_ntfy_topic(value: str) -> bool:
    topic = value.strip()
    return bool(topic) and len(topic) <= 64 and re.fullmatch(r"[-_A-Za-z0-9]+", topic) is not None


def save_ntfy_token(config: AppConfig, token: str) -> Path:
    path = ntfy_token_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = token.strip()
    if token:
        path.write_text(token + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()
    return path


def load_ntfy_token(config: AppConfig) -> tuple[str | None, str | Path | None]:
    env_token = os.environ.get(config.ntfy_env_token, "").strip()
    if env_token:
        return env_token, "environment"

    path = ntfy_token_path(config)
    if not path.exists():
        return None, None
    value = path.read_text(encoding="utf-8-sig").strip().lstrip("\ufeff")
    if not value or value.lower().startswith("disabled"):
        return None, path
    return value, path


def ntfy_configured(config: AppConfig) -> bool:
    return valid_ntfy_server_url(config.ntfy_server_url) and valid_ntfy_topic(config.ntfy_topic)


def ntfy_publish_url(config: AppConfig) -> str:
    server_url = normalize_ntfy_server_url(config.ntfy_server_url)
    topic = urllib.parse.quote(config.ntfy_topic.strip(), safe="")
    return f"{server_url}/{topic}"


def post_ntfy_message(
    config: AppConfig,
    *,
    title: str,
    message: str,
    priority: str,
    tags: str,
    attachment_path: Path | None,
    success_log: str,
) -> None:
    if not ntfy_configured(config):
        raise ValueError("ntfy server URL and topic are required")

    token, _source = load_ntfy_token(config)
    headers = {
        "User-Agent": USER_AGENT,
        "Title": title,
        "Priority": str(priority or "5"),
        "Tags": str(tags or "rotating_light"),
    }
    if config.ntfy_cache:
        headers["Cache"] = str(config.ntfy_cache)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data: bytes
    method = "POST"
    if attachment_path is not None and attachment_path.exists():
        if attachment_path.stat().st_size <= 2_000_000:
            data = attachment_path.read_bytes()
            method = "PUT"
            headers["Filename"] = attachment_path.name
            headers["Message"] = message
        else:
            print(f"[NTFY] Skipping attachment over 2 MB: {attachment_path}")
            data = message.encode("utf-8")
    else:
        data = message.encode("utf-8")

    request = urllib.request.Request(
        ntfy_publish_url(config),
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(
        request,
        timeout=8,
        context=certifi_ssl_context(),
    ) as response:
        if response.status not in (200, 201):
            print(f"[NTFY] Unexpected response: HTTP {response.status}")
        else:
            print(success_log)


def send_ntfy_test_alert(config: AppConfig) -> None:
    post_ntfy_message(
        config,
        title="Droid Alerts Test Alert",
        message="ntfy alerts are set up. Priority droid alerts will use this topic.",
        priority=config.ntfy_priority,
        tags=config.ntfy_tags,
        attachment_path=None,
        success_log="[NTFY] Test alert sent.",
    )


def send_ntfy_alert(
    config: AppConfig,
    detection: Detection,
    *,
    attachment_path: Path | None,
) -> DeliveryResult:
    try:
        post_ntfy_message(
            config,
            title=alert_title(detection),
            message=event_text(detection),
            priority=config.ntfy_priority,
            tags=config.ntfy_tags,
            attachment_path=attachment_path,
            success_log="[NTFY] Priority alert sent.",
        )
        return _delivery("ntfy", True, "Delivered")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        print(f"[NTFY] HTTP {exc.code}: {body}")
        return _delivery("ntfy", False, f"HTTP {exc.code}: {body}")
    except Exception as exc:
        print(f"[NTFY] Failed to send alert: {exc}")
        return _delivery("ntfy", False, str(exc))


def save_phone_credentials(config: AppConfig, token: str, user: str) -> Path:
    path = phone_credentials_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"token": token.strip(), "user": user.strip()}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_phone_alert_credentials(config: AppConfig) -> tuple[dict[str, str] | None, str | Path | None]:
    token = os.environ.get(config.phone_env_token, "").strip()
    user = os.environ.get(config.phone_env_user, "").strip()
    if token and user:
        return {"token": token, "user": user}, "environment"

    path = phone_credentials_path(config)
    if not path.exists():
        return None, None

    raw_text = path.read_text(encoding="utf-8-sig").strip()
    if not raw_text or raw_text.lower().startswith("disabled"):
        return None, path

    data = json.loads(raw_text)
    if not isinstance(data, dict):
        raise ValueError(f"Phone alert credentials file must be a JSON object: {path}")

    token = str(data.get("token", "")).strip()
    user = str(data.get("user", "")).strip()
    if not token or not user:
        return None, path
    return {"token": token, "user": user}, path


def phone_alerts_configured(config: AppConfig) -> bool:
    try:
        credentials, _source = load_phone_alert_credentials(config)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return credentials is not None


def encode_multipart_form(fields: dict[str, str], attachment_path: Path) -> tuple[bytes, str]:
    boundary = f"DroidAlertsBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    filename = attachment_path.name.replace('"', "_")
    content_type = "image/png" if attachment_path.suffix.lower() == ".png" else "application/octet-stream"
    chunks.append(f"--{boundary}\r\n".encode("ascii"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="attachment"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("ascii")
    )
    chunks.append(attachment_path.read_bytes())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))

    return b"".join(chunks), boundary


def post_phone_message(
    credentials: dict[str, str],
    *,
    title: str,
    message: str,
    priority: int,
    sound: str,
    attachment_path: Path | None,
    success_log: str,
) -> None:
    fields = {
        "token": credentials["token"],
        "user": credentials["user"],
        "title": title,
        "message": message,
        "priority": str(priority),
        "timestamp": str(int(time.time())),
    }
    if sound:
        fields["sound"] = sound

    headers = {"User-Agent": USER_AGENT}
    if attachment_path is not None and attachment_path.exists():
        if attachment_path.stat().st_size <= 5_242_880:
            data, boundary = encode_multipart_form(fields, attachment_path)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        else:
            print(f"[PHONE] Skipping attachment over 5 MB: {attachment_path}")
            data = urllib.parse.urlencode(fields).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        data = urllib.parse.urlencode(fields).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(
        "https://api.pushover.net/1/messages.json",
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(
        request,
        timeout=8,
        context=certifi_ssl_context(),
    ) as response:
        body = response.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        if response.status != 200 or payload.get("status") != 1:
            errors = payload.get("errors") or body[:300]
            raise RuntimeError(f"HTTP {response.status} {errors}")
        print(success_log)


def send_phone_test_alert(credentials: dict[str, str], *, sound: str) -> None:
    post_phone_message(
        credentials,
        title="Droid Alerts Test Alert",
        message="Phone alerts are set up. Priority droid alerts will use this same +1 priority sound.",
        priority=1,
        sound=sound,
        attachment_path=None,
        success_log="[PHONE] Test alert sent.",
    )


def send_phone_alert(
    credentials: dict[str, str],
    detection: Detection,
    *,
    sound: str,
    attachment_path: Path | None,
) -> DeliveryResult:
    try:
        post_phone_message(
            credentials,
            title=alert_title(detection),
            message=event_text(detection),
            priority=1,
            sound=sound,
            attachment_path=attachment_path,
            success_log="[PHONE] Priority alert sent.",
        )
        return _delivery("Pushover", True, "Delivered")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        print(f"[PHONE] HTTP {exc.code}: {body}")
        return _delivery("Pushover", False, f"HTTP {exc.code}: {body}")
    except Exception as exc:
        print(f"[PHONE] Failed to send alert: {exc}")
        return _delivery("Pushover", False, str(exc))


def enabled_alert_deliveries(
    config: AppConfig,
    detection: Detection,
    *,
    webhook_url: str | None,
    phone_credentials: dict[str, str] | None,
    ntfy_ready: bool,
    attachment_path: Path | None,
    discord_sender: Callable[..., DeliveryResult],
    ntfy_sender: Callable[..., DeliveryResult],
    phone_sender: Callable[..., DeliveryResult],
) -> tuple[AlertDelivery, ...]:
    """Build the remote deliveries allowed for this alert and channel config."""
    alert_id = alert_type_id(detection)
    deliveries: list[AlertDelivery] = []
    if (
        config.discord_enabled
        and webhook_url
        and config.channel_allows_alert("discord", alert_id)
    ):
        deliveries.append(
            AlertDelivery(
                "Discord",
                discord_sender,
                (webhook_url, detection),
                {"config": config},
            )
        )
    if (
        config.ntfy_enabled
        and ntfy_ready
        and config.channel_allows_alert("ntfy", alert_id)
    ):
        deliveries.append(
            AlertDelivery(
                "ntfy",
                ntfy_sender,
                (config, detection),
                {
                    "attachment_path": (
                        attachment_path if config.ntfy_include_attachment else None
                    )
                },
            )
        )
    if (
        config.phone_alerts_enabled
        and phone_credentials
        and config.channel_allows_alert("pushover", alert_id)
    ):
        deliveries.append(
            AlertDelivery(
                "Pushover",
                phone_sender,
                (phone_credentials, detection),
                {
                    "sound": config.phone_sound,
                    "attachment_path": (
                        attachment_path if config.phone_include_attachment else None
                    ),
                },
            )
        )
    return tuple(deliveries)


def version_parts(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lower().removeprefix("v")
    parts: list[int] = []
    for chunk in re.split(r"[^0-9]+", cleaned):
        if chunk:
            parts.append(int(chunk))
    return tuple(parts) or (0,)


def version_is_newer(candidate: str, current: str = __version__) -> bool:
    candidate_parts = list(version_parts(candidate))
    current_parts = list(version_parts(current))
    width = max(len(candidate_parts), len(current_parts))
    candidate_parts.extend([0] * (width - len(candidate_parts)))
    current_parts.extend([0] * (width - len(current_parts)))
    return tuple(candidate_parts) > tuple(current_parts)


def latest_release_info(repo: str) -> dict[str, str] | None:
    repo = repo.strip().strip("/")
    if not repo or "/" not in repo:
        return None
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=6,
            context=certifi_ssl_context(),
        ) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name", "")).strip()
    if not tag:
        return None
    package_zip_url = ""
    # Accept both the published asset name and Build EXE.bat's raw output
    # name, so a forgotten rename doesn't silently break packaged updates.
    package_asset_names = {"droidalerts.zip", "droidalerts-windows.zip"}
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name", "")).lower() in package_asset_names:
            package_zip_url = str(asset.get("browser_download_url", "") or "")
            break
    source_zip_url = str(
        payload.get("zipball_url", "")
        or f"https://github.com/{repo}/archive/refs/tags/{tag}.zip"
    )
    return {
        "tag": tag,
        "name": str(payload.get("name", "") or tag),
        "url": str(payload.get("html_url", "") or f"https://github.com/{repo}/releases/latest"),
        "zip_url": package_zip_url or source_zip_url,
        "package_zip_url": package_zip_url,
        "source_zip_url": source_zip_url,
    }


def check_for_update(config: AppConfig) -> dict[str, str] | None:
    if not config.update_check_enabled:
        return None
    release = latest_release_info(config.update_repo)
    if release and version_is_newer(release["tag"]):
        return release
    return None
