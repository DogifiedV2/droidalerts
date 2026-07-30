from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping

from ..capture import PixelBox, create_capture
from .dev_capture import BeltDevCaptureRecorder, candidate_diagnostics
from .dev_logging import BeltDevLogger, runtime_snapshot
from .events import log_track_event
from .runtime import (
    AdaptiveScanScheduler,
    DEFAULT_ACTIVE_SCAN_FPS,
    DEFAULT_IDLE_SCAN_FPS,
    MAXIMUM_SCAN_FPS,
    MINIMUM_SCAN_FPS,
    TEMPLATE_IDLE_BACKOFF_START,
    adaptive_track_timeout,
    adaptive_template_interval,
    build_tracks_payload,
    normalize_scan_fps,
)
from .sample_collection import BeltTemplateSampleCollector
from .scale_recognition import HybridCardRecognizer
from .targets import is_belt_alert_target, normalize_belt_target_tiers
from .tracking import BeltTracker


OVERLAY_FPS = 12.0
TRACK_TIMEOUT_SECONDS = 3.5
CONFIRMATION_HITS = 4
SLOW_CONFIRMATION_HITS = 2
SLOW_CADENCE_SECONDS = 0.70
SLOW_MINIMUM_IDENTITY_CONFIDENCE = 0.90
TEMPLATE_MINIMUM_DISPLACEMENT_RATIO = 0.10

StatusCallback = Callable[[dict[str, object]], None]


def run_belt_watcher(
    monitor_index: int,
    region: PixelBox,
    *,
    target_tiers: Mapping[str, str] | None = None,
    stop_event: threading.Event,
    status_callback: StatusCallback | None = None,
    capture_source: str = "monitor",
    window_title: str = "",
    window_process: str = "",
    window_class: str = "",
    device_name: str = "",
    device_path: str = "",
    device_vid: int | None = None,
    device_pid: int | None = None,
    device_backend: int = 0,
    shared_device_spec=None,
    dev_mode: bool = False,
    collect_template_samples: bool = False,
    idle_scan_fps: int = DEFAULT_IDLE_SCAN_FPS,
    active_scan_fps: int = DEFAULT_ACTIVE_SCAN_FPS,
) -> None:
    """Watch one belt region without interfering with the chat-alert watcher.

    ``target_tiers`` controls alerts only. Recognition and the belt overlay
    continue to include every exact droid name when there are no alert rules.
    """

    def emit(kind: str, **values: object) -> None:
        if status_callback is not None:
            status_callback({"type": kind, **values})

    dev_logger = BeltDevLogger(dev_mode)
    dev_capture_recorder: BeltDevCaptureRecorder | None = None
    idle_scan_fps, active_scan_fps = normalize_scan_fps(
        idle_scan_fps,
        active_scan_fps,
    )
    scan_scheduler = AdaptiveScanScheduler(idle_scan_fps, active_scan_fps)

    capture_source = str(capture_source).strip().lower()
    if capture_source not in {"monitor", "window", "device"}:
        capture_source = "monitor"

    def open_capture():
        if capture_source == "monitor":
            return create_capture(monitor_index=monitor_index, prefer_dxcam=False)
        if capture_source == "device" and shared_device_spec is not None:
            from ..device_capture import SharedDeviceCaptureClient

            capture = SharedDeviceCaptureClient(shared_device_spec)
            capture.screen_size()
            return capture
        kwargs = dict(
            monitor_index=monitor_index,
            prefer_dxcam=False,
            capture_source=capture_source,
            window_title=window_title,
            window_process=window_process,
            window_class=window_class,
        )
        if capture_source == "device":
            kwargs.update(
                device_name=device_name,
                device_path=device_path,
                device_vid=device_vid,
                device_pid=device_pid,
                device_backend=device_backend,
            )
        capture = create_capture(**kwargs)
        if capture_source != "device":
            return capture
        try:
            capture.screen_size()
        except Exception:
            try:
                capture.close()
            except Exception:
                pass
            raise
        return capture

    # DXcam caches one camera per display. Monitor capture deliberately uses
    # an independent MSS backend, while a Dashboard window selection routes
    # both watchers through Windows Graphics Capture.
    capture_started = time.perf_counter()
    capture = None
    while capture is None and not stop_event.is_set():
        try:
            capture = open_capture()
        except Exception as exc:
            dev_logger.log("capture_start_failed", error=str(exc))
            if capture_source not in {"window", "device"}:
                emit("error", message=f"Screen capture could not start: {exc}")
                emit("stopped")
                return
            emit(
                "capture_error",
                message=f"Capture source is unavailable; retrying automatically: {exc}",
            )
            stop_event.wait(1.0)
    if capture is None:
        emit("stopped")
        return
    capture_init_seconds = time.perf_counter() - capture_started
    try:
        source_width, source_height = capture.screen_size()
        source_resolution = [int(source_width), int(source_height)]
    except Exception:
        source_resolution = []

    tracker = BeltTracker(
        confirmation_hits=CONFIRMATION_HITS,
        timeout_seconds=TRACK_TIMEOUT_SECONDS,
        slow_confirmation_hits=SLOW_CONFIRMATION_HITS,
        slow_cadence_seconds=SLOW_CADENCE_SECONDS,
        slow_minimum_confidence=SLOW_MINIMUM_IDENTITY_CONFIDENCE,
        minimum_template_displacement_ratio=TEMPLATE_MINIMUM_DISPLACEMENT_RATIO,
    )
    alert_targets = normalize_belt_target_tiers(target_tiers)
    recognizer_init_started = time.perf_counter()
    detector_mode = "hybrid-v2"
    try:
        recognizer = HybridCardRecognizer()
    except Exception as exc:
        dev_logger.log(
            "recognizer_start_failed",
            detector=detector_mode,
            error=str(exc),
        )
        emit("error", message=f"Belt template recognition could not start: {exc}")
        capture.close()
        emit("stopped")
        return
    recognizer_init_seconds = time.perf_counter() - recognizer_init_started

    sample_collector: BeltTemplateSampleCollector | None = None
    if collect_template_samples:
        try:
            sample_collector = BeltTemplateSampleCollector()
        except Exception as exc:
            dev_logger.log("sample_collection_start_failed", error=str(exc))
            emit(
                "sample_collection",
                enabled=False,
                error=f"Template sample collection could not start: {exc}",
            )

    if dev_logger.enabled:
        dev_capture_recorder = BeltDevCaptureRecorder(
            dev_logger.session_dir,
            enabled=True,
        )
        dev_capture_recorder.set_session_metadata(
            runtime=runtime_snapshot(),
            monitor_index=monitor_index,
            region={
                "left": region.left,
                "top": region.top,
                "width": region.width,
                "height": region.height,
            },
            capture_source=capture_source,
            source_resolution=source_resolution,
            detector=detector_mode,
            confirmation_hits=CONFIRMATION_HITS,
            slow_confirmation_hits=SLOW_CONFIRMATION_HITS,
            idle_scan_fps=idle_scan_fps,
            active_scan_fps=active_scan_fps,
        )
        dev_logger.log(
            "session_start",
            runtime=runtime_snapshot(),
            monitor_index=monitor_index,
            region={
                "left": region.left,
                "top": region.top,
                "width": region.width,
                "height": region.height,
            },
            capture_init_seconds=capture_init_seconds,
            source_resolution=source_resolution,
            detector=detector_mode,
            recognizer_init_seconds=recognizer_init_seconds,
            recognizer=type(recognizer).__name__,
            confirmation_hits=CONFIRMATION_HITS,
            slow_confirmation_hits=SLOW_CONFIRMATION_HITS,
            slow_cadence_seconds=SLOW_CADENCE_SECONDS,
            slow_minimum_identity_confidence=SLOW_MINIMUM_IDENTITY_CONFIDENCE,
            minimum_template_displacement_ratio=TEMPLATE_MINIMUM_DISPLACEMENT_RATIO,
            base_track_timeout_seconds=TRACK_TIMEOUT_SECONDS,
            collect_template_samples=sample_collector is not None,
            idle_scan_fps=idle_scan_fps,
            active_scan_fps=active_scan_fps,
        )
        emit("dev_log", path=dev_logger.relative_path())

    if sample_collector is not None:
        emit("sample_collection", **sample_collector.status())

    frame_period = 1.0 / OVERLAY_FPS
    next_scan = 0.0
    last_scan_status = 0.0
    frame_number = 0
    last_capture_at: float | None = None
    alerted_track_ids: set[int] = set()
    last_published_tracks: list[dict[str, object]] | None = None
    emit(
        "ready",
        region=region,
        monitor_index=monitor_index,
        detector=detector_mode,
        idle_scan_fps=idle_scan_fps,
        active_scan_fps=active_scan_fps,
    )
    try:
        while not stop_event.is_set():
            loop_started = time.monotonic()
            frame_number += 1
            dev_scan_capture = None
            if loop_started >= next_scan:
                capture_frame_started = time.perf_counter()
                try:
                    frame = capture.grab(region)
                except Exception as exc:
                    dev_logger.log("capture_error", frame=frame_number, error=str(exc))
                    if capture_source in {"window", "device"}:
                        emit(
                            "capture_error",
                            message=f"Capture source was lost; retrying automatically: {exc}",
                        )
                        try:
                            capture.close()
                        except Exception:
                            pass
                        capture = None
                        while capture is None and not stop_event.is_set():
                            try:
                                capture = open_capture()
                            except Exception as recovery_exc:
                                dev_logger.log(
                                    "capture_reconnect_failed",
                                    frame=frame_number,
                                    error=str(recovery_exc),
                                )
                                stop_event.wait(1.0)
                        if capture is not None:
                            emit("capture_reconnected")
                    else:
                        emit("error", message=f"Belt screen capture failed: {exc}")
                    update = tracker.predict(loop_started, region.width)
                    next_scan = loop_started + 0.5
                else:
                    capture_seconds = time.perf_counter() - capture_frame_started
                    # This timestamp belongs to the pixels just captured. Slow
                    # detections must be tracked at capture time, not at the
                    # later instant when inference finishes.
                    now = time.monotonic()
                    observed_capture_interval = (
                        now - last_capture_at if last_capture_at is not None else None
                    )
                    tracker.timeout_seconds = adaptive_track_timeout(
                        TRACK_TIMEOUT_SECONDS,
                        observed_capture_interval,
                    )
                    last_capture_at = now
                    base_interval = 1.0 / active_scan_fps
                    try:
                        result = recognizer.analyze(frame, now=now)
                        observations = result.observations
                        update = tracker.update(observations, now, region.width)
                        if dev_capture_recorder is not None:
                            # Production exit/update decisions are attached
                            # below before this scan is allowed to expire a
                            # diagnostic track.
                            dev_scan_capture = (
                                frame,
                                result.candidates,
                                getattr(
                                    update,
                                    "observation_track_ids",
                                    {},
                                ),
                                now,
                                tracker.timeout_seconds,
                            )
                        if sample_collector is not None:
                            accepted_candidates = tuple(
                                candidate for candidate in result.candidates if candidate.accepted
                            )
                            sample_collector.observe(
                                frame,
                                accepted_candidates,
                                getattr(update, "observation_track_ids", {}),
                                now=now,
                                frame_number=frame_number,
                            )
                        completed_at = time.monotonic()
                        card_window_count = int(
                            result.diagnostics.get(
                                "card_window_count",
                                len(result.candidates),
                            )
                            or 0
                        )
                        timing = scan_scheduler.record(
                            card_window_count=card_window_count,
                            captured_at=now,
                            completed_at=completed_at,
                        )
                        scan_seconds = float(timing["scan_seconds"])
                        scan_interval_seconds = timing["scan_interval_seconds"]
                        sample_fps = float(timing["scan_fps"])
                        throughput_fps = float(timing["scan_throughput_fps"])
                        scan_interval = float(timing["next_scan_interval_seconds"])
                        next_scan = float(timing["next_scan_at"])
                        empty_candidate_scans = int(timing["empty_candidate_scans"])
                        # The detected boxes belong to the frame captured before
                        # recognition began. Predict them forward by its duration so
                        # the overlay is current when it finally reaches the GUI.
                        display_update = tracker.predict(completed_at, region.width)
                        update.tracks = display_update.tracks
                        update.events.extend(display_update.events)
                        if dev_logger.enabled:
                            dev_logger.remember_frame(
                                frame,
                                frame_number=frame_number,
                                now=completed_at,
                                metadata={
                                    "source_frame_shape": list(frame.shape),
                                    "accepted_count": len(observations),
                                    "candidates": [
                                        candidate_diagnostics(item)
                                        for item in result.candidates
                                    ],
                                },
                            )
                            issue_report = dev_logger.consume_issue_request()
                            if issue_report:
                                dev_logger.log(
                                    "issue_report",
                                    frame=frame_number,
                                    path=issue_report,
                                )
                                emit("belt_issue_report", path=issue_report)
                            evidence_event = next(
                                (
                                    event.kind
                                    for event in update.events
                                    if event.kind in {"entered", "updated"}
                                ),
                                "",
                            )
                            ambiguous_frame = bool(
                                result.diagnostics.get("ambiguous_count", 0)
                            ) and not observations
                            frame_reason = (
                                evidence_event
                                if evidence_event
                                else "ambiguous"
                                if ambiguous_frame
                                else "periodic"
                            )
                            frame_file = dev_logger.save_frame(
                                frame,
                                frame_number=frame_number,
                                now=completed_at,
                                reason=frame_reason,
                                force=bool(evidence_event) or ambiguous_frame,
                                lossless=bool(evidence_event),
                            )
                            dev_logger.log(
                                "scan",
                                frame=frame_number,
                                detector=detector_mode,
                                capture_seconds=capture_seconds,
                                scan_seconds=scan_seconds,
                                scan_interval_seconds=scan_interval_seconds,
                                scan_fps=sample_fps,
                                scan_throughput_fps=throughput_fps,
                                next_scan_interval_seconds=scan_interval,
                                empty_candidate_scans=empty_candidate_scans,
                                adaptive_track_timeout_seconds=tracker.timeout_seconds,
                                raw_observations=[],
                                candidates=[
                                    candidate_diagnostics(item)
                                    for item in result.candidates
                                ],
                                rejection_counts=dict(
                                    Counter(
                                        item.reason
                                        for item in result.candidates
                                        if not item.accepted
                                    )
                                ),
                                accepted_count=len(observations),
                                recognizer=result.diagnostics,
                                tracker=tracker.diagnostic_state(),
                                saved_frame=frame_file,
                                saved_frame_reason=(
                                    dev_logger.last_saved_reason if frame_file else ""
                                ),
                            )
                        if completed_at - last_scan_status >= 1.0:
                            emit(
                                "scan",
                                detector=detector_mode,
                                raw_count=0,
                                candidate_count=len(result.candidates),
                                accepted_count=len(observations),
                                frame=frame_number,
                                scan_seconds=scan_seconds,
                                scan_fps=sample_fps,
                                scan_throughput_fps=throughput_fps,
                                idle_scan_fps=idle_scan_fps,
                                active_scan_fps=active_scan_fps,
                                track_timeout_seconds=tracker.timeout_seconds,
                            )
                            last_scan_status = completed_at
                    except Exception as exc:
                        dev_logger.log(
                            "recognition_error",
                            detector=detector_mode,
                            frame=frame_number,
                            error=str(exc),
                        )
                        emit("error", message=f"Belt {detector_mode} scan failed: {exc}")
                        update = tracker.predict(time.monotonic(), region.width)
                        next_scan = now + base_interval
            else:
                # Overlay prediction remains smooth at 12 Hz, but no screen
                # capture is performed until the recognizer can consume it.
                update = tracker.predict(loop_started, region.width)

            if sample_collector is not None:
                collection_updates = sample_collector.process_events(update.events)
                collection_updates.extend(sample_collector.expire(time.monotonic()))
                for collection_update in collection_updates:
                    emit(
                        "sample_collection",
                        **sample_collector.status(collection_update),
                    )

            for event in update.events:
                track_id = int(event.track.id)
                if event.kind == "exited":
                    alerted_track_ids.discard(track_id)
                elif event.kind == "reacquired":
                    # The tracker proved this is the same physical card after
                    # a camera jump. Preserve its already-alerted state so a
                    # later family update cannot send a duplicate alert.
                    alerted_track_ids.add(track_id)
                alerted = (
                    event.kind in {"entered", "updated"}
                    and track_id not in alerted_track_ids
                    and is_belt_alert_target(
                        alert_targets,
                        event.track.name,
                        getattr(event.track, "family", ""),
                    )
                )
                if alerted:
                    alerted_track_ids.add(track_id)
                if dev_capture_recorder is not None:
                    try:
                        dev_capture_recorder.record_tracker_event(
                            event,
                            alerted=alerted,
                        )
                    except Exception as exc:
                        dev_logger.log(
                            "dev_capture_event_error",
                            frame=frame_number,
                            error=str(exc),
                        )
                record = log_track_event(event, alerted=alerted)
                attributes = " ".join(
                    value
                    for value in (
                        str(getattr(event.track, "family", "") or ""),
                        str(getattr(event.track, "rarity", "") or ""),
                    )
                    if value
                )
                rarity_text = f" [{attributes}]" if attributes else ""
                print(
                    f"[BELT] Track {event.track.id} {event.kind}: "
                    f"{event.track.name}{rarity_text} "
                    f"({event.track.confidence:.0%})",
                    flush=True,
                )
                emit("track_event", record=record)
            if dev_capture_recorder is not None and dev_scan_capture is not None:
                (
                    dev_frame,
                    dev_candidates,
                    dev_track_ids,
                    dev_captured_at,
                    dev_track_timeout,
                ) = dev_scan_capture
                try:
                    dev_capture_recorder.observe(
                        dev_frame,
                        dev_candidates,
                        dev_track_ids,
                        now=dev_captured_at,
                        frame_number=frame_number,
                        track_timeout_seconds=dev_track_timeout,
                    )
                except Exception as exc:
                    dev_logger.log(
                        "dev_capture_error",
                        frame=frame_number,
                        error=str(exc),
                    )
            tracks_payload = build_tracks_payload(update.tracks)
            if last_published_tracks is None or tracks_payload != last_published_tracks:
                emit("tracks", tracks=tracks_payload)
                last_published_tracks = tracks_payload
            loop_completed = time.monotonic()
            overlay_wait = max(0.0, frame_period - (loop_completed - loop_started))
            scan_wait = max(0.0, next_scan - loop_completed)
            # Wake early when the next configured scan falls between 12 Hz
            # overlay ticks. This prevents an 8 FPS request being quantized to
            # roughly 6 FPS while retaining the lightweight overlay cadence.
            stop_event.wait(min(overlay_wait, scan_wait))
    finally:
        if sample_collector is not None:
            for collection_update in sample_collector.close():
                emit(
                    "sample_collection",
                    **sample_collector.status(collection_update),
                )
            emit("sample_collection", **sample_collector.status())
        if capture is not None:
            capture.close()
        dev_logger.log("session_stop", frame=frame_number)
        if dev_capture_recorder is not None:
            capture_status = dev_capture_recorder.close()
            dev_logger.log(
                "dev_capture_stop",
                frame=frame_number,
                status=capture_status,
            )
            emit(
                "dev_capture",
                path=dev_logger.relative_path(),
                **capture_status,
            )
        emit("stopped")
