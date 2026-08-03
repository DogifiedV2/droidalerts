from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtWidgets import QFileDialog

from ..logging_io import logs_dir
from .runtime import ApplicationRuntime
from .state import StateObject


FILTERS = (
    ("all", "All"),
    ("priority", "Priority"),
    ("belt", "Belt Tracker"),
    ("deals", "Limited Deals"),
    ("failures", "Failures"),
    ("detections", "Detections"),
    ("scrap_income", "Scrap Income"),
    ("debug", "Debug"),
)


def read_last_lines(
    path: Path,
    *,
    max_lines: int,
    chunk_bytes: int = 2_000_000,
) -> list[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - chunk_bytes))
        data = handle.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > chunk_bytes and lines:
        lines = lines[1:]
    return lines[-max_lines:]


class HistoryController(StateObject):
    """Loads and filters the event log."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        *,
        parent: QObject | None = None,
    ) -> None:
        self.runtime = runtime
        self._filter = "all"
        self._search = ""
        self._signature: tuple[int, int] | None = None
        self._raw_rows: dict[str, dict[str, object]] = {}
        super().__init__(
            {
                "filters": [
                    {"id": key, "label": label} for key, label in FILTERS
                ],
                "activeFilter": "all",
                "search": "",
                "rows": [],
                "summary": "No history yet",
            },
            parent=parent,
        )
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(lambda: self.refresh(False))
        self._timer.start()
        self.refresh()

    @staticmethod
    def _is_debug(row: dict[str, object]) -> bool:
        return bool(row.get("debug")) or str(row.get("event_type") or "") in {
            "seen",
            "rejected",
            "debug_snapshot",
        }

    @staticmethod
    def _type(row: dict[str, object]) -> str:
        event_type = str(row.get("event_type") or "").strip()
        if event_type:
            return event_type.replace("_", " ")
        return "alert" if row.get("alerted") else "detected"

    @staticmethod
    def _info(row: dict[str, object]) -> str:
        if str(row.get("event_type") or "") == "scrap_income_sample":
            amount = str(row.get("amount_text") or "unreadable")
            rate = str(row.get("displayed_rate_text") or "--")
            status = str(row.get("read_status") or "unknown")
            return f"Read {amount} · showing {rate}/min · {status.replace('_', ' ')}"
        reason = str(row.get("reason") or "")
        detail = str(row.get("detail") or "")
        channel = str(row.get("channel") or "")
        if reason and detail:
            return f"{reason}: {detail}"
        if channel and detail:
            return f"{channel}: {detail}"
        return reason or detail or channel or str(row.get("scale_method") or "")

    @staticmethod
    def _time(value: str) -> str:
        if len(value) >= 15 and value[8] == "_":
            return (
                f"{value[0:4]}-{value[4:6]}-{value[6:8]} "
                f"{value[9:11]}:{value[11:13]}:{value[13:15]}"
            )
        return value

    def _matches_filter(self, row: dict[str, object]) -> bool:
        selected = self._filter
        if selected == "all":
            return True
        event_type = str(row.get("event_type") or "")
        source = str(row.get("source") or "")
        if selected == "priority":
            return (
                event_type in {"alert", "limited_deal"}
                or (event_type.startswith("belt_") and bool(row.get("alerted")))
                or (not event_type and bool(row.get("alerted")))
            )
        if selected == "belt":
            return source in {"belt_tracker", "belt-tracker"} or event_type.startswith(
                "belt_"
            )
        if selected == "deals":
            return source in {"limited_deal", "limited-deal"} or event_type == "limited_deal"
        if selected == "failures":
            return event_type == "delivery" and not bool(row.get("success"))
        if selected == "detections":
            return event_type in {
                "alert",
                "detected",
                "seen",
                "belt_entered",
                "limited_deal",
            }
        if selected == "scrap_income":
            return event_type in {"scrap_income_sample", "scrap_income_state"}
        return selected == "debug" and self._is_debug(row)

    @Slot()
    @Slot(bool)
    def refresh(self, force: bool = True) -> None:
        path = logs_dir() / "events.jsonl"
        if not path.exists():
            self._signature = None
            self._raw_rows = {}
            self.update_state(rows=[], summary="No history yet")
            return
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError as exc:
            self.runtime.detailChanged.emit(f"Could not read history: {exc}")
            return
        if not force and signature == self._signature:
            return
        try:
            lines = read_last_lines(path, max_lines=3000)
        except OSError as exc:
            self.runtime.detailChanged.emit(f"Could not read history: {exc}")
            return
        rows: list[dict[str, Any]] = []
        raw_rows: dict[str, dict[str, object]] = {}
        priority_count = 0
        for line in reversed(lines):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict) or not self._matches_filter(raw):
                continue
            if self._search and self._search not in json.dumps(
                raw, ensure_ascii=False
            ).casefold():
                continue
            event_type = str(raw.get("event_type") or "")
            if event_type == "delivery":
                status = "Delivered" if bool(raw.get("success")) else "Failed"
            elif event_type == "scrap_income_sample":
                status = "Sample"
            elif event_type == "scrap_income_state":
                status = "Paused" if str(raw.get("state") or "") == "paused" else "Resumed"
            elif raw.get("alerted"):
                status = "Alerted"
                priority_count += 1
            elif self._is_debug(raw):
                status = "Debug"
            else:
                status = "Detected"
            row_id = f"row-{len(rows)}"
            raw_rows[row_id] = raw
            rows.append(
                {
                    "id": row_id,
                    "time": self._time(str(raw.get("ts") or "")),
                    "event": self._type(raw).title(),
                    "droid": str(raw.get("droid") or ""),
                    "rarity": str(raw.get("rarity") or ""),
                    "status": status,
                    "detail": self._info(raw),
                    "tone": (
                        "danger"
                        if status == "Failed"
                        else "good"
                        if status == "Delivered"
                        else "accent"
                        if status in {"Alerted", "Resumed"}
                        else "muted"
                    ),
                }
            )
            if len(rows) >= 500:
                break
        self._raw_rows = raw_rows
        self._signature = signature
        self.update_state(
            activeFilter=self._filter,
            search=self._search,
            rows=rows,
            summary=f"{len(rows)} shown · {priority_count} priority alerts",
        )

    @Slot(str)
    def setFilter(self, filter_id: str) -> None:
        if filter_id not in {key for key, _label in FILTERS}:
            return
        self._filter = filter_id
        self._signature = None
        self.refresh()

    @Slot(str)
    def setSearch(self, value: str) -> None:
        value = value.strip().casefold()
        if value == self._search:
            return
        self._search = value
        self._signature = None
        self.refresh()

    @Slot(str)
    def showDetails(self, row_id: str) -> None:
        row = self._raw_rows.get(row_id)
        if row is None:
            return
        self.runtime.dialogs.show_message(
            "History Event Details",
            f"{self._type(row).title()} · {row.get('rarity', '')} {row.get('droid', '')}",
            note=json.dumps(row, indent=2, ensure_ascii=False),
            accept_text="Close",
        )

    @Slot()
    def exportCsv(self) -> None:
        if not self._raw_rows:
            self.runtime.dialogs.show_message(
                "Export History",
                "There are no visible history rows to export.",
            )
            return
        target, _selected = QFileDialog.getSaveFileName(
            None,
            "Export visible history",
            "droid_alerts_history.csv",
            "CSV files (*.csv)",
        )
        if not target:
            return
        rows = list(self._raw_rows.values())
        fieldnames = sorted({key for row in rows for key in row})
        try:
            with Path(target).open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=fieldnames,
                    extrasaction="ignore",
                )
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {
                            key: json.dumps(value)
                            if isinstance(value, (dict, list))
                            else value
                            for key, value in row.items()
                        }
                    )
        except OSError as exc:
            self.runtime.dialogs.show_message(
                "Export History", str(exc), tone="danger"
            )
            return
        self.runtime.detailChanged.emit(f"History exported to {target}")

    @Slot()
    def openLogs(self) -> None:
        if not self.runtime.open_path(logs_dir()):
            self.runtime.dialogs.show_message(
                "Open Logs",
                "The logs folder could not be opened.",
                tone="danger",
            )

    def shutdown(self) -> None:
        self._timer.stop()
