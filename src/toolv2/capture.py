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


class CaptureBackend(Protocol):
    def screen_size(self) -> tuple[int, int]: ...

    def grab(self, box: PixelBox) -> np.ndarray: ...

    def close(self) -> None: ...


class MSSCapture:
    def __init__(self, monitor_index: int = 1) -> None:
        import mss

        self._mss = mss.mss()
        self.monitor_index = monitor_index
        if monitor_index < 1 or monitor_index >= len(self._mss.monitors):
            self.monitor_index = 1
        mon = self._mss.monitors[self.monitor_index]
        self.monitor = MonitorInfo(
            left=int(mon["left"]),
            top=int(mon["top"]),
            width=int(mon["width"]),
            height=int(mon["height"]),
        )

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

    def screen_size(self) -> tuple[int, int]:
        return self._screen_size

    def grab(self, box: PixelBox) -> np.ndarray:
        frame = self._camera.grab(region=(box.left, box.top, box.right, box.bottom))
        if frame is None:
            raise RuntimeError("DXcam returned no frame")
        return frame

    def close(self) -> None:
        self._camera.release()


def create_capture(monitor_index: int = 1, prefer_dxcam: bool = True) -> CaptureBackend:
    if prefer_dxcam:
        try:
            return DXCamCapture(monitor_index=monitor_index)
        except Exception:
            pass
    return MSSCapture(monitor_index=monitor_index)
