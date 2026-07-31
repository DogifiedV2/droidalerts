from __future__ import annotations

import multiprocessing
import sys
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..capture import (
    MonitorInfo,
    create_capture,
    format_monitor_label,
    list_monitors,
)
from ..capture_runtime import create_configured_capture
from ..config import AppConfig
from ..device_capture import (
    device_capture_key,
    list_capture_devices,
    session_from_config,
)
from ..window_capture import (
    WINDOW_CAPTURE_EXPLANATION,
    list_capture_windows,
    resolve_capture_window,
    window_capture_available,
    window_capture_key,
    window_capture_unavailable_reason,
)
from .runtime import ApplicationRuntime
from .state import StateObject


class CaptureController(StateObject):
    """Manages capture source selection."""

    sourceChanged = Signal()
    displayGeometryChanged = Signal(bool)

    @staticmethod
    def _device_capture_available() -> bool:
        return (
            sys.platform in {"win32", "darwin"}
            or sys.platform.startswith("linux")
        )

    def __init__(
        self,
        runtime: ApplicationRuntime,
        *,
        parent: QObject | None = None,
    ) -> None:
        self.runtime = runtime
        self._monitors_by_key: dict[str, Any] = {}
        self._windows_by_key: dict[str, Any] = {}
        self._devices_by_key: dict[str, Any] = {}
        self._window_sizes: dict[str, tuple[int, int]] = {}
        self._display_geometry_signature: (
            tuple[tuple[int, int, int, int, int], ...] | None
        ) = None
        super().__init__(
            {
                "source": "monitor",
                "sourceLabel": "",
                "sourceDetail": "",
                "monitorKey": "",
                "monitors": [],
                "windowCaptureAvailable": window_capture_available(),
                "windowCaptureUnavailableReason": (
                    "" if window_capture_available()
                    else window_capture_unavailable_reason()
                ),
                "deviceCaptureAvailable": self._device_capture_available(),
                "deviceCaptureUnavailableReason": (
                    "" if self._device_capture_available()
                    else "Capture devices are not available on this platform."
                ),
                "busy": False,
            },
            parent=parent,
        )
        runtime.configChanged.connect(self.refresh)
        self.refresh()
        self._display_timer = QTimer(self)
        self._display_timer.setInterval(2000)
        self._display_timer.timeout.connect(self._poll_display_geometry)
        self._display_timer.start()
        runtime.register_shutdown(self.shutdown)

    @staticmethod
    def device_selector(config: AppConfig) -> dict[str, object]:
        return {
            "name": config.capture_device_name,
            "path": config.capture_device_path,
            "vid": config.capture_device_vid,
            "pid": config.capture_device_pid,
            "preferred_backend": config.capture_device_backend,
            "monitor_index": config.monitor_index,
        }

    def source_label(self, config: AppConfig | None = None) -> str:
        config = config or self.runtime.config
        if config.capture_source == "window":
            value = config.capture_window_title or config.capture_window_process or "Selected window"
            return f"Window · {value}"
        if config.capture_source == "device":
            return f"Capture device · {config.capture_device_name or 'Selected device'}"
        monitor = next(
            (
                value
                for value in self._monitors_by_key.values()
                if value.index == config.monitor_index
            ),
            None,
        )
        if monitor is not None:
            primary = next(
                (value for value in self._monitors_by_key.values() if value.is_primary),
                monitor,
            )
            return format_monitor_label(monitor, primary)
        return f"Monitor {config.monitor_index}"

    def ready_text(self) -> str:
        source = self.runtime.config.capture_source
        if source == "window":
            return f"{self.source_label()} is selected. Start watching when Fortnite is open."
        if source == "device":
            return f"{self.source_label()} is selected. Start when the console feed is visible."
        return "Choose the display with Fortnite, or select its window or capture device."

    @Slot()
    def refresh(self) -> None:
        config = self.runtime.config
        try:
            monitors = list_monitors()
        except Exception as exc:
            print(f"[GUI] Failed to list monitors: {exc}")
            monitors = []
        if self._display_geometry_signature is None:
            self._display_geometry_signature = self._geometry_signature(monitors)
        primary = next((item for item in monitors if item.is_primary), monitors[0] if monitors else None)
        rows: list[dict[str, object]] = []
        self._monitors_by_key = {}
        selected_key = ""
        for monitor in monitors:
            key = str(monitor.key)
            self._monitors_by_key[key] = monitor
            label = format_monitor_label(monitor, primary)
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "width": monitor.width,
                    "height": monitor.height,
                    "primary": monitor.is_primary,
                }
            )
            if monitor.index == config.monitor_index:
                selected_key = key
        if not selected_key:
            selected_key = f"unavailable:{config.monitor_index}"
            rows.append(
                {
                    "key": selected_key,
                    "label": (
                        f"Monitor {config.monitor_index} "
                        "(temporarily unavailable)"
                    ),
                    "width": 0,
                    "height": 0,
                    "primary": False,
                }
            )
        label = self.source_label(config)
        detail = {
            "monitor": "Captures the full selected display.",
            "window": "Follows the selected game window between displays.",
            "device": "Reads the selected console capture device.",
        }.get(config.capture_source, "")
        self.replace_state(
            {
                **self._state,
                "source": config.capture_source,
                "sourceLabel": label,
                "sourceDetail": detail,
                "monitorKey": selected_key,
                "monitors": rows,
            }
        )

    @staticmethod
    def _geometry_signature(
        monitors,
    ) -> tuple[tuple[int, int, int, int, int], ...]:
        return tuple(
            (monitor.index, monitor.left, monitor.top, monitor.width, monitor.height)
            for monitor in monitors
        )

    def _poll_display_geometry(self) -> None:
        try:
            monitors = list_monitors()
        except Exception as exc:
            print(f"[GUI] Failed to read display geometry: {exc}")
            return
        signature = self._geometry_signature(monitors)
        previous = self._display_geometry_signature
        self._display_geometry_signature = signature
        if previous is not None and signature != previous:
            self.runtime.close_device_capture(force=True)
            self.refresh()
            self.displayGeometryChanged.emit(True)

    @Slot()
    @Slot(bool)
    def refreshDisplayGeometry(self, automatic: bool = False) -> None:
        self.runtime.close_device_capture(force=True)
        try:
            monitors = list_monitors()
        except Exception as exc:
            print(f"[GUI] Failed to read display geometry: {exc}")
            monitors = []
        self._display_geometry_signature = self._geometry_signature(monitors)
        self.refresh()
        self.displayGeometryChanged.emit(bool(automatic))

    def shutdown(self) -> None:
        self._display_timer.stop()

    @Slot(str)
    def selectMonitor(self, key: str) -> None:
        monitor = self._monitors_by_key.get(key)
        if monitor is None:
            return
        config = self.runtime.config
        changed = config.monitor_index != monitor.index or config.capture_source != "monitor"
        self.runtime.close_device_capture(force=True)
        self.runtime.update_config(
            monitor_index=monitor.index,
            capture_source="monitor",
            capture_window_title="",
            capture_window_process="",
            capture_window_class="",
            capture_device_name="",
            capture_device_path="",
            capture_device_vid=None,
            capture_device_pid=None,
            capture_device_backend=0,
        )
        self.refresh()
        if changed:
            self.sourceChanged.emit()
            self.runtime.detailChanged.emit(f"{self.source_label()} selected")

    @Slot()
    def chooseWindow(self) -> None:
        if not window_capture_available():
            self.runtime.dialogs.show_message(
                "Select Window",
                window_capture_unavailable_reason(),
            )
            return
        self.update_state(busy=True)

        def work():
            return list_capture_windows()

        def done(result, error) -> None:
            self.update_state(busy=False)
            if error is not None:
                self.runtime.dialogs.show_message(
                    "Select Window",
                    f"Capture windows could not be listed: {error}",
                    tone="danger",
                )
                return
            windows = list(result or [])
            self._windows_by_key = {
                f"window-{index}": window for index, window in enumerate(windows)
            }
            options = [
                {
                    "id": key,
                    "label": window.title,
                    "detail": window.process_name or "Unknown application",
                    "selected": bool(window.is_fortnite),
                }
                for key, window in self._windows_by_key.items()
            ]
            if not options:
                self.runtime.dialogs.show_message(
                    "Select Window",
                    "No selectable windows were found. Open Fortnite and try again.",
                )
                return
            if sys.platform == "win32":
                note = (
                    "Keep Fortnite restored. Windows can pause capture while "
                    "it is minimized."
                )
            elif sys.platform == "darwin":
                note = (
                    "Allow Screen Recording access when macOS asks. Minimized "
                    "windows cannot be captured."
                )
            else:
                note = (
                    "X11 covered-window capture depends on the window manager. "
                    "Keep Fortnite visible if frames appear blank."
                )
            self.runtime.dialogs.choices(
                "Select Window",
                WINDOW_CAPTURE_EXPLANATION,
                options,
                note=note,
                accept_text="Use window",
                callback=self._apply_window_choice,
            )

        self.runtime.run_background(work, done, name="DroidAlertsListWindows")

    def _apply_window_choice(self, payload: dict[str, Any] | None) -> None:
        if not payload:
            return
        window = self._windows_by_key.get(str(payload.get("selected") or ""))
        if window is None:
            self.runtime.dialogs.show_message("Select Window", "Choose a window first.")
            return
        self.update_state(busy=True)
        config = self.runtime.config

        def work():
            capture = create_capture(
                monitor_index=max(1, int(config.monitor_index)),
                capture_source="window",
                window_title=window.title,
                window_process=window.process_name,
                window_class=window.class_name,
            )
            try:
                return capture.screen_size()
            finally:
                capture.close()

        def done(result, error) -> None:
            self.update_state(busy=False)
            if error is not None:
                self.runtime.dialogs.show_message(
                    "Select Window",
                    f"That window could not be captured: {error}",
                    tone="danger",
                )
                return
            self.runtime.close_device_capture(force=True)
            self.runtime.update_config(
                capture_source="window",
                capture_window_title=window.title,
                capture_window_process=window.process_name,
                capture_window_class=window.class_name,
                capture_device_name="",
                capture_device_path="",
                capture_device_vid=None,
                capture_device_pid=None,
                capture_device_backend=0,
            )
            self.refresh()
            self.sourceChanged.emit()
            width, height = result
            selector_key = window_capture_key(
                title=window.title,
                process_name=window.process_name,
                class_name=window.class_name,
            )
            self._window_sizes[selector_key] = (int(width), int(height))
            self.runtime.detailChanged.emit(
                f'Capturing "{window.title}" at {width} × {height}'
            )

        self.runtime.run_background(work, done, name="DroidAlertsCheckWindow")

    @Slot()
    def chooseDevice(self) -> None:
        if not self._device_capture_available():
            self.runtime.dialogs.show_message(
                "Select Capture Device",
                "Capture devices are not available on this platform.",
            )
            return
        self.update_state(busy=True)

        def done(result, error) -> None:
            self.update_state(busy=False)
            if error is not None:
                self.runtime.dialogs.show_message(
                    "Select Capture Device",
                    f"Capture devices could not be listed: {error}",
                    tone="danger",
                )
                return
            devices = list(result or [])
            self._devices_by_key = {
                f"device-{index}": device for index, device in enumerate(devices)
            }
            options = [
                {
                    "id": key,
                    "label": device.name,
                    "detail": device.backend_name,
                    "selected": False,
                }
                for key, device in self._devices_by_key.items()
            ]
            if not options:
                self.runtime.dialogs.show_message(
                    "Select Capture Device",
                    "No video capture devices were found.",
                )
                return
            self.runtime.dialogs.choices(
                "Select Capture Device",
                "Choose the capture card receiving your console video.",
                options,
                note="Close OBS or the device preview if the capture card is busy.",
                accept_text="Use capture device",
                callback=self._apply_device_choice,
            )

        self.runtime.run_background(
            list_capture_devices,
            done,
            name="DroidAlertsListCaptureDevices",
        )

    def _apply_device_choice(self, payload: dict[str, Any] | None) -> None:
        if not payload:
            return
        device = self._devices_by_key.get(str(payload.get("selected") or ""))
        if device is None:
            self.runtime.dialogs.show_message(
                "Select Capture Device",
                "Choose a capture device first.",
            )
            return
        candidate = AppConfig.from_dict(self.runtime.config.to_dict())
        candidate.capture_source = "device"
        candidate.capture_window_title = ""
        candidate.capture_window_process = ""
        candidate.capture_window_class = ""
        candidate.capture_device_name = device.name
        candidate.capture_device_path = device.path
        candidate.capture_device_vid = device.vid
        candidate.capture_device_pid = device.pid
        candidate.capture_device_backend = device.backend
        self.update_state(busy=True)

        def work():
            session = session_from_config(
                candidate,
                context=multiprocessing.get_context("spawn"),
            )
            try:
                size = session.screen_size()
            except Exception:
                session.close()
                raise
            return session, size

        def done(result, error) -> None:
            self.update_state(busy=False)
            if error is not None:
                self.runtime.dialogs.show_message(
                    "Select Capture Device",
                    str(error),
                    tone="danger",
                )
                return
            session, size = result
            self.runtime.close_device_capture(force=True)
            self.runtime.device_capture_session = session
            self.runtime.config = candidate
            self.runtime.configChanged.emit()
            self.runtime.save_now()
            self.refresh()
            self.sourceChanged.emit()
            self.runtime.detailChanged.emit(
                f'Capture device set to "{device.name}" ({size[0]} × {size[1]})'
            )

        self.runtime.run_background(work, done, name="DroidAlertsCheckCaptureDevice")

    def ensure_device_session(self, config: AppConfig | None = None):
        config = config or self.runtime.config
        session = self.runtime.device_capture_session
        selector = self.device_selector(config)
        if session is not None and session.matches(**selector):
            return session
        self.runtime.close_device_capture(force=True)
        session = session_from_config(
            config,
            context=multiprocessing.get_context("spawn"),
        )
        self.runtime.device_capture_session = session
        try:
            session.screen_size()
        except Exception:
            self.runtime.device_capture_session = None
            session.close()
            raise
        return session

    def create_runtime_capture(self, config: AppConfig):
        if config.capture_source == "device":
            session = self.runtime.device_capture_session
            if session is None or not session.matches(**self.device_selector(config)):
                raise RuntimeError("The selected capture device session is not running.")
            return session.client()
        return create_capture(
            monitor_index=config.monitor_index,
            capture_source=config.capture_source,
            window_title=config.capture_window_title,
            window_process=config.capture_window_process,
            window_class=config.capture_window_class,
        )

    def create_chat_capture(self, config: AppConfig | None = None):
        config = config or self.runtime.config
        if config.capture_source == "device":
            session = self.runtime.device_capture_session
            if session is not None and session.matches(**self.device_selector(config)):
                return session.client()
        return create_configured_capture(config)

    def current_capture_key(self) -> str | None:
        config = self.runtime.config
        if config.capture_source == "window":
            return window_capture_key(
                title=config.capture_window_title,
                process_name=config.capture_window_process,
                class_name=config.capture_window_class,
            )
        if config.capture_source == "device":
            return device_capture_key(
                name=config.capture_device_name,
                path=config.capture_device_path,
                vid=config.capture_device_vid,
                pid=config.capture_device_pid,
            )
        monitor = self.current_monitor()
        return str(monitor.key) if monitor is not None else None

    def current_monitor(self) -> MonitorInfo | None:
        config = self.runtime.config
        index = max(1, int(self.runtime.config.monitor_index))
        descriptor = None
        try:
            monitors = list(list_monitors())
            descriptor = next(
                (item for item in monitors if item.index == index),
                None,
            )
            if config.capture_source == "window":
                window = resolve_capture_window(
                    title=config.capture_window_title,
                    process_name=config.capture_window_process,
                    class_name=config.capture_window_class,
                )

                def intersection_area(item) -> int:
                    left = max(window.left, item.left)
                    top = max(window.top, item.top)
                    right = min(window.left + window.width, item.left + item.width)
                    bottom = min(window.top + window.height, item.top + item.height)
                    return max(0, right - left) * max(0, bottom - top)

                window_monitor = max(monitors, key=intersection_area, default=None)
                if (
                    window_monitor is not None
                    and intersection_area(window_monitor) > 0
                ):
                    descriptor = window_monitor
            elif config.capture_source == "device" and descriptor is None:
                descriptor = next(
                    (item for item in monitors if item.is_primary),
                    monitors[0] if monitors else None,
                )
        except Exception:
            pass
        if descriptor is None:
            return None
        return MonitorInfo(
            left=descriptor.left,
            top=descriptor.top,
            width=descriptor.width,
            height=descriptor.height,
            index=descriptor.index,
            key=descriptor.key,
            name=descriptor.name,
        )

    def current_belt_source(self, *, open_device: bool = False) -> MonitorInfo | None:
        monitor = self.current_monitor()
        config = self.runtime.config
        if monitor is None or config.capture_source == "monitor":
            return monitor
        capture = None
        source_left = monitor.left
        source_top = monitor.top
        try:
            if config.capture_source == "device":
                session = self.runtime.device_capture_session
                if session is not None and session.matches(
                    **self.device_selector(config)
                ):
                    capture = session.client()
                elif open_device:
                    capture = self.create_chat_capture(config)
                width, height = (
                    capture.screen_size()
                    if capture is not None
                    else (1920, 1080)
                )
                key = self.current_capture_key() or "device:unknown"
                name = config.capture_device_name or "Capture device"
            else:
                key = self.current_capture_key() or "window:unknown"
                if open_device:
                    capture = self.create_chat_capture(config)
                    width, height = capture.screen_size()
                    area = getattr(capture, "capture_area", None)
                    source_left = int(getattr(area, "left", source_left))
                    source_top = int(getattr(area, "top", source_top))
                    key = (
                        str(getattr(area, "key", "") or "")
                        or self.current_capture_key()
                        or "window:unknown"
                    )
                    self._window_sizes[key] = (int(width), int(height))
                else:
                    window = resolve_capture_window(
                        title=config.capture_window_title,
                        process_name=config.capture_window_process,
                        class_name=config.capture_window_class,
                    )
                    size = (window.width, window.height)
                    self._window_sizes[key] = size
                    source_left = window.left
                    source_top = window.top
                    width, height = size
                name = (
                    config.capture_window_title
                    or config.capture_window_process
                    or "Selected window"
                )
        finally:
            if capture is not None:
                capture.close()
        return MonitorInfo(
            left=source_left,
            top=source_top,
            width=width,
            height=height,
            index=monitor.index,
            key=key,
            name=name,
        )
