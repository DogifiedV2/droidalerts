from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .belt.targets import normalize_belt_target_tiers
from .limited_deals import (
    normalize_limited_deal_priority_alerts,
    normalize_limited_deal_target_tiers,
)
from .ui_theme import DEFAULT_THEME_KEY, normalize_theme_key


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
BELT_SCAN_FPS_MIN = 1
BELT_SCAN_FPS_MAX = 20
LEGACY_DEFAULT_ALERT_TARGETS = (
    ("Beskar", "Epic"),
    ("Beskar", "Legendary"),
    ("Diamond", "Mythic"),
    ("Rainbow", "Mythic"),
    ("Beskar", "Mythic"),
)
GALACTIC_DEFAULT_ALERT_TARGETS = (
    ("Galactic", "Epic"),
    ("Galactic", "Legendary"),
    ("Galactic", "Mythic"),
)
DEFAULT_ALERT_TARGETS = LEGACY_DEFAULT_ALERT_TARGETS + GALACTIC_DEFAULT_ALERT_TARGETS
CAPTURE_METADATA_MAX_LENGTH = 512


def normalize_capture_source(value: Any) -> str:
    source = str(value or "").strip().lower()
    return source if source in {"monitor", "window", "device"} else "monitor"


def normalize_capture_metadata(value: Any) -> str:
    text = str(value or "").replace("\x00", " ")
    return " ".join(text.split())[:CAPTURE_METADATA_MAX_LENGTH]


def normalize_belt_scan_fps(idle_fps: Any, active_fps: Any) -> tuple[int, int]:
    """Clamp persisted belt rates and keep idle no higher than active."""

    try:
        idle = int(idle_fps)
    except (TypeError, ValueError):
        idle = 4
    try:
        active = int(active_fps)
    except (TypeError, ValueError):
        active = 8
    idle = min(BELT_SCAN_FPS_MAX, max(BELT_SCAN_FPS_MIN, idle))
    active = min(BELT_SCAN_FPS_MAX, max(BELT_SCAN_FPS_MIN, active))
    return min(idle, active), active


@dataclass
class Thresholds:
    rarity_threshold: float = 0.35
    droid_threshold: float = 0.15
    scale_min: float = 0.4
    scale_max: float = 2.5


@dataclass
class AppConfig:
    config_version: int = 2
    ui_theme: str = DEFAULT_THEME_KEY
    monitor_index: int = 1
    capture_source: str = "monitor"
    capture_window_title: str = ""
    capture_window_process: str = ""
    capture_window_class: str = ""
    capture_device_name: str = ""
    capture_device_path: str = ""
    capture_device_vid: int | None = None
    capture_device_pid: int | None = None
    capture_device_backend: int = 0
    capture_interval_seconds: float = 0.25
    rebirth_ready_alert_enabled: bool = False
    rebirth_scan_interval_seconds: float = 5.0
    cb23_mission_alert_enabled: bool = False
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
    limited_deals_intro_shown: bool = False
    last_seen_version: str = ""
    # Fresh 1.3.0 installs must not see the community prompt. Older configs do
    # not contain this key, so from_dict treats a missing value as not prompted.
    discord_community_prompted: bool = True
    phone_alerts_enabled: bool = False
    phone_credentials_file: str = "phone_alerts.json"
    phone_env_token: str = "DROIDWATCHER_PHONE_ALERTS_TOKEN"
    phone_env_user: str = "DROIDWATCHER_PHONE_ALERTS_USER"
    phone_sound: str = "siren"
    phone_include_attachment: bool = False
    # Alert IDs excluded from each remote channel. Missing/empty means the
    # channel receives every enabled alert, preserving existing installs.
    channel_disabled_alerts: dict[str, list[str]] = field(default_factory=dict)
    update_check_enabled: bool = True
    anonymous_app_stats_url: str = "https://gonk.tools/api/droid-alerts/app-heartbeat"
    anonymous_stats_url: str = "https://gonk.tools/api/droid-alerts/heartbeat"
    anonymous_detection_url: str = "https://gonk.tools/api/droid-alerts/detections"
    anonymous_belt_stats_url: str = "https://gonk.tools/api/droid-alerts/belt-heartbeat"
    anonymous_belt_counts_url: str = "https://gonk.tools/api/droid-alerts/belt-counts"
    limited_deal_url: str = "https://gonk.tools/api/droid-alerts/limited-deal"
    share_debug_detections: bool = False
    debug_detection_upload_url: str = "https://gonk.tools/api/droid-alerts/debug-detections"
    update_repo: str = "DogifiedV2/droidalerts"
    advanced_mode: bool = False
    extra_checks: bool = False
    start_watcher_on_launch: bool = False
    rebirth_alert_enabled: bool = False
    belt_overlay_enabled: bool = True
    belt_dev_mode: bool = False
    belt_template_collection_enabled: bool = False
    belt_idle_scan_fps: int = 4
    belt_active_scan_fps: int = 8
    belt_cpu_warning_confirmed: bool = False
    belt_region_guide_confirmed: bool = False
    # Droid name -> minimum visual rarity tier. Empty means no Belt Tracker alerts.
    belt_target_tiers: dict[str, str] = field(default_factory=dict)
    # Rotating droid ID -> minimum Limited Deal mutation. Priority combos are exact.
    limited_deal_target_tiers: dict[str, str] = field(default_factory=dict)
    limited_deal_priority_alerts: list[list[str]] = field(default_factory=list)
    retention_days: int = 30
    max_storage_mb: int = 500
    timer_reminders_enabled: bool = False
    timer_reminder_seconds: int = 60
    timer_offset_seconds: int = 0
    validation_failures_before_calibration_prompt: int = 30
    thresholds: Thresholds = field(default_factory=Thresholds)
    alert_targets: list[list[str]] = field(
        default_factory=lambda: [list(combo) for combo in DEFAULT_ALERT_TARGETS]
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        if not isinstance(data, dict):
            raise ValueError("Config must contain a JSON object")
        raw_thresholds = data.get("thresholds")
        if raw_thresholds is None:
            thresholds: dict[str, Any] = {}
        elif isinstance(raw_thresholds, dict):
            thresholds = raw_thresholds
        else:
            raise ValueError("Config thresholds must contain a JSON object")
        belt_target_tiers = normalize_belt_target_tiers(data.get("belt_target_tiers"))
        limited_deal_target_tiers = normalize_limited_deal_target_tiers(
            data.get("limited_deal_target_tiers")
        )
        limited_deal_priority_alerts = normalize_limited_deal_priority_alerts(
            data.get("limited_deal_priority_alerts")
        )
        belt_idle_scan_fps, belt_active_scan_fps = normalize_belt_scan_fps(
            data.get("belt_idle_scan_fps", 4),
            data.get("belt_active_scan_fps", 8),
        )
        capture_source = normalize_capture_source(data.get("capture_source", "monitor"))
        capture_window_title = normalize_capture_metadata(data.get("capture_window_title", ""))
        capture_window_process = normalize_capture_metadata(
            data.get("capture_window_process", "")
        )
        capture_window_class = normalize_capture_metadata(data.get("capture_window_class", ""))
        capture_device_name = normalize_capture_metadata(data.get("capture_device_name", ""))
        capture_device_path = normalize_capture_metadata(data.get("capture_device_path", ""))
        try:
            capture_device_vid = int(data["capture_device_vid"])
        except (KeyError, TypeError, ValueError):
            capture_device_vid = None
        try:
            capture_device_pid = int(data["capture_device_pid"])
        except (KeyError, TypeError, ValueError):
            capture_device_pid = None
        try:
            capture_device_backend = int(data.get("capture_device_backend", 0))
        except (TypeError, ValueError):
            capture_device_backend = 0
        if capture_source == "window" and not any(
            (capture_window_title, capture_window_process, capture_window_class)
        ):
            capture_source = "monitor"
        if capture_source == "device" and not any(
            (capture_device_path, capture_device_name)
        ):
            capture_source = "monitor"
        config = cls(
            config_version=max(2, int(data.get("config_version", 1))),
            ui_theme=normalize_theme_key(data.get("ui_theme", DEFAULT_THEME_KEY)),
            monitor_index=int(data.get("monitor_index", 1)),
            capture_source=capture_source,
            capture_window_title=capture_window_title,
            capture_window_process=capture_window_process,
            capture_window_class=capture_window_class,
            capture_device_name=capture_device_name,
            capture_device_path=capture_device_path,
            capture_device_vid=capture_device_vid,
            capture_device_pid=capture_device_pid,
            capture_device_backend=capture_device_backend,
            capture_interval_seconds=float(data.get("capture_interval_seconds", 0.25)),
            rebirth_ready_alert_enabled=bool(
                data.get("rebirth_ready_alert_enabled", False)
            ),
            cb23_mission_alert_enabled=bool(
                data.get("cb23_mission_alert_enabled", False)
            ),
            rebirth_scan_interval_seconds=min(
                30.0,
                max(2.0, float(data.get("rebirth_scan_interval_seconds", 5.0))),
            ),
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
            limited_deals_intro_shown=bool(
                data.get("limited_deals_intro_shown", False)
            ),
            last_seen_version=str(data.get("last_seen_version", "")).strip(),
            discord_community_prompted=bool(data.get("discord_community_prompted", False)),
            phone_alerts_enabled=bool(data.get("phone_alerts_enabled", False)),
            phone_credentials_file=str(data.get("phone_credentials_file", "phone_alerts.json")),
            phone_env_token=str(data.get("phone_env_token", "DROIDWATCHER_PHONE_ALERTS_TOKEN")),
            phone_env_user=str(data.get("phone_env_user", "DROIDWATCHER_PHONE_ALERTS_USER")),
            phone_sound=str(data.get("phone_sound", "siren")),
            phone_include_attachment=bool(data.get("phone_include_attachment", False)),
            channel_disabled_alerts={
                str(channel): sorted({str(alert_id) for alert_id in alert_ids if str(alert_id)})
                for channel, alert_ids in data.get("channel_disabled_alerts", {}).items()
                if channel in {"discord", "ntfy", "pushover"} and isinstance(alert_ids, list)
            }
            if isinstance(data.get("channel_disabled_alerts", {}), dict)
            else {},
            update_check_enabled=bool(data.get("update_check_enabled", True)),
            anonymous_app_stats_url=str(
                data.get(
                    "anonymous_app_stats_url",
                    "https://gonk.tools/api/droid-alerts/app-heartbeat",
                )
            ),
            anonymous_stats_url=str(
                data.get("anonymous_stats_url", "https://gonk.tools/api/droid-alerts/heartbeat")
            ),
            anonymous_detection_url=str(
                data.get("anonymous_detection_url", "https://gonk.tools/api/droid-alerts/detections")
            ),
            anonymous_belt_stats_url=str(
                data.get(
                    "anonymous_belt_stats_url",
                    "https://gonk.tools/api/droid-alerts/belt-heartbeat",
                )
            ),
            anonymous_belt_counts_url=str(
                data.get(
                    "anonymous_belt_counts_url",
                    "https://gonk.tools/api/droid-alerts/belt-counts",
                )
            ),
            limited_deal_url=str(
                data.get(
                    "limited_deal_url",
                    "https://gonk.tools/api/droid-alerts/limited-deal",
                )
            ),
            share_debug_detections=bool(data.get("share_debug_detections", False)),
            debug_detection_upload_url=str(
                data.get("debug_detection_upload_url", "https://gonk.tools/api/droid-alerts/debug-detections")
            ),
            update_repo=str(data.get("update_repo", "DogifiedV2/droidalerts")),
            advanced_mode=bool(data.get("advanced_mode", False)),
            extra_checks=bool(data.get("extra_checks", False)),
            start_watcher_on_launch=bool(data.get("start_watcher_on_launch", False)),
            rebirth_alert_enabled=bool(data.get("rebirth_alert_enabled", False)),
            belt_overlay_enabled=bool(data.get("belt_overlay_enabled", True)),
            belt_dev_mode=bool(data.get("belt_dev_mode", False)),
            belt_template_collection_enabled=bool(
                data.get("belt_template_collection_enabled", False)
            ),
            belt_idle_scan_fps=belt_idle_scan_fps,
            belt_active_scan_fps=belt_active_scan_fps,
            belt_cpu_warning_confirmed=bool(
                data.get("belt_cpu_warning_confirmed", False)
            ),
            belt_region_guide_confirmed=bool(
                data.get("belt_region_guide_confirmed", False)
            ),
            belt_target_tiers=belt_target_tiers,
            limited_deal_target_tiers=limited_deal_target_tiers,
            limited_deal_priority_alerts=limited_deal_priority_alerts,
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
            if {tuple(pair) for pair in pairs} == set(LEGACY_DEFAULT_ALERT_TARGETS):
                pairs.extend([list(combo) for combo in GALACTIC_DEFAULT_ALERT_TARGETS])
            if pairs or not raw_targets:
                config.alert_targets = pairs
        return config

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": max(2, self.config_version),
            "ui_theme": normalize_theme_key(self.ui_theme),
            "monitor_index": self.monitor_index,
            "capture_source": normalize_capture_source(self.capture_source),
            "capture_window_title": normalize_capture_metadata(self.capture_window_title),
            "capture_window_process": normalize_capture_metadata(self.capture_window_process),
            "capture_window_class": normalize_capture_metadata(self.capture_window_class),
            "capture_device_name": normalize_capture_metadata(self.capture_device_name),
            "capture_device_path": normalize_capture_metadata(self.capture_device_path),
            "capture_device_vid": self.capture_device_vid,
            "capture_device_pid": self.capture_device_pid,
            "capture_device_backend": int(self.capture_device_backend),
            "capture_interval_seconds": self.capture_interval_seconds,
            "rebirth_ready_alert_enabled": self.rebirth_ready_alert_enabled,
            "cb23_mission_alert_enabled": self.cb23_mission_alert_enabled,
            "rebirth_scan_interval_seconds": self.rebirth_scan_interval_seconds,
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
            "limited_deals_intro_shown": self.limited_deals_intro_shown,
            "last_seen_version": self.last_seen_version.strip(),
            "discord_community_prompted": self.discord_community_prompted,
            "phone_alerts_enabled": self.phone_alerts_enabled,
            "phone_credentials_file": self.phone_credentials_file,
            "phone_env_token": self.phone_env_token,
            "phone_env_user": self.phone_env_user,
            "phone_sound": self.phone_sound,
            "phone_include_attachment": self.phone_include_attachment,
            "channel_disabled_alerts": {
                channel: sorted(set(alert_ids))
                for channel, alert_ids in self.channel_disabled_alerts.items()
                if channel in {"discord", "ntfy", "pushover"} and alert_ids
            },
            "update_check_enabled": self.update_check_enabled,
            "anonymous_app_stats_url": self.anonymous_app_stats_url,
            "anonymous_stats_url": self.anonymous_stats_url,
            "anonymous_detection_url": self.anonymous_detection_url,
            "anonymous_belt_stats_url": self.anonymous_belt_stats_url,
            "anonymous_belt_counts_url": self.anonymous_belt_counts_url,
            "limited_deal_url": self.limited_deal_url,
            "share_debug_detections": self.share_debug_detections,
            "debug_detection_upload_url": self.debug_detection_upload_url,
            "update_repo": self.update_repo,
            "advanced_mode": self.advanced_mode,
            "extra_checks": self.extra_checks,
            "start_watcher_on_launch": self.start_watcher_on_launch,
            "rebirth_alert_enabled": self.rebirth_alert_enabled,
            "belt_overlay_enabled": self.belt_overlay_enabled,
            "belt_dev_mode": self.belt_dev_mode,
            "belt_template_collection_enabled": self.belt_template_collection_enabled,
            "belt_idle_scan_fps": self.belt_idle_scan_fps,
            "belt_active_scan_fps": self.belt_active_scan_fps,
            "belt_cpu_warning_confirmed": self.belt_cpu_warning_confirmed,
            "belt_region_guide_confirmed": self.belt_region_guide_confirmed,
            "belt_target_tiers": normalize_belt_target_tiers(self.belt_target_tiers),
            "limited_deal_target_tiers": normalize_limited_deal_target_tiers(
                self.limited_deal_target_tiers
            ),
            "limited_deal_priority_alerts": normalize_limited_deal_priority_alerts(
                self.limited_deal_priority_alerts
            ),
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

    def channel_allows_alert(self, channel: str, alert_id: str) -> bool:
        """Return whether a remote channel should receive this alert type."""
        return alert_id not in self.channel_disabled_alerts.get(channel.lower(), [])


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
