from __future__ import annotations

import sys
import time

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .capture import monitor_key_from_mapping, set_dpi_awareness
from .qt_images import bgr_to_qimage
from .region import Calibration, calibration_path


MIN_SIZE = 20


def capture_virtual_screen() -> tuple[np.ndarray, dict[str, int], list[dict[str, object]]]:
    import mss

    with mss.mss() as sct:
        virtual = dict(sct.monitors[0])
        monitors = [dict(monitor) for monitor in sct.monitors[1:]]
        shot = sct.grab(virtual)
    image = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    return (
        image,
        {key: int(virtual[key]) for key in ("left", "top", "width", "height")},
        monitors,
    )


def _monitor_for_region(
    region: dict[str, int],
    monitors: list[dict[str, object]],
) -> dict[str, object]:
    cx = region["left"] + region["width"] / 2
    cy = region["top"] + region["height"] / 2
    for monitor in monitors:
        left, top = int(monitor["left"]), int(monitor["top"])
        if left <= cx < left + int(monitor["width"]) and top <= cy < top + int(monitor["height"]):
            return monitor
    return max(monitors, key=lambda item: int(item["width"]) * int(item["height"]))


class RegionSelector(QWidget):
    """Frozen virtual-desktop selector for the standalone calibrate command."""

    def __init__(self, *, capture_delay: float = 0.0) -> None:
        super().__init__(None)
        if capture_delay > 0:
            QApplication.processEvents()
            time.sleep(capture_delay)
        self.full_image, self.virtual, self.monitors = capture_virtual_screen()
        if not self.monitors:
            self.monitors = [self.virtual]
        self._image = bgr_to_qimage(self.full_image)
        self.start: QPoint | None = None
        self.region: dict[str, int] | None = None
        self.saved = False
        self._message = ""

        self.setWindowTitle("Droid Alerts: Select Alert Region")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(
            self.virtual["left"],
            self.virtual["top"],
            self.virtual["width"],
            self.virtual["height"],
        )

    def _local_region(self) -> QRect:
        if self.region is None:
            return QRect()
        return QRect(
            self.region["left"] - self.virtual["left"],
            self.region["top"] - self.virtual["top"],
            self.region["width"],
            self.region["height"],
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.drawImage(self.rect(), self._image)
        panel = QRect(18, 18, min(self.width() - 36, 970), 78)
        painter.fillRect(panel, QColor("#e6080d13"))
        painter.setPen(QPen(QColor("#39c6d8"), 1))
        painter.drawRoundedRect(panel, 9, 9)
        painter.setFont(
            QFont(
                "Segoe UI" if sys.platform == "win32" else "Avenir Next",
                13,
                QFont.Weight.DemiBold,
            )
        )
        painter.setPen(QColor("#e9f1f7"))
        painter.drawText(
            panel.adjusted(16, 8, -16, -38),
            Qt.AlignmentFlag.AlignVCenter,
            "Drag around 4–5 droid alert rows · Enter/S saves · Esc cancels",
        )
        painter.setFont(
            QFont("Cascadia Code" if sys.platform == "win32" else "Menlo", 10)
        )
        painter.setPen(QColor("#ef6672" if self._message else "#8ba0ae"))
        detail = self._message
        if not detail and self.region is not None:
            detail = (
                f"left={self.region['left']}  top={self.region['top']}  "
                f"width={self.region['width']}  height={self.region['height']} · "
                "Arrows move · Shift+Arrows resize · Ctrl = 10 px"
            )
        painter.drawText(
            panel.adjusted(16, 40, -16, -8),
            Qt.AlignmentFlag.AlignVCenter,
            detail or "Select the chat region to continue.",
        )
        selected = self._local_region()
        if not selected.isNull():
            painter.setPen(QPen(QColor("#39c6d8"), 4))
            painter.setBrush(QColor("#2239c6d8"))
            painter.drawRect(selected)

    def _global_point(self, event: QMouseEvent) -> QPoint:
        point = event.position().toPoint()
        return QPoint(
            point.x() + self.virtual["left"],
            point.y() + self.virtual["top"],
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.start = self._global_point(event)
        self._set_region(self.start, self.start)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.start is not None:
            self._set_region(self.start, self._global_point(event))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.start is not None:
            self._set_region(self.start, self._global_point(event))
            self.start = None

    def _set_region(self, start: QPoint, end: QPoint) -> None:
        self.region = {
            "left": min(start.x(), end.x()),
            "top": min(start.y(), end.y()),
            "width": abs(end.x() - start.x()),
            "height": abs(end.y() - start.y()),
        }
        self._message = ""
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_S):
            self.save()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            return
        directions = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        direction = directions.get(event.key())
        if direction is not None:
            self.nudge(*direction, event)
            return
        super().keyPressEvent(event)

    def nudge(self, dx: int, dy: int, event: QKeyEvent) -> None:
        if self.region is None:
            return
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ControlModifier else 1
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.region["width"] = max(MIN_SIZE, self.region["width"] + dx * step)
            self.region["height"] = max(MIN_SIZE, self.region["height"] + dy * step)
        else:
            self.region["left"] += dx * step
            self.region["top"] += dy * step
        self.update()

    def save(self) -> None:
        if (
            self.region is None
            or self.region["width"] < MIN_SIZE
            or self.region["height"] < MIN_SIZE
        ):
            self._message = "Region too small. Drag a larger box."
            self.update()
            return
        monitor = _monitor_for_region(self.region, self.monitors)
        monitor_index = next(
            (
                index
                for index, candidate in enumerate(self.monitors, start=1)
                if candidate == monitor
            ),
            1,
        )
        mon_left, mon_top = int(monitor["left"]), int(monitor["top"])
        mon_width, mon_height = int(monitor["width"]), int(monitor["height"])
        calibration = Calibration(
            mode="manual",
            ratios={
                "left": (self.region["left"] - mon_left) / mon_width,
                "top": (self.region["top"] - mon_top) / mon_height,
                "width": self.region["width"] / mon_width,
                "height": self.region["height"] / mon_height,
            },
            monitor_signature={"width": mon_width, "height": mon_height},
        )
        calibration.save(monitor_key_from_mapping(monitor, monitor_index))
        print("Saved calibration (percent ratios are the source of truth):")
        print(f"  {calibration.to_dict()}")
        print(f"  -> {calibration_path()}")
        self.saved = True
        self.close()

    def cancel(self) -> None:
        print("Cancelled. Calibration unchanged.")
        self.close()

    def closeEvent(self, event) -> None:
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def run(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        QApplication.instance().exec()


def run_calibrate(*, capture_delay: float = 0.0, reset: bool = False) -> None:
    set_dpi_awareness()
    if reset:
        calibration_path().unlink(missing_ok=True)
        print("Calibration reset to auto region detection.")
        return
    app = QApplication.instance() or QApplication(sys.argv)
    RegionSelector(capture_delay=capture_delay).run()
    del app


__all__ = [
    "RegionSelector",
    "_monitor_for_region",
    "capture_virtual_screen",
    "run_calibrate",
]
