from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .. import __version__
from ..config import data_dir
from ..logging_io import timestamp


DEV_FRAME_INTERVAL_SECONDS = 1.0
DEV_EVIDENCE_INTERVAL_SECONDS = 3.0
DEV_JPEG_QUALITY = 88
MAX_DEV_SESSION_BYTES = 200 * 1024 * 1024


def belt_dev_dir() -> Path:
    return data_dir() / "belt_dev"


def runtime_snapshot() -> dict[str, object]:
    packages = {}
    for name in ("opencv-python", "numpy", "mss"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not installed"

    return {
        "app_version": __version__,
        "python": sys.version,
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "processor_identifier": os.environ.get("PROCESSOR_IDENTIFIER", ""),
        "cpu_count": os.cpu_count(),
        "opencv_threads": cv2.getNumThreads(),
        "opencv_opencl": bool(cv2.ocl.haveOpenCL()),
        "packages": packages,
    }


class BeltDevLogger:
    """Detailed, local-only diagnostics for a Belt Tracker session."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.session_dir: Path | None = None
        self.log_path: Path | None = None
        self._last_frame_at = float("-inf")
        self._last_reason_at: dict[str, float] = {}
        self._written_bytes = 0
        self.last_saved_reason = ""
        if not self.enabled:
            return
        self.session_dir = belt_dev_dir() / f"session_{timestamp()}_{os.getpid()}"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.session_dir / "belt_dev.jsonl"

    def log(self, event: str, **values: object) -> None:
        if self.log_path is None:
            return
        record = {
            "timestamp": time.time(),
            "event": event,
            **values,
        }
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")
        except OSError:
            return

    def save_frame(
        self,
        frame_bgr: np.ndarray,
        *,
        frame_number: int,
        now: float,
        reason: str = "periodic",
        force: bool = False,
        lossless: bool = False,
    ) -> str:
        """Save bounded all-session evidence and return its local file name.

        Periodic JPEGs make genuine misses visible. High-value events may
        bypass the periodic interval, with a short per-reason cooldown to stop
        a long ambiguous passage from filling the session by itself.
        """

        self.last_saved_reason = ""
        if self.session_dir is None or self._written_bytes >= MAX_DEV_SESSION_BYTES:
            return ""
        reason = _safe_reason(reason)
        reason_last_at = self._last_reason_at.get(reason, float("-inf"))
        force_allowed = force and now - reason_last_at >= DEV_EVIDENCE_INTERVAL_SECONDS
        periodic_due = now - self._last_frame_at >= DEV_FRAME_INTERVAL_SECONDS
        if not force_allowed and not periodic_due:
            return ""
        actual_reason = reason if force_allowed else "periodic"
        actual_lossless = bool(lossless and force_allowed)
        try:
            suffix = ".png" if actual_lossless else ".jpg"
            parameters = () if actual_lossless else (cv2.IMWRITE_JPEG_QUALITY, DEV_JPEG_QUALITY)
            success, encoded = cv2.imencode(suffix, frame_bgr, parameters)
            if not success:
                return ""
            payload = encoded.tobytes()
            if self._written_bytes + len(payload) > MAX_DEV_SESSION_BYTES:
                return ""
            path = self.session_dir / f"frame_{frame_number:06d}_{actual_reason}{suffix}"
            path.write_bytes(payload)
        except (OSError, cv2.error):
            return ""
        self._last_frame_at = now
        self._last_reason_at[actual_reason] = now
        self._written_bytes += len(payload)
        self.last_saved_reason = actual_reason
        return path.name

    def relative_path(self) -> str:
        if self.session_dir is None:
            return ""
        try:
            return self.session_dir.relative_to(data_dir()).as_posix()
        except ValueError:
            return str(self.session_dir)


def _json_default(value: Any):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return str(value)


def _safe_reason(value: object) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in str(value or "periodic").strip().lower()
    ).strip("_")
    return cleaned[:32] or "periodic"
