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
    ) -> None:
        self.root, self.monitor, self.on_selected = root, monitor, on_selected
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
            text="Drag around the blueprint belt · Enter to save · Esc to cancel",
        )
        self.start: tuple[int, int] | None = None
        self.current: tuple[int, int, int, int] | None = None
        self.rect_id: int | None = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.window.bind("<Return>", self._save)
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self.window.focus_force()

    def _press(self, event) -> None:
        self.start = (event.x, event.y)
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#00e5ff", width=4)

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

    def _save(self, _event=None) -> None:
        if self.current is None or self.current[2] < 100 or self.current[3] < 50:
            return
        box = PixelBox(*self.current)
        self.window.destroy()
        self.on_selected(box)
