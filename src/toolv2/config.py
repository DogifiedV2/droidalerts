from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def project_root() -> Path:
    """ToolV2 project root. Everything lives under here — the tool is fully
    self-contained and never writes to OS user-data directories."""
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return project_root() / "config"


def data_dir() -> Path:
    return project_root() / "data"


def templates_dir() -> Path:
    return project_root() / "templates"


def sounds_dir() -> Path:
    return project_root() / "assets" / "sounds"


CONFIG_FILE = "config.json"
CALIBRATION_FILE = "calibration.json"

# Reference scale everything is normalized to before classification.
# The templates/columns were captured at 2560x1440 with 44px rows.
REFERENCE_ROW_HEIGHT_PX = 44
REFERENCE_SCREEN_HEIGHT = 1440


@dataclass
class Thresholds:
    rarity_threshold: float = 0.35
    droid_threshold: float = 0.15
    scale_min: float = 0.4
    scale_max: float = 2.5


@dataclass
class AppConfig:
    monitor_index: int = 1
    capture_interval_seconds: float = 1.0
    dedupe_seconds: float = 12.0
    alert_cooldown_seconds: float = 10.0
    sound_enabled: bool = True
    save_alert_samples: bool = True
    save_debug_screenshots: bool = False
    validation_failures_before_calibration_prompt: int = 30
    thresholds: Thresholds = field(default_factory=Thresholds)
    alert_targets: list[list[str]] = field(
        default_factory=lambda: [
            ["Diamond", "Mythic"],
            ["Rainbow", "Mythic"],
            ["Beskar", "Mythic"],
            ["Beskar", "Legendary"],
            ["Beskar", "Epic"],
        ]
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        thresholds = data.get("thresholds") or {}
        config = cls(
            monitor_index=int(data.get("monitor_index", 1)),
            capture_interval_seconds=float(data.get("capture_interval_seconds", 1.0)),
            dedupe_seconds=float(data.get("dedupe_seconds", 12.0)),
            alert_cooldown_seconds=float(data.get("alert_cooldown_seconds", 10.0)),
            sound_enabled=bool(data.get("sound_enabled", True)),
            save_alert_samples=bool(data.get("save_alert_samples", True)),
            save_debug_screenshots=bool(data.get("save_debug_screenshots", False)),
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
            "save_alert_samples": self.save_alert_samples,
            "save_debug_screenshots": self.save_debug_screenshots,
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
