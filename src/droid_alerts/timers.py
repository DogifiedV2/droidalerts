from __future__ import annotations

import ctypes
import sys
import threading
import time
from collections.abc import Callable

from .capture import MonitorInfo, format_tk_geometry
from .popup import CARD_BG, CARD_BG_SOFT, RAINBOW_LETTERS, RARITY_COLORS, _rounded_rect

try:
    import tkinter as tk
    from tkinter import font as tkfont
except Exception:  # pragma: no cover - tkinter availability is platform dependent.
    tk = None
    tkfont = None


# "Droid Timers" overlay: a small always-on-top, click-through strip showing
# when the next droids are due (wall-clock schedule). Beskar spawns every
# 15 minutes (xx:00/15/30/45), Galactic at xx:45 every hour, Mythic at
# xx:55 every hour, and Rainbow every 10 minutes (xx:00/10/...). Rainbow
# timing stays available on the Dashboard, while the overlay shows Beskar,
# Mythic, and Galactic side by side.
TIMER_ORDER = ("beskar", "mythic", "rainbow", "galactic")
DISPLAY_TIMER_ORDER = ("beskar", "mythic", "galactic")
TIMER_LABELS = {
    "beskar": "BESKAR",
    "mythic": "MYTHIC",
    "rainbow": "RAINBOW",
    "galactic": "GALACTIC",
}
TIMER_COLORS = {
    "beskar": "#c9cdd9",
    "mythic": RARITY_COLORS["Mythic"],
    "galactic": RARITY_COLORS["Galactic"],
}

BASE_WIDTH = 376
BASE_HEIGHT = 72
EDIT_BAR_HEIGHT = 72
MIN_SCALE = 0.6
MAX_SCALE = 2.0
DEFAULT_CENTER_X_RATIO = 0.5
DEFAULT_TOP_Y_RATIO = 0.006


def seconds_until_next(kind: str, offset_seconds: int = 0) -> int:
    """Seconds until the next spawn mark for a timer, from local wall clock."""
    lt = time.localtime()
    sec_into_hour = (lt.tm_min * 60 + lt.tm_sec + int(offset_seconds)) % 3600
    if kind == "beskar":
        return 900 - (sec_into_hour % 900)
    if kind == "rainbow":
        return 600 - (sec_into_hour % 600)
    if kind == "galactic":
        delta = 45 * 60 - sec_into_hour
        if delta <= 0:
            delta += 3600
        return delta
    # Mythic: xx:55 every hour.
    delta = 55 * 60 - sec_into_hour
    if delta <= 0:
        delta += 3600
    return delta


def format_countdown(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _apply_overlay_window_styles(window: "tk.Misc", color_hex: str, *, click_through: bool) -> None:
    """Layered color-key transparency, no-activate, and (normally)
    click-through so the strip never blocks the game. Edit mode drops the
    click-through bit so the strip can be dragged and its buttons clicked."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        gwl_exstyle = -20
        ws_ex_layered = 0x00080000
        ws_ex_transparent = 0x00000020
        ws_ex_noactivate = 0x08000000
        lwa_colorkey = 0x00000001
        r = int(color_hex[1:3], 16)
        g = int(color_hex[3:5], 16)
        b = int(color_hex[5:7], 16)
        colorref = r | (g << 8) | (b << 16)
        style = user32.GetWindowLongW(hwnd, gwl_exstyle)
        style |= ws_ex_layered | ws_ex_noactivate
        if click_through:
            style |= ws_ex_transparent
        else:
            style &= ~ws_ex_transparent
        user32.SetWindowLongW(hwnd, gwl_exstyle, style)
        user32.SetLayeredWindowAttributes(hwnd, colorref, 0, lwa_colorkey)
    except Exception:
        pass


class DroidTimersOverlay:
    """Countdown strip; position and size are user-adjustable and persisted.

    Layout is stored resolution-independently: the strip's horizontal center
    and top edge as fractions of the screen, plus a size scale factor.
    """

    def __init__(
        self,
        master: "tk.Misc | None" = None,
        *,
        stop_event: threading.Event | None = None,
        scale: float = 1.0,
        center_x_ratio: float = DEFAULT_CENTER_X_RATIO,
        top_y_ratio: float = DEFAULT_TOP_Y_RATIO,
        on_layout_change: Callable[[float, float, float], None] | None = None,
        monitor: MonitorInfo | None = None,
        reminders_enabled: bool = False,
        reminder_seconds: int = 60,
        offset_seconds: int = 0,
        on_reminder: Callable[[str, int], None] | None = None,
    ) -> None:
        if tk is None:
            raise RuntimeError("tkinter is not available")
        self._stop_event = stop_event
        self._on_layout_change = on_layout_change
        self._monitor = monitor
        self._reminders_enabled = bool(reminders_enabled)
        self._reminder_seconds = max(1, int(reminder_seconds))
        self._offset_seconds = int(offset_seconds)
        self._on_reminder = on_reminder
        self._reminded_targets: dict[str, int] = {}
        self._after_id: str | None = None
        self._time_items: dict[str, int] = {}
        self._drag_offset: tuple[int, int] | None = None
        self.edit_mode = False
        self.scale = min(MAX_SCALE, max(MIN_SCALE, float(scale)))
        self.center_x_ratio = min(1.0, max(0.0, float(center_x_ratio)))
        self.top_y_ratio = min(1.0, max(0.0, float(top_y_ratio)))

        self.window = tk.Toplevel(master) if master is not None else tk.Tk()
        window = self.window
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        self._transparent = "#010203"
        window.configure(bg=self._transparent)
        try:
            window.attributes("-transparentcolor", self._transparent)
        except Exception:
            self._transparent = CARD_BG

        self.canvas = tk.Canvas(window, bg=self._transparent, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        # Themed parents override the constructor bg (same quirk as the popup).
        self.canvas.configure(bg=self._transparent, highlightthickness=0)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self._build_card()
        window.update_idletasks()
        if self._transparent == "#010203":
            _apply_overlay_window_styles(window, self._transparent, click_through=True)
        self._tick()

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    def _card_size(self) -> tuple[int, int]:
        return int(BASE_WIDTH * self.scale), int(BASE_HEIGHT * self.scale)

    def _window_size(self) -> tuple[int, int]:
        width, height = self._card_size()
        if self.edit_mode:
            height += int(EDIT_BAR_HEIGHT * self.scale)
        return width, height

    def _apply_geometry(self) -> None:
        window = self.window
        screen_w = self._monitor.width if self._monitor is not None else window.winfo_screenwidth()
        screen_h = self._monitor.height if self._monitor is not None else window.winfo_screenheight()
        screen_left = self._monitor.left if self._monitor is not None else 0
        screen_top = self._monitor.top if self._monitor is not None else 0
        width, height = self._window_size()
        x = int(self.center_x_ratio * screen_w - width / 2)
        y = int(self.top_y_ratio * screen_h)
        x = max(0, min(screen_w - width, x))
        y = max(0, min(screen_h - height, y))
        window.geometry(
            format_tk_geometry(
                width=width,
                height=height,
                x=screen_left + x,
                y=screen_top + y,
            )
        )

    def _store_position_from_window(self) -> None:
        window = self.window
        screen_w = max(1, self._monitor.width if self._monitor is not None else window.winfo_screenwidth())
        screen_h = max(1, self._monitor.height if self._monitor is not None else window.winfo_screenheight())
        screen_left = self._monitor.left if self._monitor is not None else 0
        screen_top = self._monitor.top if self._monitor is not None else 0
        width, _height = self._window_size()
        self.center_x_ratio = (window.winfo_x() - screen_left + width / 2) / screen_w
        self.top_y_ratio = (window.winfo_y() - screen_top) / screen_h

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def _build_card(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        self._time_items = {}
        s = self.scale
        width, card_h = self._card_size()
        win_w, win_h = self._window_size()
        canvas.configure(width=win_w, height=win_h)

        border = "#5bc0de" if self.edit_mode else "#4d5160"
        _rounded_rect(canvas, 0, 0, width - 1, card_h - 1, int(18 * s), fill=border, outline="")
        _rounded_rect(canvas, 2, 2, width - 3, card_h - 3, int(16 * s), fill=CARD_BG, outline="")

        label_font = tkfont.Font(root=self.window, family="Segoe UI", size=max(7, int(12 * s)), weight="bold")
        time_font = tkfont.Font(root=self.window, family="Segoe UI Black", size=max(9, int(19 * s)))
        column_w = width // len(DISPLAY_TIMER_ORDER)
        for index, kind in enumerate(DISPLAY_TIMER_ORDER):
            center_x = column_w * index + column_w // 2
            if index > 0:
                canvas.create_line(
                    column_w * index, int(14 * s), column_w * index, card_h - int(14 * s),
                    fill=CARD_BG_SOFT,
                )
            label = TIMER_LABELS[kind]
            label_y = int(18 * s)
            if kind == "rainbow":
                total = sum(label_font.measure(ch) for ch in label)
                letter_x = center_x - total // 2
                for i, ch in enumerate(label):
                    canvas.create_text(
                        letter_x, label_y, text=ch, anchor="w",
                        fill=RAINBOW_LETTERS[i % len(RAINBOW_LETTERS)], font=label_font,
                    )
                    letter_x += label_font.measure(ch)
            else:
                canvas.create_text(
                    center_x, label_y, text=label, fill=TIMER_COLORS[kind], font=label_font
                )
            self._time_items[kind] = canvas.create_text(
                center_x, int(47 * s), text="--:--", fill="#f5f6fa", font=time_font
            )

        if self.edit_mode:
            self._build_edit_bar(card_h)
        self._apply_geometry()
        self._refresh_times()

    def _build_edit_bar(self, card_top: int) -> None:
        """Controls below the card for resizing, saving, and restoring defaults."""
        canvas = self.canvas
        s = self.scale
        bar_h = int(EDIT_BAR_HEIGHT * s)
        width, _card_h = self._card_size()
        y1 = card_top + int(4 * s)
        y2 = card_top + bar_h - int(2 * s)
        button_font = tkfont.Font(root=self.window, family="Segoe UI", size=max(8, int(11 * s)), weight="bold")

        buttons = (
            ("size-down", "−", width // 2 - int(90 * s)),
            ("size-up", "+", width // 2 - int(30 * s)),
            ("done", "Done", width // 2 + int(60 * s)),
        )
        half_w = {"size-down": int(24 * s), "size-up": int(24 * s), "done": int(44 * s)}
        for tag, text, center_x in buttons:
            fill = "#2f9e5f" if tag == "done" else CARD_BG_SOFT
            _rounded_rect(
                canvas, center_x - half_w[tag], y1, center_x + half_w[tag], y2,
                (y2 - y1) // 2, fill=fill, outline="#5bc0de", tags=(tag,),
            )
            canvas.create_text(
                center_x, (y1 + y2) // 2, text=text, fill="#f5f6fa",
                font=button_font, tags=(tag,),
            )
        canvas.tag_bind("size-down", "<Button-1>", lambda _e: self._resize_step(-0.1))
        canvas.tag_bind("size-up", "<Button-1>", lambda _e: self._resize_step(0.1))
        canvas.tag_bind("done", "<Button-1>", lambda _e: self.exit_edit_mode())

        reset_y1 = y2 + int(5 * s)
        reset_y2 = card_top + bar_h - int(2 * s)
        reset_x = width // 2
        reset_half_w = int(78 * s)
        _rounded_rect(
            canvas,
            reset_x - reset_half_w,
            reset_y1,
            reset_x + reset_half_w,
            reset_y2,
            max(1, (reset_y2 - reset_y1) // 2),
            fill=CARD_BG_SOFT,
            outline="#5bc0de",
            tags=("reset-default",),
        )
        canvas.create_text(
            reset_x,
            (reset_y1 + reset_y2) // 2,
            text="Reset to default",
            fill="#f5f6fa",
            font=button_font,
            tags=("reset-default",),
        )
        canvas.tag_bind("reset-default", "<Button-1>", lambda _e: self._reset_layout())

    def _refresh_times(self) -> None:
        for kind, item in self._time_items.items():
            remaining = seconds_until_next(kind, self._offset_seconds)
            self.canvas.itemconfigure(item, text=format_countdown(remaining))
            self._maybe_remind(kind, remaining)

    def _maybe_remind(self, kind: str, remaining: int) -> None:
        if not self._reminders_enabled or remaining > self._reminder_seconds:
            return
        target = int((time.time() + self._offset_seconds + remaining) // 60)
        if self._reminded_targets.get(kind) == target:
            return
        self._reminded_targets[kind] = target
        if self._on_reminder is not None:
            try:
                self._on_reminder(kind, remaining)
                return
            except Exception as exc:
                print(f"[TIMERS] Reminder callback failed: {exc}")
        try:
            self.window.bell()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Edit mode (move + resize)
    # ------------------------------------------------------------------
    def enter_edit_mode(self) -> None:
        if self.edit_mode:
            return
        self.edit_mode = True
        self._build_card()
        _apply_overlay_window_styles(self.window, self._transparent, click_through=False)

    def exit_edit_mode(self) -> None:
        if not self.edit_mode:
            return
        self._store_position_from_window()
        self.edit_mode = False
        self._build_card()
        _apply_overlay_window_styles(self.window, self._transparent, click_through=True)
        if self._on_layout_change is not None:
            try:
                self._on_layout_change(self.center_x_ratio, self.top_y_ratio, self.scale)
            except Exception as exc:
                print(f"[TIMERS] Failed to save overlay layout: {exc}")

    def _resize_step(self, delta: float) -> None:
        new_scale = min(MAX_SCALE, max(MIN_SCALE, round(self.scale + delta, 2)))
        if abs(new_scale - self.scale) < 0.01:
            return
        self._store_position_from_window()
        self.scale = new_scale
        self._build_card()

    def _reset_layout(self) -> None:
        self.scale = 1.0
        self.center_x_ratio = DEFAULT_CENTER_X_RATIO
        self.top_y_ratio = DEFAULT_TOP_Y_RATIO
        self._build_card()

    def _on_press(self, event: "tk.Event") -> None:
        if not self.edit_mode:
            return
        # Ignore presses on the control buttons (they have their own bindings).
        tags = self.canvas.gettags("current")
        if any(tag in ("size-down", "size-up", "done", "reset-default") for tag in tags):
            return
        self._drag_offset = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())

    def _on_drag(self, event: "tk.Event") -> None:
        if not self.edit_mode or self._drag_offset is None:
            return
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        screen_w = self._monitor.width if self._monitor is not None else self.window.winfo_screenwidth()
        screen_h = self._monitor.height if self._monitor is not None else self.window.winfo_screenheight()
        screen_left = self._monitor.left if self._monitor is not None else 0
        screen_top = self._monitor.top if self._monitor is not None else 0
        width, height = self._window_size()
        x = max(screen_left, min(screen_left + screen_w - width, x))
        y = max(screen_top, min(screen_top + screen_h - height, y))
        self.window.geometry(format_tk_geometry(x=x, y=y))

    def _on_release(self, _event: "tk.Event") -> None:
        self._drag_offset = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def alive(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except Exception:
            return False

    def _tick(self) -> None:
        if self._stop_event is not None and self._stop_event.is_set():
            self.close()
            return
        try:
            self._refresh_times()
            self.window.attributes("-topmost", True)
            self._after_id = self.window.after(500, self._tick)
        except Exception:
            pass

    def close(self) -> None:
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            if self.window.winfo_exists():
                self.window.destroy()
        except Exception:
            pass


def start_droid_timers_thread(
    stop_event: threading.Event | None = None,
    *,
    scale: float = 1.0,
    center_x_ratio: float = DEFAULT_CENTER_X_RATIO,
    top_y_ratio: float = DEFAULT_TOP_Y_RATIO,
    monitor: MonitorInfo | None = None,
    reminders_enabled: bool = False,
    reminder_seconds: int = 60,
    offset_seconds: int = 0,
) -> threading.Thread:
    """Standalone overlay for the CLI watcher (no GUI mainloop to attach to)."""

    def run() -> None:
        try:
            overlay = DroidTimersOverlay(
                stop_event=stop_event,
                scale=scale,
                center_x_ratio=center_x_ratio,
                top_y_ratio=top_y_ratio,
                monitor=monitor,
                reminders_enabled=reminders_enabled,
                reminder_seconds=reminder_seconds,
                offset_seconds=offset_seconds,
            )
            overlay.window.mainloop()
        except Exception as exc:
            print(f"[TIMERS] Failed to show Droid Timers overlay: {exc}")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread
