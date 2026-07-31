from __future__ import annotations

import multiprocessing
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    Qt,
    QThread,
    QTimer,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .classifier import Detection
from .capture import MonitorInfo
from .config import AppConfig, assets_dir

# Visual theme: text colored by droid family (Rainbow gets per-letter colors),
# rarity shown as a colored pill, card border glows in the droid accent.
DROID_TEXT_COLORS = {
    "Diamond": "#3fd9ff",
    "Beskar": "#e8eaf0",
    "Galactic": "#b44df0",
    "Rebirth": "#ffb11b",
}
RAINBOW_LETTERS = ["#ff5252", "#ff9f2e", "#ffe14d", "#5ce06b", "#42c9ff", "#b06bff", "#ff6bd6"]
DROID_ACCENTS = {
    "Default": ("#9ca3af", "#3f4652"),
    "Gold": ("#ffd34d", "#725517"),
    "Diamond": ("#3fd9ff", "#155a6e"),
    "Rainbow": ("#c05cff", "#4d2470"),
    "Beskar": ("#c9cdd9", "#4d5160"),
    "Galactic": ("#b44df0", "#3d005e"),
    "Rebirth": ("#ffb11b", "#6b4210"),
}
RARITY_COLORS = {
    "Default": "#e8e8e8",
    "Gold": "#ffd34d",
    "Diamond": "#3fd9ff",
    "Beskar": "#e8eaf0",
    "Rainbow": "#ff65d8",
    "Galactic": "#b44df0",
    "Belt": "#65f3ff",
    "Common": "#e8e8e8",
    "Rare": "#3fd9ff",
    "Epic": "#a95cff",
    "Legendary": "#ff9d2e",
    "Mythic": "#ff3fa8",
}
CARD_BG = "#12141f"
CARD_BG_SOFT = "#1a1d2c"
ICON_HAND_X = 34
ICON_HAND_Y = 50
ICON_HAND_DROP = 10
PRIORITY_GONK_ICONS = {
    "Diamond": "priority_gonk_diamond.png",
    "Rainbow": "priority_gonk_rainbow.png",
    "Beskar": "priority_gonk_beskar.png",
    "Galactic": "priority_gonk_galactic.png",
}


@dataclass(frozen=True)
class _PopupLayout:
    width: int
    height: int
    panel_width: int
    panel_height: int
    margin: int
    ui_scale: float
    show_icon: bool


def _calculate_popup_layout(
    screen_width: int,
    screen_height: int,
    scale: float,
    *,
    icon_width: int = 0,
    icon_height: int = 0,
) -> _PopupLayout:
    """Fit the card and optional character inside the selected monitor."""

    screen_width = max(1, int(screen_width))
    screen_height = max(1, int(screen_height))
    ui_scale = min(1.5, max(0.7, float(scale)))
    margin = max(16, int(24 * ui_scale))
    available_width = max(1, screen_width - margin * 2)
    available_height = max(1, screen_height - margin * 2)
    desired_panel_height = int(
        min(185, max(150, screen_height - 160)) * ui_scale
    )

    icon_overhang = max(0, int(icon_width) - ICON_HAND_X + 22)
    icon_height_extra = max(
        0,
        int(icon_height) - ICON_HAND_Y + ICON_HAND_DROP,
    ) + 10
    minimum_panel_height = max(88, int(105 * ui_scale))
    show_icon = bool(
        icon_width > 0
        and icon_height > 0
        and available_width - icon_overhang >= 220
        and available_height - icon_height_extra >= minimum_panel_height
    )

    if show_icon:
        desired_panel_width = int(
            min(
                880,
                max(620, screen_width - int(icon_width) - 90 + ICON_HAND_X),
            )
            * ui_scale
        )
        panel_width = min(desired_panel_width, available_width - icon_overhang)
        panel_height = min(desired_panel_height, available_height - icon_height_extra)
        return _PopupLayout(
            width=panel_width + icon_overhang,
            height=panel_height + icon_height_extra,
            panel_width=panel_width,
            panel_height=panel_height,
            margin=margin,
            ui_scale=ui_scale,
            show_icon=True,
        )

    desired_width = int(min(1050, max(420, screen_width - 80)) * ui_scale)
    width = min(desired_width, available_width)
    panel_height = min(desired_panel_height, available_height)
    return _PopupLayout(
        width=width,
        height=panel_height,
        panel_width=width,
        panel_height=panel_height,
        margin=margin,
        ui_scale=ui_scale,
        show_icon=False,
    )


def _is_belt_detection(detection: Detection) -> bool:
    return detection.source == "belt-tracker" or detection.rarity == "Belt"


def _is_rebirth_available_detection(detection: Detection) -> bool:
    return detection.source == "rebirth-alert"


def _is_rebirth_ready_detection(detection: Detection) -> bool:
    return detection.source == "rebirth-ready"


def _is_rebirth_detection(detection: Detection) -> bool:
    return _is_rebirth_available_detection(detection) or _is_rebirth_ready_detection(detection)


def _is_cb23_mission_detection(detection: Detection) -> bool:
    return detection.source == "cb23-mission"


def _is_scrap_alert_detection(detection: Detection) -> bool:
    return detection.source in {"scrap-alert", "scrap-inactive"}


def _is_timer_reminder_detection(detection: Detection) -> bool:
    return detection.source == "timer-reminder"


def _uses_attribute_rarity(detection: Detection) -> bool:
    return _is_belt_detection(detection) or detection.source == "limited-deal"


def _caption_text(detection: Detection) -> str:
    if _is_scrap_alert_detection(detection):
        return "SCRAP ALERT"
    if _is_cb23_mission_detection(detection):
        return "MISSION READY"
    if _is_timer_reminder_detection(detection):
        return "TIMER REMINDER"
    if _is_rebirth_detection(detection):
        return (
            "REBIRTH ALERT"
            if _is_rebirth_available_detection(detection)
            else "REBIRTH READY"
        )
    if detection.source == "limited-deal":
        return "LIMITED DEAL"
    if _is_belt_detection(detection):
        return "BELT ALERT"
    return "PRIORITY SPAWN" if detection.is_priority else "DROID SPAWN"


def _title_segments(detection: Detection) -> list[tuple[str, str]]:
    if _is_scrap_alert_detection(detection):
        return [
            (
                "SCRAP INACTIVE FOR 5+ MIN. POSSIBLY KICKED FROM THE LOBBY."
                if detection.source == "scrap-inactive"
                else "YOUR INCOME IS NO LONGER INCREASING",
                "#e7a72f",
            )
        ]
    if _is_cb23_mission_detection(detection):
        return [("CB23 MISSION", "#f04444")]
    if _is_timer_reminder_detection(detection):
        color = {
            "Beskar Timer": RARITY_COLORS["Beskar"],
            "Mythic Timer": RARITY_COLORS["Mythic"],
            "Galactic Timer": RARITY_COLORS["Galactic"],
        }.get(detection.droid, "#39c6d8")
        return [(detection.droid.upper(), color)]
    if _is_rebirth_detection(detection):
        return (
            [("REBIRTH DROID AVAILABLE", DROID_TEXT_COLORS["Rebirth"])]
            if _is_rebirth_available_detection(detection)
            else [("REBIRTH READY", "#20f070")]
        )
    attributes = (
        detection.rarity.split()
        if _uses_attribute_rarity(detection) and detection.rarity != "Belt"
        else [detection.rarity]
    )
    segments = [
        (attribute.upper() + " ", RARITY_COLORS.get(attribute, "#ffffff"))
        for attribute in attributes
    ]
    name = detection.droid.upper()
    if detection.droid == "Rainbow":
        segments.extend(
            (letter, RAINBOW_LETTERS[i % len(RAINBOW_LETTERS)]) for i, letter in enumerate(name)
        )
    else:
        segments.append((name, DROID_TEXT_COLORS.get(detection.droid, "#ffffff")))
    return segments


def _title_lines(detection: Detection) -> list[list[tuple[str, str]]]:
    """Split belt/deal attributes from the droid name to keep alerts legible."""

    segments = _title_segments(detection)
    if _is_rebirth_detection(detection):
        return [segments]
    if not _uses_attribute_rarity(detection) or detection.rarity == "Belt":
        return [segments]
    attribute_count = len(detection.rarity.split())
    attributes = list(segments[:attribute_count])
    if attributes:
        text, color = attributes[-1]
        attributes[-1] = (text.rstrip(), color)
    name = list(segments[attribute_count:])
    return [line for line in (attributes, name) if line]


def popup_icon_path(
    config: AppConfig,
    detection: Detection | None = None,
) -> Path:
    """Use the matching Gonk rarity for normal priority-spawn popups."""

    if detection is not None and detection.is_priority:
        gonk_file = PRIORITY_GONK_ICONS.get(detection.droid)
        if gonk_file:
            gonk_path = assets_dir() / gonk_file
            if gonk_path.is_file():
                return gonk_path
    return assets_dir() / config.popup_icon_file


def _centered_text_bounds(
    body_left: int,
    body_right: int,
    *,
    icon_right: int | None,
    scale: float,
) -> tuple[int, int]:
    """Reserve equal visual space around popup text so it stays card-centered."""

    padding = round(16 * scale)
    left = body_left + padding
    right = body_right - padding
    if icon_right is None:
        return left, right

    left = max(left, icon_right + round(14 * scale))
    center = (body_left + body_right) // 2
    half_width = min(center - left, right - center)
    if half_width >= 60:
        return center - half_width, center + half_width
    return left, right


def _accent(detection: Detection) -> tuple[str, str]:
    if _is_scrap_alert_detection(detection):
        return "#e7a72f", "#6f4b12"
    if _is_rebirth_ready_detection(detection):
        return "#20f070", "#126b3c"
    if _is_timer_reminder_detection(detection):
        return {
            "Beskar Timer": DROID_ACCENTS["Beskar"],
            "Mythic Timer": ("#ff3fa8", "#6b1645"),
            "Galactic Timer": DROID_ACCENTS["Galactic"],
        }.get(detection.droid, ("#39c6d8", "#17323a"))
    key = (
        "Rebirth"
        if _is_rebirth_available_detection(detection)
        else (
            detection.rarity.split(" ", 1)[0]
            if _uses_attribute_rarity(detection)
            else detection.droid
        )
    )
    return DROID_ACCENTS.get(key, ("#ff0055", "#5a0f2c"))


class _PopupWidget(QWidget):
    def __init__(
        self,
        detection: Detection,
        popup_seconds: float,
        *,
        icon_path: Path | None,
        monitor: MonitorInfo | None,
        position: str,
        scale: float,
        opacity: float,
        standalone: bool = False,
    ) -> None:
        super().__init__(None)
        self.detection = detection
        self.popup_seconds = max(0.5, float(popup_seconds))
        self._started = time.monotonic()
        self._standalone = standalone
        self._accent, self._accent_dim = _accent(detection)
        self._icon = (
            QPixmap(str(icon_path))
            if icon_path is not None and icon_path.is_file()
            else QPixmap()
        )
        self._scale = min(1.5, max(0.7, float(scale)))
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(min(1.0, max(0.55, float(opacity))))
        if icon_path is not None and icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))

        screen = QApplication.primaryScreen()
        if monitor is not None:
            left, top, screen_width, screen_height = (
                monitor.left,
                monitor.top,
                monitor.width,
                monitor.height,
            )
        elif screen is not None:
            geometry = screen.geometry()
            left, top, screen_width, screen_height = (
                geometry.left(),
                geometry.top(),
                geometry.width(),
                geometry.height(),
            )
        else:
            left, top, screen_width, screen_height = 0, 0, 1920, 1080

        width = min(screen_width - 32, max(360, round(560 * self._scale)))
        height = min(screen_height - 32, max(118, round(154 * self._scale)))
        margin = max(16, round(24 * self._scale))
        if position.endswith("left"):
            x = left + margin
        elif position.endswith("right"):
            x = left + screen_width - width - margin
        else:
            x = left + (screen_width - width) // 2
        if position.startswith("bottom"):
            y = top + screen_height - height - margin
            start_y = y + 14
        else:
            y = top + min(max(92, margin), max(margin, screen_height - height - margin))
            start_y = y - 14
        self._final_geometry = QRect(x, y, width, height)
        self.setGeometry(x, start_y, width, height)

        self._tick = QTimer(self)
        self._tick.setInterval(30)
        self._tick.timeout.connect(self._advance)
        self._tick.start()
        QTimer.singleShot(round(self.popup_seconds * 1000), self.close)

        self._entry = QPropertyAnimation(self, b"geometry", self)
        self._entry.setDuration(300)
        self._entry.setStartValue(self.geometry())
        self._entry.setEndValue(self._final_geometry)
        self._entry.setEasingCurve(QEasingCurve.Type.OutBack)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.raise_()
        self._entry.start()

    def _advance(self) -> None:
        if time.monotonic() - self._started >= self.popup_seconds:
            self.close()
            return
        self.update()

    def _font(self, pixel_size: int, *, bold: bool = False) -> QFont:
        family = "Segoe UI" if sys.platform == "win32" else "Avenir Next"
        font = QFont(family)
        font.setPixelSize(max(8, round(pixel_size * self._scale)))
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Medium)
        return font

    @staticmethod
    def _fit_font(
        painter: QPainter,
        segments: list[tuple[str, str]],
        font: QFont,
        max_width: int,
        minimum: int,
    ) -> QFont:
        fitted = QFont(font)
        while (
            fitted.pixelSize() > minimum
            and sum(
                painter.fontMetrics().horizontalAdvance(text)
                for text, _color in segments
            )
            > max_width
        ):
            fitted.setPixelSize(fitted.pixelSize() - 1)
            painter.setFont(fitted)
        return fitted

    def _draw_segments(
        self,
        painter: QPainter,
        segments: list[tuple[str, str]],
        y: int,
        font: QFont,
        center_x: int,
    ) -> None:
        painter.setFont(font)
        metrics = painter.fontMetrics()
        total = sum(metrics.horizontalAdvance(text) for text, _color in segments)
        x = center_x - total // 2
        baseline = y + (metrics.ascent() - metrics.descent()) // 2
        for text, color in segments:
            painter.setPen(QColor("#05060a"))
            painter.drawText(x + 2, baseline + 2, text)
            painter.setPen(QColor(color))
            painter.drawText(x, baseline, text)
            x += metrics.horizontalAdvance(text)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = self.rect().adjusted(4, 4, -4, -4)
        painter.setPen(QPen(QColor(self._accent_dim), 6))
        painter.setBrush(QColor(CARD_BG))
        painter.drawRoundedRect(body, 16, 16)
        painter.setPen(QPen(QColor(self._accent), 1))
        painter.drawRoundedRect(body.adjusted(2, 2, -2, -2), 14, 14)

        header_height = max(36, round(44 * self._scale))
        header = QRect(
            body.left() + 3,
            body.top() + 3,
            body.width() - 6,
            header_height,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(CARD_BG_SOFT))
        painter.drawRoundedRect(header, 12, 12)
        painter.drawRect(
            header.left(),
            header.bottom() - 12,
            header.width(),
            13,
        )
        painter.setPen(QColor(self._accent))
        caption_font = self._font(11, bold=True)
        caption_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
        painter.setFont(caption_font)
        painter.drawText(
            header,
            Qt.AlignmentFlag.AlignCenter,
            _caption_text(self.detection),
        )

        lines = _title_lines(self.detection)
        center_y = header.bottom() + (body.bottom() - header.bottom()) // 2 + 3
        icon_right: int | None = None
        if not self._icon.isNull():
            icon_size = min(
                round(72 * self._scale),
                body.bottom() - header.bottom() - round(12 * self._scale),
            )
            icon_rect = QRect(
                body.left() + round(16 * self._scale),
                header.bottom()
                + (body.bottom() - header.bottom() - icon_size) // 2,
                icon_size,
                icon_size,
            )
            painter.drawPixmap(
                icon_rect,
                self._icon,
                self._icon.rect(),
            )
            icon_right = icon_rect.right()
        content_left, content_right = _centered_text_bounds(
            body.left(),
            body.right(),
            icon_right=icon_right,
            scale=self._scale,
        )
        content_center = (body.left() + body.right()) // 2
        max_width = max(120, content_right - content_left)
        if len(lines) == 1:
            font = self._font(31, bold=True)
            painter.setFont(font)
            font = self._fit_font(painter, lines[0], font, max_width, 14)
            self._draw_segments(
                painter,
                lines[0],
                center_y,
                font,
                content_center,
            )
        else:
            attribute_font = self._font(14, bold=True)
            painter.setFont(attribute_font)
            attribute_font = self._fit_font(
                painter,
                lines[0],
                attribute_font,
                max_width,
                10,
            )
            name_font = self._font(29, bold=True)
            painter.setFont(name_font)
            name_font = self._fit_font(
                painter,
                lines[1],
                name_font,
                max_width,
                14,
            )
            self._draw_segments(
                painter,
                lines[0],
                center_y - round(17 * self._scale),
                attribute_font,
                content_center,
            )
            self._draw_segments(
                painter,
                lines[1],
                center_y + round(14 * self._scale),
                name_font,
                content_center,
            )

        elapsed = time.monotonic() - self._started
        remaining = max(0.0, 1.0 - elapsed / self.popup_seconds)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._accent))
        painter.drawRoundedRect(
            body.left(),
            body.bottom() - 2,
            round(body.width() * remaining),
            3,
            2,
            2,
        )

    def closeEvent(self, event) -> None:
        self._tick.stop()
        try:
            _ACTIVE_POPUPS.remove(self)
        except ValueError:
            pass
        super().closeEvent(event)
        if self._standalone:
            app = QApplication.instance()
            if app is not None:
                app.quit()


_ACTIVE_POPUPS: list[_PopupWidget] = []


def bring_popup_to_front(
    root: QWidget,
    x: int,
    y: int,
    width: int,
    height: int,
) -> None:
    root.setGeometry(x, y, width, height)
    root.show()
    root.raise_()


def _run_popup_process(
    detection: Detection,
    popup_seconds: float,
    icon_path: Path | None,
    monitor: MonitorInfo | None,
    position: str,
    scale: float,
    opacity: float,
) -> None:
    app = QApplication([])
    popup = _PopupWidget(
        detection,
        popup_seconds,
        icon_path=icon_path,
        monitor=monitor,
        position=position,
        scale=scale,
        opacity=opacity,
        standalone=True,
    )
    popup.show()
    app.exec()


def show_popup(
    detection: Detection,
    popup_seconds: float,
    *,
    icon_path: Path | None = None,
    parent=None,
    monitor: MonitorInfo | None = None,
    position: str = "top_center",
    scale: float = 1.0,
    opacity: float = 1.0,
) -> None:
    app = QApplication.instance()
    if app is not None and QThread.currentThread() is app.thread():
        popup = _PopupWidget(
            detection,
            popup_seconds,
            icon_path=icon_path,
            monitor=monitor,
            position=position,
            scale=scale,
            opacity=opacity,
        )
        _ACTIVE_POPUPS.append(popup)
        popup.show()
        return
    try:
        process = multiprocessing.get_context("spawn").Process(
            target=_run_popup_process,
            args=(
                detection,
                popup_seconds,
                icon_path,
                monitor,
                position,
                scale,
                opacity,
            ),
            name="DroidAlertsPopup",
            daemon=True,
        )
        process.start()
    except Exception as exc:
        print(f"[POPUP] Failed to show alert: {exc}")
