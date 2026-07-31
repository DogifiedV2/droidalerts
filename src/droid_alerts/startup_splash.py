from __future__ import annotations

import threading
import sys
from collections.abc import Callable
from types import TracebackType
from typing import TypeVar

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from . import __version__
from .platform_ui import set_dpi_awareness


_T = TypeVar("_T")


class _SplashWindow(QWidget):
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#080d13"))
        painter.setPen(QPen(QColor("#39c6d8"), 1))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 12, 12)


class StartupSplash:
    """Shows startup progress while the QML app loads."""

    def __init__(
        self,
        app: QApplication,
        window: QWidget,
        status_label: QLabel,
    ) -> None:
        self.app = app
        self.root = app
        self.window = window
        self.status_label = status_label

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.app.processEvents()

    def lift(self) -> None:
        self.window.show()
        self.window.raise_()
        self.app.processEvents()

    def run_task(self, callback: Callable[[], _T]) -> _T:
        done = threading.Event()
        result: list[_T] = []
        failure: list[tuple[BaseException, TracebackType | None]] = []

        def worker() -> None:
            try:
                result.append(callback())
            except BaseException as exc:
                failure.append((exc, exc.__traceback__))
            finally:
                done.set()

        thread = threading.Thread(target=worker, name="startup-loader", daemon=True)
        thread.start()
        while not done.wait(0.015):
            self.app.processEvents()
        thread.join()
        if failure:
            exc, traceback = failure[0]
            raise exc.with_traceback(traceback)
        return result[0]

    def close(self) -> None:
        self.window.close()
        self.app.processEvents()

    def destroy(self) -> None:
        self.close()


def create_startup_splash() -> StartupSplash | None:
    set_dpi_awareness()
    try:
        if QApplication.instance() is None:
            QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
        app = QApplication.instance() or QApplication([])
        window = _SplashWindow()
        window.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        window.setFixedSize(430, 184)

        layout = QVBoxLayout(window)
        layout.setContentsMargins(29, 24, 29, 24)
        layout.setSpacing(0)
        family = "Segoe UI" if sys.platform == "win32" else "Avenir Next"
        title = QLabel("DROID ALERTS")
        title_palette = title.palette()
        title_palette.setColor(QPalette.ColorRole.WindowText, QColor("#e9f1f7"))
        title.setPalette(title_palette)
        title.setFont(QFont(family, 19, QFont.Weight.Bold))
        layout.addWidget(title)

        version = QLabel(f"v{__version__}")
        version_palette = version.palette()
        version_palette.setColor(QPalette.ColorRole.WindowText, QColor("#8ba0ae"))
        version.setPalette(version_palette)
        version.setFont(QFont(family, 9))
        layout.addWidget(version)
        layout.addStretch(1)

        status = QLabel("Loading tracker…")
        status_palette = status.palette()
        status_palette.setColor(QPalette.ColorRole.WindowText, QColor("#39c6d8"))
        status.setPalette(status_palette)
        status.setFont(QFont(family, 10, QFont.Weight.DemiBold))
        layout.addWidget(status)

        progress = QWidget()
        progress.setFixedHeight(3)
        progress.setAutoFillBackground(True)
        progress_palette = progress.palette()
        progress_palette.setColor(QPalette.ColorRole.Window, QColor("#39c6d8"))
        progress.setPalette(progress_palette)
        layout.addSpacing(12)
        layout.addWidget(progress)

        screen = app.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            window.move(
                area.left() + (area.width() - window.width()) // 2,
                area.top() + (area.height() - window.height()) // 2,
            )
        window.show()
        window.raise_()
        app.processEvents()
        return StartupSplash(app, window, status)
    except Exception:
        return None


__all__ = ["StartupSplash", "create_startup_splash"]
