from __future__ import annotations

import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
import tkinter as tk

from .capture import set_dpi_awareness
from .region import Calibration, calibration_path

MIN_SIZE = 20


def capture_virtual_screen() -> tuple[np.ndarray, dict[str, int], list[dict[str, object]]]:
    import mss

    with mss.mss() as sct:
        virtual = dict(sct.monitors[0])
        monitors = [dict(monitor) for monitor in sct.monitors[1:]]
        shot = sct.grab(virtual)
    image = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    return image, {key: int(virtual[key]) for key in ("left", "top", "width", "height")}, monitors


def _monitor_for_region(region: dict[str, int], monitors: list[dict[str, object]]) -> dict[str, object]:
    cx = region["left"] + region["width"] / 2
    cy = region["top"] + region["height"] / 2
    for monitor in monitors:
        left, top = int(monitor["left"]), int(monitor["top"])
        if left <= cx < left + int(monitor["width"]) and top <= cy < top + int(monitor["height"]):
            return monitor
    return max(monitors, key=lambda m: int(m["width"]) * int(m["height"]))


class RegionSelector:
    """Fullscreen frozen-frame drag selector (ported from the original
    select_chat_region.py). Saves ONLY percent ratios + monitor signature to
    config/calibration.json stores the ratios as the source of truth."""

    def __init__(self, *, capture_delay: float = 0.0) -> None:
        self.root = tk.Tk()
        self.root.attributes("-topmost", True)
        self.root.withdraw()

        if capture_delay > 0:
            self.root.update_idletasks()
            time.sleep(capture_delay)

        self.full_image, self.virtual, self.monitors = capture_virtual_screen()
        if not self.monitors:
            self.monitors = [self.virtual]

        self.root.title("Droid Alerts: Select Alert Region")
        self.root.geometry(
            f"{self.virtual['width']}x{self.virtual['height']}+{self.virtual['left']}+{self.virtual['top']}"
        )
        self.root.overrideredirect(True)
        self.root.deiconify()

        self.canvas = tk.Canvas(self.root, cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.temp_image = tempfile.NamedTemporaryFile(prefix="droid_alerts_region_", suffix=".png", delete=False)
        self.temp_image.close()
        cv2.imwrite(self.temp_image.name, self.full_image)
        self.background = tk.PhotoImage(file=self.temp_image.name)
        self.canvas.create_image(0, 0, image=self.background, anchor="nw")

        self.start: tuple[int, int] | None = None
        self.region: dict[str, int] | None = None
        self.rect: int | None = None
        self.saved = False

        help_text = (
            "Drag a box around the droid alert rows (aim for 4-5 rows tall). "
            "Enter/S saves. Esc cancels. Arrows move, Shift+Arrows resize, Ctrl = 10px."
        )
        self.canvas.create_text(25, 25, anchor="nw", fill="black", font=("Segoe UI", 18, "bold"), text=help_text)
        self.canvas.create_text(23, 23, anchor="nw", fill="white", font=("Segoe UI", 18, "bold"), text=help_text)
        self.coord_text = self.canvas.create_text(23, 60, anchor="nw", fill="#00e5ff", font=("Consolas", 14, "bold"))

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_drag)
        self.root.bind("<Escape>", self.cancel)
        self.root.bind("<Return>", self.save)
        self.root.bind("s", self.save)
        self.root.bind("S", self.save)
        self.root.bind("<Left>", lambda e: self.nudge(-1, 0, e))
        self.root.bind("<Right>", lambda e: self.nudge(1, 0, e))
        self.root.bind("<Up>", lambda e: self.nudge(0, -1, e))
        self.root.bind("<Down>", lambda e: self.nudge(0, 1, e))

    def _pointer(self) -> tuple[int, int]:
        return self.root.winfo_pointerx(), self.root.winfo_pointery()

    def _redraw(self) -> None:
        if self.region is None:
            return
        x1 = self.region["left"] - self.virtual["left"]
        y1 = self.region["top"] - self.virtual["top"]
        coords = (x1, y1, x1 + self.region["width"], y1 + self.region["height"])
        if self.rect is None:
            self.rect = self.canvas.create_rectangle(*coords, outline="#ff0033", width=3)
        else:
            self.canvas.coords(self.rect, *coords)
        text = (
            f"left={self.region['left']} top={self.region['top']} "
            f"width={self.region['width']} height={self.region['height']}"
        )
        self.canvas.itemconfig(self.coord_text, text=text)

    def on_press(self, _event: tk.Event) -> None:
        self.start = self._pointer()
        self._set_region(*self.start, *self.start)

    def on_drag(self, _event: tk.Event) -> None:
        if self.start is None:
            return
        end = self._pointer()
        self._set_region(*self.start, *end)

    def _set_region(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self.region = {
            "left": min(x1, x2),
            "top": min(y1, y2),
            "width": abs(x2 - x1),
            "height": abs(y2 - y1),
        }
        self._redraw()

    def nudge(self, dx: int, dy: int, event: tk.Event) -> None:
        if self.region is None:
            return
        step = 10 if event.state & 0x0004 else 1
        if event.state & 0x0001:
            self.region["width"] = max(MIN_SIZE, self.region["width"] + dx * step)
            self.region["height"] = max(MIN_SIZE, self.region["height"] + dy * step)
        else:
            self.region["left"] += dx * step
            self.region["top"] += dy * step
        self._redraw()

    def save(self, _event: tk.Event | None = None) -> None:
        if self.region is None or self.region["width"] < MIN_SIZE or self.region["height"] < MIN_SIZE:
            self.canvas.itemconfig(self.coord_text, text="Region too small. Drag a larger box.", fill="#ff4040")
            return
        monitor = _monitor_for_region(self.region, self.monitors)
        mon_left, mon_top = int(monitor["left"]), int(monitor["top"])
        mon_w, mon_h = int(monitor["width"]), int(monitor["height"])
        calibration = Calibration(
            mode="manual",
            ratios={
                "left": (self.region["left"] - mon_left) / mon_w,
                "top": (self.region["top"] - mon_top) / mon_h,
                "width": self.region["width"] / mon_w,
                "height": self.region["height"] / mon_h,
            },
            monitor_signature={"width": mon_w, "height": mon_h},
        )
        calibration.save()
        print("Saved calibration (percent ratios are the source of truth):")
        print(f"  {calibration.to_dict()}")
        print(f"  -> {calibration_path()}")
        self.saved = True
        self.root.destroy()

    def cancel(self, _event: tk.Event | None = None) -> None:
        print("Cancelled. Calibration unchanged.")
        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            try:
                Path(self.temp_image.name).unlink(missing_ok=True)
            except Exception:
                pass


def run_calibrate(*, capture_delay: float = 0.0, reset: bool = False) -> None:
    set_dpi_awareness()
    if reset:
        calibration = Calibration()
        calibration.save()
        print("Calibration reset to auto region detection.")
        return
    RegionSelector(capture_delay=capture_delay).run()
