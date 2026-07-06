from __future__ import annotations

import ctypes
import sys
import threading
import time

from .popup import CARD_BG, CARD_BG_SOFT, RAINBOW_LETTERS, RARITY_COLORS, _rounded_rect

try:
    import tkinter as tk
    from tkinter import font as tkfont
except Exception:  # pragma: no cover - tkinter availability is platform dependent.
    tk = None
    tkfont = None


# "Droid Timers" overlay: a small always-on-top, click-through strip showing
# when the next droids are due (wall-clock schedule). Beskar spawns every
# 20 minutes (xx:00/20/40), Mythic at xx:55 every hour, Rainbow every
# 10 minutes (xx:00/10/...). Layout: Beskar left, Mythic middle, Rainbow right.
TIMER_ORDER = ("beskar", "mythic", "rainbow")
TIMER_LABELS = {"beskar": "BESKAR", "mythic": "MYTHIC", "rainbow": "RAINBOW"}
TIMER_COLORS = {"beskar": "#c9cdd9", "mythic": RARITY_COLORS["Mythic"]}


def seconds_until_next(kind: str) -> int:
    """Seconds until the next spawn mark for a timer, from local wall clock."""
    lt = time.localtime()
    sec_into_hour = lt.tm_min * 60 + lt.tm_sec
    if kind == "beskar":
        return 1200 - (sec_into_hour % 1200)
    if kind == "rainbow":
        return 600 - (sec_into_hour % 600)
    # Mythic: xx:55 every hour.
    delta = 55 * 60 - sec_into_hour
    if delta <= 0:
        delta += 3600
    return delta


def format_countdown(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _apply_overlay_window_styles(window: "tk.Misc", color_hex: str) -> None:
    """Layered color-key transparency plus click-through: the strip sits over
    the game, so mouse clicks must pass through it and it must never steal
    focus (WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)."""
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
        user32.SetWindowLongW(
            hwnd, gwl_exstyle, style | ws_ex_layered | ws_ex_transparent | ws_ex_noactivate
        )
        user32.SetLayeredWindowAttributes(hwnd, colorref, 0, lwa_colorkey)
    except Exception:
        pass


class DroidTimersOverlay:
    """Countdown strip pinned to the top-center of the screen."""

    WIDTH = 560
    HEIGHT = 72

    def __init__(self, master: "tk.Misc | None" = None, *, stop_event: threading.Event | None = None) -> None:
        if tk is None:
            raise RuntimeError("tkinter is not available")
        self._stop_event = stop_event
        self._after_id: str | None = None
        self._time_items: dict[str, int] = {}
        self.window = tk.Toplevel(master) if master is not None else tk.Tk()
        window = self.window
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        transparent = "#010203"
        window.configure(bg=transparent)
        try:
            window.attributes("-transparentcolor", transparent)
        except Exception:
            transparent = CARD_BG

        screen_w = window.winfo_screenwidth()
        x = max(0, (screen_w - self.WIDTH) // 2)
        window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+8")

        canvas = tk.Canvas(
            window, width=self.WIDTH, height=self.HEIGHT, bg=transparent, highlightthickness=0
        )
        canvas.pack(fill="both", expand=True)
        # Themed parents override the constructor bg (same quirk as the popup).
        canvas.configure(bg=transparent, highlightthickness=0)
        self.canvas = canvas

        _rounded_rect(canvas, 0, 0, self.WIDTH - 1, self.HEIGHT - 1, 18, fill="#4d5160", outline="")
        _rounded_rect(canvas, 2, 2, self.WIDTH - 3, self.HEIGHT - 3, 16, fill=CARD_BG, outline="")

        label_font = tkfont.Font(root=window, family="Segoe UI", size=12, weight="bold")
        time_font = tkfont.Font(root=window, family="Segoe UI Black", size=19)
        column_w = self.WIDTH // 3
        for index, kind in enumerate(TIMER_ORDER):
            center_x = column_w * index + column_w // 2
            if index > 0:
                canvas.create_line(
                    column_w * index, 14, column_w * index, self.HEIGHT - 14, fill=CARD_BG_SOFT
                )
            label = TIMER_LABELS[kind]
            if kind == "rainbow":
                total = sum(label_font.measure(ch) for ch in label)
                letter_x = center_x - total // 2
                for i, ch in enumerate(label):
                    canvas.create_text(
                        letter_x, 18, text=ch, anchor="w",
                        fill=RAINBOW_LETTERS[i % len(RAINBOW_LETTERS)], font=label_font,
                    )
                    letter_x += label_font.measure(ch)
            else:
                canvas.create_text(
                    center_x, 18, text=label, fill=TIMER_COLORS[kind], font=label_font
                )
            self._time_items[kind] = canvas.create_text(
                center_x, 47, text="--:--", fill="#f5f6fa", font=time_font
            )

        window.update_idletasks()
        if transparent == "#010203":
            _apply_overlay_window_styles(window, transparent)
        self._tick()

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
            for kind, item in self._time_items.items():
                self.canvas.itemconfigure(item, text=format_countdown(seconds_until_next(kind)))
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


def start_droid_timers_thread(stop_event: threading.Event | None = None) -> threading.Thread:
    """Standalone overlay for the CLI watcher (no GUI mainloop to attach to)."""

    def run() -> None:
        try:
            overlay = DroidTimersOverlay(stop_event=stop_event)
            overlay.window.mainloop()
        except Exception as exc:
            print(f"[TIMERS] Failed to show Droid Timers overlay: {exc}")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread
