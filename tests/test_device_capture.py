from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts import capture as capture_module
from droid_alerts.capture import PixelBox
from droid_alerts.belt.worker import run_belt_worker_process
from droid_alerts.config import AppConfig
from droid_alerts.device_capture import (
    CaptureDeviceDescriptor,
    DeviceCaptureSession,
    list_capture_devices,
    resolve_capture_devices,
)


def device(
    index: int,
    backend: int,
    *,
    name: str = "USB Capture HDMI",
    path: str = r"\\?\usb#vid_1234&pid_5678#card",
) -> CaptureDeviceDescriptor:
    return CaptureDeviceDescriptor(index, backend, name, path, 0x1234, 0x5678)


class DeviceSelectorTests(unittest.TestCase):
    def test_config_round_trip_preserves_device_selector(self):
        config = AppConfig.from_dict(
            {
                "capture_source": "device",
                "capture_device_name": " USB Capture HDMI ",
                "capture_device_path": r"\\?\usb#vid_1234&pid_5678#card",
                "capture_device_vid": 0x1234,
                "capture_device_pid": 0x5678,
                "capture_device_backend": cv2.CAP_MSMF,
            }
        )

        self.assertEqual("device", config.capture_source)
        self.assertEqual("USB Capture HDMI", config.capture_device_name)
        self.assertEqual("device", AppConfig.from_dict(config.to_dict()).capture_source)

    def test_device_source_without_identity_falls_back_to_monitor(self):
        self.assertEqual(
            "monitor",
            AppConfig.from_dict({"capture_source": "device"}).capture_source,
        )

    def test_device_list_deduplicates_backend_copies(self):
        with patch(
            "droid_alerts.device_capture._enumerated_devices",
            return_value=[device(0, cv2.CAP_MSMF), device(1, cv2.CAP_DSHOW)],
        ):
            devices = list_capture_devices()

        self.assertEqual(1, len(devices))
        self.assertEqual(cv2.CAP_MSMF, devices[0].backend)

    def test_resolver_uses_stable_path_and_prefers_saved_backend(self):
        candidates = [device(4, cv2.CAP_DSHOW), device(1, cv2.CAP_MSMF)]

        matches = resolve_capture_devices(
            name="USB Capture HDMI",
            path=candidates[0].path,
            vid=0x1234,
            pid=0x5678,
            preferred_backend=cv2.CAP_DSHOW,
            candidates=candidates,
        )

        self.assertEqual(cv2.CAP_DSHOW, matches[0].backend)
        self.assertEqual(4, matches[0].index)

    def test_ambiguous_name_only_selector_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Could not find"):
            resolve_capture_devices(
                name="USB Capture HDMI",
                path="",
                vid=None,
                pid=None,
                candidates=[
                    device(0, cv2.CAP_MSMF, path="device-a"),
                    device(1, cv2.CAP_MSMF, path="device-b"),
                ],
            )

    def test_capture_factory_routes_device_metadata(self):
        sentinel = object()
        with patch(
            "droid_alerts.device_capture.DeviceCaptureSession",
            return_value=sentinel,
        ) as factory:
            result = capture_module.create_capture(
                monitor_index=2,
                capture_source="device",
                device_name="USB Capture HDMI",
                device_path="device-path",
                device_vid=1,
                device_pid=2,
                device_backend=cv2.CAP_MSMF,
            )

        self.assertIs(sentinel, result)
        factory.assert_called_once_with(
            name="USB Capture HDMI",
            path="device-path",
            vid=1,
            pid=2,
            preferred_backend=cv2.CAP_MSMF,
            monitor_index=2,
        )


class FakeVideoCapture:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.released = False

    def read(self):
        time.sleep(0.005)
        return (not self.released), self.frame.copy()

    def release(self):
        self.released = True


class SharedDeviceSessionTests(unittest.TestCase):
    def test_one_reader_serves_independent_chat_and_belt_crops(self):
        frame = np.arange(80 * 120 * 3, dtype=np.uint8).reshape((80, 120, 3))
        capture = FakeVideoCapture(frame)
        descriptor = device(0, cv2.CAP_MSMF)
        with patch(
            "droid_alerts.device_capture._open_device",
            return_value=(capture, descriptor, frame.copy()),
        ):
            session = DeviceCaptureSession(
                name=descriptor.name,
                path=descriptor.path,
                vid=descriptor.vid,
                pid=descriptor.pid,
                preferred_backend=descriptor.backend,
                monitor_index=1,
            )
            try:
                self.assertEqual((120, 80), session.screen_size())
                chat = session.client().grab(PixelBox(0, 10, 30, 20))
                belt = session.client().grab(PixelBox(40, 5, 50, 35))
            finally:
                session.close()

        self.assertEqual((20, 30, 3), chat.shape)
        self.assertEqual((35, 50, 3), belt.shape)
        np.testing.assert_array_equal(frame[10:30, 0:30], chat)
        np.testing.assert_array_equal(frame[5:40, 40:90], belt)
        self.assertTrue(capture.released)

    def test_belt_worker_receives_the_gui_shared_device(self):
        received = {}
        status_queue = type("Queue", (), {"put": lambda self, _event: None})()
        shared_spec = object()

        def fake_watcher(*_args, **kwargs):
            received.update(kwargs)

        with patch("droid_alerts.belt.worker.run_belt_watcher", side_effect=fake_watcher):
            run_belt_worker_process(
                1,
                PixelBox(0, 0, 500, 300),
                {},
                threading.Event(),
                status_queue,
                capture_source="device",
                device_name="USB Capture HDMI",
                device_path="device-path",
                device_vid=1,
                device_pid=2,
                device_backend=cv2.CAP_MSMF,
                shared_device_spec=shared_spec,
            )

        self.assertEqual("device", received["capture_source"])
        self.assertEqual("USB Capture HDMI", received["device_name"])
        self.assertIs(shared_spec, received["shared_device_spec"])


if __name__ == "__main__":
    unittest.main()
