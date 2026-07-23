from __future__ import annotations

from .capture import create_capture
from .config import AppConfig


def capture_target_signature(config: AppConfig) -> tuple[object, ...]:
    return (
        config.monitor_index, config.capture_source, config.capture_window_title,
        config.capture_window_process, config.capture_window_class,
        config.capture_device_name, config.capture_device_path,
        config.capture_device_vid, config.capture_device_pid, config.capture_device_backend,
    )


def create_configured_capture(config: AppConfig, *, factory=create_capture):
    return factory(
        monitor_index=config.monitor_index,
        capture_source=config.capture_source,
        window_title=config.capture_window_title,
        window_process=config.capture_window_process,
        window_class=config.capture_window_class,
        device_name=config.capture_device_name,
        device_path=config.capture_device_path,
        device_vid=config.capture_device_vid,
        device_pid=config.capture_device_pid,
        device_backend=config.capture_device_backend,
    )


def open_configured_capture(config: AppConfig):
    capture = create_configured_capture(config)
    try:
        size = capture.screen_size()
    except Exception:
        try:
            capture.close()
        except Exception:
            pass
        raise
    return capture, size


def capture_area(capture):
    return getattr(capture, "capture_area", getattr(capture, "monitor", None))


def capture_label(config: AppConfig) -> str:
    if config.capture_source == "window":
        return config.capture_window_title or config.capture_window_process or "Selected window"
    if config.capture_source == "device":
        return config.capture_device_name or "Capture device"
    return f"Monitor {config.monitor_index}"


def capture_status(config: AppConfig) -> dict[str, object]:
    return {
        "monitor_index": config.monitor_index,
        "capture_source": config.capture_source,
        "capture_label": capture_label(config),
    }
