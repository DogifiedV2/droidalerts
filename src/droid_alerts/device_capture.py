from __future__ import annotations

import ctypes
import hashlib
import multiprocessing
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .capture import MonitorInfo, PixelBox, list_monitors


FRAME_WAIT_SECONDS = 5.0
RECONNECT_DELAY_SECONDS = 1.0
MAX_FRAME_WIDTH = 1920
MAX_FRAME_HEIGHT = 1080
MAX_FRAME_BYTES = MAX_FRAME_WIDTH * MAX_FRAME_HEIGHT * 3


@dataclass(frozen=True)
class CaptureDeviceDescriptor:
    index: int
    backend: int
    name: str
    path: str = ""
    vid: int | None = None
    pid: int | None = None

    @property
    def key(self) -> str:
        identity = self.path or f"{self.vid}:{self.pid}:{self.name.casefold()}"
        digest = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:24]
        return f"device:{digest}"

    @property
    def backend_name(self) -> str:
        try:
            return cv2.videoio_registry.getBackendName(self.backend)
        except Exception:
            return str(self.backend)


def _normalized(value: object) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split()).casefold()


def device_capture_key(*, name: str, path: str, vid: int | None, pid: int | None) -> str:
    identity = path or f"{vid}:{pid}:{name.casefold()}"
    digest = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"device:{digest}"


def _enumerated_devices() -> list[CaptureDeviceDescriptor]:
    try:
        from cv2_enumerate_cameras import enumerate_cameras
    except Exception as exc:
        raise RuntimeError(
            "Capture-device support is missing. Reinstall Droid Alerts or run "
            "pip install -r requirements.txt."
        ) from exc

    if sys.platform == "win32":
        backends = (cv2.CAP_MSMF, cv2.CAP_DSHOW)
    elif sys.platform == "darwin":
        backends = (cv2.CAP_AVFOUNDATION,)
    elif sys.platform.startswith("linux"):
        backends = (cv2.CAP_V4L2, cv2.CAP_GSTREAMER)
    else:
        return []

    devices: list[CaptureDeviceDescriptor] = []
    errors: list[str] = []
    for backend in backends:
        try:
            cameras = enumerate_cameras(backend)
        except Exception as exc:
            errors.append(str(exc))
            continue
        for camera in cameras:
            devices.append(
                CaptureDeviceDescriptor(
                    index=int(camera.index),
                    backend=int(camera.backend),
                    name=" ".join(str(camera.name or "Video capture device").split()),
                    path=" ".join(str(camera.path or "").split()),
                    vid=int(camera.vid) if camera.vid is not None else None,
                    pid=int(camera.pid) if camera.pid is not None else None,
                )
            )
    if not devices and errors:
        raise RuntimeError(f"Video capture devices could not be listed: {errors[0]}")
    return devices


def _identity(device: CaptureDeviceDescriptor) -> tuple[object, ...]:
    path = _normalized(device.path)
    if path:
        return ("path", path)
    if device.vid is not None and device.pid is not None:
        return ("usb", device.vid, device.pid, _normalized(device.name))
    return ("name", _normalized(device.name))


def list_capture_devices() -> list[CaptureDeviceDescriptor]:
    """List each video device once, preferring the native platform backend."""
    result: list[CaptureDeviceDescriptor] = []
    seen: set[tuple[object, ...]] = set()
    for device in _enumerated_devices():
        identity = _identity(device)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(device)
    return sorted(result, key=lambda item: item.name.casefold())


def _backend_priority(backend: int) -> int:
    if sys.platform == "win32":
        order = (cv2.CAP_MSMF, cv2.CAP_DSHOW)
    elif sys.platform == "darwin":
        order = (cv2.CAP_AVFOUNDATION,)
    else:
        order = (cv2.CAP_V4L2, cv2.CAP_GSTREAMER)
    try:
        return order.index(backend)
    except ValueError:
        return len(order)


def resolve_capture_devices(
    *,
    name: str,
    path: str,
    vid: int | None,
    pid: int | None,
    preferred_backend: int = 0,
    candidates: list[CaptureDeviceDescriptor] | None = None,
) -> list[CaptureDeviceDescriptor]:
    """Resolve a saved selector to backend/index candidates without trusting the index."""
    candidates = _enumerated_devices() if candidates is None else list(candidates)
    wanted_path = _normalized(path)
    wanted_name = _normalized(name)

    matches: list[CaptureDeviceDescriptor] = []
    if wanted_path:
        matches = [item for item in candidates if _normalized(item.path) == wanted_path]
        if matches and vid is not None and pid is not None:
            equivalent = [
                item
                for item in candidates
                if item.vid == vid
                and item.pid == pid
                and (not wanted_name or _normalized(item.name) == wanted_name)
            ]
            if len({_identity(item) for item in equivalent}) <= 2:
                matches.extend(item for item in equivalent if item not in matches)
    if not matches and vid is not None and pid is not None:
        matches = [
            item
            for item in candidates
            if item.vid == vid and item.pid == pid and (
                not wanted_name or _normalized(item.name) == wanted_name
            )
        ]
    if not matches and wanted_name:
        named = [item for item in candidates if _normalized(item.name) == wanted_name]
        identities = {_identity(item) for item in named}
        if len(identities) == 1:
            matches = named

    if not matches:
        label = name.strip() or "the selected capture device"
        raise RuntimeError(
            f'Could not find "{label}". Reconnect it, then select the capture device again.'
        )

    return sorted(
        matches,
        key=lambda item: (
            item.backend != preferred_backend,
            _backend_priority(item.backend),
            item.index,
        ),
    )


def _fit_frame(frame: np.ndarray) -> np.ndarray:
    if frame.ndim != 3 or frame.shape[2] < 3:
        raise RuntimeError("The capture device returned an unsupported frame format.")
    frame = np.ascontiguousarray(frame[:, :, :3])
    height, width = frame.shape[:2]
    scale = min(1.0, MAX_FRAME_WIDTH / max(1, width), MAX_FRAME_HEIGHT / max(1, height))
    if scale < 1.0:
        frame = cv2.resize(
            frame,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return np.ascontiguousarray(frame)


def _open_device(
    *,
    name: str,
    path: str,
    vid: int | None,
    pid: int | None,
    preferred_backend: int,
) -> tuple[Any, CaptureDeviceDescriptor, np.ndarray]:
    candidates = resolve_capture_devices(
        name=name,
        path=path,
        vid=vid,
        pid=pid,
        preferred_backend=preferred_backend,
    )
    failures: list[str] = []
    for device in candidates:
        capture = cv2.VideoCapture(device.index, device.backend)
        keep_open = False
        try:
            if not capture.isOpened():
                failures.append(f"{device.backend_name} could not open the device")
                continue
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            capture.set(cv2.CAP_PROP_FPS, 30)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            deadline = time.monotonic() + FRAME_WAIT_SECONDS
            while time.monotonic() < deadline:
                ok, frame = capture.read()
                if ok and frame is not None and frame.size:
                    keep_open = True
                    return capture, device, _fit_frame(frame)
                time.sleep(0.05)
            failures.append(f"{device.backend_name} returned no video frame")
        except Exception as exc:
            failures.append(str(exc))
        finally:
            if not keep_open and capture is not None:
                capture.release()
    detail = failures[0] if failures else "no compatible video backend was available"
    raise RuntimeError(
        f"The capture device could not start: {detail}. Close OBS or other capture software and try again."
    )


def _physical_monitor(monitor_index: int) -> MonitorInfo:
    try:
        descriptor = next(
            (item for item in list_monitors() if item.index == monitor_index),
            None,
        )
    except Exception:
        descriptor = None
    if descriptor is None:
        return MonitorInfo(0, 0, 1920, 1080, max(1, monitor_index), f"monitor-{monitor_index}")
    return MonitorInfo(
        descriptor.left,
        descriptor.top,
        descriptor.width,
        descriptor.height,
        descriptor.index,
        descriptor.key,
        descriptor.name,
    )


@dataclass
class SharedDeviceCaptureSpec:
    frame: Any
    lock: Any
    width: Any
    height: Any
    sequence: Any
    state: Any
    error: Any
    changed: Any
    monitor_index: int
    device_key: str
    device_name: str


def _read_error(spec: SharedDeviceCaptureSpec) -> str:
    with spec.lock:
        raw = bytes(spec.error)
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def _write_error(spec: SharedDeviceCaptureSpec, message: str) -> None:
    encoded = str(message).encode("utf-8", errors="replace")[:511]
    with spec.lock:
        spec.error[:] = b"\x00" * len(spec.error)
        spec.error[: len(encoded)] = encoded


class SharedDeviceCaptureClient:
    def __init__(self, spec: SharedDeviceCaptureSpec) -> None:
        self.spec = spec
        self.monitor = _physical_monitor(spec.monitor_index)
        self.capture_area = MonitorInfo(
            left=self.monitor.left,
            top=self.monitor.top,
            width=0,
            height=0,
            index=self.monitor.index,
            key=spec.device_key,
            name=spec.device_name,
        )

    def screen_size(self) -> tuple[int, int]:
        deadline = time.monotonic() + FRAME_WAIT_SECONDS
        while time.monotonic() < deadline:
            with self.spec.lock:
                state = int(self.spec.state.value)
                width = int(self.spec.width.value)
                height = int(self.spec.height.value)
            if state == 1 and width > 0 and height > 0:
                self.capture_area.width = width
                self.capture_area.height = height
                return width, height
            if state in {2, 3}:
                raise RuntimeError(_read_error(self.spec) or "The capture device is unavailable.")
            self.spec.changed.wait(0.05)
        raise RuntimeError("The capture device did not provide a video frame.")

    def grab(self, box: PixelBox) -> np.ndarray:
        self.screen_size()
        with self.spec.lock:
            if int(self.spec.state.value) != 1:
                raise RuntimeError(_read_error(self.spec) or "The capture device is unavailable.")
            width = int(self.spec.width.value)
            height = int(self.spec.height.value)
            left = max(0, min(width, box.left))
            top = max(0, min(height, box.top))
            right = max(left, min(width, box.right))
            bottom = max(top, min(height, box.bottom))
            if right <= left or bottom <= top:
                raise RuntimeError("The selected region is outside the capture-device frame.")
            raw = np.frombuffer(self.spec.frame, dtype=np.uint8, count=width * height * 3)
            frame = raw.reshape((height, width, 3))
            return frame[top:bottom, left:right].copy()

    def close(self) -> None:
        # The GUI-owned session outlives individual watcher clients.
        return


class DeviceCaptureSession:
    """Own one capture card and publish its latest frame to both watcher processes."""

    def __init__(
        self,
        *,
        name: str,
        path: str,
        vid: int | None,
        pid: int | None,
        preferred_backend: int,
        monitor_index: int,
        context: Any | None = None,
    ) -> None:
        context = context or multiprocessing.get_context("spawn")
        self.selector = (name, path, vid, pid, int(preferred_backend), int(monitor_index))
        self.spec = SharedDeviceCaptureSpec(
            frame=context.RawArray(ctypes.c_ubyte, MAX_FRAME_BYTES),
            lock=context.RLock(),
            width=context.RawValue(ctypes.c_int, 0),
            height=context.RawValue(ctypes.c_int, 0),
            sequence=context.RawValue(ctypes.c_ulonglong, 0),
            state=context.RawValue(ctypes.c_int, 0),
            error=context.RawArray(ctypes.c_ubyte, 512),
            changed=context.Event(),
            monitor_index=max(1, int(monitor_index)),
            device_key=device_capture_key(name=name, path=path, vid=vid, pid=pid),
            device_name=name or "Capture device",
        )
        self._stop = threading.Event()
        self._capture = None
        self._thread = threading.Thread(
            target=self._run,
            name="DroidAlertsCaptureDevice",
            daemon=True,
        )
        self._thread.start()

    def matches(self, *, name: str, path: str, vid: int | None, pid: int | None, preferred_backend: int, monitor_index: int) -> bool:
        return self.selector == (name, path, vid, pid, int(preferred_backend), int(monitor_index))

    def client(self) -> SharedDeviceCaptureClient:
        return SharedDeviceCaptureClient(self.spec)

    @property
    def monitor(self) -> MonitorInfo:
        return self.client().monitor

    @property
    def capture_area(self) -> MonitorInfo:
        client = self.client()
        try:
            client.screen_size()
        except Exception:
            pass
        return client.capture_area

    def screen_size(self) -> tuple[int, int]:
        return self.client().screen_size()

    def grab(self, box: PixelBox) -> np.ndarray:
        return self.client().grab(box)

    def _set_state(self, state: int, error: str = "") -> None:
        _write_error(self.spec, error)
        with self.spec.lock:
            self.spec.state.value = state
        self.spec.changed.set()

    def _publish(self, frame: np.ndarray) -> None:
        frame = _fit_frame(frame)
        height, width = frame.shape[:2]
        data = frame.reshape(-1)
        with self.spec.lock:
            target = np.frombuffer(self.spec.frame, dtype=np.uint8, count=data.size)
            target[:] = data
            self.spec.width.value = width
            self.spec.height.value = height
            self.spec.sequence.value += 1
            self.spec.state.value = 1
            self.spec.error[:] = b"\x00" * len(self.spec.error)
        self.spec.changed.set()

    def _run(self) -> None:
        name, path, vid, pid, preferred_backend, _monitor_index = self.selector
        while not self._stop.is_set():
            capture = None
            try:
                capture, _device, first_frame = _open_device(
                    name=name,
                    path=path,
                    vid=vid,
                    pid=pid,
                    preferred_backend=preferred_backend,
                )
                self._capture = capture
                self._publish(first_frame)
                failed_reads = 0
                while not self._stop.is_set():
                    ok, frame = capture.read()
                    if not ok or frame is None or not frame.size:
                        failed_reads += 1
                        if failed_reads >= 5:
                            raise RuntimeError("The capture device stopped providing video.")
                        time.sleep(0.05)
                        continue
                    failed_reads = 0
                    self._publish(frame)
            except Exception as exc:
                if not self._stop.is_set():
                    self._set_state(2, str(exc))
                    self._stop.wait(RECONNECT_DELAY_SECONDS)
            finally:
                self._capture = None
                if capture is not None:
                    try:
                        capture.release()
                    except Exception:
                        pass
        self._set_state(3, "The capture device was closed.")

    def close(self) -> None:
        self._stop.set()
        capture = self._capture
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass
        self._thread.join(timeout=1.0)


def session_from_config(config: Any, *, context: Any | None = None) -> DeviceCaptureSession:
    return DeviceCaptureSession(
        name=config.capture_device_name,
        path=config.capture_device_path,
        vid=config.capture_device_vid,
        pid=config.capture_device_pid,
        preferred_backend=config.capture_device_backend,
        monitor_index=config.monitor_index,
        context=context,
    )
