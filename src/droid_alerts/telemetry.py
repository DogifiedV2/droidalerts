from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .belt.names import DROID_NAMES as BELT_DROID_NAMES
from .belt.targets import belt_target_names
from .chat_alerts import CHAT_ALERT_COMBOS
from .config import AppConfig, config_dir
from .network import certifi_ssl_context

INSTALL_ID_FILE = "anonymous_install_id.txt"
BELT_PENDING_COUNTS_FILE = "anonymous_belt_counts.json"
REQUEST_TIMEOUT_SECONDS = 4.0
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 60.0
MIN_HEARTBEAT_INTERVAL_SECONDS = 30.0
MAX_HEARTBEAT_INTERVAL_SECONDS = 10 * 60.0
MAX_DEBUG_SCREENSHOT_BYTES = 3 * 1024 * 1024
MAX_BELT_BUCKETS_PER_UPLOAD = 24
USER_AGENT = f"DroidAlerts/{__version__}"
VALID_BELT_DROID_NAMES = frozenset(BELT_DROID_NAMES)
VALID_PRIORITY_ALERT_KEYS = frozenset(
    f"{droid}{rarity}".lower() for droid, rarity in CHAT_ALERT_COMBOS
)
_INSTALL_ID_LOCK = threading.Lock()
_EPHEMERAL_INSTALL_ID: str | None = None


def anonymous_install_id_path() -> Path:
    return config_dir() / INSTALL_ID_FILE


def belt_pending_counts_path() -> Path:
    return config_dir() / BELT_PENDING_COUNTS_FILE


def load_or_create_anonymous_install_id() -> str:
    global _EPHEMERAL_INSTALL_ID
    with _INSTALL_ID_LOCK:
        path = anonymous_install_id_path()
        try:
            value = path.read_text(encoding="utf-8-sig").strip().lower()
            if _valid_uuid(value):
                return value
        except OSError:
            pass

        if _EPHEMERAL_INSTALL_ID is not None:
            return _EPHEMERAL_INSTALL_ID

        value = str(uuid.uuid4())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value + "\n", encoding="utf-8")
        except OSError:
            # Keep one stable in-memory id when the config folder is
            # temporarily unwritable instead of generating one per client.
            _EPHEMERAL_INSTALL_ID = value
        return value


class _TelemetryHttpClient:
    """Shared JSON transport and heartbeat interval handling."""

    _lock: threading.Lock
    _heartbeat_interval_seconds: float

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
        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
            context=certifi_ssl_context(),
        ) as response:
            return json.loads(response.read().decode("utf-8") or "{}")

    def _apply_server_interval(self, value: object) -> None:
        try:
            interval = float(value)
        except (TypeError, ValueError):
            return
        interval = min(max(interval, MIN_HEARTBEAT_INTERVAL_SECONDS), MAX_HEARTBEAT_INTERVAL_SECONDS)
        with self._lock:
            self._heartbeat_interval_seconds = interval


class AnonymousAppTelemetryClient(_TelemetryHttpClient):
    """Best-effort heartbeat used only to estimate how long the app is open."""

    def __init__(self, config: AppConfig) -> None:
        self._lock = threading.Lock()
        self._endpoint_url = config.anonymous_app_stats_url.strip()
        self._install_id: str | None = None
        self._session_id = str(uuid.uuid4())
        self._heartbeat_interval_seconds = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if not self._endpoint_url or self._thread is not None:
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                name="DroidAlertsAnonymousAppTelemetry",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.3)
        with self._lock:
            if self._thread is thread:
                self._stop_event = None
                self._thread = None

    def _run(self) -> None:
        self._send_heartbeat()
        while True:
            with self._lock:
                stop_event = self._stop_event
                interval = self._heartbeat_interval_seconds
            if stop_event is None:
                return
            if stop_event.wait(interval):
                # Capture most of the final partial minute when the app closes.
                self._send_heartbeat()
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
            response_payload = self._post_json(
                endpoint_url,
                {
                    "installId": self._install_id,
                    "sessionId": self._session_id,
                    "appVersion": __version__,
                },
            )
            self._apply_server_interval(response_payload.get("heartbeatIntervalSeconds"))
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            return

class AnonymousTelemetryClient(_TelemetryHttpClient):
    """Best-effort anonymous active-watcher and alert-count client.

    The active-watcher heartbeat sends random UUIDs, the app version, and the
    selected priority-alert keys on the first heartbeat and after changes.
    Priority alert reports send only the anonymous IDs, app version, timestamp,
    and the droid/rarity combo.
    Debug detection sharing is a separate explicit opt-in that can upload the
    two debug PNGs for alert detections while debug mode is enabled.
    """

    def __init__(self, config: AppConfig) -> None:
        self._lock = threading.Lock()
        self._endpoint_url = config.anonymous_stats_url.strip()
        self._detection_report_url = config.anonymous_detection_url.strip()
        self._debug_upload_enabled = bool(config.share_debug_detections)
        self._debug_upload_url = config.debug_detection_upload_url.strip()
        self._priority_alerts = _priority_alert_keys(config)
        self._last_sent_priority_alerts: tuple[str, ...] | None = None
        self._install_id: str | None = None
        self._session_id = str(uuid.uuid4())
        self._heartbeat_interval_seconds = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if not self._endpoint_url or self._thread is not None:
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(target=self._run, name="DroidAlertsAnonymousTelemetry", daemon=True)
            self._thread.start()

    def apply_config(self, config: AppConfig) -> None:
        endpoint_url = config.anonymous_stats_url.strip()
        should_start = False
        should_stop = False
        with self._lock:
            self._endpoint_url = endpoint_url
            self._detection_report_url = config.anonymous_detection_url.strip()
            self._debug_upload_enabled = bool(config.share_debug_detections)
            self._debug_upload_url = config.debug_detection_upload_url.strip()
            self._priority_alerts = _priority_alert_keys(config)
            should_stop = not endpoint_url and self._thread is not None
            should_start = bool(endpoint_url) and self._thread is None

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

    def submit_alert_detection(self, *, detection, detected_at: str) -> None:
        with self._lock:
            endpoint_url = self._detection_report_url
        if not endpoint_url:
            return

        threading.Thread(
            target=self._send_alert_detection,
            args=(endpoint_url, str(detection.droid), str(detection.rarity), detected_at),
            name="DroidAlertsDetectionReport",
            daemon=True,
        ).start()

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
                priority_alerts = self._priority_alerts
                last_sent_priority_alerts = self._last_sent_priority_alerts
            if not endpoint_url:
                return
            if self._install_id is None:
                self._install_id = load_or_create_anonymous_install_id()

            payload = {
                "installId": self._install_id,
                "sessionId": self._session_id,
                "appVersion": __version__,
            }
            if priority_alerts != last_sent_priority_alerts:
                payload["priorityAlerts"] = list(priority_alerts)
            response_payload = self._post_json(endpoint_url, payload)
            if priority_alerts != last_sent_priority_alerts:
                with self._lock:
                    self._last_sent_priority_alerts = priority_alerts
            self._apply_server_interval(response_payload.get("heartbeatIntervalSeconds"))
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            # Telemetry must never affect the watcher. Network failures, API
            # deploys, rate limits, and bad responses are all safe to ignore.
            return

    def _send_alert_detection(self, endpoint_url: str, droid: str, rarity: str, detected_at: str) -> None:
        try:
            if self._install_id is None:
                self._install_id = load_or_create_anonymous_install_id()
            payload = {
                "installId": self._install_id,
                "sessionId": self._session_id,
                "appVersion": __version__,
                "detectedAt": detected_at,
                "detection": {
                    "key": _detection_key(droid, rarity),
                    "droid": droid,
                    "rarity": rarity,
                },
            }
            self._post_json(endpoint_url, payload)
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
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

class AnonymousBeltTelemetryClient(_TelemetryHttpClient):
    """Best-effort Belt Tracker presence and compact confirmed-count client.

    Only confirmed droid names and cumulative counts per anonymous session and
    UTC hour are retained. Re-sending a bucket is safe because the server keeps
    the largest cumulative value rather than adding the same upload twice.
    """

    def __init__(self, config: AppConfig) -> None:
        self._lock = threading.Lock()
        self._heartbeat_url = config.anonymous_belt_stats_url.strip()
        self._counts_url = config.anonymous_belt_counts_url.strip()
        self._target_names = _belt_target_names(config)
        self._last_sent_target_names: tuple[str, ...] | None = None
        self._install_id: str | None = None
        self._session_id = str(uuid.uuid4())
        self._heartbeat_interval_seconds = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._session_active = False
        self._buckets = self._load_buckets()

    def start(self) -> None:
        with self._lock:
            if not self._heartbeat_url or self._thread is not None:
                return
            self._session_active = True
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                name="DroidAlertsAnonymousBeltTelemetry",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread
            self._session_active = False
            self._stop_event = None
            self._thread = None
            removable = [
                key
                for key, bucket in self._buckets.items()
                if all(
                    int(count) <= int(bucket["sentCounts"].get(name, 0))
                    for name, count in bucket["counts"].items()
                )
            ]
            for key in removable:
                self._buckets.pop(key, None)
            self._save_buckets_locked()
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.2)

    def record_sighting(self, droid: object) -> None:
        name = str(droid or "").strip().upper()
        if name not in VALID_BELT_DROID_NAMES:
            return
        started_at = _utc_hour_text()
        key = _belt_bucket_key(self._session_id, started_at)
        with self._lock:
            bucket = self._buckets.setdefault(
                key,
                {
                    "sessionId": self._session_id,
                    "startedAt": started_at,
                    "counts": {},
                    "sentCounts": {},
                },
            )
            counts = bucket["counts"]
            counts[name] = min(10_000, int(counts.get(name, 0)) + 1)
            self._save_buckets_locked()

    def _run(self) -> None:
        try:
            self._send_heartbeat()
            self._flush_counts()
            while True:
                with self._lock:
                    stop_event = self._stop_event
                    interval = self._heartbeat_interval_seconds
                if stop_event is None or stop_event.wait(interval):
                    return
                self._send_heartbeat()
                self._flush_counts()
        finally:
            self._flush_counts()

    def _send_heartbeat(self) -> None:
        try:
            with self._lock:
                endpoint_url = self._heartbeat_url
                target_names = self._target_names
                last_sent_target_names = self._last_sent_target_names
            if not endpoint_url:
                return
            install_id = self._anonymous_install_id()
            payload: dict[str, Any] = {
                "installId": install_id,
                "sessionId": self._session_id,
                "appVersion": __version__,
            }
            if target_names != last_sent_target_names:
                payload["targetDroids"] = list(target_names)
            response_payload = self._post_json(endpoint_url, payload)
            if target_names != last_sent_target_names:
                with self._lock:
                    self._last_sent_target_names = target_names
            self._apply_server_interval(response_payload.get("heartbeatIntervalSeconds"))
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            return

    def _flush_counts(self) -> None:
        try:
            with self._lock:
                endpoint_url = self._counts_url
                snapshots = self._pending_bucket_snapshots_locked()
            if not endpoint_url or not snapshots:
                return
            payload = {
                "installId": self._anonymous_install_id(),
                "appVersion": __version__,
                "buckets": snapshots,
            }
            self._post_json(endpoint_url, payload)
            self._mark_buckets_sent(snapshots)
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            return

    def _pending_bucket_snapshots_locked(self) -> list[dict[str, object]]:
        snapshots: list[dict[str, object]] = []
        ordered = sorted(
            self._buckets.values(),
            key=lambda bucket: (str(bucket["startedAt"]), str(bucket["sessionId"])),
        )
        for bucket in ordered:
            counts = bucket["counts"]
            sent_counts = bucket["sentCounts"]
            if all(int(count) <= int(sent_counts.get(name, 0)) for name, count in counts.items()):
                continue
            snapshots.append(
                {
                    "sessionId": bucket["sessionId"],
                    "startedAt": bucket["startedAt"],
                    "counts": [
                        {"droid": name, "count": int(count)}
                        for name, count in sorted(counts.items())
                    ],
                }
            )
            if len(snapshots) >= MAX_BELT_BUCKETS_PER_UPLOAD:
                break
        return snapshots

    def _mark_buckets_sent(self, snapshots: list[dict[str, object]]) -> None:
        sent_by_key = {
            _belt_bucket_key(str(snapshot["sessionId"]), str(snapshot["startedAt"])): {
                str(entry["droid"]): int(entry["count"])
                for entry in snapshot["counts"]
                if isinstance(entry, dict)
            }
            for snapshot in snapshots
        }
        with self._lock:
            for key, sent_counts in sent_by_key.items():
                bucket = self._buckets.get(key)
                if bucket is None:
                    continue
                acknowledged = bucket["sentCounts"]
                for name, count in sent_counts.items():
                    acknowledged[name] = max(int(acknowledged.get(name, 0)), count)

            active_key = (
                _belt_bucket_key(self._session_id, _utc_hour_text())
                if self._session_active
                else None
            )
            removable = []
            for key, bucket in self._buckets.items():
                counts = bucket["counts"]
                sent_counts = bucket["sentCounts"]
                fully_sent = all(
                    int(count) <= int(sent_counts.get(name, 0))
                    for name, count in counts.items()
                )
                if fully_sent and key != active_key:
                    removable.append(key)
            for key in removable:
                self._buckets.pop(key, None)
            self._save_buckets_locked()

    def _anonymous_install_id(self) -> str:
        with self._lock:
            if self._install_id is None:
                self._install_id = load_or_create_anonymous_install_id()
            return self._install_id

    def _load_buckets(self) -> dict[str, dict[str, object]]:
        try:
            payload = json.loads(belt_pending_counts_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        raw_buckets = payload.get("buckets") if isinstance(payload, dict) else None
        if not isinstance(raw_buckets, list):
            return {}

        buckets: dict[str, dict[str, object]] = {}
        for raw_bucket in raw_buckets:
            if not isinstance(raw_bucket, dict):
                continue
            session_id = str(raw_bucket.get("sessionId") or "").lower()
            started_at = str(raw_bucket.get("startedAt") or "")
            if not _valid_uuid(session_id) or not _valid_utc_hour(started_at):
                continue
            counts = _valid_belt_counts(raw_bucket.get("counts"))
            sent_counts = _valid_belt_counts(raw_bucket.get("sentCounts"))
            if not counts:
                continue
            buckets[_belt_bucket_key(session_id, started_at)] = {
                "sessionId": session_id,
                "startedAt": started_at,
                "counts": counts,
                "sentCounts": sent_counts,
            }
        return buckets

    def _save_buckets_locked(self) -> None:
        path = belt_pending_counts_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not self._buckets:
                path.unlink(missing_ok=True)
                return
            temporary = path.with_suffix(path.suffix + ".tmp")
            payload = {
                "buckets": sorted(
                    self._buckets.values(),
                    key=lambda bucket: (str(bucket["startedAt"]), str(bucket["sessionId"])),
                )
            }
            temporary.write_text(
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            return

def _priority_alert_keys(config: AppConfig) -> tuple[str, ...]:
    keys = {
        _detection_key(droid, rarity)
        for droid, rarity in config.targets
    }
    return tuple(sorted(keys & VALID_PRIORITY_ALERT_KEYS))


def _belt_target_names(config: AppConfig) -> tuple[str, ...]:
    return belt_target_names(config.belt_target_tiers)


def _utc_hour_text() -> str:
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return hour.isoformat().replace("+00:00", "Z")


def _belt_bucket_key(session_id: str, started_at: str) -> str:
    return f"{session_id}|{started_at}"


def _valid_utc_hour(value: str) -> bool:
    if not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
        and parsed.minute == 0
        and parsed.second == 0
        and parsed.microsecond == 0
    )


def _valid_belt_counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for raw_name, raw_count in value.items():
        name = str(raw_name).strip().upper()
        if name not in VALID_BELT_DROID_NAMES or isinstance(raw_count, bool):
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if 0 < count <= 10_000:
            counts[name] = count
    return counts


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
