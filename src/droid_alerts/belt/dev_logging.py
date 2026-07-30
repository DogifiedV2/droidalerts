from __future__ import annotations

from collections import deque
from dataclasses import dataclass
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
DEV_RING_INTERVAL_SECONDS = 0.50
DEV_RING_SECONDS = 15.0
DEV_RING_JPEG_QUALITY = 84
BELT_ISSUE_KINDS = ("missed", "wrong", "duplicate", "other")


def belt_dev_dir() -> Path:
    return data_dir() / "belt_dev"


def belt_miss_request_path() -> Path:
    return belt_dev_dir() / "report_miss.request"


def request_belt_miss_report(note: str = "") -> Path:
    """Compatibility wrapper for a missed-card Developer Mode report."""

    return request_belt_issue_report("missed", note)


def request_belt_issue_report(issue_kind: str, note: str = "") -> Path:
    """Ask the active dev-mode watcher to preserve its recent frame buffer."""

    path = belt_miss_request_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized_kind = str(issue_kind).strip().casefold()
    if normalized_kind not in BELT_ISSUE_KINDS:
        raise ValueError(f"Unknown Belt issue type: {issue_kind}")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "created_at": time.time(),
                "issue_kind": normalized_kind,
                "note": str(note).strip()[:500],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


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


@dataclass(frozen=True)
class _BufferedFrame:
    frame_number: int
    captured_at: float
    payload: bytes
    metadata: dict[str, object]


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
        self._started_wall_time = time.time()
        self._last_buffered_at = float("-inf")
        self._frame_buffer: deque[_BufferedFrame] = deque(
            maxlen=max(
                2,
                round(DEV_RING_SECONDS / DEV_RING_INTERVAL_SECONDS),
            )
        )
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

    def remember_frame(
        self,
        frame_bgr: np.ndarray,
        *,
        frame_number: int,
        now: float,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Retain a small in-memory history for a manual missed-card report."""

        if self.session_dir is None:
            return
        if now - self._last_buffered_at < DEV_RING_INTERVAL_SECONDS:
            return
        try:
            success, encoded = cv2.imencode(
                ".jpg",
                frame_bgr,
                (cv2.IMWRITE_JPEG_QUALITY, DEV_RING_JPEG_QUALITY),
            )
            if not success:
                return
            payload = encoded.tobytes()
        except cv2.error:
            return
        self._last_buffered_at = now
        self._frame_buffer.append(
            _BufferedFrame(
                frame_number=int(frame_number),
                captured_at=float(now),
                payload=payload,
                metadata=dict(metadata or {}),
            )
        )
        while (
            self._frame_buffer
            and now - self._frame_buffer[0].captured_at > DEV_RING_SECONDS
        ):
            self._frame_buffer.popleft()

    def consume_issue_request(self) -> str:
        """Write the buffered evidence for one fresh manual issue request."""

        if self.session_dir is None:
            return ""
        request_path = belt_miss_request_path()
        try:
            request_modified_at = request_path.stat().st_mtime
            request = json.loads(request_path.read_text(encoding="utf-8"))
            request_path.unlink(missing_ok=True)
        except FileNotFoundError:
            return ""
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            try:
                request_path.unlink(missing_ok=True)
            except OSError:
                pass
            return ""
        if request_modified_at + 0.01 < self._started_wall_time:
            return ""
        if not isinstance(request, dict) or not self._frame_buffer:
            return ""

        issue_kind = str(request.get("issue_kind", "missed")).strip().casefold()
        if issue_kind not in BELT_ISSUE_KINDS:
            issue_kind = "other"
        report_dir = self.session_dir / f"issue_{timestamp()}_{issue_kind}"
        try:
            report_dir.mkdir(parents=True, exist_ok=False)
        except OSError:
            return ""
        records: list[dict[str, object]] = []
        written_paths: list[Path] = []
        try:
            for buffered in self._frame_buffer:
                if (
                    self._written_bytes + len(buffered.payload)
                    > MAX_DEV_SESSION_BYTES
                ):
                    break
                file_name = f"frame_{buffered.frame_number:06d}.jpg"
                frame_path = report_dir / file_name
                frame_path.write_bytes(buffered.payload)
                written_paths.append(frame_path)
                self._written_bytes += len(buffered.payload)
                records.append(
                    {
                        "frame": buffered.frame_number,
                        "captured_at_monotonic": buffered.captured_at,
                        "image": file_name,
                        **buffered.metadata,
                    }
                )
            if not records:
                report_dir.rmdir()
                return ""
            manifest_path = report_dir / "manifest.json"
            manifest = {
                "version": 2,
                "created_at": time.time(),
                "label_status": "unreviewed",
                "training_status": "never_auto_promote",
                "issue_kind": issue_kind,
                "note": str(request.get("note", ""))[:500],
                "frames": records,
            }
            manifest_path.write_text(
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    indent=2,
                    default=_json_default,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            for written_path in written_paths:
                try:
                    written_path.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                report_dir.rmdir()
            except OSError:
                pass
            return ""
        try:
            return report_dir.relative_to(data_dir()).as_posix()
        except ValueError:
            return str(report_dir)

    def consume_miss_request(self) -> str:
        """Compatibility wrapper for the original missed-card request API."""

        return self.consume_issue_request()

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
