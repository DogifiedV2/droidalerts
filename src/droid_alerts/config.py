from __future__ import annotations

import json
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
    monitor_index: int = 1
    capture_interval_seconds: float = 0.25
    dedupe_seconds: float = 12.0
    alert_cooldown_seconds: float = 10.0
    sound_enabled: bool = True
    popup_enabled: bool = True
    droid_timers_enabled: bool = False
    popup_seconds: float = 8.0
    popup_icon_file: str = "signals_icon.png"
    save_alert_samples: bool = True
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
    phone_alerts_enabled: bool = True
    phone_credentials_file: str = "phone_alerts.json"
    phone_env_token: str = "DROIDWATCHER_PHONE_ALERTS_TOKEN"
    phone_env_user: str = "DROIDWATCHER_PHONE_ALERTS_USER"
    phone_sound: str = "siren"
    phone_include_attachment: bool = True
    update_check_enabled: bool = True
    update_repo: str = "DogifiedV2/droidalerts"
    advanced_mode: bool = False
    extra_checks: bool = False
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
            monitor_index=int(data.get("monitor_index", 1)),
            capture_interval_seconds=float(data.get("capture_interval_seconds", 0.25)),
            dedupe_seconds=float(data.get("dedupe_seconds", 12.0)),
            alert_cooldown_seconds=float(data.get("alert_cooldown_seconds", 10.0)),
            sound_enabled=bool(data.get("sound_enabled", True)),
            popup_enabled=bool(data.get("popup_enabled", True)),
            droid_timers_enabled=bool(data.get("droid_timers_enabled", False)),
            popup_seconds=float(data.get("popup_seconds", 8.0)),
            popup_icon_file=str(data.get("popup_icon_file", "signals_icon.png")),
            save_alert_samples=bool(data.get("save_alert_samples", True)),
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
            phone_alerts_enabled=bool(data.get("phone_alerts_enabled", True)),
            phone_credentials_file=str(data.get("phone_credentials_file", "phone_alerts.json")),
            phone_env_token=str(data.get("phone_env_token", "DROIDWATCHER_PHONE_ALERTS_TOKEN")),
            phone_env_user=str(data.get("phone_env_user", "DROIDWATCHER_PHONE_ALERTS_USER")),
            phone_sound=str(data.get("phone_sound", "siren")),
            phone_include_attachment=bool(data.get("phone_include_attachment", True)),
            update_check_enabled=bool(data.get("update_check_enabled", True)),
            update_repo=str(data.get("update_repo", "DogifiedV2/droidalerts")),
            advanced_mode=bool(data.get("advanced_mode", False)),
            extra_checks=bool(data.get("extra_checks", False)),
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
            config.alert_targets = [list(pair) for pair in data["alert_targets"]]
        return config

    def to_dict(self) -> dict[str, Any]:
        return {
            "monitor_index": self.monitor_index,
            "capture_interval_seconds": self.capture_interval_seconds,
            "dedupe_seconds": self.dedupe_seconds,
            "alert_cooldown_seconds": self.alert_cooldown_seconds,
            "sound_enabled": self.sound_enabled,
            "popup_enabled": self.popup_enabled,
            "droid_timers_enabled": self.droid_timers_enabled,
            "popup_seconds": self.popup_seconds,
            "popup_icon_file": self.popup_icon_file,
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
            "update_repo": self.update_repo,
            "advanced_mode": self.advanced_mode,
            "extra_checks": self.extra_checks,
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
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return AppConfig.from_dict(data)


def save_config(config: AppConfig) -> None:
    path = config_dir() / CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
