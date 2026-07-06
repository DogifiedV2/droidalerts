from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import cv2

from .alerts import AlertPolicy, row_hash
from .capture import create_capture, set_dpi_awareness
from .config import AppConfig, load_config, templates_dir
from .logging_io import alert_samples_dir, append_event, debug_dir, timestamp
from .notifications import (
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


def run_watch(
    *,
    debug: bool = False,
    config: AppConfig | None = None,
    stop_event=None,
    popup_parent=None,
) -> None:
    set_dpi_awareness()
    config = config or load_config()
    capture = create_capture(monitor_index=config.monitor_index)
    screen_w, screen_h = capture.screen_size()

    resolver = RegionResolver(
        screen_w,
        screen_h,
        max_failures=config.validation_failures_before_calibration_prompt,
    )
    box, region_source = resolver.resolve()
    pipeline = Pipeline(templates_dir(), config.thresholds, extra_checks=config.extra_checks)
    policy = AlertPolicy(config)
    webhook_url = None
    phone_credentials = None

    print(f"Droid Alerts watching monitor {config.monitor_index} ({screen_w}x{screen_h})")
    print(f"Region [{region_source}]: left={box.left} top={box.top} w={box.width} h={box.height}")
    print(f"Targets: {sorted(config.targets)}")
    print(f"Extra checks (washed-out colors/HDR): {'ENABLED' if config.extra_checks else 'DISABLED'}")
    print(f"Popup alerts: {'ENABLED' if config.popup_enabled else 'DISABLED'}")
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
    try:
        while not _stop_requested(stop_event):
            started = time.monotonic()
            frame_index += 1
            try:
                band = capture.grab(box)
            except Exception as exc:
                print(f"capture error: {exc}")
                if _wait_or_stop(stop_event, config.capture_interval_seconds):
                    break
                continue

            result = pipeline.detect(band, screen_height=screen_h, screen_width=screen_w, keep_normalized=True)

            # Region-health tracking: alert-free stretches are normal (spawns
            # are random), so only frames with phrase-like rows that still
            # classify to nothing count as misses, and the hint prints once.
            if result.detections:
                misfire_count = 0
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
                    "frame": frame_index,
                    "scale": round(result.scale, 4),
                    "scale_method": result.scale_method,
                    "row_hash": digest,
                    "alerted": fire,
                    **detection.to_dict(),
                }
                label = f"{detection.droid} {detection.rarity}"
                should_log_event = fire or debug or not recently_logged
                if should_log_event:
                    append_event(event)
                if fire:
                    print(f"[ALERT] {event['ts']} {label} score={detection.score:.2f}")
                    logged_spawn_keys[spawn_key] = now
                    policy.notify(detection)
                    sample_paths = None
                    if config.save_alert_samples and norm is not None:
                        sample_paths = _save_sample(norm, detection, label)
                    if config.popup_enabled:
                        show_popup(
                            detection,
                            config.popup_seconds,
                            icon_path=popup_icon_path(config),
                            parent=popup_parent,
                        )
                    if webhook_url:
                        threading.Thread(
                            target=send_discord_alert,
                            args=(webhook_url, detection),
                            daemon=True,
                        ).start()
                    if config.ntfy_enabled and ntfy_configured(config):
                        attachment_path = None
                        if config.ntfy_include_attachment and sample_paths is not None:
                            attachment_path = sample_paths[1]
                        threading.Thread(
                            target=send_ntfy_alert,
                            args=(config, detection),
                            kwargs={"attachment_path": attachment_path},
                            daemon=True,
                        ).start()
                    if phone_credentials:
                        attachment_path = None
                        if config.phone_include_attachment and sample_paths is not None:
                            attachment_path = sample_paths[1]
                        threading.Thread(
                            target=send_phone_alert,
                            args=(phone_credentials, detection),
                            kwargs={"sound": config.phone_sound, "attachment_path": attachment_path},
                            daemon=True,
                        ).start()
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
                    detail = f" {rejection['detail']}" if rejection.get("detail") else ""
                    print(
                        f"[REJECTED] {timestamp()} y={rejection['y']} "
                        f"droid={rejection['droid']} reason={rejection['reason']}{detail}"
                    )

            if debug and debug_hotkey is not None and debug_hotkey():
                saved = _save_debug(band, result, reason="manual")
                print("[debug] saved manual chat-box snapshot:")
                for path in saved:
                    print(f"        {path}")
            if debug and next_debug_snapshot_at is not None and time.monotonic() >= next_debug_snapshot_at:
                saved = _save_debug(band, result, reason="macos_interval")
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
        capture.close()


def _save_sample(normalized_band, detection, label: str) -> tuple[Path, Path]:
    from .classifier import draw_detections

    stamp = timestamp()
    folder = alert_samples_dir() / label.replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    raw_path = folder / f"{stamp}_raw.png"
    det_path = folder / f"{stamp}_det.png"
    cv2.imwrite(str(raw_path), normalized_band)
    cv2.imwrite(str(det_path), draw_detections(normalized_band, [detection]))
    return raw_path, det_path


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
    cv2.imwrite(paths[0], band)
    if result.normalized_image is not None:
        overlay_path = str(out / f"{prefix}_candidate_check.png")
        overlay = _draw_debug_overlay(result.normalized_image, result)
        overlay = draw_detections(overlay, result.detections)
        cv2.imwrite(overlay_path, overlay)
        paths.append(overlay_path)
    return paths


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
