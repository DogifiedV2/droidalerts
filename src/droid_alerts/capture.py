from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np


def set_dpi_awareness() -> None:
    """Per-monitor DPI awareness so captured pixel coordinates match physical pixels."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


@dataclass(frozen=True)
class PixelBox:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass
class MonitorInfo:
    left: int
    top: int
    width: int
    height: int
    index: int = 1
    key: str = "monitor-1"
    name: str = ""


@dataclass(frozen=True)
class MonitorDescriptor:
    index: int
    left: int
    top: int
    width: int
    height: int
    is_primary: bool = False
    name: str = ""
    unique_id: str = ""

    @property
    def key(self) -> str:
        return monitor_key_from_mapping(
            {
                "left": self.left,
                "top": self.top,
                "width": self.width,
                "height": self.height,
                "name": self.name,
                "unique_id": self.unique_id,
            },
            self.index,
        )


def format_tk_geometry(
    *,
    x: int,
    y: int,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """Format an absolute Tk window geometry on the virtual desktop.

    Tk treats a bare ``-N`` position as an offset from the right or bottom
    edge. Prefixing each coordinate with ``+`` preserves negative virtual
    desktop coordinates as ``+-N``, which is required for monitors above or
    left of the primary display.
    """
    if (width is None) != (height is None):
        raise ValueError("width and height must be provided together")
    size = "" if width is None else f"{int(width)}x{int(height)}"
    return f"{size}+{int(x)}+{int(y)}"


def monitor_key_from_mapping(monitor: dict[str, object], _index: int) -> str:
    """Stable-enough key for per-display settings, with geometry fallback."""
    unique_id = str(monitor.get("unique_id") or "").strip()
    if unique_id:
        return f"id:{unique_id}"
    name = str(monitor.get("name") or monitor.get("output") or "").strip()
    geometry = "x".join(str(int(monitor.get(key, 0))) for key in ("width", "height"))
    position = ",".join(str(int(monitor.get(key, 0))) for key in ("left", "top"))
    return f"display:{name or 'unnamed'}:{geometry}@{position}"


def _mss_instance():
    import mss

    # MSS 10.2 introduced the public MSS class while retaining the old
    # lowercase factory for compatibility. Keep supporting the project's
    # mss>=9.0 requirement without producing a deprecation warning on newer
    # releases.
    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


def list_monitors() -> list[MonitorDescriptor]:
    """Return physical monitors in the same one-based order MSS captures."""
    sct = _mss_instance()
    try:
        raw_monitors = [dict(monitor) for monitor in sct.monitors[1:]]
        try:
            primary_monitor = dict(sct.primary_monitor)
        except Exception:
            primary_monitor = None
    finally:
        sct.close()

    def same_geometry(first: dict[str, object], second: dict[str, object]) -> bool:
        keys = ("left", "top", "width", "height")
        return all(int(first[key]) == int(second[key]) for key in keys)

    primary_index = next(
        (
            index
            for index, monitor in enumerate(raw_monitors, start=1)
            if bool(monitor.get("is_primary"))
        ),
        None,
    )
    if primary_index is None and primary_monitor is not None:
        primary_index = next(
            (
                index
                for index, monitor in enumerate(raw_monitors, start=1)
                if same_geometry(monitor, primary_monitor)
            ),
            None,
        )
    if primary_index is None:
        primary_index = next(
            (
                index
                for index, monitor in enumerate(raw_monitors, start=1)
                if int(monitor["left"]) == 0 and int(monitor["top"]) == 0
            ),
            1 if raw_monitors else None,
        )

    return [
        MonitorDescriptor(
            index=index,
            left=int(monitor["left"]),
            top=int(monitor["top"]),
            width=int(monitor["width"]),
            height=int(monitor["height"]),
            is_primary=index == primary_index,
            name=str(monitor.get("name") or monitor.get("output") or "").strip(),
            unique_id=str(monitor.get("unique_id") or "").strip(),
        )
        for index, monitor in enumerate(raw_monitors, start=1)
    ]


def format_monitor_label(
    monitor: MonitorDescriptor,
    primary: MonitorDescriptor | None = None,
) -> str:
    """Build a concise, game-style display label for a monitor picker."""
    label = f"Monitor {monitor.index}: {monitor.width} × {monitor.height}"
    monitor_name = monitor.name.strip()
    if monitor_name.casefold() not in {"", "generic pnp monitor"}:
        label += f" — {monitor_name}"

    position = ""
    if monitor.is_primary:
        position = "Primary"
    elif primary is not None:
        monitor_center_x = monitor.left + monitor.width / 2
        monitor_center_y = monitor.top + monitor.height / 2
        primary_center_x = primary.left + primary.width / 2
        primary_center_y = primary.top + primary.height / 2
        horizontal_distance = abs(monitor_center_x - primary_center_x)
        vertical_distance = abs(monitor_center_y - primary_center_y)
        if horizontal_distance >= vertical_distance:
            position = "Left" if monitor_center_x < primary_center_x else "Right"
        else:
            position = "Above" if monitor_center_y < primary_center_y else "Below"
    if position:
        label += f" ({position})"
    return label


class CaptureBackend(Protocol):
    monitor: MonitorInfo
    capture_area: MonitorInfo

    def screen_size(self) -> tuple[int, int]: ...

    def grab(self, box: PixelBox) -> np.ndarray: ...

    def close(self) -> None: ...


class MSSCapture:
    def __init__(self, monitor_index: int = 1) -> None:
        self._mss = _mss_instance()
        self.monitor_index = monitor_index
        if monitor_index < 1 or monitor_index >= len(self._mss.monitors):
            self.monitor_index = 1
        mon = self._mss.monitors[self.monitor_index]
        self.monitor = MonitorInfo(
            left=int(mon["left"]),
            top=int(mon["top"]),
            width=int(mon["width"]),
            height=int(mon["height"]),
            index=self.monitor_index,
            key=monitor_key_from_mapping(dict(mon), self.monitor_index),
            name=str(mon.get("name") or mon.get("output") or "").strip(),
        )
        self.capture_area = self.monitor

    def screen_size(self) -> tuple[int, int]:
        return (self.monitor.width, self.monitor.height)

    def grab(self, box: PixelBox) -> np.ndarray:
        region = {
            "left": self.monitor.left + box.left,
            "top": self.monitor.top + box.top,
            "width": box.width,
            "height": box.height,
        }
        shot = self._mss.grab(region)
        bgra = np.asarray(shot)
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)

    def close(self) -> None:
        self._mss.close()


class DXCamCapture:
    def __init__(self, monitor_index: int = 1) -> None:
        import dxcam

        output_idx = max(0, monitor_index - 1)
        self._camera = dxcam.create(output_idx=output_idx, output_color="BGR")
        if self._camera is None:
            raise RuntimeError("dxcam.create returned None")
        self._screen_size = self._camera.width, self._camera.height
        descriptor = next(
            (monitor for monitor in list_monitors() if monitor.index == monitor_index),
            None,
        )
        self.monitor = MonitorInfo(
            left=descriptor.left if descriptor is not None else 0,
            top=descriptor.top if descriptor is not None else 0,
            width=self._screen_size[0],
            height=self._screen_size[1],
            index=monitor_index,
            key=descriptor.key if descriptor is not None else f"dxcam:{monitor_index}",
            name=descriptor.name if descriptor is not None else "",
        )
        self.capture_area = self.monitor

    def screen_size(self) -> tuple[int, int]:
        return self._screen_size

    def grab(self, box: PixelBox) -> np.ndarray:
        frame = self._camera.grab(region=(box.left, box.top, box.right, box.bottom))
        if frame is None:
            raise RuntimeError("DXcam returned no frame")
        return frame

    def close(self) -> None:
        self._camera.release()


def create_capture(
    monitor_index: int = 1,
    prefer_dxcam: bool = True,
    *,
    capture_source: str = "monitor",
    window_title: str = "",
    window_process: str = "",
    window_class: str = "",
    device_name: str = "",
    device_path: str = "",
    device_vid: int | None = None,
    device_pid: int | None = None,
    device_backend: int = 0,
) -> CaptureBackend:
    source = str(capture_source).strip().lower()
    if source == "window":
        from .window_capture import WindowsGraphicsCapture

        return WindowsGraphicsCapture(
            title=window_title,
            process_name=window_process,
            class_name=window_class,
            monitor_index=monitor_index,
        )
    if source == "device":
        from .device_capture import DeviceCaptureSession

        return DeviceCaptureSession(
            name=device_name,
            path=device_path,
            vid=device_vid,
            pid=device_pid,
            preferred_backend=device_backend,
            monitor_index=monitor_index,
        )
    if prefer_dxcam:
        try:
            return DXCamCapture(monitor_index=monitor_index)
        except Exception:
            pass
    return MSSCapture(monitor_index=monitor_index)
