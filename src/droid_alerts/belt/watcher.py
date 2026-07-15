from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping

from ..capture import PixelBox, create_capture
from .dev_logging import BeltDevLogger, runtime_snapshot
from .events import log_track_event
from .sample_collection import BeltTemplateSampleCollector
from .targets import is_belt_alert_target, normalize_belt_target_tiers
from .template_recognition import TemplateCardRecognizer
from .tracking import BeltTracker


OVERLAY_FPS = 12.0
DEFAULT_IDLE_SCAN_FPS = 4
DEFAULT_ACTIVE_SCAN_FPS = 8
MINIMUM_SCAN_FPS = 1
MAXIMUM_SCAN_FPS = 20
TRACK_TIMEOUT_SECONDS = 3.5
CONFIRMATION_HITS = 4
SLOW_CONFIRMATION_HITS = 3
SLOW_CADENCE_SECONDS = 4.0 / 3.0
SLOW_MINIMUM_IDENTITY_CONFIDENCE = 0.78
TEMPLATE_IDLE_BACKOFF_START = 12

StatusCallback = Callable[[dict[str, object]], None]


def normalize_scan_fps(idle_scan_fps: int, active_scan_fps: int) -> tuple[int, int]:
    idle = min(MAXIMUM_SCAN_FPS, max(MINIMUM_SCAN_FPS, int(idle_scan_fps)))
    active = min(MAXIMUM_SCAN_FPS, max(MINIMUM_SCAN_FPS, int(active_scan_fps)))
    return min(idle, active), active


def adaptive_template_interval(
    empty_card_scans: int,
    idle_scan_fps: int = DEFAULT_IDLE_SCAN_FPS,
    active_scan_fps: int = DEFAULT_ACTIVE_SCAN_FPS,
) -> float:
    """Use the configured active rate, backing off to idle when the belt is empty."""

    if max(0, int(empty_card_scans)) >= TEMPLATE_IDLE_BACKOFF_START:
        return 1.0 / idle_scan_fps
    return 1.0 / active_scan_fps


def run_belt_watcher(
    monitor_index: int,
    region: PixelBox,
    *,
    target_tiers: Mapping[str, str] | None = None,
    stop_event: threading.Event,
    status_callback: StatusCallback | None = None,
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
    idle_scan_fps, active_scan_fps = normalize_scan_fps(
        idle_scan_fps,
        active_scan_fps,
    )

    # DXcam caches one camera per display. The chat watcher may already own it,
    # so Belt Tracker deliberately uses an independent MSS capture.
    capture_started = time.perf_counter()
    try:
        capture = create_capture(monitor_index=monitor_index, prefer_dxcam=False)
    except Exception as exc:
        dev_logger.log("capture_start_failed", error=str(exc))
        emit("error", message=f"Screen capture could not start: {exc}")
        emit("stopped")
        return
    capture_init_seconds = time.perf_counter() - capture_started

    tracker = BeltTracker(
        confirmation_hits=CONFIRMATION_HITS,
        timeout_seconds=TRACK_TIMEOUT_SECONDS,
        slow_confirmation_hits=SLOW_CONFIRMATION_HITS,
        slow_cadence_seconds=SLOW_CADENCE_SECONDS,
        slow_minimum_confidence=SLOW_MINIMUM_IDENTITY_CONFIDENCE,
    )
    alert_targets = normalize_belt_target_tiers(target_tiers)
    recognizer_init_started = time.perf_counter()
    detector_mode = "templates"
    try:
        recognizer = TemplateCardRecognizer()
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
            detector=detector_mode,
            recognizer_init_seconds=recognizer_init_seconds,
            recognizer=type(recognizer).__name__,
            confirmation_hits=CONFIRMATION_HITS,
            slow_confirmation_hits=SLOW_CONFIRMATION_HITS,
            slow_cadence_seconds=SLOW_CADENCE_SECONDS,
            slow_minimum_identity_confidence=SLOW_MINIMUM_IDENTITY_CONFIDENCE,
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
    previous_scan_completed_at: float | None = None
    empty_candidate_scans = 0
    frame_number = 0
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
            if loop_started >= next_scan:
                capture_frame_started = time.perf_counter()
                try:
                    frame = capture.grab(region)
                except Exception as exc:
                    dev_logger.log("capture_error", frame=frame_number, error=str(exc))
                    emit("error", message=f"Belt screen capture failed: {exc}")
                    update = tracker.predict(loop_started, region.width)
                    next_scan = loop_started + 0.5
                else:
                    capture_seconds = time.perf_counter() - capture_frame_started
                    # This timestamp belongs to the pixels just captured. Slow
                    # detections must be tracked at capture time, not at the
                    # later instant when inference finishes.
                    now = time.monotonic()
                    base_interval = 1.0 / active_scan_fps
                    try:
                        result = recognizer.analyze(frame)
                        observations = result.observations
                        update = tracker.update(observations, now, region.width)
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
                        scan_seconds = max(0.001, completed_at - now)
                        tracker.timeout_seconds = TRACK_TIMEOUT_SECONDS
                        scan_interval_seconds = (
                            completed_at - previous_scan_completed_at
                            if previous_scan_completed_at is not None
                            else None
                        )
                        sample_seconds = (
                            scan_interval_seconds
                            if scan_interval_seconds is not None
                            else max(scan_seconds, base_interval)
                        )
                        sample_fps = 1.0 / max(0.001, sample_seconds)
                        throughput_fps = 1.0 / scan_seconds
                        card_window_count = int(
                            result.diagnostics.get(
                                "card_window_count",
                                len(result.candidates),
                            )
                            or 0
                        )
                        empty_candidate_scans = (
                            0 if card_window_count else empty_candidate_scans + 1
                        )
                        scan_interval = adaptive_template_interval(
                            empty_candidate_scans,
                            idle_scan_fps,
                            active_scan_fps,
                        )
                        next_scan = now + scan_interval
                        # The detected boxes belong to the frame captured before
                        # recognition began. Predict them forward by its duration so
                        # the overlay is current when it finally reaches the GUI.
                        display_update = tracker.predict(completed_at, region.width)
                        update.tracks = display_update.tracks
                        update.events.extend(display_update.events)
                        if dev_logger.enabled:
                            frame_file = dev_logger.save_frame(
                                frame,
                                frame_number=frame_number,
                                now=completed_at,
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
                                raw_observations=[
                                    {
                                        "text": item.text,
                                        "confidence": item.confidence,
                                        "box": list(item.box),
                                    }
                                    for item in result.text_observations
                                ],
                                candidates=[
                                    _candidate_diagnostics(item) for item in result.candidates
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
                            )
                        previous_scan_completed_at = completed_at
                        if completed_at - last_scan_status >= 1.0:
                            emit(
                                "scan",
                                detector=detector_mode,
                                raw_count=len(result.text_observations),
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
                alerted = event.kind == "entered" and is_belt_alert_target(
                    alert_targets,
                    event.track.name,
                    getattr(event.track, "family", ""),
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
            emit(
                "tracks",
                tracks=[
                    {
                        "id": track.id,
                        "name": track.name,
                        "family": str(getattr(track, "family", "") or ""),
                        "rarity": str(getattr(track, "rarity", "") or ""),
                        "box": tuple(round(value) for value in track.box),
                        "confidence": track.confidence,
                    }
                    for track in update.tracks
                ],
            )
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
        capture.close()
        dev_logger.log("session_stop", frame=frame_number)
        emit("stopped")


def _candidate_diagnostics(candidate) -> dict[str, object]:
    context = candidate.context
    return {
        "name": candidate.canonical_name,
        "raw_text": candidate.raw_text,
        "identity_confidence": candidate.ocr_confidence,
        "name_box": list(candidate.name_box),
        "accepted": candidate.accepted,
        "reason": candidate.reason,
        "family": candidate.family,
        "family_confidence": candidate.family_confidence,
        "rarity": candidate.rarity,
        "rarity_confidence": candidate.rarity_confidence,
        "context": {
            "art_box": list(context.art_box),
            "card_box": list(context.card_box),
            "nameplate_dark_fraction": context.nameplate_dark_fraction,
            "art_standard_deviation": context.art_standard_deviation,
            "art_edge_density": context.art_edge_density,
            "frame_line_ratio": context.frame_line_ratio,
            "accepted": context.accepted,
            "reason": context.reason,
        },
    }
