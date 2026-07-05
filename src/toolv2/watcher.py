from __future__ import annotations

import sys
import time
from collections.abc import Callable

import cv2

from .alerts import AlertPolicy, row_hash
from .capture import create_capture, set_dpi_awareness
from .config import AppConfig, load_config, templates_dir
from .logging_io import alert_samples_dir, append_event, debug_dir, timestamp
from .pipeline import Pipeline
from .region import NeedsCalibration, RegionResolver, validate_region


def run_watch(*, debug: bool = False, config: AppConfig | None = None) -> None:
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
    pipeline = Pipeline(templates_dir(), config.thresholds)
    policy = AlertPolicy(config)

    print(f"ToolV2 watching monitor {config.monitor_index} ({screen_w}x{screen_h})")
    print(f"Region [{region_source}]: left={box.left} top={box.top} w={box.width} h={box.height}")
    print(f"Targets: {sorted(config.targets)}")
    debug_hotkey = _debug_hotkey() if debug else None
    if debug:
        print("Debug mode: manual snapshots only; no automatic screenshots are saved.")
        if debug_hotkey is not None:
            print("Debug hotkey: press numpad + to save the current chat box + candidate check.")
        else:
            print("Debug hotkey unavailable on this platform; no keyboard snapshots will be saved.")

    frame_index = 0
    try:
        while True:
            started = time.monotonic()
            frame_index += 1
            try:
                band = capture.grab(box)
            except Exception as exc:
                print(f"capture error: {exc}")
                time.sleep(config.capture_interval_seconds)
                continue

            result = pipeline.detect(band, screen_height=screen_h, keep_normalized=True)

            if result.candidate_rows > 0:
                try:
                    validation = validate_region(band, pipeline.detector.templates, screen_height=screen_h)
                    resolver.record_validation(validation.ok)
                except NeedsCalibration as exc:
                    print(f"\n[!] {exc}\n    Continuing with current region; recalibrate when possible.")
                    resolver.consecutive_failures = 0

            for detection in result.detections:
                _x1, y1, _x2, y2 = detection.row_box
                norm = result.normalized_image
                row = norm[y1:y2, :] if norm is not None else band
                digest = row_hash(row)
                fire = policy.should_alert(detection, digest)
                event = {
                    "ts": timestamp(),
                    "frame": frame_index,
                    "scale": round(result.scale, 4),
                    "scale_method": result.scale_method,
                    "row_hash": digest,
                    "alerted": fire,
                    **detection.to_dict(),
                }
                append_event(event)
                label = f"{detection.droid} {detection.rarity}"
                if fire:
                    print(f"[ALERT] {event['ts']} {label} score={detection.score:.2f}")
                    policy.notify(detection)
                    if config.save_alert_samples and norm is not None:
                        _save_sample(norm, detection, label)
                else:
                    print(
                        f"[SEEN] {event['ts']} {label} "
                        f"score={detection.score:.2f} rarity={detection.rarity_score:.2f} "
                        f"margin={detection.rarity_margin:.2f} priority={detection.is_priority}"
                    )

            if debug and debug_hotkey is not None and debug_hotkey():
                saved = _save_debug(band, result, reason="manual")
                print("[debug] saved manual chat-box snapshot:")
                for path in saved:
                    print(f"        {path}")

            elapsed = time.monotonic() - started
            time.sleep(max(0.05, config.capture_interval_seconds - elapsed))
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        capture.close()


def _save_sample(normalized_band, detection, label: str) -> None:
    from .classifier import draw_detections

    stamp = timestamp()
    folder = alert_samples_dir() / label.replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(folder / f"{stamp}_raw.png"), normalized_band)
    cv2.imwrite(str(folder / f"{stamp}_det.png"), draw_detections(normalized_band, [detection]))


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
