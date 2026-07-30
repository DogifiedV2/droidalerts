from __future__ import annotations

import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from ..config import load_config, save_config
from ..device_capture import DeviceCaptureSession
from ..telemetry import AnonymousAppTelemetryClient
from ..timer_sync import start_timer_schedule_sync
from .dialogs import DialogController
from .state import UiDispatcher


class ApplicationRuntime(QObject):
    """Owns shared application services and configuration."""

    configChanged = Signal()
    detailChanged = Signal(str)

    def __init__(self, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = load_config()
        self.dialogs = DialogController(parent=self)
        self.dispatcher = UiDispatcher(parent=self)
        self.main_window = None
        self.device_capture_session: DeviceCaptureSession | None = None
        self._closed = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self.save_now)
        self._shutdown_callbacks: list[Callable[[], None]] = []
        self.app_telemetry = AnonymousAppTelemetryClient(self.config)

    def start(self) -> None:
        self.app_telemetry.start()
        start_timer_schedule_sync(self.config.timer_schedule_url)

    def register_shutdown(self, callback: Callable[[], None]) -> None:
        self._shutdown_callbacks.append(callback)

    def update_config(
        self,
        *,
        save: bool = True,
        announce: bool = True,
        **changes: Any,
    ) -> None:
        changed = False
        for key, value in changes.items():
            if not hasattr(self.config, key):
                raise AttributeError(f"Unknown setting: {key}")
            if getattr(self.config, key) != value:
                setattr(self.config, key, value)
                changed = True
        if not changed:
            return
        self.configChanged.emit()
        if save:
            self._save_timer.start()
            if announce:
                self.detailChanged.emit("Settings save automatically")

    def save_now(self) -> None:
        if self._closed:
            return
        save_config(self.config)
        self.detailChanged.emit("Settings saved")

    def run_background(
        self,
        work: Callable[[], Any],
        done: Callable[[Any, BaseException | None], None],
        *,
        name: str,
    ) -> threading.Thread:
        def runner() -> None:
            try:
                result = work()
            except BaseException as exc:
                self.dispatcher.post(lambda error=exc: done(None, error))
            else:
                self.dispatcher.post(lambda value=result: done(value, None))

        thread = threading.Thread(target=runner, name=name, daemon=True)
        thread.start()
        return thread

    @staticmethod
    def open_url(url: str) -> None:
        webbrowser.open(url)

    @staticmethod
    def open_path(path: Path) -> bool:
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True) if not target.suffix else None
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(target)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError:
            return False
        return True

    def close_device_capture(self, *, force: bool = False) -> None:
        session = self.device_capture_session
        if session is None:
            return
        if not force:
            return
        self.device_capture_session = None
        try:
            session.close()
        except Exception:
            pass

    def shutdown(self) -> None:
        if self._closed:
            return
        if self._save_timer.isActive():
            self._save_timer.stop()
            save_config(self.config)
        self._closed = True
        self.dispatcher.close()
        for callback in reversed(self._shutdown_callbacks):
            try:
                callback()
            except Exception as exc:
                print(f"[GUI] Shutdown callback failed: {exc}")
        self.close_device_capture(force=True)
        stop = getattr(self.app_telemetry, "stop", None)
        if callable(stop):
            stop()
