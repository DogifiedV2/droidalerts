from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from . import __version__
from .config import AppConfig, config_dir

INSTALL_ID_FILE = "anonymous_install_id.txt"
REQUEST_TIMEOUT_SECONDS = 4.0
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 60.0
MIN_HEARTBEAT_INTERVAL_SECONDS = 30.0
MAX_HEARTBEAT_INTERVAL_SECONDS = 10 * 60.0
MAX_DEBUG_SCREENSHOT_BYTES = 3 * 1024 * 1024
USER_AGENT = f"DroidAlerts/{__version__}"


def anonymous_install_id_path() -> Path:
    return config_dir() / INSTALL_ID_FILE


def load_or_create_anonymous_install_id() -> str:
    path = anonymous_install_id_path()
    try:
        value = path.read_text(encoding="utf-8-sig").strip().lower()
        if _valid_uuid(value):
            return value
    except OSError:
        pass

    value = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")
    except OSError:
        # If the config folder is temporarily unwritable, keep telemetry working
        # for this run with an ephemeral anonymous id instead of crashing watch.
        pass
    return value


class AnonymousTelemetryClient:
    """Best-effort anonymous active-watcher and opt-in debug upload client.

    The active-watcher heartbeat sends only random UUIDs and the app version.
    Debug detection sharing is a separate explicit opt-in that can upload the
    two debug PNGs for alert detections while debug mode is enabled.
    """

    def __init__(self, config: AppConfig) -> None:
        self._lock = threading.Lock()
        self._enabled = bool(config.share_anonymous_data)
        self._endpoint_url = config.anonymous_stats_url.strip()
        self._debug_upload_enabled = bool(config.share_debug_detections)
        self._debug_upload_url = config.debug_detection_upload_url.strip()
        self._install_id: str | None = None
        self._session_id = str(uuid.uuid4())
        self._heartbeat_interval_seconds = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if not self._enabled or not self._endpoint_url or self._thread is not None:
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(target=self._run, name="DroidAlertsAnonymousTelemetry", daemon=True)
            self._thread.start()

    def apply_config(self, config: AppConfig) -> None:
        enabled = bool(config.share_anonymous_data)
        endpoint_url = config.anonymous_stats_url.strip()
        should_start = False
        should_stop = False
        with self._lock:
            self._enabled = enabled
            self._endpoint_url = endpoint_url
            self._debug_upload_enabled = bool(config.share_debug_detections)
            self._debug_upload_url = config.debug_detection_upload_url.strip()
            should_stop = (not enabled or not endpoint_url) and self._thread is not None
            should_start = enabled and bool(endpoint_url) and self._thread is None

        if should_stop:
            self.stop()
        elif should_start:
            self.start()

    def stop(self) -> None:
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread
            self._stop_event = None
            self._thread = None
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.2)

    def submit_debug_detection(self, *, detection, event: dict[str, Any], screenshot_paths: list[str]) -> None:
        with self._lock:
            enabled = self._debug_upload_enabled
            endpoint_url = self._debug_upload_url
        if not enabled or not endpoint_url or len(screenshot_paths) < 2:
            return

        threading.Thread(
            target=self._send_debug_detection,
            args=(endpoint_url, detection, dict(event), list(screenshot_paths[:2])),
            name="DroidAlertsDebugUpload",
            daemon=True,
        ).start()

    def _run(self) -> None:
        self._send_heartbeat()
        while True:
            with self._lock:
                stop_event = self._stop_event
                interval = self._heartbeat_interval_seconds
            if stop_event is None or stop_event.wait(interval):
                return
            self._send_heartbeat()

    def _send_heartbeat(self) -> None:
        try:
            with self._lock:
                endpoint_url = self._endpoint_url
            if not endpoint_url:
                return
            if self._install_id is None:
                self._install_id = load_or_create_anonymous_install_id()

            payload = {
                "installId": self._install_id,
                "sessionId": self._session_id,
                "appVersion": __version__,
            }
            response_payload = self._post_json(endpoint_url, payload)
            self._apply_server_interval(response_payload.get("heartbeatIntervalSeconds"))
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            # Telemetry must never affect the watcher. Network failures, API
            # deploys, rate limits, and bad responses are all safe to ignore.
            return

    def _send_debug_detection(self, endpoint_url: str, detection, event: dict[str, Any], screenshot_paths: list[str]) -> None:
        try:
            if self._install_id is None:
                self._install_id = load_or_create_anonymous_install_id()
            screenshots = _debug_screenshots_payload(screenshot_paths)
            if len(screenshots) != 2:
                return

            detection_key = _detection_key(detection.droid, detection.rarity)
            payload = {
                "installId": self._install_id,
                "sessionId": self._session_id,
                "appVersion": __version__,
                "detectedAt": str(event.get("ts") or ""),
                "metadata": {
                    "resolution": _resolution_metadata(event),
                    "monitorIndex": _safe_int(event.get("monitor_index")),
                    "captureRegion": _capture_region_metadata(event.get("capture_region")),
                    "scale": _round_float(event.get("scale")),
                    "scaleMethod": str(event.get("scale_method") or ""),
                },
                "storage": {
                    "installId": self._install_id,
                    "detectionKey": detection_key,
                },
                "detection": {
                    "key": detection_key,
                    "droid": detection.droid,
                    "rarity": detection.rarity,
                    "score": _round_float(detection.score),
                    "rarityScore": _round_float(detection.rarity_score),
                    "droidScore": _round_float(detection.droid_score),
                    "rarityMargin": _round_float(detection.rarity_margin),
                    "source": detection.source,
                    "frame": event.get("frame"),
                    "rowHash": event.get("row_hash"),
                    "scale": event.get("scale"),
                    "scaleMethod": event.get("scale_method"),
                },
                "screenshots": screenshots,
            }
            self._post_json(endpoint_url, payload)
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            return

    def _post_json(self, endpoint_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            endpoint_url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8") or "{}")

    def _apply_server_interval(self, value: object) -> None:
        try:
            interval = float(value)
        except (TypeError, ValueError):
            return
        interval = min(max(interval, MIN_HEARTBEAT_INTERVAL_SECONDS), MAX_HEARTBEAT_INTERVAL_SECONDS)
        with self._lock:
            self._heartbeat_interval_seconds = interval


def _debug_screenshots_payload(screenshot_paths: list[str]) -> list[dict[str, str]]:
    screenshots = []
    for path_text in screenshot_paths:
        path = Path(path_text)
        data = path.read_bytes()
        if not data or len(data) > MAX_DEBUG_SCREENSHOT_BYTES:
            return []
        name = "candidate_check" if path.name.endswith("_candidate_check.png") else "roi"
        screenshots.append(
            {
                "name": name,
                "contentType": "image/png",
                "dataBase64": base64.b64encode(data).decode("ascii"),
            }
        )
    return screenshots


def _round_float(value: object) -> float | None:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _detection_key(droid: object, rarity: object) -> str:
    text = f"{droid}{rarity}".lower()
    cleaned = "".join(char for char in text if char.isalnum())
    return cleaned or "unknown"


def _resolution_metadata(event: dict[str, Any]) -> dict[str, object]:
    width = _safe_int(event.get("screen_width"))
    height = _safe_int(event.get("screen_height"))
    text = f"{width}x{height}" if width and height else ""
    return {
        "width": width,
        "height": height,
        "text": text,
    }


def _capture_region_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {
        "source": str(value.get("source") or ""),
        "left": _safe_int(value.get("left")),
        "top": _safe_int(value.get("top")),
        "width": _safe_int(value.get("width")),
        "height": _safe_int(value.get("height")),
    }


def _valid_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (TypeError, ValueError, AttributeError):
        return False
