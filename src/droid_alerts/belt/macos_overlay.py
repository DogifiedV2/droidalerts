from __future__ import annotations

import multiprocessing
import sys
import traceback
from queue import Empty, Full
from typing import Any


MAX_VISIBLE_LABELS = 16


def _window_behavior(AppKit: Any) -> int:
    behavior = (
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        | AppKit.NSWindowCollectionBehaviorStationary
    )
    can_join_all_apps = getattr(
        AppKit, "NSWindowCollectionBehaviorCanJoinAllApplications", None
    )
    if can_join_all_apps is not None:
        behavior |= can_join_all_apps
    return behavior


def _hex_color(AppKit: Any, value: str) -> Any:
    value = value.lstrip("#")
    red, green, blue = (int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, 1.0)


class _NativeMacOSOverlay:
    """Native panels owned by an accessory helper application."""

    def __init__(
        self,
        monitor: tuple[int, int, int, int],
        region: tuple[int, int, int, int],
    ) -> None:
        import AppKit

        self.AppKit = AppKit
        self.monitor_left, self.monitor_top, _monitor_width, _monitor_height = monitor
        self.region_left, self.region_top, self.region_width, self.region_height = region
        screens = list(AppKit.NSScreen.screens())
        if not screens:
            raise RuntimeError("macOS did not report a display for the belt overlay")
        self.primary_height = float(screens[0].frame().size.height)
        self.behavior = _window_behavior(AppKit)
        self.border_panels: list[Any] = []
        self.label_panels: list[tuple[Any, Any]] = []
        self._create_panels()

    def _rect(self, left: float, top: float, width: float, height: float) -> Any:
        cocoa_y = self.primary_height - top - height
        return self.AppKit.NSMakeRect(float(left), float(cocoa_y), float(width), float(height))

    def _panel(self, left: float, top: float, width: float, height: float, color: str) -> Any:
        AppKit = self.AppKit
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            self._rect(left, top, width, height),
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        panel.setFloatingPanel_(True)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setIgnoresMouseEvents_(True)
        panel.setExcludedFromWindowsMenu_(True)
        panel.setReleasedWhenClosed_(False)
        panel.setHasShadow_(False)
        panel.setOpaque_(True)
        panel.setBackgroundColor_(_hex_color(AppKit, color))
        panel.setCollectionBehavior_(self.behavior)
        panel.setLevel_(AppKit.NSScreenSaverWindowLevel)
        panel.orderFrontRegardless()
        return panel

    def _create_panels(self) -> None:
        left = self.monitor_left + self.region_left
        top = self.monitor_top + self.region_top
        width = self.region_width
        height = self.region_height
        thickness = 3
        for x, y, panel_width, panel_height in (
            (left, top, width, thickness),
            (left, top + height - thickness, width, thickness),
            (left, top, thickness, height),
            (left + width - thickness, top, thickness, height),
        ):
            self.border_panels.append(
                self._panel(x, y, max(1, panel_width), max(1, panel_height), "#00e5ff")
            )

        AppKit = self.AppKit
        for _ in range(MAX_VISIBLE_LABELS):
            panel = self._panel(left, top, 1, 1, "#07111f")
            label = AppKit.NSTextField.labelWithString_("")
            label.setAlignment_(AppKit.NSTextAlignmentCenter)
            label.setFont_(AppKit.NSFont.boldSystemFontOfSize_(11.0))
            label.setTextColor_(_hex_color(AppKit, "#65f3ff"))
            label.setBackgroundColor_(_hex_color(AppKit, "#07111f"))
            label.setDrawsBackground_(True)
            panel.setContentView_(label)
            panel.orderOut_(None)
            self.label_panels.append((panel, label))

    def update_tracks(self, tracks: list[dict[str, object]]) -> None:
        ordered = sorted(tracks, key=lambda track: int(tuple(track["box"])[0]))
        region_screen_left = self.monitor_left + self.region_left
        region_screen_top = self.monitor_top + self.region_top
        for index, (panel, label) in enumerate(self.label_panels):
            if index >= len(ordered):
                panel.orderOut_(None)
                continue
            track = ordered[index]
            track_id = int(track["id"])
            box = tuple(int(value) for value in track["box"])
            attributes = " ".join(
                value
                for value in (
                    str(track.get("family") or "").upper(),
                    str(track.get("rarity") or "").upper(),
                )
                if value
            )
            suffix = f" · {attributes}" if attributes else ""
            label.setStringValue_(f'{track["name"]}{suffix}  #{track_id}')
            label.sizeToFit()
            fitting = label.fittingSize()
            width = max(80.0, float(fitting.width) + 14.0)
            height = max(24.0, float(fitting.height) + 6.0)
            center_x = box[0] + box[2] // 2
            left = region_screen_left + center_x - width / 2
            top = max(self.monitor_top, region_screen_top - height - 6)
            panel.setFrame_display_(self._rect(left, top, width, height), True)
            label.setFrame_(self.AppKit.NSMakeRect(0, 0, width, height))
            panel.orderFrontRegardless()

    def close(self) -> None:
        for panel in [*self.border_panels, *(panel for panel, _label in self.label_panels)]:
            panel.orderOut_(None)
            panel.close()
        self.border_panels.clear()
        self.label_panels.clear()


def _run_macos_overlay_process(
    monitor: tuple[int, int, int, int],
    region: tuple[int, int, int, int],
    command_queue: Any,
    status_queue: Any,
) -> None:
    overlay: _NativeMacOSOverlay | None = None
    try:
        import AppKit
        from Foundation import NSDate, NSRunLoop

        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        overlay = _NativeMacOSOverlay(monitor, region)
        status_queue.put(("ready", ""))

        running = True
        while running:
            latest_tracks: list[dict[str, object]] | None = None
            while True:
                try:
                    command, payload = command_queue.get_nowait()
                except Empty:
                    break
                if command == "stop":
                    running = False
                    break
                if command == "tracks":
                    latest_tracks = payload
            if latest_tracks is not None:
                overlay.update_tracks(latest_tracks)
            if running:
                NSRunLoop.currentRunLoop().runUntilDate_(
                    NSDate.dateWithTimeIntervalSinceNow_(0.02)
                )
    except BaseException:
        try:
            status_queue.put(("error", traceback.format_exc()))
        except Exception:
            pass
    finally:
        if overlay is not None:
            overlay.close()


class MacOSOverlayController:
    """Own the native overlay helper without affecting the main Tk application's policy."""

    def __init__(self) -> None:
        self._process: Any | None = None
        self._command_queue: Any | None = None

    def configure(
        self,
        monitor: Any,
        region: Any,
        *,
        startup_timeout: float = 4.0,
    ) -> None:
        if sys.platform != "darwin":
            return
        self.close()
        context = multiprocessing.get_context("spawn")
        command_queue = context.Queue(maxsize=2)
        status_queue = context.Queue(maxsize=2)
        process = context.Process(
            target=_run_macos_overlay_process,
            args=(
                (int(monitor.left), int(monitor.top), int(monitor.width), int(monitor.height)),
                (int(region.left), int(region.top), int(region.width), int(region.height)),
                command_queue,
                status_queue,
            ),
            name="DroidAlertsMacOSOverlay",
            daemon=True,
        )
        process.start()
        try:
            state, detail = status_queue.get(timeout=startup_timeout)
        except Empty as exc:
            process.terminate()
            process.join(timeout=1.0)
            raise RuntimeError("macOS belt overlay did not start") from exc
        finally:
            status_queue.close()
        if state != "ready":
            process.join(timeout=1.0)
            raise RuntimeError(f"macOS belt overlay failed to start:\n{detail}")
        self._process = process
        self._command_queue = command_queue

    def update_tracks(self, tracks: list[dict[str, object]]) -> None:
        process = self._process
        command_queue = self._command_queue
        if process is None or command_queue is None or not process.is_alive():
            return
        payload = [dict(track) for track in tracks]
        try:
            command_queue.put_nowait(("tracks", payload))
        except Full:
            try:
                command_queue.get_nowait()
            except Empty:
                pass
            try:
                command_queue.put_nowait(("tracks", payload))
            except Full:
                pass

    def close(self) -> None:
        process = self._process
        command_queue = self._command_queue
        self._process = None
        self._command_queue = None
        if process is None:
            return
        if process.is_alive() and command_queue is not None:
            try:
                command_queue.put_nowait(("stop", None))
            except Full:
                try:
                    command_queue.get_nowait()
                    command_queue.put_nowait(("stop", None))
                except (Empty, Full):
                    pass
            process.join(timeout=1.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
        if command_queue is not None:
            command_queue.close()
