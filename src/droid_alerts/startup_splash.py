from __future__ import annotations

import threading
import tkinter as tk
from collections.abc import Callable
from types import TracebackType
from typing import TypeVar

from . import __version__
from .platform_ui import set_dpi_awareness


_T = TypeVar("_T")


class StartupSplash:
    """Small stdlib-only splash that can appear before heavy app imports."""

    def __init__(self, root: tk.Tk, window: tk.Toplevel, status_var: tk.StringVar) -> None:
        self.root = root
        self.window = window
        self.status_var = status_var
        self._animation_after_id: str | None = None
        self._animation_step = 0
        self._loading_bar: tk.Canvas | None = None

    def attach_loading_bar(self, loading_bar: tk.Canvas) -> None:
        self._loading_bar = loading_bar
        self._animate()

    def _animate(self) -> None:
        if self._loading_bar is None:
            return
        try:
            width = max(1, self._loading_bar.winfo_width())
            segment_width = max(70, width // 4)
            travel = width + segment_width
            x = (self._animation_step * 9) % travel - segment_width
            self._loading_bar.coords("progress", x, 0, x + segment_width, 3)
            self._animation_step += 1
            self._animation_after_id = self.window.after(24, self._animate)
        except tk.TclError:
            self._animation_after_id = None

    def set_status(self, message: str) -> None:
        try:
            self.status_var.set(message)
            self.window.update_idletasks()
        except tk.TclError:
            pass

    def lift(self) -> None:
        try:
            self.window.lift()
            self.window.update_idletasks()
        except tk.TclError:
            pass

    def run_task(self, callback: Callable[[], _T]) -> _T:
        """Run blocking startup work while continuing to repaint the splash."""
        done = threading.Event()
        result: list[_T] = []
        failure: list[tuple[BaseException, TracebackType | None]] = []

        def worker() -> None:
            try:
                result.append(callback())
            except BaseException as exc:
                failure.append((exc, exc.__traceback__))
            finally:
                done.set()

        thread = threading.Thread(target=worker, name="startup-loader", daemon=True)
        thread.start()
        while not done.wait(0.015):
            try:
                self.window.update()
            except tk.TclError:
                # The startup work still has to finish even if the OS removes
                # the temporary splash window during shutdown.
                pass
        thread.join()
        if failure:
            exc, traceback = failure[0]
            raise exc.with_traceback(traceback)
        return result[0]

    def close(self) -> None:
        try:
            if self._animation_after_id is not None:
                self.window.after_cancel(self._animation_after_id)
        except tk.TclError:
            pass
        self._animation_after_id = None
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    def destroy(self) -> None:
        """Dispose of both the splash and its hidden application root."""
        self.close()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def create_startup_splash() -> StartupSplash | None:
    """Create and paint a splash immediately, without importing app modules."""
    set_dpi_awareness()
    root: tk.Tk | None = None
    try:
        root = tk.Tk()
        root.withdraw()

        window = tk.Toplevel(root)
        window.overrideredirect(True)
        window.configure(background="#39c6d8")
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass

        width, height = 430, 184
        x = max(0, (window.winfo_screenwidth() - width) // 2)
        y = max(0, (window.winfo_screenheight() - height) // 2)
        window.geometry(f"{width}x{height}+{x}+{y}")

        body = tk.Frame(window, background="#091017", padx=28, pady=22)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(
            body,
            text="DROID ALERTS",
            background="#091017",
            foreground="#f1f7fa",
            font=("Segoe UI", 19, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            body,
            text=f"v{__version__}",
            background="#091017",
            foreground="#91a2af",
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(2, 25))

        status_var = tk.StringVar(master=window, value="Loading tracker…")
        tk.Label(
            body,
            textvariable=status_var,
            background="#091017",
            foreground="#39c6d8",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x")
        loading_bar = tk.Canvas(
            body,
            height=3,
            background="#172630",
            highlightthickness=0,
            borderwidth=0,
        )
        loading_bar.pack(fill="x", pady=(12, 0))
        loading_bar.create_rectangle(0, 0, 90, 3, fill="#39c6d8", outline="", tags="progress")

        splash = StartupSplash(root, window, status_var)
        splash.attach_loading_bar(loading_bar)
        window.update()
        return splash
    except tk.TclError:
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        return None
