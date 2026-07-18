from __future__ import annotations

import ctypes
import hashlib
import ntpath
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np

from .capture import MonitorInfo, PixelBox, list_monitors


WINDOW_CAPTURE_EXPLANATION = (
    "If you select the Fortnite window, the tool keeps watch at all times, "
    "even if something is covering it."
)

_MAX_WINDOW_TEXT = 512
_FRAME_WAIT_SECONDS = 5.0
_CROP_REFRESH_SECONDS = 0.05


@dataclass(frozen=True)
class WindowDescriptor:
    hwnd: int
    title: str
    process_name: str
    class_name: str
    process_id: int
    left: int
    top: int
    width: int
    height: int

    @property
    def is_fortnite(self) -> bool:
        haystack = f"{self.title} {self.process_name}".casefold()
        return "fortnite" in haystack


def _clean_text(value: object) -> str:
    text = str(value or "").replace("\x00", " ")
    return " ".join(text.split())[:_MAX_WINDOW_TEXT]


def _window_selector_key(
    *,
    title: str,
    process_name: str,
    class_name: str,
) -> tuple[str, str, str]:
    return (
        _clean_text(title).casefold(),
        _clean_text(process_name).casefold(),
        _clean_text(class_name).casefold(),
    )


def window_capture_key(
    *,
    title: str,
    process_name: str,
    class_name: str,
) -> str:
    """Stable calibration key for a persisted window selector."""
    normalized_title, normalized_process, normalized_class = _window_selector_key(
        title=title,
        process_name=process_name,
        class_name=class_name,
    )
    selector = "\x00".join(
        (normalized_title, normalized_process, normalized_class)
    ).encode("utf-8")
    digest = hashlib.sha256(selector).hexdigest()[:24]
    return f"window:{digest}"


def _hwnd_value(hwnd: object) -> int:
    return int(getattr(hwnd, "value", hwnd) or 0)


def list_capture_windows() -> list[WindowDescriptor]:
    """Return visible top-level windows that Windows Graphics Capture can target."""
    if sys.platform != "win32":
        return []

    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    enum_proc_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )
    user32.EnumWindows.argtypes = (enum_proc_type, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    )
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = (
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    )
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.GetWindowRect.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.RECT),
    )
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    kernel32.OpenProcess.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetCurrentProcessId.restype = wintypes.DWORD

    current_process_id = int(kernel32.GetCurrentProcessId())
    process_query_limited_information = 0x1000
    ws_child = 0x40000000
    ws_ex_toolwindow = 0x00000080
    gwl_style = -16
    gwl_exstyle = -20
    windows: list[WindowDescriptor] = []

    def process_name(process_id: int) -> str:
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            process_id,
        )
        if not handle:
            return ""
        try:
            capacity = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(capacity.value)
            if not kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(capacity),
            ):
                return ""
            return _clean_text(ntpath.basename(buffer.value))
        finally:
            kernel32.CloseHandle(handle)

    @enum_proc_type
    def collect(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True

            style = int(user32.GetWindowLongW(hwnd, gwl_style))
            ex_style = int(user32.GetWindowLongW(hwnd, gwl_exstyle))
            if style & ws_child or ex_style & ws_ex_toolwindow:
                return True

            title_length = int(user32.GetWindowTextLengthW(hwnd))
            if title_length <= 0:
                return True
            title_buffer = ctypes.create_unicode_buffer(min(title_length + 1, _MAX_WINDOW_TEXT + 1))
            if user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer)) <= 0:
                return True
            title = _clean_text(title_buffer.value)
            if not title:
                return True

            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width < 160 or height < 120:
                return True

            process_id_value = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id_value))
            process_id = int(process_id_value.value)
            if not process_id or process_id == current_process_id:
                return True

            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
            windows.append(
                WindowDescriptor(
                    hwnd=_hwnd_value(hwnd),
                    title=title,
                    process_name=process_name(process_id),
                    class_name=_clean_text(class_buffer.value),
                    process_id=process_id,
                    left=int(rect.left),
                    top=int(rect.top),
                    width=width,
                    height=height,
                )
            )
        except Exception:
            # One inaccessible or closing window must not break the picker.
            pass
        return True

    if not user32.EnumWindows(collect, 0):
        raise ctypes.WinError()

    windows.sort(
        key=lambda item: (
            not item.is_fortnite,
            item.title.casefold(),
            item.process_name.casefold(),
        )
    )
    return windows


def resolve_capture_window(
    *,
    title: str,
    process_name: str,
    class_name: str,
    candidates: list[WindowDescriptor] | None = None,
) -> WindowDescriptor:
    """Resolve a persisted selector without trusting a stale HWND."""
    wanted_title, wanted_process, wanted_class = _window_selector_key(
        title=title,
        process_name=process_name,
        class_name=class_name,
    )
    if not any((wanted_title, wanted_process, wanted_class)):
        raise RuntimeError("No window has been selected.")

    candidates = list_capture_windows() if candidates is None else candidates
    eligible: list[tuple[int, WindowDescriptor]] = []
    for candidate in candidates:
        candidate_title, candidate_process, candidate_class = _window_selector_key(
            title=candidate.title,
            process_name=candidate.process_name,
            class_name=candidate.class_name,
        )

        # The executable is the strongest persisted identity. Requiring an
        # exact match prevents a different app from spoofing only the title.
        if wanted_process and candidate_process != wanted_process:
            continue
        if not wanted_process and wanted_title and candidate_title != wanted_title:
            continue
        if not wanted_process and not wanted_title and candidate_class != wanted_class:
            continue

        score = 0
        if wanted_process and candidate_process == wanted_process:
            score += 8
        if wanted_class and candidate_class == wanted_class:
            score += 4
        if wanted_title and candidate_title == wanted_title:
            score += 2
        if candidate.is_fortnite:
            score += 1
        eligible.append((score, candidate))

    if not eligible:
        label = _clean_text(title) or _clean_text(process_name) or "the selected window"
        raise RuntimeError(
            f'Could not find "{label}". Open Fortnite, then select its window again.'
        )
    eligible.sort(key=lambda item: item[0], reverse=True)
    return eligible[0][1]


def _physical_monitor_for_window(
    window: WindowDescriptor,
    fallback_index: int,
) -> MonitorInfo:
    try:
        monitors = list_monitors()
    except Exception:
        monitors = []

    def intersection_area(monitor) -> int:
        left = max(window.left, monitor.left)
        top = max(window.top, monitor.top)
        right = min(window.left + window.width, monitor.left + monitor.width)
        bottom = min(window.top + window.height, monitor.top + monitor.height)
        return max(0, right - left) * max(0, bottom - top)

    descriptor = max(monitors, key=intersection_area, default=None)
    if descriptor is None or intersection_area(descriptor) == 0:
        descriptor = next(
            (monitor for monitor in monitors if monitor.index == fallback_index),
            None,
        )
    if descriptor is None:
        return MonitorInfo(
            left=window.left,
            top=window.top,
            width=window.width,
            height=window.height,
            index=max(1, fallback_index),
            key=f"window-display:{max(1, fallback_index)}",
            name=window.title,
        )
    return MonitorInfo(
        left=descriptor.left,
        top=descriptor.top,
        width=descriptor.width,
        height=descriptor.height,
        index=descriptor.index,
        key=descriptor.key,
        name=descriptor.name,
    )


class WindowsGraphicsCapture:
    """Capture one HWND through Windows Graphics Capture.

    The native library owns the capture thread. Python copies only the
    requested chat crop from each delivered frame, and never uploads or stores
    the rest of the selected window.
    """

    def __init__(
        self,
        *,
        title: str,
        process_name: str,
        class_name: str,
        monitor_index: int = 1,
    ) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Window capture is only available on Windows.")
        try:
            from windows_capture import WindowsCapture
        except Exception as exc:
            raise RuntimeError(
                "Window capture support is missing. Reinstall Droid Alerts or "
                "run pip install -r requirements.txt."
            ) from exc

        self.window = resolve_capture_window(
            title=title,
            process_name=process_name,
            class_name=class_name,
        )
        self.monitor = _physical_monitor_for_window(self.window, monitor_index)
        calibration_key = window_capture_key(
            title=self.window.title,
            process_name=self.window.process_name,
            class_name=self.window.class_name,
        )
        self.capture_area = MonitorInfo(
            left=self.window.left,
            top=self.window.top,
            width=self.window.width,
            height=self.window.height,
            index=self.monitor.index,
            key=calibration_key,
            name=self.window.title,
        )

        self._condition = threading.Condition()
        self._capture_size: tuple[int, int] | None = None
        self._requested_box: PixelBox | None = None
        self._latest_box: PixelBox | None = None
        self._latest_crop: np.ndarray | None = None
        self._crop_sequence = 0
        self._returned_sequence = 0
        self._last_crop_at = 0.0
        self._closed = False
        self._error = ""

        native_capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=self.window.hwnd,
        )

        @native_capture.event
        def on_frame_arrived(frame, _capture_control):
            self._on_frame(frame)

        @native_capture.event
        def on_closed():
            with self._condition:
                self._closed = True
                self._condition.notify_all()

        self._native_capture = native_capture
        try:
            self._capture_control = native_capture.start_free_threaded()
        except Exception as exc:
            self._closed = True
            raise RuntimeError(f"Could not start window capture: {exc}") from exc

        # Surface dimensions come from WGC itself and may differ slightly from
        # the Win32 window rectangle because of borders and DPI scaling.
        width, height = self.screen_size()
        self.capture_area.width = width
        self.capture_area.height = height

    def _on_frame(self, frame) -> None:
        try:
            width = int(frame.width)
            height = int(frame.height)
            if width <= 0 or height <= 0:
                return
            now = time.monotonic()
            with self._condition:
                size_changed = self._capture_size != (width, height)
                self._capture_size = (width, height)
                if size_changed:
                    self.capture_area.width = width
                    self.capture_area.height = height
                    self._latest_box = None
                    self._latest_crop = None

                requested = self._requested_box
                if requested is None:
                    self._condition.notify_all()
                    return
                if now - self._last_crop_at < _CROP_REFRESH_SECONDS:
                    self._condition.notify_all()
                    return

                left = max(0, min(width, requested.left))
                top = max(0, min(height, requested.top))
                right = max(left, min(width, requested.right))
                bottom = max(top, min(height, requested.bottom))
                if right <= left or bottom <= top:
                    self._error = "The selected chat region is outside the captured window."
                    self._condition.notify_all()
                    return

                self._latest_crop = frame.frame_buffer[top:bottom, left:right, :3].copy()
                self._latest_box = PixelBox(left, top, right - left, bottom - top)
                self._crop_sequence += 1
                self._error = ""
                self._last_crop_at = now
                self._condition.notify_all()
        except Exception as exc:
            with self._condition:
                self._error = str(exc)
                self._condition.notify_all()

    def screen_size(self) -> tuple[int, int]:
        deadline = time.monotonic() + _FRAME_WAIT_SECONDS
        with self._condition:
            while self._capture_size is None and not self._closed and not self._error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            if self._capture_size is not None:
                return self._capture_size
            if self._error:
                raise RuntimeError(self._error)
            if self._closed:
                raise RuntimeError("The selected window was closed.")
            raise RuntimeError(
                "Fortnite did not provide a capture frame. Make sure it is open and not minimized."
            )

    def grab(self, box: PixelBox) -> np.ndarray:
        deadline = time.monotonic() + _FRAME_WAIT_SECONDS
        with self._condition:
            if self._closed:
                raise RuntimeError("The selected window was closed.")
            if self._requested_box != box:
                self._requested_box = box
                self._latest_box = None
                self._latest_crop = None
                self._error = ""
                self._last_crop_at = 0.0

            while (
                (
                    self._latest_crop is None
                    or self._crop_sequence <= self._returned_sequence
                )
                and not self._closed
                and not self._error
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            if (
                self._latest_crop is not None
                and self._crop_sequence > self._returned_sequence
            ):
                self._returned_sequence = self._crop_sequence
                return self._latest_crop.copy()
            if self._error:
                error = self._error
                self._error = ""
                raise RuntimeError(error)
            if self._closed:
                raise RuntimeError("The selected window was closed.")
            raise RuntimeError(
                "No new Fortnite frame arrived. Restore the window if it is minimized."
            )

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        try:
            self._capture_control.stop()
        except Exception:
            pass
