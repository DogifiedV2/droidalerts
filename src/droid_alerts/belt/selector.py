from __future__ import annotations

import tkinter as tk
import tempfile
from collections.abc import Callable
from pathlib import Path

import cv2

from ..capture import MonitorDescriptor, MonitorInfo, PixelBox, create_capture, format_tk_geometry


class RegionSelector:
    def __init__(
        self,
        root: tk.Misc,
        monitor: MonitorDescriptor | MonitorInfo,
        on_selected: Callable[[PixelBox], None],
        *,
        on_cancelled: Callable[[], None] | None = None,
    ) -> None:
        self.root, self.monitor = root, monitor
        self.on_selected, self.on_cancelled = on_selected, on_cancelled
        self._finished = False
        # Chat monitoring may own DXcam's singleton for this display. An
        # independent MSS capture avoids releasing that camera underneath it.
        capture = create_capture(monitor.index, prefer_dxcam=False)
        try:
            frame = capture.grab(PixelBox(0, 0, monitor.width, monitor.height))
        finally:
            capture.close()
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.geometry(
            format_tk_geometry(
                width=monitor.width,
                height=monitor.height,
                x=monitor.left,
                y=monitor.top,
            )
        )
        self.canvas = tk.Canvas(self.window, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        temp = tempfile.NamedTemporaryFile(
            prefix="droid_alerts_belt_region_", suffix=".png", delete=False
        )
        temp.close()
        temp_path = Path(temp.name)
        try:
            encoded_ok, encoded = cv2.imencode(".png", frame)
            if not encoded_ok:
                raise RuntimeError("Could not prepare the belt-region screenshot")
            temp_path.write_bytes(encoded.tobytes())
            self.photo = tk.PhotoImage(file=str(temp_path))
        finally:
            temp_path.unlink(missing_ok=True)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.create_rectangle(14, 14, 610, 58, fill="#07111f", outline="#00e5ff", width=2)
        self.canvas.create_text(
            28, 36, anchor="w", fill="white", font=("Segoe UI", 14, "bold"),
            text="Drag around the blueprint belt · Enter or click Save",
        )
        self.canvas.create_rectangle(
            500, 20, 566, 52, fill="#087f8c", outline="#65f3ff", width=2,
            tags=("save_control",),
        )
        self.canvas.create_text(
            533, 36, text="Save", fill="white", font=("Segoe UI", 11, "bold"),
            tags=("save_control",),
        )
        self.canvas.tag_bind("save_control", "<Button-1>", self._save)
        self.start: tuple[int, int] | None = None
        self.current: tuple[int, int, int, int] | None = None
        self.rect_id: int | None = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self._bind_shortcuts()
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.after_idle(self._focus_selector)

    def _bind_shortcuts(self) -> None:
        for target in (self.window, self.canvas):
            target.bind("<Return>", self._save, add="+")
            target.bind("<KP_Enter>", self._save, add="+")
            target.bind("<KeyPress-s>", self._save, add="+")
            target.bind("<Escape>", self._cancel, add="+")

    def _focus_selector(self) -> None:
        if self._finished:
            return
        try:
            self.window.lift()
            self.window.focus_force()
            self.canvas.focus_set()
        except tk.TclError:
            pass

    def _press(self, event) -> None:
        self._focus_selector()
        self.start = (event.x, event.y)
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#00e5ff", width=4
        )

    def _drag(self, event) -> None:
        if self.start is None or self.rect_id is None:
            return
        self.canvas.coords(self.rect_id, *self.start, event.x, event.y)

    def _release(self, event) -> None:
        if self.start is None:
            return
        x1, y1 = self.start
        x2, y2 = event.x, event.y
        self.current = min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
        self._focus_selector()

    def _save(self, _event=None) -> str:
        if self.current is None or self.current[2] < 100 or self.current[3] < 50:
            return "break"
        box = PixelBox(*self.current)
        self._finished = True
        self.window.destroy()
        self.on_selected(box)
        return "break"

    def _cancel(self, _event=None) -> str:
        if self._finished:
            return "break"
        self._finished = True
        self.window.destroy()
        if self.on_cancelled is not None:
            self.on_cancelled()
        return "break"
