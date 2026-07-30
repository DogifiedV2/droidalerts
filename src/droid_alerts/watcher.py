from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import cv2

from .alerts import AlertPolicy, row_hash
from .auxiliary_alerts import AuxiliaryAlertSchedule
from .alert_delivery import (
    build_delivery_event,
    delivery_retryable as _delivery_retryable,
    execute_alert_delivery,
    persist_delivery_event,
)
from .capture import PixelBox, create_capture, set_dpi_awareness
from .capture_runtime import (
    capture_area as _capture_area,
    capture_label as _capture_label,
    capture_status as _capture_status,
    capture_target_signature as _capture_target_signature,
    create_configured_capture as _build_configured_capture,
)
from .cb23_mission import (
    CB23MissionDetector,
    CB23MissionGate,
    CB23MissionMatch,
    cb23_mission_region,
)
from .classifier import Detection
from .config import (
    CALIBRATION_FILE,
    CONFIG_FILE,
    AppConfig,
    config_dir,
    load_config,
    templates_dir,
)
from .image_io import write_cv_image
from .logging_io import alert_samples_dir, append_event_safely, debug_dir, timestamp
from .notifications import (
    AlertDelivery,
    enabled_alert_deliveries,
    load_discord_webhook,
    load_phone_alert_credentials,
    ntfy_configured,
    send_discord_alert,
    send_ntfy_alert,
    send_phone_alert,
)
from .pipeline import Pipeline
from .popup import popup_icon_path, show_popup
from .region import RegionResolver
from .rebirth import (
    RebirthAlertDetector,
    RebirthAlertTracker,
    RebirthHudDetector,
    RebirthMatch,
    RebirthPresenceGate,
    rebirth_region,
)
from .scrap_alert import (
    CreditHudDetector,
    ScrapIncomeTracker,
    ScrapVisibilityTracker,
    scrap_debug_detail,
)
from .telemetry import AnonymousTelemetryClient


REBIRTH_SCAN_INTERVAL_SECONDS = 0.4
CB23_MISSION_SCAN_INTERVAL_SECONDS = 0.5


def _create_configured_capture(config: AppConfig):
    """Compatibility facade that keeps the capture factory patchable."""
    return _build_configured_capture(config, factory=create_capture)


def _open_configured_capture(config: AppConfig):
    """Compatibility facade around the extracted capture constructor."""
    capture = _create_configured_capture(config)
    try:
        return capture, capture.screen_size()
    except Exception:
        try:
            capture.close()
        except Exception:
            pass
        raise


def _report_log_error(
    exc: Exception,
    *,
    event: dict[str, object],
    emit: Callable[..., None] | None = None,
) -> None:
    print(f"[LOG] Failed to write {event.get('event_type', 'event')}: {exc}")
    if emit is not None:
        emit("log_error", message=str(exc), failed_event_type=event.get("event_type", ""))


def run_watch(
    *,
    debug: bool = False,
    config: AppConfig | None = None,
    stop_event=None,
    popup_parent=None,
    popup_callback: Callable[[Detection], None] | None = None,
    status_callback: Callable[[dict[str, object]], None] | None = None,
    capture_factory: Callable[[AppConfig], object] | None = None,
    local_sound_allowed: Callable[[], bool] | None = None,
) -> None:
    def emit(event_type: str, **data: object) -> None:
        if status_callback is None:
            return
        try:
            status_callback({"type": event_type, **data})
        except Exception:
            pass

    def log_event(event: dict[str, object]) -> bool:
        return append_event_safely(
            event,
            on_error=lambda exc: _report_log_error(
                exc,
                event=event,
                emit=emit,
            ),
        )

    def can_play_local_sound() -> bool:
        if local_sound_allowed is None:
            return True
        try:
            return bool(local_sound_allowed())
        except Exception:
            return True

    set_dpi_awareness()
    config = config or load_config()
    if config.droid_timers_enabled:
        from .timer_sync import start_timer_schedule_sync

        start_timer_schedule_sync(config.timer_schedule_url, stop_event=stop_event)

    def open_capture(target_config: AppConfig):
        if capture_factory is None:
            return _open_configured_capture(target_config)
        capture = capture_factory(target_config)
        try:
            size = capture.screen_size()
        except Exception:
            try:
                capture.close()
            except Exception:
                pass
            raise
        return capture, size

    while True:
        try:
            capture, (screen_w, screen_h) = open_capture(config)
            break
        except Exception as exc:
            if config.capture_source not in {"window", "device"}:
                raise
            print(f"[CAPTURE] Waiting for selected source: {exc}")
            emit("capture_error", message=str(exc))
            if _wait_or_stop(stop_event, 1.0):
                emit("watcher_stopped")
                return
    active_capture_config = config

    resolver = RegionResolver(
        screen_w,
        screen_h,
        monitor_key=getattr(_capture_area(capture), "key", None),
    )
    box, region_source = resolver.resolve()
    pipeline = Pipeline(templates_dir(), config.thresholds, extra_checks=config.extra_checks)
    policy = AlertPolicy(config)
    webhook_url = None
    phone_credentials = None

    print(
        f"Droid Alerts watching {_capture_label(active_capture_config)} "
        f"({screen_w}x{screen_h})"
    )
    print(f"Region [{region_source}]: left={box.left} top={box.top} w={box.width} h={box.height}")
    print(f"Targets: {sorted(config.targets)}")
    print(f"Extra checks (washed-out colors/HDR): {'ENABLED' if config.extra_checks else 'DISABLED'}")
    print(f"Popup alerts: {'ENABLED' if config.popup_enabled else 'DISABLED'}")
    # The GUI manages the Droid Timers overlay itself; the CLI watcher spawns
    # a standalone one so `python main.py watch` gets it too.
    if config.droid_timers_enabled and popup_parent is None:
        from .timers import start_droid_timers_thread

        start_droid_timers_thread(
            stop_event,
            scale=config.droid_timers_scale,
            center_x_ratio=config.droid_timers_center_x,
            top_y_ratio=config.droid_timers_top_y,
            monitor=getattr(capture, "monitor", None),
            reminders_enabled=config.timer_reminders_enabled,
            reminder_seconds=config.timer_reminder_seconds,
            offset_seconds=config.timer_offset_seconds,
        )
        print("Droid Timers overlay: ENABLED")
    elif config.droid_timers_enabled:
        print("Droid Timers overlay: ENABLED (GUI-managed)")
    else:
        print("Droid Timers overlay: DISABLED")
    if config.discord_enabled:
        try:
            webhook_url, webhook_source = load_discord_webhook(config)
            if webhook_url:
                print(f"Discord webhook alerts: ENABLED from {webhook_source}")
            else:
                print("Discord webhook alerts: DISABLED (missing webhook)")
        except Exception as exc:
            print(f"Discord webhook alerts: DISABLED ({exc})")
    else:
        print("Discord webhook alerts: DISABLED")
    if config.ntfy_enabled:
        if ntfy_configured(config):
            print(f"ntfy alerts: ENABLED via {config.ntfy_server_url.rstrip('/')}/{config.ntfy_topic}")
        else:
            print("ntfy alerts: DISABLED (missing server/topic)")
    else:
        print("ntfy alerts: DISABLED")
    if config.phone_alerts_enabled:
        try:
            phone_credentials, phone_source = load_phone_alert_credentials(config)
            if phone_credentials:
                print(f"Phone alerts: ENABLED from {phone_source}")
            else:
                print("Phone alerts: DISABLED (missing Pushover credentials)")
        except Exception as exc:
            print(f"Phone alerts: DISABLED ({exc})")
    else:
        print("Phone alerts: DISABLED")
    debug_hotkey = _debug_hotkey() if debug and sys.platform != "darwin" else None
    debug_interval_seconds = 5.0 if debug and sys.platform == "darwin" else None
    next_debug_snapshot_at = time.monotonic() + debug_interval_seconds if debug_interval_seconds else None
    if debug:
        if debug_interval_seconds is not None:
            print("Debug mode on macOS: saving chat-box snapshots every 5 seconds.")
        elif debug_hotkey is not None:
            print("Debug mode: manual snapshots only; no automatic screenshots are saved.")
            print("Debug hotkey: press numpad + to save the current chat box + candidate check.")
        else:
            print("Debug hotkey unavailable on this platform; no keyboard snapshots will be saved.")

    frame_index = 0
    misfire_count = 0
    calibration_hint_shown = False
    logged_spawn_keys: dict[str, float] = {}
    log_dedupe_seconds = max(12.0, config.dedupe_seconds)
    telemetry = AnonymousTelemetryClient(config)
    telemetry.start()
    emit(
        "watcher_ready",
        screen_width=screen_w,
        screen_height=screen_h,
        region_source=region_source,
        rebirth_alert_enabled=config.rebirth_alert_enabled,
        **_capture_status(active_capture_config),
    )
    last_scan_status_at = 0.0
    rebirth_available_detector: RebirthAlertDetector | None = None
    rebirth_available_detector_failed = False
    rebirth_available_gate = RebirthPresenceGate()
    rebirth_ready_detector: RebirthHudDetector | None = None
    rebirth_ready_tracker = RebirthAlertTracker()
    credit_hud_detector: CreditHudDetector | None = None
    scrap_income_tracker = ScrapIncomeTracker(stall_seconds=30.0)
    scrap_visibility_tracker = ScrapVisibilityTracker(inactive_seconds=300.0)
    cb23_mission_detector: CB23MissionDetector | None = None
    cb23_mission_detector_failed = False
    cb23_mission_gate = CB23MissionGate()
    auxiliary_schedule = AuxiliaryAlertSchedule()

    def deliver(delivery: AlertDelivery, event: dict[str, object]) -> None:
        execution = execute_alert_delivery(
            delivery,
            wait_before_retry=lambda seconds: _wait_or_stop(stop_event, seconds),
        )
        delivery_event = build_delivery_event(
            execution,
            event,
            extra_fields={"rebirth_level": event.get("rebirth_level")},
        )
        persist_delivery_event(
            delivery_event,
            on_error=lambda exc: _report_log_error(exc, event=delivery_event, emit=emit),
        )
        emit("delivery", result=delivery_event)

    def dispatch_alert_channels(
        detection: Detection,
        event: dict[str, object],
        *,
        attachment_path: Path | None,
    ) -> None:
        if config.popup_enabled:
            if popup_callback is not None:
                popup_callback(detection)
            else:
                show_popup(
                    detection,
                    config.popup_seconds,
                    icon_path=popup_icon_path(config, detection),
                    parent=popup_parent,
                    monitor=getattr(capture, "monitor", None),
                    position=config.popup_position,
                    scale=config.popup_scale,
                    opacity=config.popup_opacity,
                )

        deliveries = enabled_alert_deliveries(
            config,
            detection,
            webhook_url=webhook_url,
            phone_credentials=phone_credentials,
            ntfy_ready=config.ntfy_enabled and ntfy_configured(config),
            attachment_path=attachment_path,
            discord_sender=send_discord_alert,
            ntfy_sender=send_ntfy_alert,
            phone_sender=send_phone_alert,
        )
        for delivery in deliveries:
            threading.Thread(
                target=deliver,
                args=(delivery, event),
                daemon=True,
            ).start()

    def fire_rebirth_available_alert(
        match: RebirthMatch,
        rebirth_band,
        rebirth_box,
    ) -> None:
        local_box = match.box or (0, 0, rebirth_band.shape[1], rebirth_band.shape[0])
        x1, y1, x2, y2 = local_box
        detection = Detection(
            droid="Rebirth",
            rarity="Available",
            row_box=(
                rebirth_box.left + x1,
                rebirth_box.top + y1,
                rebirth_box.left + x2,
                rebirth_box.top + y2,
            ),
            droid_score=match.score,
            rarity_score=match.score,
            rarity_margin=match.score,
            score=match.score,
            source="rebirth-alert",
        )
        event = {
            "ts": timestamp(),
            "event_type": "alert",
            "frame": frame_index,
            "screen_width": screen_w,
            "screen_height": screen_h,
            "monitor_index": active_capture_config.monitor_index,
            "capture_source": active_capture_config.capture_source,
            "capture_region": {
                "source": "rebirth-right",
                "left": rebirth_box.left,
                "top": rebirth_box.top,
                "width": rebirth_box.width,
                "height": rebirth_box.height,
            },
            "template_scale": round(match.template_scale, 4),
            "alerted": True,
            **detection.to_dict(),
        }
        # Rebirth alerts are opt-in priority events even though they are not a
        # chat droid/rarity pair from classifier.PRIORITY_ALERTS.
        event["is_priority"] = True
        event["should_alert"] = True

        sample_path = None
        needs_attachment = (
            (config.ntfy_enabled and config.ntfy_include_attachment)
            or (phone_credentials is not None and config.phone_include_attachment)
        )
        if config.save_alert_samples or needs_attachment:
            try:
                sample_path = _save_rebirth_available_sample(rebirth_band, local_box)
            except Exception as exc:
                print(f"[SAMPLE] Failed to save Rebirth Alert screenshot: {exc}")
            else:
                event["sample_path"] = str(sample_path)

        log_event(event)
        emit("alert", event=event)
        print(f"[ALERT] {event['ts']} Rebirth droid available score={match.score:.2f}")
        if can_play_local_sound():
            try:
                policy.notify(detection)
            except Exception as exc:
                print(f"[SOUND] Failed to play Rebirth Alert: {exc}")
                emit("sound_error", message=str(exc))
        dispatch_alert_channels(detection, event, attachment_path=sample_path)

    def fire_rebirth_ready_alert(level: int, ready_score: float) -> None:
        detection = Detection(
            droid="Rebirth",
            rarity="Ready",
            row_box=(0, 0, 0, 0),
            droid_score=1.0,
            rarity_score=ready_score,
            rarity_margin=1.0,
            score=ready_score,
            source="rebirth-ready",
        )
        event: dict[str, object] = {
            "ts": timestamp(),
            "event_type": "alert",
            "source": "rebirth_ready",
            "droid": "Rebirth",
            "rarity": "Ready",
            "rebirth_level": level,
            "detail": f"Rebirth {level} is ready",
            "alerted": True,
            "is_priority": True,
            "score": round(ready_score, 4),
            "screen_width": screen_w,
            "screen_height": screen_h,
            "monitor_index": active_capture_config.monitor_index,
            "capture_source": active_capture_config.capture_source,
        }
        log_event(event)
        emit("alert", event=event)
        print(f"[ALERT] {event['ts']} Rebirth Ready level={level} score={ready_score:.2f}")
        if can_play_local_sound():
            try:
                policy.notify(detection)
            except Exception as exc:
                print(f"[SOUND] Failed to play Rebirth alert: {exc}")
                emit("sound_error", message=str(exc))
        dispatch_alert_channels(detection, event, attachment_path=None)

    def fire_scrap_alert(icon_score: float) -> None:
        detection = Detection(
            droid="Scrap",
            rarity="Stalled",
            row_box=(0, 0, 0, 0),
            droid_score=icon_score,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=icon_score,
            source="scrap-alert",
        )
        event: dict[str, object] = {
            "ts": timestamp(),
            "event_type": "alert",
            "source": "scrap_alert",
            "droid": "Scrap",
            "rarity": "Stalled",
            "detail": "Your income is no longer increasing",
            "alerted": True,
            "is_priority": True,
            "score": round(icon_score, 4),
            "screen_width": screen_w,
            "screen_height": screen_h,
            "monitor_index": active_capture_config.monitor_index,
            "capture_source": active_capture_config.capture_source,
        }
        log_event(event)
        emit("alert", event=event)
        print(f"[ALERT] {event['ts']} Scrap income stopped changing")
        if can_play_local_sound():
            try:
                policy.notify(detection)
            except Exception as exc:
                print(f"[SOUND] Failed to play Scrap Alert: {exc}")
                emit("sound_error", message=str(exc))
        dispatch_alert_channels(detection, event, attachment_path=None)

    def fire_scrap_inactive_alert(icon_score: float) -> None:
        detection = Detection(
            droid="Scrap",
            rarity="Inactive",
            row_box=(0, 0, 0, 0),
            droid_score=icon_score,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=icon_score,
            source="scrap-inactive",
        )
        detail = "Scrap inactive for 5+ min. Possibly kicked from the lobby."
        event: dict[str, object] = {
            "ts": timestamp(),
            "event_type": "alert",
            "source": "scrap_inactive",
            "droid": "Scrap",
            "rarity": "Inactive",
            "detail": detail,
            "alerted": True,
            "is_priority": True,
            "score": round(icon_score, 4),
            "screen_width": screen_w,
            "screen_height": screen_h,
            "monitor_index": active_capture_config.monitor_index,
            "capture_source": active_capture_config.capture_source,
        }
        log_event(event)
        emit("alert", event=event)
        print(f"[ALERT] {event['ts']} {detail}")
        if can_play_local_sound():
            try:
                policy.notify(detection)
            except Exception as exc:
                print(f"[SOUND] Failed to play Scrap inactive alert: {exc}")
                emit("sound_error", message=str(exc))
        dispatch_alert_channels(detection, event, attachment_path=None)

    def fire_cb23_mission_alert(
        match: CB23MissionMatch,
        mission_band,
        mission_box,
    ) -> None:
        local_box = match.box or (0, 0, mission_band.shape[1], mission_band.shape[0])
        x1, y1, x2, y2 = local_box
        detection = Detection(
            droid="CB23",
            rarity="Mission",
            row_box=(
                mission_box.left + x1,
                mission_box.top + y1,
                mission_box.left + x2,
                mission_box.top + y2,
            ),
            droid_score=match.score,
            rarity_score=match.mission_score,
            rarity_margin=match.mission_score,
            score=match.score,
            source="cb23-mission",
        )
        event = {
            "ts": timestamp(),
            "event_type": "alert",
            "frame": frame_index,
            "screen_width": screen_w,
            "screen_height": screen_h,
            "monitor_index": active_capture_config.monitor_index,
            "capture_source": active_capture_config.capture_source,
            "capture_region": {
                "source": "cb23-mission-left",
                "left": mission_box.left,
                "top": mission_box.top,
                "width": mission_box.width,
                "height": mission_box.height,
            },
            "template_scale": round(match.template_scale, 4),
            "mission_score": round(match.mission_score, 4),
            "alerted": True,
            **detection.to_dict(),
        }
        event["is_priority"] = True
        event["should_alert"] = True

        sample_path = None
        needs_attachment = (
            (config.ntfy_enabled and config.ntfy_include_attachment)
            or (phone_credentials is not None and config.phone_include_attachment)
        )
        if config.save_alert_samples or needs_attachment:
            try:
                sample_path = _save_cb23_mission_sample(mission_band, local_box)
            except Exception as exc:
                print(f"[SAMPLE] Failed to save CB23 Mission screenshot: {exc}")
            else:
                event["sample_path"] = str(sample_path)

        log_event(event)
        emit("alert", event=event)
        print(f"[ALERT] {event['ts']} CB23 Mission score={match.score:.2f}")
        if can_play_local_sound():
            try:
                policy.notify(detection)
            except Exception as exc:
                print(f"[SOUND] Failed to play CB23 Mission alert: {exc}")
                emit("sound_error", message=str(exc))
        dispatch_alert_channels(detection, event, attachment_path=sample_path)

    # Live settings: the GUI autosaves config.json (and region nudges save
    # calibration.json); watching the file mtimes lets changes apply on the
    # next frame instead of requiring a watcher restart.
    def _settings_signature() -> tuple[int, int]:
        def mtime(path) -> int:
            try:
                return path.stat().st_mtime_ns
            except OSError:
                return 0

        return (
            mtime(config_dir() / CONFIG_FILE),
            mtime(config_dir() / CALIBRATION_FILE),
        )

    settings_signature = _settings_signature()
    pending_capture_config: AppConfig | None = None
    next_capture_retry_at = 0.0

    def switch_capture(target_config: AppConfig) -> None:
        nonlocal capture
        nonlocal active_capture_config
        nonlocal screen_w
        nonlocal screen_h
        nonlocal resolver
        nonlocal box
        nonlocal region_source

        replacement, replacement_size = open_capture(target_config)
        replacement_width, replacement_height = replacement_size
        try:
            replacement_resolver = RegionResolver(
                replacement_width,
                replacement_height,
                monitor_key=getattr(_capture_area(replacement), "key", None),
            )
            replacement_box, replacement_source = replacement_resolver.resolve()
        except Exception:
            try:
                replacement.close()
            except Exception:
                pass
            raise

        previous = capture
        capture = replacement
        active_capture_config = target_config
        screen_w, screen_h = replacement_width, replacement_height
        resolver = replacement_resolver
        box, region_source = replacement_box, replacement_source
        try:
            previous.close()
        except Exception:
            pass

    try:
        while not _stop_requested(stop_event):
            started = time.monotonic()
            frame_index += 1

            new_signature = _settings_signature()
            if new_signature != settings_signature:
                try:
                    new_config = load_config()
                except Exception as exc:
                    # Mid-write race with the GUI's save; retry next frame.
                    print(f"[CONFIG] reload failed, will retry: {exc}")
                else:
                    settings_signature = new_signature
                    capture_changed = (
                        _capture_target_signature(new_config)
                        != _capture_target_signature(active_capture_config)
                    )
                    config = new_config
                    policy.apply_config(config)
                    telemetry.apply_config(config)
                    pipeline.apply_thresholds(config.thresholds)
                    pipeline.detector.extra_checks = config.extra_checks
                    log_dedupe_seconds = max(12.0, config.dedupe_seconds)
                    if not config.rebirth_alert_enabled:
                        rebirth_available_gate.reset()
                    if not config.cb23_mission_alert_enabled:
                        cb23_mission_gate.reset()
                    if not config.scrap_alert_enabled:
                        scrap_income_tracker.reset()
                        scrap_visibility_tracker.reset()
                    webhook_url = None
                    if config.discord_enabled:
                        try:
                            webhook_url, _source = load_discord_webhook(config)
                        except Exception:
                            webhook_url = None
                    phone_credentials = None
                    if config.phone_alerts_enabled:
                        try:
                            phone_credentials, _source = load_phone_alert_credentials(config)
                        except Exception:
                            phone_credentials = None
                    if capture_changed:
                        try:
                            switch_capture(config)
                        except Exception as exc:
                            print(f"[CAPTURE] Could not switch capture target: {exc}")
                            emit("capture_error", message=str(exc))
                            pending_capture_config = config
                            next_capture_retry_at = time.monotonic() + 3.0
                        else:
                            pending_capture_config = None
                    else:
                        pending_capture_config = None
                    resolver = RegionResolver(
                        screen_w,
                        screen_h,
                                monitor_key=getattr(_capture_area(capture), "key", None),
                    )
                    box, region_source = resolver.resolve()
                    print(
                        f"[CONFIG] Settings reloaded: region [{region_source}] "
                        f"targets {sorted(config.targets)}"
                    )
                    emit(
                        "config_reloaded",
                        screen_width=screen_w,
                        screen_height=screen_h,
                        region_source=region_source,
                        rebirth_alert_enabled=config.rebirth_alert_enabled,
                        **_capture_status(active_capture_config),
                    )

            if (
                pending_capture_config is not None
                and time.monotonic() >= next_capture_retry_at
            ):
                try:
                    switch_capture(pending_capture_config)
                except Exception as exc:
                    print(f"[CAPTURE] Capture target is still unavailable: {exc}")
                    emit("capture_error", message=str(exc))
                    next_capture_retry_at = time.monotonic() + 3.0
                else:
                    pending_capture_config = None
                    emit(
                        "config_reloaded",
                        screen_width=screen_w,
                        screen_height=screen_h,
                        region_source=region_source,
                        rebirth_alert_enabled=config.rebirth_alert_enabled,
                        **_capture_status(active_capture_config),
                    )

            try:
                current_size = capture.screen_size()
                if current_size != (screen_w, screen_h):
                    screen_w, screen_h = current_size
                    resolver = RegionResolver(
                        screen_w,
                        screen_h,
                                monitor_key=getattr(_capture_area(capture), "key", None),
                    )
                    box, region_source = resolver.resolve()
                    emit(
                        "config_reloaded",
                        screen_width=screen_w,
                        screen_height=screen_h,
                        region_source=region_source,
                        rebirth_alert_enabled=config.rebirth_alert_enabled,
                        **_capture_status(active_capture_config),
                    )
                band = capture.grab(box)
            except Exception as exc:
                print(f"capture error: {exc}")
                emit("capture_error", message=str(exc))
                recovery_delay = (
                    1.0
                    if active_capture_config.capture_source in {"window", "device"}
                    else config.capture_interval_seconds
                )
                if _wait_or_stop(stop_event, recovery_delay):
                    break
                if active_capture_config.capture_source == "window":
                    try:
                        switch_capture(active_capture_config)
                    except Exception as recovery_exc:
                        print(f"[CAPTURE] Source reconnect failed: {recovery_exc}")
                    else:
                        emit(
                            "config_reloaded",
                            screen_width=screen_w,
                            screen_height=screen_h,
                            region_source=region_source,
                            rebirth_alert_enabled=config.rebirth_alert_enabled,
                            **_capture_status(active_capture_config),
                        )
                continue

            result = pipeline.detect(band, screen_height=screen_h, screen_width=screen_w, keep_normalized=True)
            scan_now = time.monotonic()
            if config.rebirth_alert_enabled and auxiliary_schedule.rebirth_available_due(
                scan_now, REBIRTH_SCAN_INTERVAL_SECONDS
            ):
                if rebirth_available_detector is None and not rebirth_available_detector_failed:
                    try:
                        rebirth_available_detector = RebirthAlertDetector()
                    except Exception as exc:
                        rebirth_available_detector_failed = True
                        print(f"[REBIRTH] Detector unavailable: {exc}")
                        emit("rebirth_error", message=str(exc))
                if rebirth_available_detector is not None:
                    rebirth_box = rebirth_region(box, screen_w, screen_h)
                    try:
                        rebirth_band = capture.grab(rebirth_box)
                        rebirth_match = rebirth_available_detector.detect(
                            rebirth_band,
                            screen_width=screen_w,
                            screen_height=screen_h,
                        )
                    except Exception as exc:
                        print(f"[REBIRTH] Scan failed: {exc}")
                        emit("rebirth_error", message=str(exc))
                    else:
                        confirmed = rebirth_available_gate.update(rebirth_match.matched)
                        emit(
                            "rebirth_scan",
                            matched=rebirth_match.matched,
                            score=round(rebirth_match.score, 4),
                            scanned_at=timestamp(),
                        )
                        cooldown = max(12.0, config.dedupe_seconds, config.alert_cooldown_seconds)
                        if confirmed and auxiliary_schedule.can_alert_rebirth_available(
                            scan_now, cooldown
                        ):
                            fire_rebirth_available_alert(rebirth_match, rebirth_band, rebirth_box)
            if (
                config.cb23_mission_alert_enabled
                and auxiliary_schedule.cb23_due(
                    scan_now, CB23_MISSION_SCAN_INTERVAL_SECONDS
                )
            ):
                if cb23_mission_detector is None and not cb23_mission_detector_failed:
                    try:
                        cb23_mission_detector = CB23MissionDetector()
                    except Exception as exc:
                        cb23_mission_detector_failed = True
                        print(f"[CB23 MISSION] Detector unavailable: {exc}")
                        emit("cb23_mission_error", message=str(exc))
                if cb23_mission_detector is not None:
                    mission_box = cb23_mission_region(screen_w, screen_h)
                    try:
                        mission_band = capture.grab(mission_box)
                        mission_match = cb23_mission_detector.detect(
                            mission_band,
                            screen_width=screen_w,
                            screen_height=screen_h,
                        )
                    except Exception as exc:
                        print(f"[CB23 MISSION] Scan failed: {exc}")
                        emit("cb23_mission_error", message=str(exc))
                    else:
                        confirmed = cb23_mission_gate.update(mission_match.matched)
                        emit(
                            "cb23_mission_scan",
                            matched=mission_match.matched,
                            score=round(mission_match.score, 4),
                            mission_score=round(mission_match.mission_score, 4),
                            scanned_at=timestamp(),
                        )
                        cooldown = max(
                            12.0,
                            config.dedupe_seconds,
                            config.alert_cooldown_seconds,
                        )
                        if confirmed and auxiliary_schedule.can_alert_cb23(
                            scan_now, cooldown
                        ):
                            fire_cb23_mission_alert(
                                mission_match,
                                mission_band,
                                mission_box,
                            )
            status_now = time.monotonic()
            if status_now - last_scan_status_at >= 1.0:
                last_scan_status_at = status_now
                emit(
                    "scan",
                    frame=frame_index,
                    scanned_at=timestamp(),
                    detections=len(result.detections),
                    scale=round(result.scale, 4),
                )

            hud_alert_enabled = (
                config.rebirth_ready_alert_enabled or config.scrap_alert_enabled
            )
            if hud_alert_enabled and auxiliary_schedule.rebirth_ready_due(
                status_now, config.rebirth_scan_interval_seconds
            ):
                try:
                    bottom_top = max(0, int(round(screen_h * 0.62)))
                    bottom = capture.grab(
                        PixelBox(0, bottom_top, screen_w, screen_h - bottom_top)
                    )
                    if config.rebirth_ready_alert_enabled:
                        if rebirth_ready_detector is None:
                            rebirth_ready_detector = RebirthHudDetector()
                        observation = rebirth_ready_detector.detect(
                            bottom,
                            screen_height=screen_h,
                            screen_width=screen_w,
                        )
                        alert_level = rebirth_ready_tracker.observe(observation)
                        emit(
                            "rebirth_scan",
                            ready=observation.ready,
                            ready_score=round(observation.ready_score, 4),
                            rebirth_level=observation.level,
                            level_score=round(observation.level_score, 4),
                        )
                        if alert_level is not None:
                            fire_rebirth_ready_alert(alert_level, observation.ready_score)
                    if config.scrap_alert_enabled:
                        if credit_hud_detector is None:
                            credit_hud_detector = CreditHudDetector()
                        credit_observation = credit_hud_detector.detect(
                            bottom,
                            screen_height=screen_h,
                            screen_width=screen_w,
                        )
                        previous_fingerprint = scrap_income_tracker.fingerprint
                        should_alert_scrap = scrap_income_tracker.observe(
                            credit_observation,
                            now=status_now,
                        )
                        should_alert_scrap_inactive = scrap_visibility_tracker.observe(
                            icon_visible=credit_observation.icon_visible,
                            now=status_now,
                        )
                        display_changed = (
                            credit_observation.visible
                            and credit_observation.fingerprint != previous_fingerprint
                        )
                        unchanged_seconds = scrap_income_tracker.unchanged_seconds(status_now)
                        emit(
                            "scrap_scan",
                            visible=credit_observation.visible,
                            changed=display_changed,
                            unchanged_seconds=round(unchanged_seconds, 1),
                            icon_missing_seconds=round(
                                scrap_visibility_tracker.missing_seconds(status_now),
                                1,
                            ),
                            icon_score=round(credit_observation.icon_score, 4),
                        )
                        if debug:
                            debug_detail = scrap_debug_detail(
                                credit_observation,
                                changed=display_changed,
                                unchanged_seconds=unchanged_seconds,
                            )
                            log_event(
                                {
                                    "ts": timestamp(),
                                    "event_type": "scrap_scan",
                                    "source": "scrap_alert",
                                    "droid": "Scrap",
                                    "rarity": "Debug",
                                    "debug": True,
                                    "visible": credit_observation.visible,
                                    "changed": display_changed,
                                    "unchanged_seconds": round(unchanged_seconds, 1),
                                    "icon_missing_seconds": round(
                                        scrap_visibility_tracker.missing_seconds(status_now),
                                        1,
                                    ),
                                    "icon_score": round(credit_observation.icon_score, 4),
                                    "detail": debug_detail,
                                }
                            )
                            if not credit_observation.visible:
                                print(
                                    f"[SCRAP] {debug_detail} "
                                    f"(icon score={credit_observation.icon_score:.2f})"
                                )
                            elif credit_observation.fingerprint != previous_fingerprint:
                                print(
                                    f"[SCRAP] {debug_detail} "
                                    f"(icon score={credit_observation.icon_score:.2f})"
                                )
                            else:
                                print(
                                    f"[SCRAP] {debug_detail} "
                                    f"(icon score={credit_observation.icon_score:.2f})"
                                )
                        if should_alert_scrap:
                            fire_scrap_alert(credit_observation.icon_score)
                        if should_alert_scrap_inactive:
                            fire_scrap_inactive_alert(credit_observation.icon_score)
                except Exception as exc:
                    # This optional low-frequency detector must never stop the
                    # normal priority-spawn watcher.
                    print(f"[HUD] Scan failed: {exc}")
                    emit("hud_error", message=str(exc))

            # Region-health tracking: alert-free stretches are normal (spawns
            # are random), so only frames with phrase-like rows that still
            # classify to nothing count as misses, and the hint prints once.
            if result.detections:
                misfire_count = 0
                try:
                    resolver.mark_validated()
                except OSError as exc:
                    print(f"[REGION] Could not save validated monitor size: {exc}")
            elif result.phrase_row_boxes:
                misfire_count += 1
                if misfire_count >= config.validation_failures_before_calibration_prompt and not calibration_hint_shown:
                    calibration_hint_shown = True
                    print(
                        f"\n[!] {misfire_count} frames had alert-like rows that never classified. "
                        "If real alerts are being missed, run: python main.py calibrate\n"
                        "    (Continuing to watch; this message won't repeat this session.)"
                    )

            for detection in result.detections:
                _x1, y1, _x2, y2 = detection.row_box
                norm = result.normalized_image
                row = norm[y1:y2, :] if norm is not None else band
                digest = row_hash(row)
                fire = policy.should_alert(detection, digest)
                now = time.monotonic()
                spawn_key = _spawn_key(detection)
                recently_logged = _recently_logged(
                    logged_spawn_keys,
                    spawn_key,
                    now,
                    log_dedupe_seconds,
                )
                event = {
                    "ts": timestamp(),
                    "event_type": "alert" if fire else ("seen" if debug else "detected"),
                    "debug": debug,
                    "frame": frame_index,
                    "screen_width": screen_w,
                    "screen_height": screen_h,
                    "monitor_index": active_capture_config.monitor_index,
                    "capture_source": active_capture_config.capture_source,
                    "capture_region": {
                        "source": region_source,
                        "left": box.left,
                        "top": box.top,
                        "width": box.width,
                        "height": box.height,
                    },
                    "scale": round(result.scale, 4),
                    "scale_method": result.scale_method,
                    "row_hash": digest,
                    "alerted": fire,
                    **detection.to_dict(),
                }
                label = f"{detection.droid} {detection.rarity}"
                sample_paths = None
                needs_attachment = (
                    (config.ntfy_enabled and config.ntfy_include_attachment)
                    or (phone_credentials is not None and config.phone_include_attachment)
                )
                if fire and (config.save_alert_samples or needs_attachment) and norm is not None:
                    try:
                        sample_paths = _save_sample(norm, detection, label)
                    except Exception as exc:
                        print(f"[SAMPLE] Failed to save alert screenshot: {exc}")
                    else:
                        event["sample_path"] = str(sample_paths[1])
                should_log_event = fire or debug or not recently_logged
                if should_log_event:
                    log_event(event)
                    emit("alert" if fire else "detection", event=event)
                if fire:
                    print(f"[ALERT] {event['ts']} {label} score={detection.score:.2f}")
                    logged_spawn_keys[spawn_key] = now
                    # The persistent wake alarm is started by the GUI from the
                    # emitted alert event. Once active, do not let a normal
                    # async sound replace its loop in the OS audio player.
                    if can_play_local_sound():
                        try:
                            policy.notify(detection)
                        except Exception as exc:
                            print(f"[SOUND] Failed to play alert: {exc}")
                            emit("sound_error", message=str(exc))
                    telemetry.submit_alert_detection(detection=detection, detected_at=event["ts"])
                    if debug and config.share_debug_detections:
                        try:
                            debug_paths = _save_debug(band, result, reason="shared_alert")
                        except Exception as exc:
                            print(f"[DEBUG] Failed to save shared alert snapshot: {exc}")
                            emit("debug_save_error", message=str(exc))
                        else:
                            log_event(
                                _debug_snapshot_event(
                                    frame_index, result, debug_paths, reason="shared_alert"
                                )
                            )
                            telemetry.submit_debug_detection(
                                detection=detection,
                                event=event,
                                screenshot_paths=debug_paths,
                            )
                    attachment_path = sample_paths[1] if sample_paths is not None else None
                    dispatch_alert_channels(
                        detection,
                        event,
                        attachment_path=attachment_path,
                    )
                elif debug:
                    print(
                        f"[SEEN] {event['ts']} {label} "
                        f"score={detection.score:.2f} rarity={detection.rarity_score:.2f} "
                        f"margin={detection.rarity_margin:.2f} priority={detection.is_priority}"
                    )
                elif not recently_logged:
                    logged_spawn_keys[spawn_key] = now
                    print(
                        f"[DETECTED] {event['ts']} {label} "
                        f"score={detection.score:.2f} priority={detection.is_priority}"
                    )

            if debug:
                now = time.monotonic()
                for rejection in result.rejections:
                    rej_key = f"rej|{rejection['droid']}|{rejection['reason']}|{rejection['y'] // 32}"
                    if _recently_logged(logged_spawn_keys, rej_key, now, log_dedupe_seconds):
                        continue
                    logged_spawn_keys[rej_key] = now
                    rejected_event = {
                        "ts": timestamp(),
                        "event_type": "rejected",
                        "debug": True,
                        "frame": frame_index,
                        "scale": round(result.scale, 4),
                        "scale_method": result.scale_method,
                        "y": rejection["y"],
                        "droid": rejection["droid"],
                        "rarity": "",
                        "reason": rejection["reason"],
                        "detail": rejection.get("detail", ""),
                        "alerted": False,
                        "is_priority": False,
                        "score": None,
                    }
                    log_event(rejected_event)
                    detail = f" {rejection['detail']}" if rejection.get("detail") else ""
                    print(
                        f"[REJECTED] {rejected_event['ts']} y={rejection['y']} "
                        f"droid={rejection['droid']} reason={rejection['reason']}{detail}"
                    )

            if debug and debug_hotkey is not None and debug_hotkey():
                try:
                    saved = _save_debug(band, result, reason="manual")
                except Exception as exc:
                    print(f"[DEBUG] Failed to save manual snapshot: {exc}")
                    emit("debug_save_error", message=str(exc))
                else:
                    log_event(
                        _debug_snapshot_event(frame_index, result, saved, reason="manual"),
                    )
                    print("[debug] saved manual chat-box snapshot:")
                    for path in saved:
                        print(f"        {path}")
            if debug and next_debug_snapshot_at is not None and time.monotonic() >= next_debug_snapshot_at:
                try:
                    saved = _save_debug(band, result, reason="macos_interval")
                except Exception as exc:
                    print(f"[DEBUG] Failed to save timed macOS snapshot: {exc}")
                    emit("debug_save_error", message=str(exc))
                else:
                    log_event(
                        _debug_snapshot_event(
                            frame_index, result, saved, reason="macos_interval"
                        )
                    )
                    print("[debug] saved timed macOS chat-box snapshot:")
                    for path in saved:
                        print(f"        {path}")
                next_debug_snapshot_at = time.monotonic() + debug_interval_seconds

            elapsed = time.monotonic() - started
            if _wait_or_stop(stop_event, max(0.05, config.capture_interval_seconds - elapsed)):
                break
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        telemetry.stop()
        capture.close()
        emit("watcher_stopped")


def _save_sample(normalized_band, detection, label: str) -> tuple[Path, Path]:
    from .classifier import draw_detections

    stamp = timestamp()
    folder = alert_samples_dir() / label.replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    raw_path = folder / f"{stamp}_raw.png"
    det_path = folder / f"{stamp}_det.png"
    write_cv_image(raw_path, normalized_band)
    write_cv_image(det_path, draw_detections(normalized_band, [detection]))
    return raw_path, det_path


def _save_rebirth_available_sample(
    rebirth_band,
    match_box: tuple[int, int, int, int],
) -> Path:
    stamp = timestamp()
    folder = alert_samples_dir() / "Rebirth_Alert"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp}_det.png"
    marked = rebirth_band.copy()
    x1, y1, x2, y2 = match_box
    cv2.rectangle(marked, (x1, y1), (max(x1, x2 - 1), max(y1, y2 - 1)), (0, 255, 255), 2)
    write_cv_image(path, marked)
    return path


def _save_cb23_mission_sample(
    mission_band,
    match_box: tuple[int, int, int, int],
) -> Path:
    stamp = timestamp()
    folder = alert_samples_dir() / "CB23_Mission"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp}_det.png"
    marked = mission_band.copy()
    x1, y1, x2, y2 = match_box
    cv2.rectangle(marked, (x1, y1), (max(x1, x2 - 1), max(y1, y2 - 1)), (0, 255, 255), 2)
    write_cv_image(path, marked)
    return path


def _stop_requested(stop_event) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def _wait_or_stop(stop_event, seconds: float) -> bool:
    if stop_event is None:
        time.sleep(seconds)
        return False
    return bool(stop_event.wait(seconds))


def _spawn_key(detection) -> str:
    _x1, y1, _x2, y2 = detection.row_box
    y_bucket = ((y1 + y2) // 2) // 32
    return f"{detection.droid}|{detection.rarity}|{y_bucket}"


def _recently_logged(
    logged_spawn_keys: dict[str, float],
    spawn_key: str,
    now: float,
    window_seconds: float,
) -> bool:
    cutoff = now - window_seconds
    stale = [key for key, seen_at in logged_spawn_keys.items() if seen_at < cutoff]
    for key in stale:
        del logged_spawn_keys[key]
    seen_at = logged_spawn_keys.get(spawn_key)
    return seen_at is not None and now - seen_at < window_seconds


def _save_debug(band, result, *, reason: str) -> list[str]:
    from .classifier import draw_detections

    stamp = timestamp()
    out = debug_dir()
    out.mkdir(parents=True, exist_ok=True)
    prefix = f"{reason}_roi_{stamp}"
    paths = [str(out / f"{prefix}.png")]
    write_cv_image(paths[0], band)
    if result.normalized_image is not None:
        overlay_path = str(out / f"{prefix}_candidate_check.png")
        overlay = _draw_debug_overlay(result.normalized_image, result)
        overlay = draw_detections(overlay, result.detections)
        write_cv_image(overlay_path, overlay)
        paths.append(overlay_path)
    return paths


def _debug_snapshot_event(frame_index: int, result, paths: list[str], *, reason: str) -> dict[str, object]:
    return {
        "ts": timestamp(),
        "event_type": "debug_snapshot",
        "debug": True,
        "frame": frame_index,
        "scale": round(result.scale, 4),
        "scale_method": result.scale_method,
        "droid": "",
        "rarity": "",
        "reason": reason,
        "detail": "; ".join(paths),
        "alerted": False,
        "is_priority": False,
        "score": None,
    }


def _draw_debug_overlay(image, result):
    output = image.copy()
    for x1, y1, x2, y2 in result.candidate_row_boxes:
        cv2.rectangle(output, (x1, y1), (x2 - 1, y2 - 1), (110, 110, 110), 1)
        cv2.putText(output, "candidate", (max(0, x1 + 6), max(14, y1 + 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (110, 110, 110), 1)
    for x1, y1, x2, y2 in result.phrase_row_boxes:
        cv2.rectangle(output, (x1, y1), (x2 - 1, y2 - 1), (0, 210, 255), 1)
        cv2.putText(output, "phrase", (max(0, x1 + 6), max(28, y1 + 28)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 210, 255), 1)
    return output


def _debug_hotkey() -> Callable[[], bool] | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
    except Exception:
        return None

    vk_numpad_plus = 0x6B

    def pressed() -> bool:
        try:
            return bool(user32.GetAsyncKeyState(vk_numpad_plus) & 0x0001)
        except Exception:
            return False

    pressed()
    return pressed
