from __future__ import annotations

import multiprocessing
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QPoint,
    QRect,
    Qt,
    QThread,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
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
    "Stellar": "#ffe14d",
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
    "Stellar": ("#ffe14d", "#6b5700"),
    "Rebirth": ("#ffb11b", "#6b4210"),
}
RARITY_COLORS = {
    "Default": "#e8e8e8",
    "Gold": "#ffd34d",
    "Diamond": "#3fd9ff",
    "Beskar": "#e8eaf0",
    "Rainbow": "#ff65d8",
    "Galactic": "#b44df0",
    "Stellar": "#ffe14d",
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
POPUP_BASE_WIDTH = 560
POPUP_BASE_HEIGHT = 154
POPUP_EDIT_BAR_HEIGHT = 96
POPUP_MIN_SCALE = 0.7
POPUP_MAX_SCALE = 1.5


@dataclass(frozen=True)
class _PopupLayout:
    width: int
    height: int
    panel_width: int
    panel_height: int
    margin: int
    ui_scale: float
    show_icon: bool


@dataclass(frozen=True)
class _PopupRequest:
    detection: Detection
    popup_seconds: float
    icon_path: Path | None
    monitor: MonitorInfo | None
    position: str
    center_x_ratio: float | None
    top_y_ratio: float | None
    scale: float
    opacity: float


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


def _is_chat_droid_detection(detection: Detection) -> bool:
    """Return whether this is a spawn detected from Droid Tycoon chat."""

    return detection.source.startswith("roi:")


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
        center_x_ratio: float | None = None,
        top_y_ratio: float | None = None,
        scale: float,
        opacity: float,
        standalone: bool = False,
        edit_mode: bool = False,
        on_layout_change: (
            Callable[[str, float, float, float, bool], None] | None
        ) = None,
    ) -> None:
        super().__init__(None)
        self.detection = detection
        self.popup_seconds = max(0.5, float(popup_seconds))
        self._started = time.monotonic()
        self._standalone = standalone
        self.edit_mode = bool(edit_mode)
        self._on_layout_change = on_layout_change
        self._legacy_position = str(position or "top_center")
        self._custom_position = (
            center_x_ratio is not None and top_y_ratio is not None
        )
        self.center_x_ratio = (
            min(1.0, max(0.0, float(center_x_ratio)))
            if center_x_ratio is not None
            else None
        )
        self.top_y_ratio = (
            min(1.0, max(0.0, float(top_y_ratio)))
            if top_y_ratio is not None
            else None
        )
        self._drag_offset: QPoint | None = None
        self._accent, self._accent_dim = _accent(detection)
        self._icon = (
            QPixmap(str(icon_path))
            if icon_path is not None and icon_path.is_file()
            else QPixmap()
        )
        self._scale = min(POPUP_MAX_SCALE, max(POPUP_MIN_SCALE, float(scale)))
        self._apply_window_mode()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(min(1.0, max(0.55, float(opacity))))
        if icon_path is not None and icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))

        screen = QApplication.primaryScreen()
        if monitor is not None:
            self._screen = QRect(
                monitor.left,
                monitor.top,
                monitor.width,
                monitor.height,
            )
        elif screen is not None:
            self._screen = screen.geometry()
        else:
            self._screen = QRect(0, 0, 1920, 1080)

        self._apply_geometry(animate=not self.edit_mode)
        if self.edit_mode:
            self._store_position()

        self._tick = QTimer(self)
        self._tick.setInterval(30)
        self._tick.timeout.connect(self._advance)
        self._tick.start()
        if not self.edit_mode:
            QTimer.singleShot(round(self.popup_seconds * 1000), self.close)

        self._entry = QPropertyAnimation(self, b"geometry", self)
        self._entry.setDuration(300)
        self._entry.setStartValue(self.geometry())
        self._entry.setEndValue(self._final_geometry)
        self._entry.setEasingCurve(QEasingCurve.Type.OutBack)

    def _apply_window_mode(self) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        if not self.edit_mode:
            flags |= (
                Qt.WindowType.WindowTransparentForInput
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
        self.setWindowFlags(flags)

    def _card_size(self) -> tuple[int, int]:
        width = min(
            max(1, self._screen.width() - 32),
            max(360, round(POPUP_BASE_WIDTH * self._scale)),
        )
        height = min(
            max(1, self._screen.height() - 32),
            max(118, round(POPUP_BASE_HEIGHT * self._scale)),
        )
        return width, height

    def _window_size(self) -> tuple[int, int]:
        width, height = self._card_size()
        if self.edit_mode:
            height += round(POPUP_EDIT_BAR_HEIGHT * self._scale)
        return width, min(max(1, self._screen.height() - 2), height)

    def _legacy_origin(self, width: int, card_height: int) -> tuple[int, int]:
        left, top = self._screen.left(), self._screen.top()
        screen_width, screen_height = self._screen.width(), self._screen.height()
        margin = max(16, round(24 * self._scale))
        if self._legacy_position.endswith("left"):
            x = left + margin
        elif self._legacy_position.endswith("right"):
            x = left + screen_width - width - margin
        else:
            x = left + (screen_width - width) // 2
        if self._legacy_position.startswith("bottom"):
            y = top + screen_height - card_height - margin
        else:
            y = top + min(
                max(92, margin),
                max(margin, screen_height - card_height - margin),
            )
        return x, y

    def _apply_geometry(self, *, animate: bool = False) -> None:
        width, card_height = self._card_size()
        _window_width, window_height = self._window_size()
        if self.center_x_ratio is not None and self.top_y_ratio is not None:
            x = round(
                self._screen.left()
                + self.center_x_ratio * self._screen.width()
                - width / 2
            )
            y = round(
                self._screen.top() + self.top_y_ratio * self._screen.height()
            )
        else:
            x, y = self._legacy_origin(width, card_height)
        x = max(
            self._screen.left(),
            min(self._screen.right() - width + 1, x),
        )
        y = max(
            self._screen.top(),
            min(self._screen.bottom() - window_height + 1, y),
        )
        self._card_width = width
        self._card_height = card_height
        self._final_geometry = QRect(x, y, width, window_height)
        start_y = y
        if animate:
            start_y += 14 if self._legacy_position.startswith("bottom") else -14
        self.setGeometry(x, start_y, width, window_height)

    def _store_position(self) -> None:
        self.center_x_ratio = min(
            1.0,
            max(
                0.0,
                (self.x() - self._screen.left() + self._card_width / 2)
                / max(1, self._screen.width()),
            ),
        )
        self.top_y_ratio = min(
            1.0,
            max(
                0.0,
                (self.y() - self._screen.top()) / max(1, self._screen.height()),
            ),
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.raise_()
        if not self.edit_mode:
            self._entry.start()
        else:
            self.activateWindow()

    def _advance(self) -> None:
        if not self.edit_mode and time.monotonic() - self._started >= self.popup_seconds:
            self.close()
            return
        self.update()

    def _button_rects(self) -> dict[str, QRect]:
        scale = self._scale
        first_top = self._card_height + round(24 * scale)
        first_height = max(24, round(30 * scale))
        center = self._card_width // 2
        return {
            "smaller": QRect(
                center - round(168 * scale),
                first_top,
                round(48 * scale),
                first_height,
            ),
            "larger": QRect(
                center - round(112 * scale),
                first_top,
                round(48 * scale),
                first_height,
            ),
            "done": QRect(
                center - round(54 * scale),
                first_top,
                round(102 * scale),
                first_height,
            ),
            "cancel": QRect(
                center + round(58 * scale),
                first_top,
                round(102 * scale),
                first_height,
            ),
            "reset": QRect(
                center - round(86 * scale),
                first_top + first_height + round(7 * scale),
                round(172 * scale),
                max(22, round(27 * scale)),
            ),
        }

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
        body = QRect(0, 0, self._card_width, self._card_height).adjusted(
            4, 4, -4, -4
        )
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
        remaining = (
            1.0
            if self.edit_mode
            else max(0.0, 1.0 - elapsed / self.popup_seconds)
        )
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

        if self.edit_mode:
            painter.setFont(self._font(10, bold=True))
            painter.setPen(QColor("#8ba0ae"))
            painter.drawText(
                QRect(
                    0,
                    self._card_height + round(2 * self._scale),
                    self._card_width,
                    round(20 * self._scale),
                ),
                Qt.AlignmentFlag.AlignCenter,
                "DRAG THE POPUP TO MOVE IT",
            )
            for key, rect in self._button_rects().items():
                painter.setBrush(QColor("#17323a" if key == "done" else "#182330"))
                painter.setPen(QPen(QColor("#39c6d8"), 1))
                painter.drawRoundedRect(rect, rect.height() // 2, rect.height() // 2)
                painter.setPen(QColor("#e9f1f7"))
                painter.drawText(
                    rect,
                    Qt.AlignmentFlag.AlignCenter,
                    {
                        "smaller": "−",
                        "larger": "+",
                        "done": "Done",
                        "cancel": "Cancel",
                        "reset": "Reset to default",
                    }[key],
                )

    def _resize_step(self, delta: float) -> None:
        self._store_position()
        self._custom_position = True
        self._scale = min(
            POPUP_MAX_SCALE,
            max(POPUP_MIN_SCALE, round(self._scale + delta, 2)),
        )
        self._apply_geometry()
        self.update()

    def _reset_layout(self) -> None:
        self._legacy_position = "top_center"
        self._custom_position = False
        self.center_x_ratio = None
        self.top_y_ratio = None
        self._scale = 1.0
        self._apply_geometry()
        self._store_position()
        self.update()

    def _finish_editing(self, save: bool) -> None:
        if save:
            self._store_position()
            if self._on_layout_change is not None:
                self._on_layout_change(
                    self._legacy_position,
                    float(self.center_x_ratio or 0.5),
                    float(self.top_y_ratio or 0.0),
                    self._scale,
                    self._custom_position,
                )
        self.close()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.edit_mode or event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position().toPoint()
        for key, rect in self._button_rects().items():
            if not rect.contains(point):
                continue
            if key == "smaller":
                self._resize_step(-0.1)
            elif key == "larger":
                self._resize_step(0.1)
            elif key == "reset":
                self._reset_layout()
            else:
                self._finish_editing(key == "done")
            return
        self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.edit_mode or self._drag_offset is None:
            return
        candidate = event.globalPosition().toPoint() - self._drag_offset
        x = max(
            self._screen.left(),
            min(self._screen.right() - self.width() + 1, candidate.x()),
        )
        y = max(
            self._screen.top(),
            min(self._screen.bottom() - self.height() + 1, candidate.y()),
        )
        self._custom_position = True
        self.move(x, y)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self._store_position()
        self._drag_offset = None

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.edit_mode and event.key() == Qt.Key.Key_Escape:
            self._finish_editing(False)
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        global _ACTIVE_POPUP_EDITOR
        self._tick.stop()
        was_active = False
        try:
            _ACTIVE_POPUPS.remove(self)
            was_active = True
        except ValueError:
            pass
        if _ACTIVE_POPUP_EDITOR is self:
            _ACTIVE_POPUP_EDITOR = None
        super().closeEvent(event)
        if was_active:
            QTimer.singleShot(0, _show_next_queued_popup)
        if self._standalone:
            app = QApplication.instance()
            if app is not None:
                app.quit()


_ACTIVE_POPUPS: list[_PopupWidget] = []
_POPUP_QUEUE: list[_PopupRequest] = []
_ACTIVE_POPUP_EDITOR: _PopupWidget | None = None


def _show_popup_request(request: _PopupRequest) -> None:
    popup = _PopupWidget(
        request.detection,
        request.popup_seconds,
        icon_path=request.icon_path,
        monitor=request.monitor,
        position=request.position,
        center_x_ratio=request.center_x_ratio,
        top_y_ratio=request.top_y_ratio,
        scale=request.scale,
        opacity=request.opacity,
    )
    _ACTIVE_POPUPS.append(popup)
    popup.show()


def _show_next_queued_popup() -> None:
    if _ACTIVE_POPUPS or not _POPUP_QUEUE:
        return
    _show_popup_request(_POPUP_QUEUE.pop(0))


def _queue_popup_request(request: _PopupRequest) -> None:
    if not _is_chat_droid_detection(request.detection):
        _POPUP_QUEUE.append(request)
        return
    index = 0
    while index < len(_POPUP_QUEUE) and _is_chat_droid_detection(
        _POPUP_QUEUE[index].detection
    ):
        index += 1
    _POPUP_QUEUE.insert(index, request)


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
    center_x_ratio: float | None,
    top_y_ratio: float | None,
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
        center_x_ratio=center_x_ratio,
        top_y_ratio=top_y_ratio,
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
    center_x_ratio: float | None = None,
    top_y_ratio: float | None = None,
    scale: float = 1.0,
    opacity: float = 1.0,
) -> None:
    app = QApplication.instance()
    if app is not None and QThread.currentThread() is app.thread():
        request = _PopupRequest(
            detection,
            popup_seconds,
            icon_path,
            monitor,
            position,
            center_x_ratio,
            top_y_ratio,
            scale,
            opacity,
        )
        if _is_chat_droid_detection(detection):
            active_is_droid = bool(
                _ACTIVE_POPUPS
                and _is_chat_droid_detection(_ACTIVE_POPUPS[0].detection)
            )
            if active_is_droid:
                # Preserve FIFO order between chat spawns, ahead of all other
                # queued alert types.
                _queue_popup_request(request)
            else:
                # A chat spawn replaces a lower-priority alert immediately.
                # Alerts already waiting remain queued behind chat spawns.
                for popup in tuple(_ACTIVE_POPUPS):
                    popup.close()
                _queue_popup_request(request)
                _show_next_queued_popup()
        else:
            _queue_popup_request(request)
            _show_next_queued_popup()
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
                center_x_ratio,
                top_y_ratio,
                scale,
                opacity,
            ),
            name="DroidAlertsPopup",
            daemon=True,
        )
        process.start()
    except Exception as exc:
        print(f"[POPUP] Failed to show alert: {exc}")


def adjust_priority_popup(
    config: AppConfig,
    *,
    monitor: MonitorInfo | None = None,
    on_layout_change: (
        Callable[[str, float, float, float, bool], None] | None
    ) = None,
) -> _PopupWidget | None:
    """Show a draggable priority-alert preview with resize controls."""

    global _ACTIVE_POPUP_EDITOR
    app = QApplication.instance()
    if app is None or QThread.currentThread() is not app.thread():
        return None
    if _ACTIVE_POPUP_EDITOR is not None and _ACTIVE_POPUP_EDITOR.isVisible():
        _ACTIVE_POPUP_EDITOR.raise_()
        _ACTIVE_POPUP_EDITOR.activateWindow()
        return _ACTIVE_POPUP_EDITOR
    detection = Detection(
        droid="Rebirth",
        rarity="Ready",
        row_box=(0, 0, 0, 0),
        droid_score=1.0,
        rarity_score=1.0,
        rarity_margin=1.0,
        score=1.0,
        source="rebirth-ready",
    )
    editor = _PopupWidget(
        detection,
        config.popup_seconds,
        icon_path=popup_icon_path(config, detection),
        monitor=monitor,
        position=config.popup_position,
        center_x_ratio=(
            config.popup_center_x if config.popup_custom_position else None
        ),
        top_y_ratio=config.popup_top_y if config.popup_custom_position else None,
        scale=config.popup_scale,
        opacity=config.popup_opacity,
        edit_mode=True,
        on_layout_change=on_layout_change,
    )
    _ACTIVE_POPUP_EDITOR = editor
    editor.show()
    return editor


def hide_popup_editor() -> None:
    global _ACTIVE_POPUP_EDITOR
    editor = _ACTIVE_POPUP_EDITOR
    if editor is not None:
        _ACTIVE_POPUP_EDITOR = None
        editor.close()
        editor.deleteLater()
