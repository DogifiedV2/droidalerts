from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def app_root() -> Path:
    """Writable app folder.

    In source runs this is the repository root. In a PyInstaller build this is
    the folder containing Droid Alerts.exe, so config and logs stay beside the
    app and survive updates.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bundled_root() -> Path:
    """Read-only bundled files folder for PyInstaller builds."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return app_root()


def project_root() -> Path:
    """Droid Alerts project root. Everything lives under here, so the tool is fully
    self-contained and never writes to OS user-data directories."""
    return app_root()


def config_dir() -> Path:
    return project_root() / "config"


def data_dir() -> Path:
    return project_root() / "data"


def templates_dir() -> Path:
    bundled = bundled_root() / "templates"
    return bundled if bundled.exists() else project_root() / "templates"


def sounds_dir() -> Path:
    return assets_dir() / "sounds"


def user_sounds_dir() -> Path:
    """Writable folder for sounds added from the GUI."""
    return data_dir() / "sounds"


def assets_dir() -> Path:
    bundled = bundled_root() / "assets"
    return bundled if bundled.exists() else project_root() / "assets"


CONFIG_FILE = "config.json"
CALIBRATION_FILE = "calibration.json"

# Reference scale everything is normalized to before classification.
# The templates/columns were captured at 2560x1440 with 44px rows.
REFERENCE_ROW_HEIGHT_PX = 44
REFERENCE_SCREEN_HEIGHT = 1440
REFERENCE_SCREEN_WIDTH = 2560


@dataclass
class Thresholds:
    rarity_threshold: float = 0.35
    droid_threshold: float = 0.15
    scale_min: float = 0.4
    scale_max: float = 2.5


@dataclass
class AppConfig:
    config_version: int = 2
    monitor_index: int = 1
    capture_interval_seconds: float = 0.25
    dedupe_seconds: float = 12.0
    alert_cooldown_seconds: float = 10.0
    sound_enabled: bool = True
    popup_enabled: bool = True
    droid_timers_enabled: bool = False
    # Overlay layout, user-adjustable via "Adjust Timers": size factor plus
    # position as fractions of the screen (center-x, top-y).
    droid_timers_scale: float = 1.0
    droid_timers_center_x: float = 0.5
    droid_timers_top_y: float = 0.006
    popup_seconds: float = 8.0
    popup_icon_file: str = "signals_icon.png"
    popup_position: str = "top_center"
    popup_scale: float = 1.0
    popup_opacity: float = 1.0
    sound_file: str = ""
    save_alert_samples: bool = False
    save_debug_screenshots: bool = False
    discord_enabled: bool = False
    discord_webhook_file: str = "discord_webhook.txt"
    discord_env_var: str = "DROID_DISCORD_WEBHOOK_URL"
    ntfy_enabled: bool = False
    ntfy_server_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token_file: str = "ntfy_token.txt"
    ntfy_env_token: str = "DROIDALERTS_NTFY_TOKEN"
    ntfy_priority: str = "5"
    ntfy_tags: str = "rotating_light"
    ntfy_cache: str = "no"
    ntfy_include_attachment: bool = False
    notification_setup_prompted: bool = False
    intro_shown: bool = False
    phone_alerts_enabled: bool = False
    phone_credentials_file: str = "phone_alerts.json"
    phone_env_token: str = "DROIDWATCHER_PHONE_ALERTS_TOKEN"
    phone_env_user: str = "DROIDWATCHER_PHONE_ALERTS_USER"
    phone_sound: str = "siren"
    phone_include_attachment: bool = False
    update_check_enabled: bool = True
    anonymous_stats_url: str = "https://gonk.tools/api/droid-alerts/heartbeat"
    anonymous_detection_url: str = "https://gonk.tools/api/droid-alerts/detections"
    share_debug_detections: bool = False
    debug_detection_upload_url: str = "https://gonk.tools/api/droid-alerts/debug-detections"
    update_repo: str = "DogifiedV2/droidalerts"
    advanced_mode: bool = False
    extra_checks: bool = False
    start_watcher_on_launch: bool = False
    pause_when_game_closed: bool = False
    retention_days: int = 30
    max_storage_mb: int = 500
    timer_reminders_enabled: bool = False
    timer_reminder_seconds: int = 60
    timer_offset_seconds: int = 0
    validation_failures_before_calibration_prompt: int = 30
    thresholds: Thresholds = field(default_factory=Thresholds)
    alert_targets: list[list[str]] = field(
        default_factory=lambda: [
            ["Beskar", "Epic"],
            ["Beskar", "Legendary"],
            ["Diamond", "Mythic"],
            ["Rainbow", "Mythic"],
            ["Beskar", "Mythic"],
        ]
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        thresholds = data.get("thresholds") or {}
        config = cls(
            config_version=max(2, int(data.get("config_version", 1))),
            monitor_index=int(data.get("monitor_index", 1)),
            capture_interval_seconds=float(data.get("capture_interval_seconds", 0.25)),
            dedupe_seconds=float(data.get("dedupe_seconds", 12.0)),
            alert_cooldown_seconds=float(data.get("alert_cooldown_seconds", 10.0)),
            sound_enabled=bool(data.get("sound_enabled", True)),
            popup_enabled=bool(data.get("popup_enabled", True)),
            droid_timers_enabled=bool(data.get("droid_timers_enabled", False)),
            droid_timers_scale=float(data.get("droid_timers_scale", 1.0)),
            droid_timers_center_x=float(data.get("droid_timers_center_x", 0.5)),
            droid_timers_top_y=float(data.get("droid_timers_top_y", 0.006)),
            popup_seconds=float(data.get("popup_seconds", 8.0)),
            popup_icon_file=str(data.get("popup_icon_file", "signals_icon.png")),
            popup_position=str(data.get("popup_position", "top_center")),
            popup_scale=float(data.get("popup_scale", 1.0)),
            popup_opacity=float(data.get("popup_opacity", 1.0)),
            sound_file=str(data.get("sound_file", "")),
            save_alert_samples=bool(data.get("save_alert_samples", False)),
            save_debug_screenshots=bool(data.get("save_debug_screenshots", False)),
            discord_enabled=bool(data.get("discord_enabled", False)),
            discord_webhook_file=str(data.get("discord_webhook_file", "discord_webhook.txt")),
            discord_env_var=str(data.get("discord_env_var", "DROID_DISCORD_WEBHOOK_URL")),
            ntfy_enabled=bool(data.get("ntfy_enabled", False)),
            ntfy_server_url=str(data.get("ntfy_server_url", "https://ntfy.sh")),
            ntfy_topic=str(data.get("ntfy_topic", "")),
            ntfy_token_file=str(data.get("ntfy_token_file", "ntfy_token.txt")),
            ntfy_env_token=str(data.get("ntfy_env_token", "DROIDALERTS_NTFY_TOKEN")),
            ntfy_priority=str(data.get("ntfy_priority", "5")),
            ntfy_tags=str(data.get("ntfy_tags", "rotating_light")),
            ntfy_cache=str(data.get("ntfy_cache", "no")),
            ntfy_include_attachment=bool(data.get("ntfy_include_attachment", False)),
            notification_setup_prompted=bool(data.get("notification_setup_prompted", False)),
            intro_shown=bool(data.get("intro_shown", False)),
            phone_alerts_enabled=bool(data.get("phone_alerts_enabled", False)),
            phone_credentials_file=str(data.get("phone_credentials_file", "phone_alerts.json")),
            phone_env_token=str(data.get("phone_env_token", "DROIDWATCHER_PHONE_ALERTS_TOKEN")),
            phone_env_user=str(data.get("phone_env_user", "DROIDWATCHER_PHONE_ALERTS_USER")),
            phone_sound=str(data.get("phone_sound", "siren")),
            phone_include_attachment=bool(data.get("phone_include_attachment", False)),
            update_check_enabled=bool(data.get("update_check_enabled", True)),
            anonymous_stats_url=str(
                data.get("anonymous_stats_url", "https://gonk.tools/api/droid-alerts/heartbeat")
            ),
            anonymous_detection_url=str(
                data.get("anonymous_detection_url", "https://gonk.tools/api/droid-alerts/detections")
            ),
            share_debug_detections=bool(data.get("share_debug_detections", False)),
            debug_detection_upload_url=str(
                data.get("debug_detection_upload_url", "https://gonk.tools/api/droid-alerts/debug-detections")
            ),
            update_repo=str(data.get("update_repo", "DogifiedV2/droidalerts")),
            advanced_mode=bool(data.get("advanced_mode", False)),
            extra_checks=bool(data.get("extra_checks", False)),
            start_watcher_on_launch=bool(data.get("start_watcher_on_launch", False)),
            pause_when_game_closed=bool(data.get("pause_when_game_closed", False)),
            retention_days=int(data.get("retention_days", 30)),
            max_storage_mb=int(data.get("max_storage_mb", 500)),
            timer_reminders_enabled=bool(data.get("timer_reminders_enabled", False)),
            timer_reminder_seconds=int(data.get("timer_reminder_seconds", 60)),
            timer_offset_seconds=int(data.get("timer_offset_seconds", 0)),
            validation_failures_before_calibration_prompt=int(
                data.get("validation_failures_before_calibration_prompt", 30)
            ),
            thresholds=Thresholds(
                rarity_threshold=float(thresholds.get("rarity_threshold", 0.35)),
                droid_threshold=float(thresholds.get("droid_threshold", 0.15)),
                scale_min=float(thresholds.get("scale_min", 0.4)),
                scale_max=float(thresholds.get("scale_max", 2.5)),
            ),
        )
        if isinstance(data.get("alert_targets"), list):
            raw_targets = data["alert_targets"]
            pairs = [
                [str(pair[0]), str(pair[1])]
                for pair in raw_targets
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            ]
            if pairs or not raw_targets:
                config.alert_targets = pairs
        return config

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": max(2, self.config_version),
            "monitor_index": self.monitor_index,
            "capture_interval_seconds": self.capture_interval_seconds,
            "dedupe_seconds": self.dedupe_seconds,
            "alert_cooldown_seconds": self.alert_cooldown_seconds,
            "sound_enabled": self.sound_enabled,
            "popup_enabled": self.popup_enabled,
            "droid_timers_enabled": self.droid_timers_enabled,
            "droid_timers_scale": self.droid_timers_scale,
            "droid_timers_center_x": self.droid_timers_center_x,
            "droid_timers_top_y": self.droid_timers_top_y,
            "popup_seconds": self.popup_seconds,
            "popup_icon_file": self.popup_icon_file,
            "popup_position": self.popup_position,
            "popup_scale": self.popup_scale,
            "popup_opacity": self.popup_opacity,
            "sound_file": self.sound_file,
            "save_alert_samples": self.save_alert_samples,
            "save_debug_screenshots": self.save_debug_screenshots,
            "discord_enabled": self.discord_enabled,
            "discord_webhook_file": self.discord_webhook_file,
            "discord_env_var": self.discord_env_var,
            "ntfy_enabled": self.ntfy_enabled,
            "ntfy_server_url": self.ntfy_server_url,
            "ntfy_topic": self.ntfy_topic,
            "ntfy_token_file": self.ntfy_token_file,
            "ntfy_env_token": self.ntfy_env_token,
            "ntfy_priority": self.ntfy_priority,
            "ntfy_tags": self.ntfy_tags,
            "ntfy_cache": self.ntfy_cache,
            "ntfy_include_attachment": self.ntfy_include_attachment,
            "notification_setup_prompted": self.notification_setup_prompted,
            "intro_shown": self.intro_shown,
            "phone_alerts_enabled": self.phone_alerts_enabled,
            "phone_credentials_file": self.phone_credentials_file,
            "phone_env_token": self.phone_env_token,
            "phone_env_user": self.phone_env_user,
            "phone_sound": self.phone_sound,
            "phone_include_attachment": self.phone_include_attachment,
            "update_check_enabled": self.update_check_enabled,
            "anonymous_stats_url": self.anonymous_stats_url,
            "anonymous_detection_url": self.anonymous_detection_url,
            "share_debug_detections": self.share_debug_detections,
            "debug_detection_upload_url": self.debug_detection_upload_url,
            "update_repo": self.update_repo,
            "advanced_mode": self.advanced_mode,
            "extra_checks": self.extra_checks,
            "start_watcher_on_launch": self.start_watcher_on_launch,
            "pause_when_game_closed": self.pause_when_game_closed,
            "retention_days": self.retention_days,
            "max_storage_mb": self.max_storage_mb,
            "timer_reminders_enabled": self.timer_reminders_enabled,
            "timer_reminder_seconds": self.timer_reminder_seconds,
            "timer_offset_seconds": self.timer_offset_seconds,
            "validation_failures_before_calibration_prompt": self.validation_failures_before_calibration_prompt,
            "thresholds": {
                "rarity_threshold": self.thresholds.rarity_threshold,
                "droid_threshold": self.thresholds.droid_threshold,
                "scale_min": self.thresholds.scale_min,
                "scale_max": self.thresholds.scale_max,
            },
            "alert_targets": self.alert_targets,
        }

    @property
    def targets(self) -> set[tuple[str, str]]:
        return {(droid, rarity) for droid, rarity in self.alert_targets}


def load_config() -> AppConfig:
    path = config_dir() / CONFIG_FILE
    if not path.exists():
        config = AppConfig()
        save_config(config)
        return config

    def parse(candidate: Path) -> AppConfig:
        data = json.loads(candidate.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Config must contain a JSON object")
        return AppConfig.from_dict(data)

    try:
        return parse(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            return parse(backup)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # Keep the broken file for support instead of preventing the app
            # from opening. Atomic saves will rebuild a valid config below.
            corrupt = path.with_suffix(path.suffix + ".corrupt")
            try:
                if path.exists():
                    shutil.copy2(path, corrupt)
            except OSError:
                pass
            config = AppConfig()
            save_config(config)
            return config


def save_config(config: AppConfig) -> None:
    path = config_dir() / CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    backup = path.with_suffix(path.suffix + ".bak")
    temp.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
    if path.exists():
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
            shutil.copy2(path, backup)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    temp.replace(path)
