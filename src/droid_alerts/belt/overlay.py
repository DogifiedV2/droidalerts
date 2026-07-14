from __future__ import annotations

import itertools
import tkinter as tk

from ..capture import MonitorDescriptor, MonitorInfo, PixelBox
from .macos_overlay import configure_macos_overlay, refresh_macos_overlay


MAX_VISIBLE_LABELS = 16


class BeltOverlay:
    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self._border: list[tk.Toplevel] = []
        self._labels: list[tuple[tk.Toplevel, tk.Label]] = []
        self._region: PixelBox | None = None
        self._monitor: MonitorDescriptor | MonitorInfo | None = None
        self._topmost_after_id: str | None = None
        self._window_ids = itertools.count(1)

    def configure(self, monitor: MonitorDescriptor | MonitorInfo, region: PixelBox) -> None:
        self.close()
        self._monitor, self._region = monitor, region
        color, thickness = "#00e5ff", 3
        left, top = monitor.left + region.left, monitor.top + region.top
        for x, y, width, height in (
            (left, top, region.width, thickness),
            (left, top + region.height - thickness, region.width, thickness),
            (left, top, thickness, region.height),
            (left + region.width - thickness, top, thickness, region.height),
        ):
            window = self._window(color)
            window.geometry(f"{max(1, width)}x{max(1, height)}{x:+d}{y:+d}")
            self._border.append(window)
        # Pre-create every label before the game takes focus. Updating text and
        # geometry on these windows cannot activate/tab out a fullscreen game.
        for _ in range(MAX_VISIBLE_LABELS):
            window = self._window("#07111f")
            label = tk.Label(
                window, bg="#07111f", fg="#65f3ff", font=("Segoe UI", 11, "bold"),
                padx=7, pady=3, relief="solid", borderwidth=1,
            )
            label.pack()
            window.geometry(f"1x1{left:+d}{top:+d}")
            self._labels.append((window, label))
        self._refresh_topmost()

    def update_tracks(self, tracks: list[dict[str, object]]) -> None:
        if self._monitor is None or self._region is None:
            return
        ordered = sorted(tracks, key=lambda track: int(tuple(track["box"])[0]))
        hidden_x = self._monitor.left + self._region.left
        hidden_y = self._monitor.top + self._region.top
        for index, (window, label) in enumerate(self._labels):
            if index >= len(ordered):
                label.configure(text="")
                window.geometry(f"1x1{hidden_x:+d}{hidden_y:+d}")
                continue
            track = ordered[index]
            track_id = int(track["id"])
            box = tuple(int(value) for value in track["box"])
            label.configure(text=f'{track["name"]}  #{track_id}')
            window.update_idletasks()
            center_x = box[0] + box[2] // 2
            width = max(80, window.winfo_reqwidth())
            x = self._monitor.left + self._region.left + center_x - width // 2
            # Keep labels outside the scan box so capture never OCRs its own
            # overlay and the in-game names remain visible.
            y = max(self._monitor.top, self._monitor.top + self._region.top - window.winfo_reqheight() - 6)
            window.geometry(f"{width}x{window.winfo_reqheight()}{x:+d}{y:+d}")

    def _refresh_topmost(self) -> None:
        """Reassert topmost after fullscreen apps such as GeForce NOW focus."""
        self._topmost_after_id = None
        windows = [*self._border, *(window for window, _label in self._labels)]
        for window in windows:
            try:
                window.attributes("-topmost", True)
                refresh_macos_overlay(getattr(window, "_belt_native_window", None))
            except tk.TclError:
                pass
        if windows:
            self._topmost_after_id = self.root.after(500, self._refresh_topmost)

    def _window(self, background: str) -> tk.Toplevel:
        window = tk.Toplevel(self.root)
        window.title(f"droid-alerts-belt-overlay-{next(self._window_ids)}")
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=background)
        try:
            if window.tk.call("tk", "windowingsystem") == "win32":
                import ctypes
                hwnd = window.winfo_id()
                style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
                ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x20 | 0x80000)
        except Exception:
            pass
        window._belt_native_window = configure_macos_overlay(window)
        return window

    def close(self) -> None:
        if self._topmost_after_id is not None:
            try:
                self.root.after_cancel(self._topmost_after_id)
            except tk.TclError:
                pass
            self._topmost_after_id = None
        for window in [*self._border, *(window for window, _label in self._labels)]:
            try:
                window.destroy()
            except tk.TclError:
                pass
        self._border.clear()
        self._labels.clear()
        self._region = None
        self._monitor = None
