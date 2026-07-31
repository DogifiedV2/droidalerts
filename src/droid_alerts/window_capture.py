from __future__ import annotations

import ctypes
import hashlib
import ntpath
import os
import sys
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

from .capture import MonitorInfo, PixelBox, _mss_instance, list_monitors


WINDOW_CAPTURE_EXPLANATION = (
    "Select the Fortnite window that Droid Alerts should watch."
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


def _list_windows_win32() -> list[WindowDescriptor]:
    """Return visible top-level windows that Windows Graphics Capture can target."""
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


def window_capture_available() -> bool:
    if sys.platform in {"win32", "darwin"}:
        return True
    if not sys.platform.startswith("linux"):
        return False
    session_type = os.environ.get("XDG_SESSION_TYPE", "").strip().casefold()
    return session_type != "wayland" and bool(os.environ.get("DISPLAY"))


def window_capture_unavailable_reason() -> str:
    if sys.platform.startswith("linux"):
        session_type = os.environ.get("XDG_SESSION_TYPE", "").strip().casefold()
        if session_type == "wayland":
            return (
                "Window selection is not available on Wayland yet. "
                "Use a monitor or capture device."
            )
        if not os.environ.get("DISPLAY"):
            return "Window selection requires an X11 desktop session."
    return "Window capture is not available on this platform."


def _list_windows_x11() -> list[WindowDescriptor]:
    if not window_capture_available():
        raise RuntimeError(window_capture_unavailable_reason())
    try:
        from Xlib import X
        from Xlib.display import Display
    except Exception as exc:
        raise RuntimeError(
            "X11 window support is missing. Run pip install -r requirements.txt."
        ) from exc

    display = Display()
    try:
        root = display.screen().root
        client_list = root.get_full_property(
            display.intern_atom("_NET_CLIENT_LIST_STACKING"),
            X.AnyPropertyType,
        )
        if client_list is not None:
            window_ids = [int(value) for value in client_list.value]
        else:
            window_ids = [int(window.id) for window in root.query_tree().children]
        utf8 = display.intern_atom("UTF8_STRING")
        name_atom = display.intern_atom("_NET_WM_NAME")
        pid_atom = display.intern_atom("_NET_WM_PID")
        current_process_id = os.getpid()
        windows: list[WindowDescriptor] = []
        for window_id in window_ids:
            try:
                window = display.create_resource_object("window", window_id)
                attributes = window.get_attributes()
                if attributes.map_state != X.IsViewable:
                    continue
                title_property = window.get_full_property(name_atom, utf8)
                if title_property is not None:
                    title = _clean_text(
                        bytes(title_property.value).decode("utf-8", errors="replace")
                    )
                else:
                    title = _clean_text(window.get_wm_name())
                if not title:
                    continue
                pid_property = window.get_full_property(
                    pid_atom,
                    X.AnyPropertyType,
                )
                process_id = (
                    int(pid_property.value[0])
                    if pid_property is not None and len(pid_property.value)
                    else 0
                )
                if process_id == current_process_id:
                    continue
                wm_class = window.get_wm_class() or ()
                class_name = _clean_text(" ".join(str(value) for value in wm_class))
                process_name = ""
                if process_id:
                    try:
                        process_path = os.path.join(
                            "/proc",
                            str(process_id),
                            "comm",
                        )
                        with open(
                            process_path,
                            "r",
                            encoding="utf-8",
                            errors="replace",
                        ) as handle:
                            process_name = _clean_text(handle.read())
                    except OSError:
                        process_name = ""
                if not process_name and wm_class:
                    process_name = _clean_text(wm_class[-1])
                geometry = window.get_geometry()
                position = window.translate_coords(root, 0, 0)
                width = int(geometry.width)
                height = int(geometry.height)
                if width < 160 or height < 120:
                    continue
                windows.append(
                    WindowDescriptor(
                        hwnd=window_id,
                        title=title,
                        process_name=process_name,
                        class_name=class_name,
                        process_id=process_id,
                        left=int(position.x),
                        top=int(position.y),
                        width=width,
                        height=height,
                    )
                )
            except Exception:
                continue
    finally:
        display.close()
    windows.sort(
        key=lambda item: (
            not item.is_fortnite,
            item.title.casefold(),
            item.process_name.casefold(),
        )
    )
    return windows


def _list_windows_macos() -> list[WindowDescriptor]:
    try:
        import AppKit
        import Quartz
    except Exception as exc:
        raise RuntimeError(
            "macOS window support is missing. Run pip install -r requirements.txt."
        ) from exc

    options = (
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements
    )
    records = Quartz.CGWindowListCopyWindowInfo(
        options,
        Quartz.kCGNullWindowID,
    )
    current_process_id = os.getpid()
    windows: list[WindowDescriptor] = []
    for record in records or ():
        try:
            process_id = int(record.get(Quartz.kCGWindowOwnerPID, 0))
            if not process_id or process_id == current_process_id:
                continue
            if int(record.get(Quartz.kCGWindowLayer, 0)) != 0:
                continue
            title = _clean_text(record.get(Quartz.kCGWindowName))
            owner = _clean_text(record.get(Quartz.kCGWindowOwnerName))
            if not title:
                title = owner
            if not title:
                continue
            bounds = record.get(Quartz.kCGWindowBounds) or {}
            width = int(round(float(bounds.get("Width", 0))))
            height = int(round(float(bounds.get("Height", 0))))
            if width < 160 or height < 120:
                continue
            application = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(
                process_id
            )
            bundle_id = (
                _clean_text(application.bundleIdentifier())
                if application is not None
                else ""
            )
            windows.append(
                WindowDescriptor(
                    hwnd=int(record.get(Quartz.kCGWindowNumber, 0)),
                    title=title,
                    process_name=owner,
                    class_name=bundle_id,
                    process_id=process_id,
                    left=int(round(float(bounds.get("X", 0)))),
                    top=int(round(float(bounds.get("Y", 0)))),
                    width=width,
                    height=height,
                )
            )
        except Exception:
            continue
    windows.sort(
        key=lambda item: (
            not item.is_fortnite,
            item.title.casefold(),
            item.process_name.casefold(),
        )
    )
    return windows


def list_capture_windows() -> list[WindowDescriptor]:
    if sys.platform == "win32":
        return _list_windows_win32()
    if sys.platform == "darwin":
        return _list_windows_macos()
    if sys.platform.startswith("linux"):
        return _list_windows_x11()
    return []


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


class X11WindowCapture:
    """Capture one X11 window, using its backing pixels when available."""

    def __init__(
        self,
        *,
        title: str,
        process_name: str,
        class_name: str,
        monitor_index: int = 1,
    ) -> None:
        if not sys.platform.startswith("linux") or not window_capture_available():
            raise RuntimeError(window_capture_unavailable_reason())
        try:
            from Xlib.display import Display
        except Exception as exc:
            raise RuntimeError(
                "X11 window support is missing. Run pip install -r requirements.txt."
            ) from exc

        self.window = resolve_capture_window(
            title=title,
            process_name=process_name,
            class_name=class_name,
        )
        self.monitor = _physical_monitor_for_window(self.window, monitor_index)
        self.capture_area = MonitorInfo(
            left=self.window.left,
            top=self.window.top,
            width=self.window.width,
            height=self.window.height,
            index=self.monitor.index,
            key=window_capture_key(
                title=self.window.title,
                process_name=self.window.process_name,
                class_name=self.window.class_name,
            ),
            name=self.window.title,
        )
        self._display = Display()
        self._native_window = self._display.create_resource_object(
            "window",
            self.window.hwnd,
        )
        self._mss = _mss_instance()
        self._closed = False
        self._refresh_geometry()

    def _refresh_geometry(self) -> tuple[int, int, int, int]:
        if self._closed:
            raise RuntimeError("The selected window was closed.")
        try:
            root = self._display.screen().root
            geometry = self._native_window.get_geometry()
            position = self._native_window.translate_coords(root, 0, 0)
            left = int(position.x)
            top = int(position.y)
            width = int(geometry.width)
            height = int(geometry.height)
        except Exception as exc:
            raise RuntimeError("The selected X11 window was closed.") from exc
        if width <= 0 or height <= 0:
            raise RuntimeError("The selected X11 window has no visible area.")
        self.capture_area.left = left
        self.capture_area.top = top
        self.capture_area.width = width
        self.capture_area.height = height
        return left, top, width, height

    def screen_size(self) -> tuple[int, int]:
        _left, _top, width, height = self._refresh_geometry()
        return width, height

    def _grab_window_pixels(self, box: PixelBox) -> np.ndarray | None:
        try:
            from Xlib import X

            image = self._native_window.get_image(
                box.left,
                box.top,
                box.width,
                box.height,
                X.ZPixmap,
                0xFFFFFFFF,
            )
            if image is None or not image.data:
                return None
            payload = np.frombuffer(image.data, dtype=np.uint8)
            row_bytes = payload.size // box.height
            channels = 4 if row_bytes >= box.width * 4 else 3
            if row_bytes < box.width * channels:
                return None
            rows = payload[: row_bytes * box.height].reshape(
                box.height,
                row_bytes,
            )
            pixels = rows[:, : box.width * channels].reshape(
                box.height,
                box.width,
                channels,
            )
            return np.ascontiguousarray(pixels[:, :, :3])
        except Exception:
            return None

    def grab(self, box: PixelBox) -> np.ndarray:
        left, top, width, height = self._refresh_geometry()
        crop_left = max(0, min(width, box.left))
        crop_top = max(0, min(height, box.top))
        crop_right = max(crop_left, min(width, box.right))
        crop_bottom = max(crop_top, min(height, box.bottom))
        if crop_right <= crop_left or crop_bottom <= crop_top:
            raise RuntimeError("The selected region is outside the captured window.")
        crop = PixelBox(
            crop_left,
            crop_top,
            crop_right - crop_left,
            crop_bottom - crop_top,
        )
        window_pixels = self._grab_window_pixels(crop)
        if window_pixels is not None:
            return window_pixels
        shot = self._mss.grab(
            {
                "left": left + crop.left,
                "top": top + crop.top,
                "width": crop.width,
                "height": crop.height,
            }
        )
        return cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._mss.close()
        finally:
            self._display.close()


def _cgimage_to_bgr(image) -> np.ndarray:
    try:
        import Quartz
    except Exception as exc:
        raise RuntimeError(
            "macOS window support is missing. Run pip install -r requirements.txt."
        ) from exc

    width = int(Quartz.CGImageGetWidth(image))
    height = int(Quartz.CGImageGetHeight(image))
    row_bytes = int(Quartz.CGImageGetBytesPerRow(image))
    provider = Quartz.CGImageGetDataProvider(image)
    payload = bytes(Quartz.CGDataProviderCopyData(provider))
    if width <= 0 or height <= 0 or row_bytes < width * 4:
        raise RuntimeError("macOS returned an unsupported window image.")
    rows = np.frombuffer(payload, dtype=np.uint8).reshape(height, row_bytes)
    bgra = rows[:, : width * 4].reshape(height, width, 4)
    return np.ascontiguousarray(bgra[:, :, :3])


def _macos_screencapturekit_source(window_id: int):
    import ScreenCaptureKit

    content_result: dict[str, object] = {}
    content_ready = threading.Event()

    def content_handler(content, error) -> None:
        content_result["content"] = content
        content_result["error"] = error
        content_ready.set()

    ScreenCaptureKit.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
        True,
        True,
        content_handler,
    )
    if not content_ready.wait(_FRAME_WAIT_SECONDS):
        raise RuntimeError("macOS timed out while listing capture windows.")
    error = content_result.get("error")
    if error is not None:
        raise RuntimeError(f"macOS could not list capture windows: {error}")
    content = content_result.get("content")
    windows = content.windows() if content is not None else ()
    selected = next(
        (
            window
            for window in windows
            if int(window.windowID()) == int(window_id)
        ),
        None,
    )
    if selected is None:
        raise RuntimeError("The selected macOS window was closed.")

    content_filter = (
        ScreenCaptureKit.SCContentFilter.alloc()
        .initWithDesktopIndependentWindow_(selected)
    )
    configuration = ScreenCaptureKit.SCStreamConfiguration.alloc().init()
    frame = selected.frame()
    configuration.setWidth_(max(1, round(float(frame.size.width) * 2)))
    configuration.setHeight_(max(1, round(float(frame.size.height) * 2)))
    configuration.setShowsCursor_(False)
    return ScreenCaptureKit, content_filter, configuration


def _macos_screencapturekit_image(
    window_id: int,
    source=None,
) -> np.ndarray:
    ScreenCaptureKit, content_filter, configuration = (
        source or _macos_screencapturekit_source(window_id)
    )

    image_result: dict[str, object] = {}
    image_ready = threading.Event()

    def image_handler(image, error) -> None:
        image_result["image"] = image
        image_result["error"] = error
        image_ready.set()

    ScreenCaptureKit.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
        content_filter,
        configuration,
        image_handler,
    )
    if not image_ready.wait(_FRAME_WAIT_SECONDS):
        raise RuntimeError("macOS timed out while capturing the selected window.")
    error = image_result.get("error")
    if error is not None:
        raise RuntimeError(f"macOS could not capture the selected window: {error}")
    image = image_result.get("image")
    if image is None:
        raise RuntimeError("macOS returned no image for the selected window.")
    return _cgimage_to_bgr(image)


def _macos_window_image(window_id: int) -> np.ndarray:
    try:
        import ScreenCaptureKit

        screenshot_manager = getattr(
            ScreenCaptureKit,
            "SCScreenshotManager",
            None,
        )
    except Exception:
        screenshot_manager = None
    if screenshot_manager is not None:
        return _macos_screencapturekit_image(window_id)

    try:
        import Quartz
    except Exception as exc:
        raise RuntimeError(
            "macOS window support is missing. Run pip install -r requirements.txt."
        ) from exc
    image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        int(window_id),
        (
            Quartz.kCGWindowImageBoundsIgnoreFraming
            | Quartz.kCGWindowImageBestResolution
        ),
    )
    if image is None:
        raise RuntimeError(
            "macOS could not capture the selected window. Allow Screen Recording "
            "access and keep the window open."
        )
    return _cgimage_to_bgr(image)


class MacOSWindowCapture:
    """Capture one macOS window through ScreenCaptureKit."""

    def __init__(
        self,
        *,
        title: str,
        process_name: str,
        class_name: str,
        monitor_index: int = 1,
    ) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("This window backend is only available on macOS.")
        self.window = resolve_capture_window(
            title=title,
            process_name=process_name,
            class_name=class_name,
        )
        self.monitor = _physical_monitor_for_window(self.window, monitor_index)
        self.capture_area = MonitorInfo(
            left=self.window.left,
            top=self.window.top,
            width=self.window.width,
            height=self.window.height,
            index=self.monitor.index,
            key=window_capture_key(
                title=self.window.title,
                process_name=self.window.process_name,
                class_name=self.window.class_name,
            ),
            name=self.window.title,
        )
        self._closed = False
        self._screencapturekit_source = None
        try:
            import ScreenCaptureKit

            if getattr(ScreenCaptureKit, "SCScreenshotManager", None) is not None:
                self._screencapturekit_source = (
                    _macos_screencapturekit_source(self.window.hwnd)
                )
        except Exception:
            self._screencapturekit_source = None
        self._latest_frame = self._capture_frame()
        self._update_size(self._latest_frame)

    def _capture_frame(self) -> np.ndarray:
        if self._screencapturekit_source is not None:
            return _macos_screencapturekit_image(
                self.window.hwnd,
                self._screencapturekit_source,
            )
        return _macos_window_image(self.window.hwnd)

    def _update_size(self, frame: np.ndarray) -> None:
        height, width = frame.shape[:2]
        self.capture_area.width = width
        self.capture_area.height = height

    def screen_size(self) -> tuple[int, int]:
        if self._closed:
            raise RuntimeError("The selected window was closed.")
        return self.capture_area.width, self.capture_area.height

    def grab(self, box: PixelBox) -> np.ndarray:
        if self._closed:
            raise RuntimeError("The selected window was closed.")
        frame = self._capture_frame()
        self._latest_frame = frame
        self._update_size(frame)
        height, width = frame.shape[:2]
        left = max(0, min(width, box.left))
        top = max(0, min(height, box.top))
        right = max(left, min(width, box.right))
        bottom = max(top, min(height, box.bottom))
        if right <= left or bottom <= top:
            raise RuntimeError("The selected region is outside the captured window.")
        return frame[top:bottom, left:right].copy()

    def close(self) -> None:
        self._closed = True
        self._latest_frame = None
        self._screencapturekit_source = None


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


def create_window_capture(
    *,
    title: str,
    process_name: str,
    class_name: str,
    monitor_index: int = 1,
):
    capture_type = {
        "win32": WindowsGraphicsCapture,
        "darwin": MacOSWindowCapture,
    }.get(sys.platform)
    if capture_type is None and sys.platform.startswith("linux"):
        capture_type = X11WindowCapture
    if capture_type is None:
        raise RuntimeError(window_capture_unavailable_reason())
    return capture_type(
        title=title,
        process_name=process_name,
        class_name=class_name,
        monitor_index=monitor_index,
    )
