from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, Qt, QThread
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .capture import MonitorInfo
from .config import AppConfig, assets_dir, save_config
from .overlay_window import OverlayTopmostGuard


BASE_WIDTH = 228
BASE_HEIGHT = 68
EDIT_BAR_HEIGHT = 70
MIN_SCALE = 0.6
MAX_SCALE = 2.0
DEFAULT_CENTER_X_RATIO = 0.10
DEFAULT_TOP_Y_RATIO = 0.78

CARD_BG = "#0e151d"
CARD_LINE = "#243140"
ACCENT = "#39c6d8"
TEXT = "#e9f1f7"
MUTED = "#8ba0ae"


class ScrapIncomeOverlay(QWidget):
    """Click-through credits/min card with the timers' temporary edit mode."""

    def __init__(
        self,
        *,
        scale: float = 1.0,
        center_x_ratio: float = DEFAULT_CENTER_X_RATIO,
        top_y_ratio: float = DEFAULT_TOP_Y_RATIO,
        monitor: MonitorInfo | None = None,
        on_layout_change: Callable[[float, float, float], None] | None = None,
    ) -> None:
        super().__init__(None)
        self.scale = min(MAX_SCALE, max(MIN_SCALE, float(scale)))
        self.center_x_ratio = min(1.0, max(0.0, float(center_x_ratio)))
        self.top_y_ratio = min(1.0, max(0.0, float(top_y_ratio)))
        self._monitor = monitor
        self._on_layout_change = on_layout_change
        self._rate_text = "--"
        self._drag_offset: QPoint | None = None
        self.edit_mode = False
        self._icon = QPixmap(str(assets_dir() / "credit_icon.png"))

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_window_mode()
        self._apply_geometry()
        self._topmost_guard = OverlayTopmostGuard(self)

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

    def _screen_geometry(self) -> QRect:
        if self._monitor is not None:
            return QRect(
                self._monitor.left,
                self._monitor.top,
                self._monitor.width,
                self._monitor.height,
            )
        screen = QApplication.primaryScreen()
        return screen.geometry() if screen is not None else QRect(0, 0, 1920, 1080)

    def _card_size(self) -> tuple[int, int]:
        return round(BASE_WIDTH * self.scale), round(BASE_HEIGHT * self.scale)

    def _window_size(self) -> tuple[int, int]:
        width, height = self._card_size()
        if self.edit_mode:
            height += round(EDIT_BAR_HEIGHT * self.scale)
        return width, height

    def _apply_geometry(self) -> None:
        screen = self._screen_geometry()
        width, height = self._window_size()
        x = round(screen.left() + self.center_x_ratio * screen.width() - width / 2)
        y = round(screen.top() + self.top_y_ratio * screen.height())
        x = max(screen.left(), min(screen.right() - width + 1, x))
        y = max(screen.top(), min(screen.bottom() - height + 1, y))
        self.setGeometry(x, y, width, height)

    def _store_position(self) -> None:
        screen = self._screen_geometry()
        self.center_x_ratio = min(
            1.0,
            max(0.0, (self.x() - screen.left() + self.width() / 2) / max(1, screen.width())),
        )
        self.top_y_ratio = min(
            1.0,
            max(0.0, (self.y() - screen.top()) / max(1, screen.height())),
        )

    def _font(self, points: int, *, bold: bool = False) -> QFont:
        font = QFont("Segoe UI" if sys.platform == "win32" else "Avenir Next")
        font.setPointSizeF(max(7.0, points * self.scale))
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Medium)
        return font

    def set_rate(self, rate_text: str) -> None:
        self._rate_text = str(rate_text).strip() or "0"
        self.update()
        if not self.isVisible():
            self.show()
        self._topmost_guard.refresh()

    def _button_rects(self) -> dict[str, QRect]:
        width, card_height = self._card_size()
        row_y = card_height + round(5 * self.scale)
        row_h = round(27 * self.scale)
        return {
            "smaller": QRect(width // 2 - round(105 * self.scale), row_y, round(48 * self.scale), row_h),
            "larger": QRect(width // 2 - round(51 * self.scale), row_y, round(48 * self.scale), row_h),
            "done": QRect(width // 2 + round(5 * self.scale), row_y, round(100 * self.scale), row_h),
            "reset": QRect(
                width // 2 - round(76 * self.scale),
                row_y + row_h + round(5 * self.scale),
                round(152 * self.scale),
                round(26 * self.scale),
            ),
        }

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, card_height = self._card_size()
        card = QRect(1, 1, width - 2, card_height - 2)
        painter.setBrush(QColor(CARD_BG))
        painter.setPen(QPen(QColor(ACCENT if self.edit_mode else CARD_LINE), 1))
        painter.drawRoundedRect(card, round(12 * self.scale), round(12 * self.scale))

        icon_size = round(42 * self.scale)
        icon_rect = QRect(round(13 * self.scale), (card_height - icon_size) // 2, icon_size, icon_size)
        if not self._icon.isNull():
            painter.drawPixmap(icon_rect, self._icon)
        text_left = round(64 * self.scale)
        painter.setFont(self._font(22, bold=True))
        painter.setPen(QColor(TEXT))
        painter.drawText(
            QRect(text_left, round(7 * self.scale), width - text_left - round(12 * self.scale), round(35 * self.scale)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._rate_text,
        )
        painter.setFont(self._font(10, bold=True))
        painter.setPen(QColor(MUTED))
        painter.drawText(
            QRect(text_left + round(2 * self.scale), round(39 * self.scale), width - text_left, round(18 * self.scale)),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "/ MIN",
        )

        if not self.edit_mode:
            return
        painter.setFont(self._font(10, bold=True))
        for key, rect in self._button_rects().items():
            painter.setBrush(QColor("#17323a" if key == "done" else "#182330"))
            painter.setPen(QPen(QColor(ACCENT), 1))
            painter.drawRoundedRect(rect, rect.height() // 2, rect.height() // 2)
            painter.setPen(QColor(TEXT))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                {"smaller": "−", "larger": "+", "done": "Done", "reset": "Reset position"}[key],
            )

    def enter_edit_mode(self) -> None:
        if self.edit_mode:
            self.raise_()
            return
        self.edit_mode = True
        self._apply_window_mode()
        self._apply_geometry()
        self.show()
        self._topmost_guard.refresh()
        self.activateWindow()
        self.update()

    def exit_edit_mode(self) -> None:
        if not self.edit_mode:
            return
        self._store_position()
        self.edit_mode = False
        self._apply_window_mode()
        self._apply_geometry()
        self.show()
        self._topmost_guard.refresh()
        if self._on_layout_change is not None:
            self._on_layout_change(self.center_x_ratio, self.top_y_ratio, self.scale)

    def _resize_step(self, delta: float) -> None:
        self._store_position()
        self.scale = min(MAX_SCALE, max(MIN_SCALE, round(self.scale + delta, 2)))
        self._apply_geometry()
        self.update()

    def _reset_layout(self) -> None:
        self.scale = 1.0
        self.center_x_ratio = DEFAULT_CENTER_X_RATIO
        self.top_y_ratio = DEFAULT_TOP_Y_RATIO
        self._apply_geometry()
        self.update()

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
                self.exit_edit_mode()
            return
        self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.edit_mode or self._drag_offset is None:
            return
        screen = self._screen_geometry()
        candidate = event.globalPosition().toPoint() - self._drag_offset
        self.move(
            max(screen.left(), min(screen.right() - self.width() + 1, candidate.x())),
            max(screen.top(), min(screen.bottom() - self.height() + 1, candidate.y())),
        )

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self._drag_offset = None

    def close(self) -> bool:
        self._topmost_guard.stop()
        return super().close()


_ACTIVE_OVERLAY: ScrapIncomeOverlay | None = None


def _layout_saver(config: AppConfig) -> Callable[[float, float, float], None]:
    def save_layout(center_x: float, top_y: float, scale: float) -> None:
        config.scrap_income_overlay_center_x = center_x
        config.scrap_income_overlay_top_y = top_y
        config.scrap_income_overlay_scale = scale
        save_config(config)

    return save_layout


def _ensure_overlay(config: AppConfig, monitor: MonitorInfo | None = None) -> ScrapIncomeOverlay | None:
    global _ACTIVE_OVERLAY
    app = QApplication.instance()
    if app is None or QThread.currentThread() is not app.thread():
        return None
    if _ACTIVE_OVERLAY is None:
        _ACTIVE_OVERLAY = ScrapIncomeOverlay(
            scale=config.scrap_income_overlay_scale,
            center_x_ratio=config.scrap_income_overlay_center_x,
            top_y_ratio=config.scrap_income_overlay_top_y,
            monitor=monitor,
            on_layout_change=_layout_saver(config),
        )
    return _ACTIVE_OVERLAY


def update_scrap_income_overlay(
    config: AppConfig,
    rate_text: str | None,
    *,
    monitor: MonitorInfo | None = None,
) -> None:
    if not config.scrap_income_overlay_enabled:
        if _ACTIVE_OVERLAY is not None:
            _ACTIVE_OVERLAY.hide()
        return
    overlay = _ensure_overlay(config, monitor)
    if overlay is not None and rate_text:
        overlay.set_rate(rate_text)
    elif overlay is not None:
        overlay.show()
        overlay._topmost_guard.refresh()


def adjust_scrap_income_overlay(
    config: AppConfig,
    *,
    monitor: MonitorInfo | None = None,
) -> ScrapIncomeOverlay | None:
    overlay = _ensure_overlay(config, monitor)
    if overlay is not None:
        overlay.set_rate("1.4T")
        overlay.enter_edit_mode()
    return overlay


def hide_scrap_income_overlay() -> None:
    global _ACTIVE_OVERLAY
    if _ACTIVE_OVERLAY is not None:
        _ACTIVE_OVERLAY.close()
        _ACTIVE_OVERLAY.deleteLater()
    _ACTIVE_OVERLAY = None
