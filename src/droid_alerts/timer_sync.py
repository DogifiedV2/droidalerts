from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .network import certifi_ssl_context


DEFAULT_TIMER_SCHEDULE_URL = "https://gonk.tools/api/droid-alerts/spawn-schedule"
DEFAULT_TIMER_SCHEDULES: dict[str, tuple[int, int]] = {
    "beskar": (15 * 60, 0),
    "mythic": (60 * 60, 55 * 60),
    "rainbow": (10 * 60, 0),
    "galactic": (60 * 60, 45 * 60),
}
SYNC_SAMPLE_COUNT = 3
SYNC_TIMEOUT_SECONDS = 3.0
SYNC_RETRY_SECONDS = 60


@dataclass(frozen=True)
class _SyncSample:
    payload: Mapping[str, Any]
    sent_wall: float
    received_wall: float
    received_monotonic: float
    round_trip_seconds: float


class SyncedTimerSchedule:
    """Authoritative spawn schedule with a monotonic, server-synchronized clock."""

    def __init__(
        self,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._lock = threading.Lock()
        self._schedules = dict(DEFAULT_TIMER_SCHEDULES)
        self._server_anchor_seconds: float | None = None
        self._monotonic_anchor: float | None = None
        self._schedule_version: int | None = None

    def apply_server_payload(
        self,
        payload: Mapping[str, Any],
        *,
        sent_wall: float,
        received_wall: float,
        received_monotonic: float | None = None,
    ) -> int:
        server_time_ms = int(payload["serverTimeMs"])
        schedule_version = int(payload["scheduleVersion"])
        raw_schedules = payload["schedules"]
        if schedule_version < 1 or not isinstance(raw_schedules, Mapping):
            raise ValueError("Invalid timer schedule response")

        schedules: dict[str, tuple[int, int]] = {}
        for kind in DEFAULT_TIMER_SCHEDULES:
            raw = raw_schedules.get(kind)
            if not isinstance(raw, Mapping):
                raise ValueError(f"Missing {kind} timer schedule")
            interval = int(raw["intervalSeconds"])
            offset = int(raw["offsetSeconds"])
            if interval < 60 or interval > 24 * 60 * 60 or not 0 <= offset < interval:
                raise ValueError(f"Invalid {kind} timer schedule")
            schedules[kind] = (interval, offset)

        # The response timestamp is treated as occurring at the request
        # midpoint, compensating for ordinary round-trip network latency.
        midpoint_wall = (float(sent_wall) + float(received_wall)) / 2.0
        clock_offset = server_time_ms / 1000.0 - midpoint_wall
        server_at_receive = float(received_wall) + clock_offset
        monotonic_anchor = (
            self._monotonic_clock()
            if received_monotonic is None
            else float(received_monotonic)
        )
        refresh_after = max(60, min(3600, int(payload.get("refreshAfterSeconds", 600))))

        with self._lock:
            self._schedules = schedules
            self._server_anchor_seconds = server_at_receive
            self._monotonic_anchor = monotonic_anchor
            self._schedule_version = schedule_version
        return refresh_after

    def current_time_seconds(self) -> float:
        with self._lock:
            server_anchor = self._server_anchor_seconds
            monotonic_anchor = self._monotonic_anchor
        if server_anchor is None or monotonic_anchor is None:
            return self._wall_clock()
        return server_anchor + (self._monotonic_clock() - monotonic_anchor)

    def seconds_until_next(self, kind: str, manual_offset_seconds: int = 0) -> int:
        with self._lock:
            try:
                interval, schedule_offset = self._schedules[kind]
            except KeyError as exc:
                raise ValueError(f"Unknown timer kind: {kind}") from exc
        now = int(self.current_time_seconds()) + int(manual_offset_seconds)
        return interval - ((now - schedule_offset) % interval)

    @property
    def synchronized(self) -> bool:
        with self._lock:
            return self._server_anchor_seconds is not None

    @property
    def schedule_version(self) -> int | None:
        with self._lock:
            return self._schedule_version


TIMER_SCHEDULE_CLOCK = SyncedTimerSchedule()
_sync_thread_lock = threading.Lock()
_sync_thread: threading.Thread | None = None
_sync_endpoint = ""


def _fetch_sample(endpoint_url: str) -> _SyncSample:
    request = urllib.request.Request(
        endpoint_url,
        headers={"Accept": "application/json", "User-Agent": "DroidAlerts-TimerSync/1"},
    )
    sent_wall = time.time()
    sent_monotonic = time.monotonic()
    with urllib.request.urlopen(
        request,
        timeout=SYNC_TIMEOUT_SECONDS,
        context=certifi_ssl_context(),
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))
    received_monotonic = time.monotonic()
    received_wall = time.time()
    if not isinstance(payload, Mapping):
        raise ValueError("Timer schedule response must be a JSON object")
    return _SyncSample(
        payload=payload,
        sent_wall=sent_wall,
        received_wall=received_wall,
        received_monotonic=received_monotonic,
        round_trip_seconds=max(0.0, received_monotonic - sent_monotonic),
    )


def _synchronize_once(endpoint_url: str) -> int:
    samples: list[_SyncSample] = []
    for _ in range(SYNC_SAMPLE_COUNT):
        try:
            samples.append(_fetch_sample(endpoint_url))
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError, TimeoutError):
            continue
    if not samples:
        raise OSError("Timer schedule service is unavailable")
    best = min(samples, key=lambda sample: sample.round_trip_seconds)
    return TIMER_SCHEDULE_CLOCK.apply_server_payload(
        best.payload,
        sent_wall=best.sent_wall,
        received_wall=best.received_wall,
        received_monotonic=best.received_monotonic,
    )


def start_timer_schedule_sync(
    endpoint_url: str = DEFAULT_TIMER_SCHEDULE_URL,
    *,
    stop_event: threading.Event | None = None,
) -> threading.Thread | None:
    """Start the shared background synchronizer once per process."""
    endpoint = str(endpoint_url).strip()
    if not endpoint:
        return None

    global _sync_endpoint, _sync_thread
    with _sync_thread_lock:
        if _sync_thread is not None and _sync_thread.is_alive() and _sync_endpoint == endpoint:
            return _sync_thread

        def run() -> None:
            refresh_after = SYNC_RETRY_SECONDS
            while stop_event is None or not stop_event.is_set():
                try:
                    refresh_after = _synchronize_once(endpoint)
                except (OSError, ValueError, KeyError, TypeError):
                    refresh_after = SYNC_RETRY_SECONDS
                if stop_event is not None:
                    if stop_event.wait(refresh_after):
                        return
                else:
                    time.sleep(refresh_after)

        _sync_endpoint = endpoint
        _sync_thread = threading.Thread(
            target=run,
            name="droid-timer-sync",
            daemon=True,
        )
        _sync_thread.start()
        return _sync_thread
