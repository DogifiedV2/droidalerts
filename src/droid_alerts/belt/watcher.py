from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable

from ..capture import PixelBox, create_capture
from .dev_logging import BeltDevLogger, runtime_snapshot
from .events import log_track_event
from .ocr import RapidOcrEngine
from .recognition import CardRecognizer, CardRecognitionConfig
from .tracking import BeltTracker


CAPTURE_FPS = 12.0
OCR_INTERVAL_SECONDS = 0.25
TRACK_TIMEOUT_SECONDS = 3.5
CONFIRMATION_HITS = 4
MINIMUM_OCR_CONFIDENCE = 0.70
MAX_ADAPTIVE_TRACK_TIMEOUT_SECONDS = 60.0

StatusCallback = Callable[[dict[str, object]], None]


def adaptive_track_timeout(ocr_seconds: float) -> float:
    """Keep tracks alive across slow OCR passes without making them permanent."""

    return min(
        MAX_ADAPTIVE_TRACK_TIMEOUT_SECONDS,
        max(TRACK_TIMEOUT_SECONDS, max(0.0, float(ocr_seconds)) * 2.5 + 0.5),
    )


def run_belt_watcher(
    monitor_index: int,
    region: PixelBox,
    *,
    target_names: Iterable[str] = (),
    stop_event: threading.Event,
    status_callback: StatusCallback | None = None,
    ocr_engine=None,
    dev_mode: bool = False,
) -> None:
    """Watch one belt region without interfering with the chat-alert watcher.

    ``target_names`` controls alerts only. Recognition and the belt overlay
    continue to include every exact droid name when no alerts are selected.
    """

    def emit(kind: str, **values: object) -> None:
        if status_callback is not None:
            status_callback({"type": kind, **values})

    dev_logger = BeltDevLogger(dev_mode)

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
    )
    alert_targets = {str(name).strip().upper() for name in target_names if str(name).strip()}
    ocr_init_started = time.perf_counter()
    try:
        engine = ocr_engine if ocr_engine is not None else RapidOcrEngine()
        recognizer = CardRecognizer(
            engine,
            config=CardRecognitionConfig(minimum_ocr_confidence=MINIMUM_OCR_CONFIDENCE),
        )
    except Exception as exc:
        dev_logger.log("ocr_start_failed", error=str(exc))
        emit("error", message=f"Belt OCR could not start: {exc}")
        capture.close()
        emit("stopped")
        return
    ocr_init_seconds = time.perf_counter() - ocr_init_started

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
            ocr_init_seconds=ocr_init_seconds,
            ocr_engine=type(engine).__name__,
            ocr_engine_init_seconds=float(getattr(engine, "init_seconds", 0.0)),
            ocr_engine_params=dict(getattr(engine, "engine_params", {})),
            confirmation_hits=CONFIRMATION_HITS,
            base_track_timeout_seconds=TRACK_TIMEOUT_SECONDS,
            minimum_ocr_confidence=MINIMUM_OCR_CONFIDENCE,
        )
        emit("dev_log", path=dev_logger.relative_path())

    frame_period = 1.0 / CAPTURE_FPS
    next_ocr = 0.0
    last_scan_status = 0.0
    previous_ocr_completed_at: float | None = None
    frame_number = 0
    emit("ready", region=region, monitor_index=monitor_index)
    try:
        while not stop_event.is_set():
            loop_started = time.monotonic()
            frame_number += 1
            capture_frame_started = time.perf_counter()
            try:
                frame = capture.grab(region)
            except Exception as exc:
                dev_logger.log("capture_error", frame=frame_number, error=str(exc))
                emit("error", message=f"Belt screen capture failed: {exc}")
                stop_event.wait(0.5)
                continue
            capture_seconds = time.perf_counter() - capture_frame_started

            now = time.monotonic()
            if now >= next_ocr:
                try:
                    result = recognizer.analyze(frame)
                    observations = result.observations
                    update = tracker.update(observations, now, region.width)
                    completed_at = time.monotonic()
                    ocr_seconds = max(0.001, completed_at - now)
                    tracker.timeout_seconds = adaptive_track_timeout(ocr_seconds)
                    # The detected boxes belong to the frame captured before
                    # OCR began. Predict them forward by the OCR duration so
                    # the overlay is current when it finally reaches the GUI.
                    display_update = tracker.predict(completed_at, region.width)
                    update.tracks = display_update.tracks
                    update.events.extend(display_update.events)
                    if dev_logger.enabled:
                        scan_interval_seconds = (
                            completed_at - previous_ocr_completed_at
                            if previous_ocr_completed_at is not None
                            else None
                        )
                        frame_file = dev_logger.save_frame(
                            frame,
                            frame_number=frame_number,
                            now=completed_at,
                        )
                        dev_logger.log(
                            "scan",
                            frame=frame_number,
                            capture_seconds=capture_seconds,
                            ocr_seconds=ocr_seconds,
                            scan_interval_seconds=scan_interval_seconds,
                            ocr_fps=1.0 / max(ocr_seconds, OCR_INTERVAL_SECONDS),
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
                    previous_ocr_completed_at = completed_at
                    if completed_at - last_scan_status >= 1.0:
                        emit(
                            "scan",
                            raw_count=len(result.text_observations),
                            candidate_count=len(result.candidates),
                            accepted_count=len(observations),
                            frame=frame_number,
                            ocr_seconds=ocr_seconds,
                            ocr_fps=1.0 / max(ocr_seconds, OCR_INTERVAL_SECONDS),
                            track_timeout_seconds=tracker.timeout_seconds,
                        )
                        last_scan_status = completed_at
                except Exception as exc:
                    dev_logger.log("ocr_error", frame=frame_number, error=str(exc))
                    emit("error", message=f"Belt OCR frame failed: {exc}")
                    update = tracker.predict(time.monotonic(), region.width)
                next_ocr = now + OCR_INTERVAL_SECONDS
            else:
                update = tracker.predict(now, region.width)

            for event in update.events:
                alerted = event.kind == "entered" and event.track.name.upper() in alert_targets
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
            elapsed = time.monotonic() - loop_started
            stop_event.wait(max(0.0, frame_period - elapsed))
    finally:
        capture.close()
        dev_logger.log("session_stop", frame=frame_number)
        emit("stopped")


def _candidate_diagnostics(candidate) -> dict[str, object]:
    context = candidate.context
    return {
        "name": candidate.canonical_name,
        "raw_text": candidate.raw_text,
        "ocr_confidence": candidate.ocr_confidence,
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
