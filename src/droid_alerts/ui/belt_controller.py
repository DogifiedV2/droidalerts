from __future__ import annotations

import multiprocessing
from queue import Empty as QueueEmpty
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..belt.names import DROID_NAMES
from ..belt.region import RelativeRegion, load_region, save_region
from ..belt.targets import (
    BELT_FAMILY_ORDER,
    belt_target_label,
    is_belt_alert_target,
    normalize_belt_target_tiers,
)
from ..belt.worker import run_belt_worker_process
from ..capture import PixelBox
from ..classifier import Detection
from ..config import AppConfig
from ..telemetry import AnonymousBeltTelemetryClient
from .capture_controller import CaptureController
from .constants import BELT_REGION_INSTRUCTIONS
from .dashboard_controller import DashboardController
from .region_selector import RegionSelector
from .runtime import ApplicationRuntime
from .state import StateObject


class BeltController(StateObject):
    """Runs the Belt Tracker and its settings."""

    statusChanged = Signal(str)
    historyChanged = Signal()

    def __init__(
        self,
        runtime: ApplicationRuntime,
        capture: CaptureController,
        dashboard: DashboardController,
        *,
        parent: QObject | None = None,
    ) -> None:
        self.runtime = runtime
        self.capture = capture
        self.dashboard = dashboard
        self.process = None
        self.stop_event = None
        self.status_queue = None
        self.telemetry: AnonymousBeltTelemetryClient | None = None
        self.region: PixelBox | None = None
        self.selector: RegionSelector | None = None
        self._worker_ready = False
        self._status = "Stopped"
        self._error = ""
        self._visible_tracks: list[dict[str, object]] = []
        self._last_scan = "No belt scans yet"
        self._sample_status = "Template collection is off"
        self._overlay_requested = False
        self._restart_after_capture_change = False
        self._stopping = False
        super().__init__({}, parent=parent)
        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._poll_process)
        capture.sourceChanged.connect(self._source_changed)
        capture.displayGeometryChanged.connect(self.refreshForDisplayGeometry)
        runtime.configChanged.connect(self.refresh)
        runtime.register_shutdown(self.shutdown)
        self.load_region()
        self.refresh()

    @property
    def status(self) -> str:
        return self._status

    def is_tracking(self) -> bool:
        return self.process is not None

    def _set_status(self, status: str) -> None:
        if self._status != status:
            self._status = status
            self.statusChanged.emit(status)

    def load_region(self) -> None:
        try:
            monitor = self.capture.current_belt_source(open_device=False)
        except Exception:
            self.region = None
            return
        if monitor is None:
            self.region = None
            return
        legacy_monitor = (
            self.capture.current_monitor()
            if self.runtime.config.capture_source == "window"
            else None
        )
        relative = load_region(monitor, legacy_monitor=legacy_monitor)
        self.region = relative.to_pixels(monitor)

    def _target_rows(self) -> list[dict[str, str]]:
        tiers = normalize_belt_target_tiers(self.runtime.config.belt_target_tiers)
        return [
            {
                "id": name,
                "droid": name,
                "minimum": belt_target_label(tiers[name]),
                "tone": tiers[name].lower(),
            }
            for name in DROID_NAMES
            if name in tiers
        ]

    @Slot()
    def refresh(self) -> None:
        config = self.runtime.config
        region = self.region
        tracks = len(self._visible_tracks)
        self.replace_state(
            {
                "tracking": self.is_tracking(),
                "status": self._status,
                "statusTone": {
                    "Running": "good",
                    "Warning": "warning",
                    "Error": "danger",
                }.get(self._status, "muted"),
                "title": (
                    "Tracking blueprint belt"
                    if self.is_tracking() and self._worker_ready
                    else "Loading Belt Tracker"
                    if self.is_tracking()
                    else "Not tracking"
                ),
                "detail": (
                    self._state.get("detail")
                    or (
                        "Ready to track the selected blueprint belt region."
                        if region is not None
                        else BELT_REGION_INSTRUCTIONS
                    )
                ),
                "buttonText": "Stop Tracking" if self.is_tracking() else "Start Tracking",
                "controlsEnabled": not self.is_tracking(),
                "regionLabel": (
                    f"{region.width} × {region.height} @ ({region.left}, {region.top})"
                    if region is not None
                    else "No belt region selected"
                ),
                "targets": self._target_rows(),
                "targetCount": len(config.belt_target_tiers),
                "overlayEnabled": config.belt_overlay_enabled,
                "trackCount": tracks,
                "trackLabel": f"{tracks} active track{'s' if tracks != 1 else ''}",
                "lastScan": self._last_scan,
                "sampleStatus": self._sample_status,
            }
        )

    @Slot()
    def toggleTracking(self) -> None:
        self.stopTracking() if self.is_tracking() else self.startTracking()

    @Slot()
    def startTracking(self) -> None:
        if self.is_tracking():
            return
        monitor = self.capture.current_monitor()
        if monitor is None:
            self._set_status("Error")
            self.update_state(
                detail="Choose an available game display from Dashboard."
            )
            return
        config = AppConfig.from_dict(self.runtime.config.to_dict())
        device_spec = None
        if config.capture_source == "device":
            try:
                device_spec = self.capture.ensure_device_session(config).spec
            except Exception as exc:
                self._set_status("Error")
                self.update_state(detail=str(exc))
                return
        # Do not open Windows Graphics Capture on Qt's UI thread. The Belt
        # Tracker worker owns capture startup and reports failures while it
        # retries, so clicking Start remains responsive.
        self.load_region()
        if self.region is None:
            self.update_state(detail="Select the belt region first.")
            return
        context = multiprocessing.get_context("spawn")
        self.stop_event = context.Event()
        self.status_queue = context.Queue()
        self._worker_ready = False
        self._stopping = False
        self._error = ""
        self._visible_tracks = []
        self.telemetry = AnonymousBeltTelemetryClient(config)
        process = context.Process(
            target=run_belt_worker_process,
            args=(
                monitor.index,
                self.region,
                dict(config.belt_target_tiers),
                self.stop_event,
                self.status_queue,
                config.belt_dev_mode,
                config.belt_template_collection_enabled,
                config.belt_idle_scan_fps,
                config.belt_active_scan_fps,
                config.capture_source,
                config.capture_window_title,
                config.capture_window_process,
                config.capture_window_class,
                config.capture_device_name,
                config.capture_device_path,
                config.capture_device_vid,
                config.capture_device_pid,
                config.capture_device_backend,
                device_spec,
            ),
            name="DroidAlertsBeltTracker",
            daemon=True,
        )
        self.process = process
        try:
            process.start()
        except Exception as exc:
            self._worker_finished(exc, process)
            return
        self._set_status("Running")
        self._overlay_requested = True
        self.update_state(detail="This can take a little bit")
        self._poll.start()
        self._update_overlay()
        self.runtime.detailChanged.emit("Belt Tracker started")
        self.refresh()

    def _poll_process(self) -> None:
        process = self.process
        if process is None:
            self._poll.stop()
            return
        self._drain_status()
        if process is not self.process or process.is_alive():
            return
        process.join(timeout=0)
        self._drain_status()
        error = (
            RuntimeError(f"Belt Tracker process exited with code {process.exitcode}")
            if process.exitcode not in (None, 0) and not self._stopping
            else None
        )
        self._worker_finished(error, process)

    def _drain_status(self) -> None:
        queue = self.status_queue
        if queue is None:
            return
        while True:
            try:
                event = queue.get_nowait()
            except QueueEmpty:
                return
            except (EOFError, OSError, ValueError):
                return
            if isinstance(event, dict):
                self._handle_status(event)

    def _handle_status(self, event: dict[str, object]) -> None:
        kind = str(event.get("type") or "")
        if kind == "ready":
            self._worker_ready = True
            if self.telemetry is not None:
                self.telemetry.start()
            self._set_status("Running")
            self.update_state(
                detail=(
                    f"{self.capture.source_label()} · Region "
                    f"{self.region.width} × {self.region.height}"
                    if self.region is not None
                    else self.capture.source_label()
                )
            )
        elif kind == "scan":
            self._set_status("Running")
            accepted = int(event.get("accepted_count") or 0)
            candidates = int(event.get("candidate_count") or 0)
            fps = float(event.get("scan_fps") or 0.0)
            self._last_scan = (
                f"{accepted} accepted · {candidates} visual candidates · {fps:.1f} FPS"
            )
        elif kind == "tracks":
            tracks = event.get("tracks")
            if isinstance(tracks, list):
                self._visible_tracks = [
                    value for value in tracks if isinstance(value, dict)
                ]
                self._update_overlay()
        elif kind == "track_event":
            record = event.get("record")
            if isinstance(record, dict):
                if str(record.get("event") or "") == "entered" and self.telemetry is not None:
                    self.telemetry.record_sighting(record.get("droid"))
                if bool(record.get("alerted")):
                    self._send_alert(record)
                self.historyChanged.emit()
        elif kind == "sample_collection":
            error = str(event.get("error") or "").strip()
            if error:
                self._sample_status = error
            elif bool(event.get("enabled")):
                total = int(event.get("total_samples") or 0)
                count = int(event.get("droid_count") or 0)
                self._sample_status = f"{total} samples across {count} droids"
        elif kind in {"error", "capture_error"}:
            self._error = str(event.get("message") or "Unknown Belt Tracker error")
            self._set_status("Warning")
            self.update_state(detail=self._error)
        elif kind == "capture_reconnected":
            self._error = ""
            self._set_status("Running")
            self.update_state(detail="Capture source reconnected automatically.")
        elif kind == "dev_log":
            self.runtime.detailChanged.emit(
                f"Blueprint collection: data/{event.get('path') or 'belt_dev'}"
            )
        elif kind == "manual_capture":
            self.runtime.detailChanged.emit(
                f"Belt screenshot saved: data/{event.get('path') or 'belt_dev'}"
            )
        self.refresh()

    def _send_alert(self, record: dict[str, object]) -> None:
        droid = str(record.get("droid") or "").strip()
        family = str(record.get("card_family") or "").strip()
        config = AppConfig.from_dict(self.runtime.config.to_dict())
        if not droid or not is_belt_alert_target(config.belt_target_tiers, droid, family):
            return
        try:
            confidence = min(1.0, max(0.0, float(record.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        rarity = str(record.get("rarity") or "").strip() or "Belt"
        detection = Detection(
            droid=droid,
            rarity=rarity,
            row_box=(0, 0, 0, 0),
            droid_score=confidence,
            rarity_score=confidence,
            rarity_margin=confidence,
            score=confidence,
            source="belt-tracker",
            shape_score=1.0,
        )
        self.dashboard.dispatch_detection(
            detection,
            source="belt_tracker",
            rarity="" if rarity == "Belt" else rarity,
        )

    @Slot()
    def stopTracking(self) -> None:
        if self.stop_event is None:
            return
        self._stopping = True
        self.stop_event.set()
        process = self.process
        if not self._worker_ready and process is not None and process.is_alive():
            process.terminate()
        self.update_state(buttonText="Stopping…", detail="Stopping Belt Tracker…")

    def _worker_finished(self, error: BaseException | None, process) -> None:
        if process is not self.process:
            return
        self._poll.stop()
        telemetry, self.telemetry = self.telemetry, None
        if telemetry is not None:
            telemetry.stop()
        queue, self.status_queue = self.status_queue, None
        if queue is not None:
            try:
                queue.close()
            except (OSError, ValueError):
                pass
        try:
            process.close()
        except (OSError, ValueError):
            pass
        self.process = None
        self.stop_event = None
        self._worker_ready = False
        self._visible_tracks = []
        stopping = self._stopping
        if stopping:
            message = ""
        elif error is not None:
            message = str(error)
        else:
            message = self._error
        restart = self._restart_after_capture_change and not message
        self._restart_after_capture_change = False
        self._stopping = False
        self._error = ""
        if message:
            self._set_status("Error")
            self.update_state(detail=message)
            self.runtime.dialogs.show_message("Belt Tracker", message, tone="danger")
        else:
            self._set_status("Stopped")
            self.update_state(detail="Ready to track the selected blueprint belt region.")
        self._update_overlay()
        self.runtime.detailChanged.emit("Belt Tracker stopped")
        self.refresh()
        if restart:
            self.load_region()
            QTimer.singleShot(150, self.startTracking)

    @Slot()
    def selectRegion(self) -> None:
        if self.is_tracking():
            self.update_state(detail="Stop Belt Tracker before changing its region.")
            return
        source = self.capture.current_belt_source(open_device=True)
        display = self.capture.current_monitor()
        if source is None or display is None:
            self.runtime.dialogs.show_message(
                "Belt Region",
                "The selected Dashboard display is not available.",
                tone="danger",
            )
            return
        if not self.runtime.config.belt_region_guide_confirmed:
            self.runtime.dialogs.confirm(
                "Official Belt Tracker Setup",
                "Stand at the start of the belt with two complete blueprint cards visible.",
                note="Other camera angles and framing may not detect reliably.",
                accept_text="Use this setup",
                callback=lambda payload: self._region_guide_response(
                    payload, source, display
                ),
            )
            return
        self._open_selector(source, display)

    def _region_guide_response(self, payload, source, display) -> None:
        if payload is None:
            return
        self.runtime.update_config(belt_region_guide_confirmed=True)
        self._open_selector(source, display)

    def _open_selector(self, source, display) -> None:
        from .overlays import belt_overlay

        self._overlay_requested = True
        belt_overlay().hide()
        window = self.runtime.main_window
        if window is not None:
            window.hide()

        def open_after_hide() -> None:
            try:
                capture = (
                    self.capture.create_chat_capture()
                    if self.runtime.config.capture_source in {"window", "device"}
                    else None
                )
                self.selector = RegionSelector(
                    source,
                    lambda box: self._region_selected(box, source),
                    on_cancelled=self._selector_cancelled,
                    capture=capture,
                    display_monitor=display,
                    title="Drag around the blueprint belt",
                )
                self.selector.show()
            except Exception as exc:
                self._selector_cancelled()
                self.runtime.dialogs.show_message(
                    "Belt Region", str(exc), tone="danger"
                )

        QTimer.singleShot(300, open_after_hide)

    def _restore_window(self) -> None:
        window = self.runtime.main_window
        if window is not None:
            window.show()
            window.raise_()
            window.requestActivate()

    def _selector_cancelled(self) -> None:
        self.selector = None
        self._restore_window()
        self._update_overlay()

    def _region_selected(self, box: PixelBox, monitor) -> None:
        self.selector = None
        try:
            relative = RelativeRegion.from_pixels(box, monitor)
            save_region(monitor, relative)
        except Exception as exc:
            self._restore_window()
            self.update_state(detail=f"Could not save the belt region: {exc}")
            self.refresh()
            self._update_overlay()
            self.runtime.dialogs.show_message(
                "Belt Region",
                f"Could not save the region: {exc}",
                tone="danger",
            )
            return
        self.region = box
        self._restore_window()
        self.update_state(detail="Ready to track the selected blueprint belt region.")
        self.runtime.detailChanged.emit("Belt region saved")
        self.refresh()
        self._update_overlay()

    @Slot()
    def chooseTargets(self) -> None:
        if self.is_tracking():
            self.update_state(detail="Stop Belt Tracker before changing target droids.")
            return
        rules = normalize_belt_target_tiers(self.runtime.config.belt_target_tiers)
        choices = [{"id": "", "label": "Off"}] + [
            {"id": family, "label": belt_target_label(family)}
            for family in BELT_FAMILY_ORDER
        ]
        self.runtime.dialogs.rules(
            "Belt Priority Alerts",
            "Choose the minimum belt tier for each droid. Higher tiers also alert.",
            [
                {
                    "id": name,
                    "label": name,
                    "detail": "",
                    "value": rules.get(name, ""),
                }
                for name in DROID_NAMES
            ],
            choices,
            callback=self._save_targets,
        )

    def _save_targets(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        values = payload.get("values", {})
        rules = normalize_belt_target_tiers(values if isinstance(values, dict) else {})
        self.runtime.update_config(belt_target_tiers=rules)
        self.runtime.detailChanged.emit("Belt alert rules saved")
        self.refresh()

    @Slot(bool)
    def setOverlayEnabled(self, enabled: bool) -> None:
        if enabled:
            self._overlay_requested = True
        self.runtime.update_config(belt_overlay_enabled=enabled)
        self._update_overlay()
        self.refresh()

    def _update_overlay(self) -> None:
        try:
            from .overlays import belt_overlay

            if (
                self._overlay_requested
                and self.runtime.config.belt_overlay_enabled
                and self.region is not None
            ):
                monitor = self.capture.current_monitor()
                source = self.capture.current_belt_source()
                region = self.region
                tracks = self._visible_tracks
                overlay_monitor = monitor
                if (
                    self.runtime.config.capture_source == "window"
                    and source is not None
                ):
                    overlay_monitor = source
                elif monitor is not None and source is not None and (
                    monitor.width != source.width or monitor.height != source.height
                ):
                    scale = min(
                        monitor.width / max(1, source.width),
                        monitor.height / max(1, source.height),
                    )
                    x_offset = round((monitor.width - source.width * scale) / 2)
                    y_offset = round((monitor.height - source.height * scale) / 2)
                    region = PixelBox(
                        x_offset + round(region.left * scale),
                        y_offset + round(region.top * scale),
                        max(1, round(region.width * scale)),
                        max(1, round(region.height * scale)),
                    )
                    tracks = [
                        {
                            **track,
                            "box": [
                                round(int(value) * scale)
                                for value in tuple(track.get("box", (0, 0, 0, 0)))
                            ],
                        }
                        for track in tracks
                    ]
                belt_overlay().show_tracks(
                    overlay_monitor,
                    region,
                    tracks,
                )
            else:
                belt_overlay().hide()
        except Exception as exc:
            print(f"[GUI] Belt overlay update failed: {exc}")

    @Slot()
    def pageOpened(self) -> None:
        self._overlay_requested = True
        self._update_overlay()
        if self.runtime.config.belt_cpu_warning_confirmed:
            return
        self.runtime.dialogs.confirm(
            "Belt Tracker CPU Usage",
            "Belt Tracker uses more CPU than normal chat monitoring.",
            note="The tracker scans blueprint artwork continuously while it is running.",
            accept_text="Confirm",
            cancel_text="",
            callback=self._cpu_warning_response,
        )

    def _cpu_warning_response(self, payload) -> None:
        if payload is None:
            return
        self.runtime.update_config(belt_cpu_warning_confirmed=True)

    def _source_changed(self) -> None:
        if self.is_tracking():
            self._restart_after_capture_change = True
            self.stopTracking()
            return
        self.load_region()
        self.refresh()
        self._update_overlay()

    @Slot(bool)
    def refreshForDisplayGeometry(self, _automatic: bool) -> None:
        if self.is_tracking():
            self._restart_after_capture_change = True
            self.stopTracking()
            return
        self.load_region()
        self.refresh()
        self._update_overlay()

    @Slot()
    def showFaq(self) -> None:
        self.runtime.dialogs.show_message(
            "Belt Tracker FAQ",
            "Frame two complete blueprint cards at the start of the belt. "
            "Select only the conveyor area, then choose per-droid minimum tiers.",
            note="The tracker runs independently from chat watching.",
        )

    def shutdown(self) -> None:
        self._restart_after_capture_change = False
        self._poll.stop()
        if self.stop_event is not None:
            self.stop_event.set()
        process = self.process
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=0.2)
                process.close()
            except (OSError, ValueError):
                pass
        self.process = None
        self._update_overlay()
