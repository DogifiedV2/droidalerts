from __future__ import annotations

import sys

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..capture import MonitorInfo, PixelBox
from ..overlay_window import OverlayTopmostGuard


OVERLAY_FLAGS = (
    Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
    | Qt.WindowType.Tool
    | Qt.WindowType.WindowTransparentForInput
    | Qt.WindowType.WindowDoesNotAcceptFocus
)


class RegionOutline(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self._label = ""
        self._color = QColor("#ef6672")
        self.setWindowFlags(OVERLAY_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._topmost_guard = OverlayTopmostGuard(self)

    def show_region(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
        label: str,
    ) -> None:
        self._label = label
        margin = 28
        self.setGeometry(left - 3, top - margin, width + 6, height + margin + 3)
        self.show()
        self._topmost_guard.refresh()
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self._color, 4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRect(3, 28, self.width() - 7, self.height() - 32))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        label_width = min(self.width() - 6, max(220, len(self._label) * 7 + 24))
        painter.drawRoundedRect(QRect(3, 2, label_width, 24), 5, 5)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(
            QFont(
                "Segoe UI" if sys.platform == "win32" else "Avenir Next",
                9,
                QFont.Weight.DemiBold,
            )
        )
        painter.drawText(
            QRect(11, 2, label_width - 16, 24),
            Qt.AlignmentFlag.AlignVCenter,
            self._label,
        )


class BeltTrackOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self._region = PixelBox(0, 0, 1, 1)
        self._tracks: list[dict[str, object]] = []
        self.setWindowFlags(OVERLAY_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._topmost_guard = OverlayTopmostGuard(self)

    def show_tracks(
        self,
        monitor: MonitorInfo | None,
        region: PixelBox,
        tracks: list[dict[str, object]],
    ) -> None:
        if monitor is None:
            self.hide()
            return
        self._region = region
        self._tracks = [dict(track) for track in tracks[:16]]
        label_height = 34
        self.setGeometry(
            monitor.left + region.left,
            monitor.top + region.top - label_height,
            region.width,
            region.height + label_height,
        )
        self.show()
        self._topmost_guard.refresh()
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#39c6d8"), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRect(1, 33, self.width() - 3, self.height() - 35))
        painter.setFont(
            QFont(
                "Segoe UI" if sys.platform == "win32" else "Avenir Next",
                9,
                QFont.Weight.DemiBold,
            )
        )
        for track in sorted(
            self._tracks,
            key=lambda item: int(tuple(item.get("box", (0, 0, 0, 0)))[0]),
        ):
            box = tuple(int(value) for value in track.get("box", (0, 0, 0, 0)))
            if len(box) != 4:
                continue
            attributes = " ".join(
                value
                for value in (
                    str(track.get("family") or "").upper(),
                    str(track.get("rarity") or "").upper(),
                )
                if value
            )
            text = str(track.get("name") or "Unknown")
            if attributes:
                text += f" · {attributes}"
            text += f"  #{track.get('id', '')}"
            metrics = painter.fontMetrics()
            width = min(self.width(), max(80, metrics.horizontalAdvance(text) + 18))
            center = box[0] + box[2] // 2
            x = max(0, min(self.width() - width, center - width // 2))
            painter.setPen(QPen(QColor("#39c6d8"), 1))
            painter.setBrush(QColor("#ee07111f"))
            painter.drawRoundedRect(QRect(x, 3, width, 26), 6, 6)
            painter.setPen(QColor("#65f3ff"))
            painter.drawText(
                QRect(x + 8, 3, width - 16, 26),
                Qt.AlignmentFlag.AlignVCenter,
                text,
            )


_region_outline: RegionOutline | None = None
_belt_overlay: BeltTrackOverlay | None = None


def region_outline() -> RegionOutline:
    global _region_outline
    if _region_outline is None:
        _region_outline = RegionOutline()
    return _region_outline


def belt_overlay() -> BeltTrackOverlay:
    global _belt_overlay
    if _belt_overlay is None:
        _belt_overlay = BeltTrackOverlay()
    return _belt_overlay


def close_all_overlays() -> None:
    global _region_outline, _belt_overlay
    for overlay in (_region_outline, _belt_overlay):
        if overlay is not None:
            overlay.close()
    _region_outline = None
    _belt_overlay = None
