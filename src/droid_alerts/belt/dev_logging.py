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


DEV_FRAME_INTERVAL_SECONDS = 15.0
MAX_DEV_FRAMES = 8


def belt_dev_dir() -> Path:
    return data_dir() / "belt_dev"


def runtime_snapshot() -> dict[str, object]:
    packages = {}
    for name in ("rapidocr", "onnxruntime", "opencv-python", "numpy", "mss"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not installed"

    onnx: dict[str, object] = {}
    try:
        import onnxruntime

        onnx = {
            "device": onnxruntime.get_device(),
            "available_providers": list(onnxruntime.get_available_providers()),
        }
    except Exception as exc:
        onnx = {"error": str(exc)}

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
        "onnxruntime": onnx,
    }


class BeltDevLogger:
    """Detailed, local-only diagnostics for a Belt Tracker session."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.session_dir: Path | None = None
        self.log_path: Path | None = None
        self._last_frame_at = float("-inf")
        self._saved_frames = 0
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

    def save_frame(self, frame_bgr: np.ndarray, *, frame_number: int, now: float) -> str:
        if (
            self.session_dir is None
            or self._saved_frames >= MAX_DEV_FRAMES
            or now - self._last_frame_at < DEV_FRAME_INTERVAL_SECONDS
        ):
            return ""
        try:
            success, encoded = cv2.imencode(".png", frame_bgr)
            if not success:
                return ""
            path = self.session_dir / f"frame_{frame_number:06d}.png"
            path.write_bytes(encoded.tobytes())
        except (OSError, cv2.error):
            return ""
        self._last_frame_at = now
        self._saved_frames += 1
        return path.name

    def relative_path(self) -> str:
        if self.session_dir is None:
            return ""
        try:
            return str(self.session_dir.relative_to(data_dir()))
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
