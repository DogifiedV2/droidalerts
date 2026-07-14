from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable

from ..capture import PixelBox, create_capture
from .events import log_track_event
from .ocr import RapidOcrEngine
from .recognition import CardRecognizer, CardRecognitionConfig
from .tracking import BeltTracker


CAPTURE_FPS = 12.0
OCR_INTERVAL_SECONDS = 0.25
TRACK_TIMEOUT_SECONDS = 3.5
CONFIRMATION_HITS = 4
MINIMUM_OCR_CONFIDENCE = 0.70

StatusCallback = Callable[[dict[str, object]], None]


def run_belt_watcher(
    monitor_index: int,
    region: PixelBox,
    *,
    target_names: Iterable[str] = (),
    stop_event: threading.Event,
    status_callback: StatusCallback | None = None,
    ocr_engine=None,
) -> None:
    """Watch one belt region without interfering with the chat-alert watcher.

    ``target_names`` controls alerts only. Recognition and the belt overlay
    continue to include every exact droid name when no alerts are selected.
    """

    def emit(kind: str, **values: object) -> None:
        if status_callback is not None:
            status_callback({"type": kind, **values})

    # DXcam caches one camera per display. The chat watcher may already own it,
    # so Belt Tracker deliberately uses an independent MSS capture.
    try:
        capture = create_capture(monitor_index=monitor_index, prefer_dxcam=False)
    except Exception as exc:
        emit("error", message=f"Screen capture could not start: {exc}")
        emit("stopped")
        return

    tracker = BeltTracker(
        confirmation_hits=CONFIRMATION_HITS,
        timeout_seconds=TRACK_TIMEOUT_SECONDS,
    )
    alert_targets = {str(name).strip().upper() for name in target_names if str(name).strip()}
    try:
        engine = ocr_engine if ocr_engine is not None else RapidOcrEngine()
        recognizer = CardRecognizer(
            engine,
            config=CardRecognitionConfig(minimum_ocr_confidence=MINIMUM_OCR_CONFIDENCE),
        )
    except Exception as exc:
        emit("error", message=f"Belt OCR could not start: {exc}")
        capture.close()
        emit("stopped")
        return

    frame_period = 1.0 / CAPTURE_FPS
    next_ocr = 0.0
    last_scan_status = 0.0
    frame_number = 0
    emit("ready", region=region, monitor_index=monitor_index)
    try:
        while not stop_event.is_set():
            loop_started = time.monotonic()
            frame_number += 1
            try:
                frame = capture.grab(region)
            except Exception as exc:
                emit("error", message=f"Belt screen capture failed: {exc}")
                stop_event.wait(0.5)
                continue

            now = time.monotonic()
            if now >= next_ocr:
                try:
                    result = recognizer.analyze(frame)
                    observations = result.observations
                    update = tracker.update(observations, now, region.width)
                    completed_at = time.monotonic()
                    # The detected boxes belong to the frame captured before
                    # OCR began. Predict them forward by the OCR duration so
                    # the overlay is current when it finally reaches the GUI.
                    display_update = tracker.predict(completed_at, region.width)
                    update.tracks = display_update.tracks
                    update.events.extend(display_update.events)
                    ocr_seconds = max(0.001, completed_at - now)
                    if completed_at - last_scan_status >= 1.0:
                        emit(
                            "scan",
                            raw_count=len(result.text_observations),
                            candidate_count=len(result.candidates),
                            accepted_count=len(observations),
                            frame=frame_number,
                            ocr_seconds=ocr_seconds,
                            ocr_fps=1.0 / max(ocr_seconds, OCR_INTERVAL_SECONDS),
                        )
                        last_scan_status = completed_at
                except Exception as exc:
                    emit("error", message=f"Belt OCR frame failed: {exc}")
                    update = tracker.predict(time.monotonic(), region.width)
                next_ocr = now + OCR_INTERVAL_SECONDS
            else:
                update = tracker.predict(now, region.width)

            for event in update.events:
                alerted = event.kind == "entered" and event.track.name.upper() in alert_targets
                record = log_track_event(event, alerted=alerted)
                print(
                    f"[BELT] Track {event.track.id} {event.kind}: {event.track.name} "
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
        emit("stopped")
