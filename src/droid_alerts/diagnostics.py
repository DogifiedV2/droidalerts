from __future__ import annotations

import json
import platform
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .belt.region import regions_path as belt_regions_path
from .capture import list_monitors
from .config import AppConfig, data_dir, project_root
from .logging_io import debug_dir, logs_dir, timestamp
from .region import calibration_path


def create_support_bundle(config: AppConfig) -> Path:
    """Create a credential-free diagnostics ZIP the user can inspect and share."""
    out_dir = data_dir() / "support_bundles"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"droid_alerts_support_{timestamp()}.zip"

    config_data = config.to_dict()
    for key in (
        "ntfy_topic",
        "anonymous_app_stats_url",
        "anonymous_stats_url",
        "anonymous_detection_url",
        "anonymous_belt_stats_url",
        "anonymous_belt_counts_url",
        "debug_detection_upload_url",
    ):
        if config_data.get(key):
            config_data[key] = "<redacted>"
    system = {
        "app_version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "monitors": [asdict(monitor) for monitor in _safe_monitors()],
    }

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("system.json", json.dumps(system, indent=2) + "\n")
        bundle.writestr("config_redacted.json", json.dumps(config_data, indent=2) + "\n")
        calibration = calibration_path()
        if calibration.exists():
            bundle.write(calibration, "calibration.json")
        belt_regions = belt_regions_path()
        if belt_regions.exists():
            bundle.write(belt_regions, "belt_regions.json")
        events = logs_dir() / "events.jsonl"
        if events.exists():
            bundle.writestr("recent_events.jsonl", _redacted_event_tail(events, 500))
        for index, screenshot in enumerate(_latest_files(debug_dir(), "*.png", 4), start=1):
            try:
                bundle.write(screenshot, f"debug/{index:02d}_{screenshot.name}")
            except OSError:
                continue
        bundle.writestr(
            "README.txt",
            "This bundle excludes Discord webhooks, ntfy tokens, Pushover credentials, and anonymous IDs.\n",
        )
    return out_path


def _safe_monitors():
    try:
        return list_monitors()
    except Exception:
        return []


def _tail_lines(path: Path, count: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    text = "\n".join(lines[-count:])
    return text + ("\n" if text else "")


def _redacted_event_tail(path: Path, count: int) -> str:
    text = _tail_lines(path, count)
    replacements = {
        str(project_root()): "<app-folder>",
    }
    for source, replacement in replacements.items():
        if source:
            text = text.replace(source, replacement)
            text = text.replace(source.replace("\\", "/"), replacement)
    return text


def _latest_files(root: Path, pattern: str, count: int) -> list[Path]:
    if not root.exists():
        return []
    files = [file for file in root.rglob(pattern) if file.is_file()]
    files.sort(key=_safe_mtime, reverse=True)
    return files[:count]


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
