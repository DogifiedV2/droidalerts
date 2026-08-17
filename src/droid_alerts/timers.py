from __future__ import annotations

import math
import multiprocessing
import queue
import sys
import threading
from collections.abc import Callable, Mapping, Sequence

from PySide6.QtCore import QPoint, QRect, Qt, QThread, QTimer
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .capture import MonitorInfo
from .classifier import Detection
from .config import AppConfig, save_config
from .overlay_window import OverlayTopmostGuard
from .popup import RARITY_COLORS
from .timer_sync import TIMER_SCHEDULE_CLOCK


TIMER_ORDER = ("galactic", "stellar", "mythic")
DISPLAY_TIMER_ORDER = TIMER_ORDER
TIMER_LABELS = {
    "galactic": "GALACTIC",
    "stellar": "STELLAR",
    "mythic": "MYTHIC",
}
TIMER_COLORS = {
    "galactic": RARITY_COLORS["Galactic"],
    "stellar": RARITY_COLORS["Stellar"],
    "mythic": RARITY_COLORS["Mythic"],
}
TIMER_PERIOD_SECONDS = {
    "galactic": 1800,
    "stellar": 3600,
    "mythic": 3600,
}

BASE_WIDTH = 420
BASE_HEIGHT = 78
EDIT_BAR_HEIGHT = 72
MIN_SCALE = 0.6
MAX_SCALE = 2.0
DEFAULT_CENTER_X_RATIO = 0.5
DEFAULT_TOP_Y_RATIO = 0.006
TIMER_REFRESH_GUARD_MS = 8

CARD_BG = "#0e151d"
CARD_BG_HOT = "#1c1817"
CARD_LINE = "#243140"
CARD_LINE_EDIT = "#39c6d8"
TEXT = "#e9f1f7"
MUTED = "#8ba0ae"
ACCENT = "#39c6d8"
WARNING = "#f4b942"


def timer_reminder_detection(kind: str, remaining: int) -> Detection:
    label = TIMER_LABELS.get(str(kind).lower(), str(kind)).title()
    return Detection(
        droid=f"{label} Timer",
        rarity=str(max(0, int(remaining))),
        row_box=(0, 0, 0, 0),
        droid_score=1.0,
        rarity_score=1.0,
        rarity_margin=1.0,
        score=1.0,
        source="timer-reminder",
    )


def _edit_bar_row_bounds(card_top: int, scale: float) -> tuple[int, int, int, int]:
    """Return the vertical bounds for the edit bar's two button rows."""
    bar_h = int(EDIT_BAR_HEIGHT * scale)
    first_y1 = card_top + int(4 * scale)
    first_y2 = card_top + int(32 * scale)
    reset_y1 = first_y2 + int(5 * scale)
    reset_y2 = card_top + bar_h - int(2 * scale)
    return first_y1, first_y2, reset_y1, reset_y2


def seconds_until_next(kind: str, offset_seconds: int = 0) -> int:
    """Seconds until the authoritative UTC spawn mark."""
    return TIMER_SCHEDULE_CLOCK.seconds_until_next(kind, offset_seconds)


def format_countdown(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def next_timer_refresh_delay_ms(
    offset_seconds: int = 0,
    *,
    now_seconds: float | None = None,
) -> int:
    """Delay until just after the next authoritative epoch-second boundary."""
    now = (
        TIMER_SCHEDULE_CLOCK.current_time_seconds()
        if now_seconds is None
        else float(now_seconds)
    ) + int(offset_seconds)
    fractional_second = now - math.floor(now)
    until_next_second = 1.0 - fractional_second
    return math.ceil(until_next_second * 1000) + TIMER_REFRESH_GUARD_MS


class DroidTimersOverlay(QWidget):
    """Qt always-on-top countdown strip with a temporary drag/resize mode."""

    def __init__(
        self,
        master: QWidget | None = None,
        *,
        stop_event=None,
        scale: float = 1.0,
        center_x_ratio: float = DEFAULT_CENTER_X_RATIO,
        top_y_ratio: float = DEFAULT_TOP_Y_RATIO,
        on_layout_change: Callable[[float, float, float], None] | None = None,
        monitor: MonitorInfo | None = None,
        reminders_enabled: bool = False,
        reminder_seconds: int = 60,
        reminder_rules: Mapping[str, Sequence[int]] | None = None,
        offset_seconds: int = 0,
        on_reminder: Callable[[str, int], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.window = self  # Compatibility with the previous overlay surface.
        self._stop_event = stop_event
        self._on_layout_change = on_layout_change
        self._monitor = monitor
        self._reminders_enabled = bool(reminders_enabled)
        self._reminder_seconds = max(1, int(reminder_seconds))
        self._reminder_rules = {
            kind: sorted(
                {
                    max(1, int(value))
                    for value in (
                        reminder_rules.get(kind, [])
                        if reminder_rules is not None
                        else (self._reminder_seconds,)
                    )
                }
            )
            for kind in DISPLAY_TIMER_ORDER
        }
        self._offset_seconds = int(offset_seconds)
        self._on_reminder = on_reminder
        self._reminded_targets: dict[tuple[str, int], int] = {}
        self._remaining: dict[str, int] = {}
        self._drag_offset: QPoint | None = None
        self.edit_mode = False
        self.scale = min(MAX_SCALE, max(MIN_SCALE, float(scale)))
        self.center_x_ratio = min(1.0, max(0.0, float(center_x_ratio)))
        self.top_y_ratio = min(1.0, max(0.0, float(top_y_ratio)))

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_window_mode()
        self._apply_geometry()
        self._topmost_guard = OverlayTopmostGuard(self)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._tick()

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
            max(
                0.0,
                (self.x() - screen.left() + self.width() / 2)
                / max(1, screen.width()),
            ),
        )
        self.top_y_ratio = min(
            1.0,
            max(0.0, (self.y() - screen.top()) / max(1, screen.height())),
        )

    def _font(
        self,
        points: int,
        *,
        bold: bool = False,
        minimum_points: int = 7,
    ) -> QFont:
        family = "Segoe UI" if sys.platform == "win32" else "Avenir Next"
        font = QFont(family)
        # The 1.3.9 Tk overlay specified its fonts in points. The Qt port used
        # raw pixels instead, making both labels and countdowns much smaller
        # on scaled Windows displays.
        font.setPointSizeF(max(minimum_points, points * self.scale))
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Medium)
        return font

    def _button_rects(self) -> dict[str, QRect]:
        width, card_height = self._card_size()
        y1, y2, reset_y1, reset_y2 = _edit_bar_row_bounds(card_height, self.scale)
        return {
            "smaller": QRect(
                width // 2 - round(120 * self.scale),
                y1,
                round(52 * self.scale),
                y2 - y1,
            ),
            "larger": QRect(
                width // 2 - round(58 * self.scale),
                y1,
                round(52 * self.scale),
                y2 - y1,
            ),
            "done": QRect(
                width // 2 + round(10 * self.scale),
                y1,
                round(110 * self.scale),
                y2 - y1,
            ),
            "reset": QRect(
                width // 2 - round(86 * self.scale),
                reset_y1,
                round(172 * self.scale),
                reset_y2 - reset_y1,
            ),
        }

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, card_height = self._card_size()
        card = QRect(1, 1, width - 2, card_height - 2)
        hottest = min(self._remaining.values(), default=9999) <= 60
        painter.setBrush(QColor(CARD_BG_HOT if hottest else CARD_BG))
        painter.setPen(
            QPen(QColor(CARD_LINE_EDIT if self.edit_mode else CARD_LINE), 1)
        )
        painter.drawRoundedRect(card, round(12 * self.scale), round(12 * self.scale))

        column_width = width / len(DISPLAY_TIMER_ORDER)
        for index, kind in enumerate(DISPLAY_TIMER_ORDER):
            left = round(index * column_width)
            right = round((index + 1) * column_width)
            if index:
                painter.setPen(QPen(QColor(CARD_LINE), 1))
                painter.drawLine(
                    left,
                    round(14 * self.scale),
                    left,
                    card_height - round(14 * self.scale),
                )
            remaining = self._remaining.get(kind, 0)
            color = WARNING if remaining <= 60 else TIMER_COLORS[kind]
            painter.setFont(self._font(12, bold=True))
            painter.setPen(QColor(color))
            painter.drawText(
                QRect(left, round(10 * self.scale), right - left, round(22 * self.scale)),
                Qt.AlignmentFlag.AlignCenter,
                TIMER_LABELS[kind],
            )
            painter.setFont(self._font(19, bold=True, minimum_points=9))
            painter.setPen(QColor(TEXT))
            painter.drawText(
                QRect(left, round(29 * self.scale), right - left, round(33 * self.scale)),
                Qt.AlignmentFlag.AlignCenter,
                format_countdown(remaining),
            )
            progress = min(
                1.0,
                max(0.0, remaining / max(1, TIMER_PERIOD_SECONDS[kind])),
            )
            line_left = left + round(12 * self.scale)
            line_width = max(1, right - left - round(24 * self.scale))
            line_y = card_height - round(9 * self.scale)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#182330"))
            painter.drawRoundedRect(
                line_left,
                line_y,
                line_width,
                max(2, round(3 * self.scale)),
                2,
                2,
            )
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(
                line_left,
                line_y,
                round(line_width * progress),
                max(2, round(3 * self.scale)),
                2,
                2,
            )

        if not self.edit_mode:
            return
        painter.setFont(self._font(11, bold=True, minimum_points=8))
        for key, rect in self._button_rects().items():
            painter.setBrush(QColor("#17323a" if key == "done" else "#182330"))
            painter.setPen(QPen(QColor(ACCENT), 1))
            painter.drawRoundedRect(rect, rect.height() // 2, rect.height() // 2)
            painter.setPen(QColor(TEXT))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                {
                    "smaller": "−",
                    "larger": "+",
                    "done": "Done",
                    "reset": "Reset to default",
                }[key],
            )

    def _maybe_remind(self, kind: str, remaining: int) -> None:
        if not self._reminders_enabled:
            return
        target = int(
            (
                TIMER_SCHEDULE_CLOCK.current_time_seconds()
                + self._offset_seconds
                + remaining
            )
            // 60
        )
        eligible = [
            lead
            for lead in self._reminder_rules.get(kind, ())
            if remaining <= lead
            and self._reminded_targets.get((kind, lead)) != target
        ]
        if not eligible:
            return
        # If the overlay starts late, consume every already-passed threshold
        # but emit only the closest useful reminder instead of a stale burst.
        for lead in eligible:
            self._reminded_targets[(kind, lead)] = target
        if self._on_reminder is not None:
            try:
                self._on_reminder(kind, remaining)
            except Exception as exc:
                print(f"[TIMERS] Reminder callback failed: {exc}")

    def _tick(self) -> None:
        if self._stop_event is not None and self._stop_event.is_set():
            self.close()
            app = QApplication.instance()
            if self.parent() is None and app is not None:
                app.quit()
            return
        for kind in DISPLAY_TIMER_ORDER:
            remaining = seconds_until_next(kind, self._offset_seconds)
            self._remaining[kind] = remaining
            self._maybe_remind(kind, remaining)
        self.update()
        if not self.edit_mode:
            self._topmost_guard.refresh()
        self._timer.start(next_timer_refresh_delay_ms(self._offset_seconds))

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
            self._on_layout_change(
                self.center_x_ratio,
                self.top_y_ratio,
                self.scale,
            )

    def _resize_step(self, delta: float) -> None:
        self._store_position()
        self.scale = min(
            MAX_SCALE,
            max(MIN_SCALE, round(self.scale + delta, 2)),
        )
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
            if rect.contains(point):
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
        x = max(screen.left(), min(screen.right() - self.width() + 1, candidate.x()))
        y = max(screen.top(), min(screen.bottom() - self.height() + 1, candidate.y()))
        self.move(x, y)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self._drag_offset = None

    @property
    def alive(self) -> bool:
        return self.isVisible()

    def close(self) -> bool:
        self._timer.stop()
        self._topmost_guard.stop()
        return super().close()


_ACTIVE_TIMER_OVERLAY: DroidTimersOverlay | None = None


def _timer_overlays() -> list[DroidTimersOverlay]:
    app = QApplication.instance()
    overlays = (
        [
            widget
            for widget in app.topLevelWidgets()
            if isinstance(widget, DroidTimersOverlay)
        ]
        if app is not None
        else []
    )
    if _ACTIVE_TIMER_OVERLAY is not None and _ACTIVE_TIMER_OVERLAY not in overlays:
        overlays.append(_ACTIVE_TIMER_OVERLAY)
    return overlays


def _close_timer_overlays() -> None:
    global _ACTIVE_TIMER_OVERLAY
    for overlay in _timer_overlays():
        overlay.close()
        overlay.deleteLater()
    _ACTIVE_TIMER_OVERLAY = None


def _layout_saver(config: AppConfig) -> Callable[[float, float, float], None]:
    def save_layout(center_x: float, top_y: float, scale: float) -> None:
        config.droid_timers_center_x = center_x
        config.droid_timers_top_y = top_y
        config.droid_timers_scale = scale
        save_config(config)

    return save_layout


def show_droid_timers(
    config: AppConfig,
    monitor: MonitorInfo | None = None,
    *,
    on_reminder: Callable[[str, int], None] | None = None,
) -> DroidTimersOverlay | None:
    """Show or refresh the GUI-managed Qt timer strip."""
    global _ACTIVE_TIMER_OVERLAY
    app = QApplication.instance()
    if app is None or QThread.currentThread() is not app.thread():
        return None
    _close_timer_overlays()
    overlay = DroidTimersOverlay(
        scale=config.droid_timers_scale,
        center_x_ratio=config.droid_timers_center_x,
        top_y_ratio=config.droid_timers_top_y,
        on_layout_change=_layout_saver(config),
        monitor=monitor,
        reminders_enabled=config.timer_reminders_enabled,
        reminder_seconds=config.timer_reminder_seconds,
        reminder_rules=config.timer_reminder_rules,
        offset_seconds=config.timer_offset_seconds,
        on_reminder=on_reminder,
    )
    _ACTIVE_TIMER_OVERLAY = overlay
    overlay.show()
    overlay._topmost_guard.refresh()
    return overlay


def hide_droid_timers() -> None:
    _close_timer_overlays()


def adjust_droid_timers(
    config: AppConfig,
    *,
    on_reminder: Callable[[str, int], None] | None = None,
) -> DroidTimersOverlay | None:
    overlay = _ACTIVE_TIMER_OVERLAY
    if overlay is None or not overlay.alive:
        overlay = show_droid_timers(config, on_reminder=on_reminder)
    if overlay is not None:
        overlay.enter_edit_mode()
    return overlay


def _run_standalone_timer_overlay(
    stop_event,
    scale: float,
    center_x_ratio: float,
    top_y_ratio: float,
    monitor: MonitorInfo | None,
    reminders_enabled: bool,
    reminder_seconds: int,
    reminder_rules: Mapping[str, Sequence[int]] | None,
    offset_seconds: int,
    reminder_queue,
    parent_connection,
) -> None:
    app = QApplication([])
    overlay = DroidTimersOverlay(
        stop_event=stop_event,
        scale=scale,
        center_x_ratio=center_x_ratio,
        top_y_ratio=top_y_ratio,
        monitor=monitor,
        reminders_enabled=reminders_enabled,
        reminder_seconds=reminder_seconds,
        reminder_rules=reminder_rules,
        offset_seconds=offset_seconds,
        on_reminder=(
            (lambda kind, remaining: reminder_queue.put((kind, remaining)))
            if reminder_queue is not None
            else None
        ),
    )
    overlay.show()
    overlay._topmost_guard.refresh()
    parent_timer = QTimer()

    def check_parent() -> None:
        try:
            if not parent_connection.poll():
                return
            parent_connection.recv_bytes()
        except (EOFError, OSError):
            overlay.close()
            app.quit()

    parent_timer.timeout.connect(check_parent)
    parent_timer.start(500)
    try:
        app.exec()
    finally:
        parent_timer.stop()
        parent_connection.close()


class _StandaloneTimerHandle:
    def __init__(
        self,
        process,
        bridge: threading.Thread | None,
        process_stop,
        reminder_queue,
        parent_connection,
    ) -> None:
        self.process = process
        self.bridge = bridge
        self.process_stop = process_stop
        self.reminder_queue = reminder_queue
        self.parent_connection = parent_connection
        self._stopped = False

    def is_alive(self) -> bool:
        return self.process.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self.process.join(timeout)

    def stop(self, timeout: float = 1.5) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.process_stop.set()
        try:
            self.parent_connection.close()
        except (OSError, ValueError):
            pass
        self.process.join(max(0.0, float(timeout)))
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(1.0)
        if self.reminder_queue is not None:
            self.reminder_queue.close()
            self.reminder_queue.join_thread()


def start_droid_timers_thread(
    stop_event: threading.Event | None = None,
    *,
    scale: float = 1.0,
    center_x_ratio: float = DEFAULT_CENTER_X_RATIO,
    top_y_ratio: float = DEFAULT_TOP_Y_RATIO,
    monitor: MonitorInfo | None = None,
    reminders_enabled: bool = False,
    reminder_seconds: int = 60,
    reminder_rules: Mapping[str, Sequence[int]] | None = None,
    offset_seconds: int = 0,
    on_reminder: Callable[[str, int], None] | None = None,
) -> _StandaloneTimerHandle:
    """Start the CLI overlay in its own process so Qt stays on a main thread."""
    context = multiprocessing.get_context("spawn")
    process_stop = context.Event()
    reminder_queue = context.Queue() if on_reminder is not None else None
    child_connection, parent_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_run_standalone_timer_overlay,
        args=(
            process_stop,
            scale,
            center_x_ratio,
            top_y_ratio,
            monitor,
            reminders_enabled,
            reminder_seconds,
            reminder_rules,
            offset_seconds,
            reminder_queue,
            child_connection,
        ),
        name="DroidAlertsTimers",
        daemon=True,
    )
    process.start()
    child_connection.close()
    bridge = None
    if stop_event is not None or reminder_queue is not None:
        def relay_events() -> None:
            while process.is_alive():
                if stop_event is not None and stop_event.is_set():
                    process_stop.set()
                    return
                if reminder_queue is None:
                    process.join(0.1)
                    continue
                try:
                    kind, remaining = reminder_queue.get(timeout=0.1)
                except (queue.Empty, EOFError, OSError, ValueError):
                    if not process.is_alive():
                        return
                    continue
                if on_reminder is not None:
                    try:
                        on_reminder(str(kind), int(remaining))
                    except Exception as exc:
                        print(f"[TIMERS] Reminder delivery failed: {exc}")

        bridge = threading.Thread(
            target=relay_events,
            name="droid-timers-bridge",
            daemon=True,
        )
        bridge.start()
    return _StandaloneTimerHandle(
        process,
        bridge,
        process_stop,
        reminder_queue,
        parent_connection,
    )


__all__ = [
    "DISPLAY_TIMER_ORDER",
    "DroidTimersOverlay",
    "TIMER_COLORS",
    "TIMER_PERIOD_SECONDS",
    "_edit_bar_row_bounds",
    "adjust_droid_timers",
    "format_countdown",
    "hide_droid_timers",
    "next_timer_refresh_delay_ms",
    "seconds_until_next",
    "show_droid_timers",
    "start_droid_timers_thread",
    "timer_reminder_detection",
]
