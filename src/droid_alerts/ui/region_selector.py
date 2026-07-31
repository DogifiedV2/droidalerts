from __future__ import annotations

import sys
from collections.abc import Callable

import cv2
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..capture import MonitorInfo, PixelBox, create_capture
from ..qt_images import bgr_to_qimage


class RegionSelector(QWidget):
    """Qt fullscreen frozen-frame selector used by Belt and chat calibration."""

    def __init__(
        self,
        monitor: MonitorInfo,
        on_selected: Callable[[PixelBox], None],
        *,
        on_cancelled: Callable[[], None] | None = None,
        capture=None,
        display_monitor: MonitorInfo | None = None,
        title: str = "Drag around the blueprint belt",
        minimum_size: tuple[int, int] = (100, 50),
    ) -> None:
        super().__init__(None)
        self.monitor = monitor
        self._on_selected = on_selected
        self._on_cancelled = on_cancelled
        self._title = title
        self._minimum_size = minimum_size
        self._start: QPoint | None = None
        self._selection = QRect()
        self._finished = False
        display_monitor = display_monitor or monitor
        capture = capture or create_capture(monitor.index, prefer_dxcam=False)
        try:
            frame = capture.grab(PixelBox(0, 0, monitor.width, monitor.height))
        finally:
            try:
                capture.close()
            except Exception:
                pass
        scale = min(
            display_monitor.width / max(1, monitor.width),
            display_monitor.height / max(1, monitor.height),
        )
        display_width = max(1, round(monitor.width * scale))
        display_height = max(1, round(monitor.height * scale))
        if (display_width, display_height) != (monitor.width, monitor.height):
            frame = cv2.resize(
                frame,
                (display_width, display_height),
                interpolation=cv2.INTER_AREA,
            )
        self._source_scale_x = monitor.width / display_width
        self._source_scale_y = monitor.height / display_height
        self._image = bgr_to_qimage(frame)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(
            display_monitor.left + max(0, (display_monitor.width - display_width) // 2),
            display_monitor.top + max(0, (display_monitor.height - display_height) // 2),
            display_width,
            display_height,
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.drawImage(self.rect(), self._image)
        painter.fillRect(QRect(14, 14, min(630, self.width() - 28), 50), QColor("#d907111f"))
        painter.setPen(QPen(QColor("#39c6d8"), 2))
        painter.drawRoundedRect(QRect(14, 14, min(630, self.width() - 28), 50), 8, 8)
        painter.setFont(
            QFont(
                "Segoe UI" if sys.platform == "win32" else "Avenir Next",
                13,
                QFont.Weight.DemiBold,
            )
        )
        painter.setPen(QColor("#e9f1f7"))
        painter.drawText(
            QRect(28, 14, max(120, self.width() - 180), 50),
            Qt.AlignmentFlag.AlignVCenter,
            f"{self._title} · Enter saves · Esc cancels",
        )
        if not self._selection.isNull():
            painter.setPen(QPen(QColor("#39c6d8"), 4))
            painter.setBrush(QColor("#2239c6d8"))
            painter.drawRect(self._selection)
            painter.setFont(
                QFont("Cascadia Code" if sys.platform == "win32" else "Menlo", 10)
            )
            painter.setPen(QColor("#e9f1f7"))
            painter.drawText(
                self._selection.adjusted(8, 8, -8, -8),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                f"{self._selection.width()} × {self._selection.height()}",
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._start = event.position().toPoint()
        self._selection = QRect(self._start, self._start)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._start is None:
            return
        self._selection = QRect(self._start, event.position().toPoint()).normalized()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._start is not None:
            self._selection = QRect(self._start, event.position().toPoint()).normalized()
            self._start = None
            self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_S):
            self.save()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            return
        super().keyPressEvent(event)

    def save(self) -> None:
        if (
            self._selection.width() < self._minimum_size[0]
            or self._selection.height() < self._minimum_size[1]
        ):
            return
        box = PixelBox(
            round(self._selection.left() * self._source_scale_x),
            round(self._selection.top() * self._source_scale_y),
            max(1, round(self._selection.width() * self._source_scale_x)),
            max(1, round(self._selection.height() * self._source_scale_y)),
        )
        self._finished = True
        self.close()
        self._on_selected(box)

    def cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.close()
        if self._on_cancelled is not None:
            self._on_cancelled()

    def closeEvent(self, event) -> None:
        cancelled = not self._finished
        if cancelled:
            self._finished = True
        super().closeEvent(event)
        if cancelled and self._on_cancelled is not None:
            self._on_cancelled()
