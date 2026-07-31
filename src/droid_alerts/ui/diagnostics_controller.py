from __future__ import annotations

import time
from pathlib import Path

import cv2
from PySide6.QtCore import QObject, QTimer, Slot

from ..capture import PixelBox
from ..config import AppConfig, config_dir, data_dir
from ..diagnostics import create_support_bundle
from ..logging_io import alert_samples_dir, debug_dir, logs_dir
from ..maintenance import (
    cleanup_runtime_data,
    clear_debug_captures,
    clear_history,
    format_bytes,
    storage_summary,
)
from ..notifications import check_for_update
from ..region import Calibration, RegionResolver
from ..updater import (
    download_and_install_update,
    exit_for_external_update,
    preferred_update_url,
    restart_program,
)
from .capture_controller import CaptureController
from .image_preview import ImagePreviewDialog
from .overlays import region_outline
from .runtime import ApplicationRuntime
from .state import StateObject


class DiagnosticsController(StateObject):
    """Handles diagnostics, storage, and updates."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        capture: CaptureController,
        *,
        history_refresh=None,
        parent: QObject | None = None,
    ) -> None:
        self.runtime = runtime
        self.capture = capture
        self._history_refresh = history_refresh
        self._region_box: PixelBox | None = None
        self._region_screen_size: tuple[int, int] | None = None
        self._region_offset = (0, 0)
        self._region_key: str | None = None
        self._preview: ImagePreviewDialog | None = None
        self._update_running = False
        self._last_cleanup = 0.0
        super().__init__(
            {
                "regionVisible": False,
                "regionStatus": "Chat region is hidden",
                "storage": "Calculating storage…",
                "updateStatus": "",
                "busy": False,
            },
            parent=parent,
        )
        self._storage_timer = QTimer(self)
        self._storage_timer.setInterval(3_600_000)
        self._storage_timer.timeout.connect(self.refreshStorage)
        self._storage_timer.start()
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(15 * 60 * 1000)
        self._update_timer.timeout.connect(lambda: self.checkUpdates(False))
        self._update_timer.start()
        capture.sourceChanged.connect(self.hideRegion)
        capture.displayGeometryChanged.connect(
            lambda _automatic: self.hideRegion()
        )
        runtime.register_shutdown(self.shutdown)
        QTimer.singleShot(500, self.refreshStorage)
        QTimer.singleShot(1500, lambda: self.checkUpdates(False))

    @Slot()
    def toggleRegion(self) -> None:
        self.hideRegion() if self._region_box is not None else self.showRegion()

    @Slot()
    def showRegion(self) -> None:
        capture = None
        try:
            if self.runtime.config.capture_source == "device":
                capture = self.capture.create_chat_capture()
                width, height = capture.screen_size()
                area = getattr(
                    capture,
                    "capture_area",
                    getattr(capture, "monitor", None),
                )
            else:
                # The outline only needs source geometry. Opening another WGC
                # session here can contend with the running watcher and blocks
                # the UI while that redundant session waits for its first frame.
                area = self.capture.current_belt_source(open_device=False)
                if area is None:
                    raise RuntimeError("The selected capture source is unavailable.")
                width, height = int(area.width), int(area.height)
            box, source = RegionResolver(
                width,
                height,
                monitor_key=getattr(area, "key", None),
            ).resolve()
            self._region_box = box
            self._region_screen_size = (width, height)
            self._region_key = getattr(area, "key", None) or self.capture.current_capture_key()
            if self.runtime.config.capture_source == "device":
                frame = capture.grab(PixelBox(0, 0, width, height))
                cv2.rectangle(
                    frame,
                    (box.left, box.top),
                    (max(box.left, box.right - 1), max(box.top, box.bottom - 1)),
                    (0, 0, 255),
                    max(2, round(height / 540)),
                )
                self._preview = ImagePreviewDialog(
                    "Chat Region Preview",
                    f"Capture device · {width} × {height} · Region: {source}",
                    frame,
                )
                self._preview.show()
                self._region_offset = (0, 0)
            else:
                left = int(getattr(area, "left", 0))
                top = int(getattr(area, "top", 0))
                self._region_offset = (left, top)
                region_outline().show_region(
                    left + box.left,
                    top + box.top,
                    box.width,
                    box.height,
                    f"Droid Alerts region · {source}",
                )
            self.update_state(
                regionVisible=True,
                regionStatus=(
                    f"{box.width} × {box.height} @ ({box.left}, {box.top}) · {source}"
                ),
            )
        except Exception as exc:
            self._region_box = None
            self.runtime.dialogs.show_message(
                "Chat Region",
                str(exc),
                tone="danger",
            )
        finally:
            if capture is not None:
                capture.close()

    @Slot()
    def hideRegion(self) -> None:
        region_outline().hide()
        if self._preview is not None:
            self._preview.close()
            self._preview = None
        self._region_box = None
        self._region_screen_size = None
        self._region_offset = (0, 0)
        self._region_key = None
        self.update_state(
            regionVisible=False,
            regionStatus="Chat region is hidden",
        )

    @Slot(int, int)
    def nudgeRegion(self, delta_x: int, delta_y: int) -> None:
        box, size = self._region_box, self._region_screen_size
        if box is None or size is None:
            self.showRegion()
            return
        width, height = size
        left = max(0, min(max(0, width - box.width), box.left + delta_x))
        top = max(0, min(max(0, height - box.height), box.top + delta_y))
        box = PixelBox(left, top, box.width, box.height)
        self._region_box = box
        Calibration(
            mode="manual",
            ratios={
                "left": box.left / max(1, width),
                "top": box.top / max(1, height),
                "width": box.width / max(1, width),
                "height": box.height / max(1, height),
            },
            monitor_signature={"width": width, "height": height},
        ).save(self._region_key)
        if self.runtime.config.capture_source == "device":
            self.hideRegion()
            self.showRegion()
            return
        offset_x, offset_y = self._region_offset
        region_outline().show_region(
            offset_x + box.left,
            offset_y + box.top,
            box.width,
            box.height,
            "Droid Alerts region · manual",
        )
        self.update_state(
            regionStatus=f"Saved · {box.width} × {box.height} @ ({box.left}, {box.top})"
        )

    @Slot()
    def autoDetectRegion(self) -> None:
        Calibration().save(self.capture.current_capture_key())
        was_visible = self._region_box is not None
        self.hideRegion()
        if was_visible:
            self.showRegion()
        self.runtime.detailChanged.emit("Automatic chat region saved and applied")

    @Slot()
    def createSupportBundle(self) -> None:
        self.update_state(busy=True)
        self.runtime.detailChanged.emit("Creating support bundle…")

        def done(result, error) -> None:
            self.update_state(busy=False)
            if error is not None:
                self.runtime.dialogs.show_message(
                    "Support Bundle", str(error), tone="danger"
                )
                return
            path = Path(result)
            self.runtime.detailChanged.emit(f"Support bundle created: {path.name}")

            def open_folder(payload) -> None:
                if payload is not None:
                    self.runtime.open_path(path.parent)

            self.runtime.dialogs.confirm(
                "Support Bundle",
                f"Created a redacted support bundle:\n\n{path}",
                accept_text="Open folder",
                callback=open_folder,
            )
            self.refreshStorage()

        self.runtime.run_background(
            lambda: create_support_bundle(
                AppConfig.from_dict(self.runtime.config.to_dict())
            ),
            done,
            name="DroidAlertsSupportBundle",
        )

    @Slot()
    def refreshStorage(self) -> None:
        config = AppConfig.from_dict(self.runtime.config.to_dict())

        def work():
            now = time.monotonic()
            cleanup = None
            if now - self._last_cleanup >= 3600:
                cleanup = cleanup_runtime_data(
                    config.retention_days,
                    config.max_storage_mb,
                )
                self._last_cleanup = now
            return storage_summary(), cleanup

        def done(result, error) -> None:
            if error is not None:
                self.update_state(storage=f"Storage could not be read: {error}")
                return
            summary, cleanup = result
            text = (
                f"{format_bytes(summary['total'])} used · "
                f"History {format_bytes(summary['logs'])} · "
                f"Samples {format_bytes(summary['samples'])} · "
                f"Debug {format_bytes(summary['debug'])} · "
                f"Belt dev {format_bytes(summary['belt_dev'])}"
            )
            if cleanup is not None and cleanup.deleted_files:
                text += f" · cleanup removed {cleanup.deleted_files} files"
            self.update_state(storage=text)

        self.runtime.run_background(work, done, name="DroidAlertsStorage")

    @Slot()
    def storageSettingsChanged(self) -> None:
        self._last_cleanup = 0.0
        self.refreshStorage()

    @Slot()
    def clearDebug(self) -> None:
        self.runtime.dialogs.confirm(
            "Clear Debug Captures",
            "Delete all locally saved debug screenshots?",
            tone="danger",
            accept_text="Delete captures",
            callback=lambda payload: (
                self._clear_debug_confirmed() if payload is not None else None
            ),
        )

    def _clear_debug_confirmed(self) -> None:
        result = clear_debug_captures()
        self.runtime.detailChanged.emit(
            f"Deleted {result.deleted_files} debug files, freeing "
            f"{format_bytes(result.freed_bytes)}"
        )
        self.refreshStorage()

    @Slot()
    def clearHistory(self) -> None:
        self.runtime.dialogs.confirm(
            "Clear History",
            "Delete all event history? This cannot be undone.",
            tone="danger",
            accept_text="Clear history",
            callback=lambda payload: (
                self._clear_history_confirmed() if payload is not None else None
            ),
        )

    def _clear_history_confirmed(self) -> None:
        result = clear_history()
        if callable(self._history_refresh):
            self._history_refresh()
        self.runtime.detailChanged.emit(
            f"Deleted {result.deleted_files} history files, freeing "
            f"{format_bytes(result.freed_bytes)}"
        )
        self.refreshStorage()

    @Slot(str)
    def openFolder(self, folder: str) -> None:
        path = {
            "data": data_dir(),
            "config": config_dir(),
            "logs": logs_dir(),
            "samples": alert_samples_dir(),
            "debug": debug_dir(),
        }.get(folder)
        if path is not None and not self.runtime.open_path(path):
            self.runtime.dialogs.show_message(
                "Open Folder", "The folder could not be opened.", tone="danger"
            )

    @Slot()
    @Slot(bool)
    def checkUpdates(self, manual: bool = True) -> None:
        if self._update_running:
            return
        config = AppConfig.from_dict(self.runtime.config.to_dict())
        if not manual and not config.update_check_enabled:
            return
        if manual:
            config.update_check_enabled = True
        self._update_running = True
        self.update_state(updateStatus="Checking for updates…")

        def done(result, error) -> None:
            self._update_running = False
            if error is not None:
                self.update_state(updateStatus="Update check failed")
                if manual:
                    self.runtime.dialogs.show_message(
                        "Updates", str(error), tone="danger"
                    )
                return
            if result is None:
                self.update_state(updateStatus="Droid Alerts is up to date")
                return
            self.update_state(updateStatus=f"{result['name']} is available")
            if manual:
                self._offer_update(result)

        self.runtime.run_background(
            lambda: check_for_update(config),
            done,
            name="DroidAlertsUpdateCheck",
        )

    def _offer_update(self, release: dict[str, str]) -> None:
        self.runtime.dialogs.confirm(
            "Update Available",
            f"{release['name']} is available. Install it now?",
            note="Droid Alerts will restart after the files are replaced.",
            accept_text="Install update",
            callback=lambda payload: (
                self._install_update(release) if payload is not None else None
            ),
        )

    def _install_update(self, release: dict[str, str]) -> None:
        self.update_state(busy=True, updateStatus=f"Downloading {release['tag']}…")

        def work():
            return download_and_install_update(
                preferred_update_url(release),
                release["tag"],
                progress=lambda text: self.runtime.dispatcher.post(
                    lambda value=text: self.update_state(updateStatus=value)
                ),
            )

        def done(result, error) -> None:
            self.update_state(busy=False)
            if error is not None:
                self.update_state(updateStatus=f"Update failed: {error}")
                self.runtime.dialogs.confirm(
                    "Update Failed",
                    str(error),
                    accept_text="Open release page",
                    callback=lambda payload: (
                        self.runtime.open_url(release["url"])
                        if payload is not None
                        else None
                    ),
                )
                return
            self.update_state(updateStatus="Update installed, restarting…")
            self.runtime.shutdown()
            if result.external_restart:
                exit_for_external_update()
            else:
                restart_program()

        self.runtime.run_background(work, done, name="DroidAlertsUpdater")

    def shutdown(self) -> None:
        self._storage_timer.stop()
        self._update_timer.stop()
        self.hideRegion()
