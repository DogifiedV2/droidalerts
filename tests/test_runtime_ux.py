from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts import config as config_module
from droid_alerts import maintenance
from droid_alerts.capture import format_tk_geometry, monitor_key_from_mapping
from droid_alerts.config import AppConfig
from droid_alerts.region import Calibration
from droid_alerts.timers import DISPLAY_TIMER_ORDER, TIMER_ORDER, seconds_until_next
from droid_alerts.updater import _safe_extract
from droid_alerts.watcher import _delivery_retryable


def main() -> int:
    failures: list[str] = []

    if DISPLAY_TIMER_ORDER != ("beskar", "mythic") or "rainbow" not in TIMER_ORDER:
        failures.append("timer overlay visibility does not preserve the hidden Rainbow timer")

    configured = AppConfig(
        ui_theme="midnight",
        popup_position="bottom_right",
        popup_scale=1.25,
        retention_days=7,
        timer_reminders_enabled=True,
        alert_targets=[["Beskar", "Mythic"]],
    )
    restored = AppConfig.from_dict(configured.to_dict())
    if (
        restored.ui_theme != "midnight"
        or restored.popup_position != "bottom_right"
        or restored.retention_days != 7
    ):
        failures.append("new AppConfig fields did not round-trip")
    if restored.targets != {("Beskar", "Mythic")}:
        failures.append("fixed alert selections did not round-trip")

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        with patch.object(config_module, "config_dir", return_value=root):
            first = AppConfig(retention_days=7)
            config_module.save_config(first)
            second = AppConfig(retention_days=30)
            config_module.save_config(second)
            (root / "config.json").write_text("{broken", encoding="utf-8")
            recovered = config_module.load_config()
            if recovered.retention_days != 7:
                failures.append("invalid config did not recover from the last valid backup")

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        root.joinpath("config.json").write_text("{broken", encoding="utf-8")
        with patch.object(config_module, "config_dir", return_value=root):
            recovered = config_module.load_config()
        if recovered.config_version != 2 or not root.joinpath("config.json.corrupt").exists():
            failures.append("unrecoverable config did not fall back safely while preserving evidence")

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "calibration.json"
        with patch("droid_alerts.region.calibration_path", return_value=path):
            Calibration(mode="manual", ratios={"left": 0.1, "top": 0.2, "width": 0.3, "height": 0.4}).save("display-a")
            Calibration(mode="manual", ratios={"left": 0.2, "top": 0.3, "width": 0.3, "height": 0.3}).save("display-b")
            if Calibration.load("display-a").ratios["left"] != 0.1:
                failures.append("display-a calibration was overwritten")
            if Calibration.load("display-b").ratios["left"] != 0.2:
                failures.append("display-b calibration was not stored")
            if Calibration.load("missing-display").mode != "auto":
                failures.append("unknown displays should start with automatic calibration")

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        logs = root / "logs"
        samples = root / "samples"
        debug = root / "debug"
        for path in (logs, samples, debug):
            path.mkdir(parents=True)
        old_file = samples / "old.png"
        recent_file = samples / "recent.png"
        old_file.write_bytes(b"old")
        recent_file.write_bytes(b"recent")
        old_time = time.time() - 10 * 86400
        os.utime(old_file, (old_time, old_time))
        old_stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(old_time))
        recent_stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        (logs / "events.jsonl").write_text(
            json.dumps({"ts": old_stamp, "event_type": "detected"}) + "\n"
            + json.dumps({"ts": recent_stamp, "event_type": "alert"}) + "\n",
            encoding="utf-8",
        )
        with (
            patch.object(maintenance, "data_dir", return_value=root),
            patch.object(maintenance, "logs_dir", return_value=logs),
            patch.object(maintenance, "alert_samples_dir", return_value=samples),
            patch.object(maintenance, "debug_dir", return_value=debug),
        ):
            maintenance.cleanup_runtime_data(7, 0)
        if old_file.exists() or not recent_file.exists():
            failures.append("retention cleanup did not distinguish old and recent captures")
        remaining_events = (logs / "events.jsonl").read_text(encoding="utf-8")
        if old_stamp in remaining_events or recent_stamp not in remaining_events:
            failures.append("retention cleanup trimmed the wrong history rows")

    monitor = {"left": 1920, "top": 0, "width": 2560, "height": 1440}
    if monitor_key_from_mapping(monitor, 1) != monitor_key_from_mapping(monitor, 3):
        failures.append("monitor fallback identity depends on enumeration order")

    stacked_geometry = format_tk_geometry(width=560, height=72, x=680, y=-1074)
    if stacked_geometry != "560x72+680+-1074":
        failures.append("Tk geometry did not preserve an above-primary monitor coordinate")
    left_geometry = format_tk_geometry(width=560, height=72, x=-1920, y=24)
    if left_geometry != "560x72+-1920+24":
        failures.append("Tk geometry did not preserve a left-of-primary monitor coordinate")
    if format_tk_geometry(x=-1920, y=-1080) != "+-1920+-1080":
        failures.append("position-only Tk geometry did not preserve negative coordinates")

    noon = time.struct_time((2026, 7, 10, 12, 0, 0, 3, 191, -1))
    with patch("droid_alerts.timers.time.localtime", return_value=noon):
        if seconds_until_next("beskar") != 900 or seconds_until_next("rainbow") != 600:
            failures.append("timer boundaries are incorrect at the hour")
        if seconds_until_next("mythic") != 3300:
            failures.append("mythic countdown is incorrect at the hour")
    between_beskar_spawns = time.struct_time((2026, 7, 10, 12, 7, 30, 3, 191, -1))
    with patch("droid_alerts.timers.time.localtime", return_value=between_beskar_spawns):
        if seconds_until_next("beskar") != 450:
            failures.append("Beskar timer does not use 15-minute spawn intervals")

    if not _delivery_retryable("HTTP 503") or _delivery_retryable("HTTP 401"):
        failures.append("delivery retry classification is incorrect")

    with tempfile.TemporaryDirectory() as folder:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("../escape.txt", "nope")
        payload.seek(0)
        try:
            with zipfile.ZipFile(payload) as archive:
                _safe_extract(archive, Path(folder) / "extract")
        except RuntimeError:
            pass
        else:
            failures.append("update extraction accepted a path-traversal member")

    if failures:
        print("runtime UX failures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "runtime UX OK: config recovery, calibration, monitor geometry, retention, "
        "timers, retries, and safe updates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
