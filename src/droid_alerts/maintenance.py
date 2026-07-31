from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .config import data_dir
from .logging_io import EVENT_LOG_LOCK, alert_samples_dir, debug_dir, logs_dir


SHORT_LIVED_HISTORY_TYPES = frozenset(
    {"detected", "seen", "rejected", "scrap_scan", "debug_snapshot"}
)
SHORT_LIVED_HISTORY_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class CleanupResult:
    deleted_files: int = 0
    freed_bytes: int = 0


def format_bytes(value: int) -> str:
    amount = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024.0 or unit == "GB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} GB"


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for file in path.rglob("*"):
        try:
            if file.is_file():
                total += file.stat().st_size
        except OSError:
            continue
    return total


def storage_summary() -> dict[str, int]:
    summary = {"logs": 0, "samples": 0, "debug": 0, "belt_dev": 0, "total": 0}
    root = data_dir()
    if not root.exists():
        return summary

    categories = {
        logs_dir(): "logs",
        alert_samples_dir(): "samples",
        debug_dir(): "debug",
        root / "belt_dev": "belt_dev",
    }
    pending: list[tuple[Path, str | None]] = [(root, None)]
    while pending:
        folder, inherited_key = pending.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    child = Path(entry.path)
                    pending.append((child, inherited_key or categories.get(child)))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            summary["total"] += size
            if inherited_key is not None:
                summary[inherited_key] += size
    return summary


def clear_directory(path: Path) -> CleanupResult:
    deleted = freed = 0
    if not path.exists():
        return CleanupResult()
    for file in sorted(path.rglob("*"), reverse=True):
        try:
            if file.is_file():
                freed += file.stat().st_size
                file.unlink()
                deleted += 1
            elif file.is_dir():
                file.rmdir()
        except OSError:
            continue
    return CleanupResult(deleted, freed)


def clear_history() -> CleanupResult:
    with EVENT_LOG_LOCK:
        return clear_directory(logs_dir())


def clear_debug_captures() -> CleanupResult:
    return clear_directory(debug_dir())


def cleanup_runtime_data(retention_days: int, max_storage_mb: int) -> CleanupResult:
    """Remove old captures and cap runtime-data usage without touching updates."""
    deleted = freed = 0
    managed_roots = [
        logs_dir(),
        alert_samples_dir(),
        debug_dir(),
        data_dir() / "belt_dev",
        data_dir() / "support_bundles",
    ]
    protected_log = logs_dir() / "events.jsonl"
    cutoff = time.time() - max(0, retention_days) * 86400

    if retention_days > 0:
        for root in managed_roots:
            if not root.exists():
                continue
            for file in root.rglob("*"):
                try:
                    if not file.is_file() or file == protected_log or file.stat().st_mtime >= cutoff:
                        continue
                    size = file.stat().st_size
                    file.unlink()
                    deleted += 1
                    freed += size
                except OSError:
                    continue
    with EVENT_LOG_LOCK:
        _trim_history_by_age(
            protected_log,
            cutoff if retention_days > 0 else 0.0,
            short_lived_cutoff=time.time() - SHORT_LIVED_HISTORY_SECONDS,
        )

    max_bytes = max(0, max_storage_mb) * 1024 * 1024
    if max_bytes > 0:
        total = sum(directory_size(root) for root in managed_roots)
        candidates: list[Path] = []
        for root in managed_roots[1:]:
            if root.exists():
                candidates.extend(file for file in root.rglob("*") if file.is_file())
        candidates.sort(key=lambda file: _safe_mtime(file))
        for file in candidates:
            if total <= max_bytes:
                break
            try:
                size = file.stat().st_size
                file.unlink()
                total -= size
                deleted += 1
                freed += size
            except OSError:
                continue
        if total > max_bytes and protected_log.exists():
            with EVENT_LOG_LOCK:
                before = protected_log.stat().st_size
                _trim_history_to_bytes(protected_log, max(1_000_000, max_bytes // 4))
                after = protected_log.stat().st_size if protected_log.exists() else 0
            freed += max(0, before - after)

    return CleanupResult(deleted, freed)


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _trim_history_by_age(
    path: Path,
    cutoff: float,
    *,
    short_lived_cutoff: float | None = None,
) -> None:
    if not path.exists():
        return

    def event_metadata(line: bytes) -> tuple[float, str, bool] | None:
        try:
            event = json.loads(line)
            value = str(event.get("ts") or "")[:15]
            timestamp = time.mktime(time.strptime(value, "%Y%m%d_%H%M%S"))
            return (
                timestamp,
                str(event.get("event_type") or ""),
                bool(event.get("alerted")),
            )
        except (
            AttributeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ):
            return None

    def should_delete(metadata: tuple[float, str, bool] | None) -> bool:
        if metadata is None:
            return False
        timestamp, event_type, alerted = metadata
        if alerted:
            return False
        if timestamp < cutoff:
            return True
        return (
            short_lived_cutoff is not None
            and event_type in SHORT_LIVED_HISTORY_TYPES
            and timestamp < short_lived_cutoff
        )

    newest_cutoff = max(cutoff, short_lived_cutoff or cutoff)

    try:
        # History is append-only and chronological. Scan only the portion old
        # enough to contain removable entries, and avoid rewriting the file at
        # all when there is nothing to remove.
        needs_rewrite = False
        with path.open("rb") as source:
            for line in source:
                metadata = event_metadata(line)
                if metadata is None:
                    continue
                if should_delete(metadata):
                    needs_rewrite = True
                    break
                if metadata[0] >= newest_cutoff:
                    break
        if not needs_rewrite:
            return

        temp = path.with_suffix(path.suffix + ".tmp")
        processed = 0
        with path.open("rb") as source, temp.open("wb") as target:
            for line in source:
                metadata = event_metadata(line)
                if should_delete(metadata):
                    processed += 1
                    if processed % 512 == 0:
                        time.sleep(0)
                    continue
                target.write(line)
                if metadata is not None and metadata[0] >= newest_cutoff:
                    # Everything after this point is newer than both retention
                    # cutoffs, so it can be copied without further JSON work.
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                    break
                processed += 1
                if processed % 512 == 0:
                    # Explicitly yield during large startup cleanups so the Qt
                    # event loop remains responsive.
                    time.sleep(0)
            else:
                target.flush()
        temp.replace(path)
    except OSError:
        return


def _trim_history_to_bytes(path: Path, limit: int) -> None:
    try:
        lines = path.read_bytes().splitlines(keepends=True)
    except OSError:
        return
    kept: list[bytes] = []
    size = 0
    for line in reversed(lines):
        if kept and size + len(line) > limit:
            break
        kept.append(line)
        size += len(line)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(b"".join(reversed(kept)))
    temp.replace(path)


def _atomic_lines_write(path: Path, lines: list[str]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    text = "\n".join(lines)
    temp.write_text(text + ("\n" if text else ""), encoding="utf-8")
    temp.replace(path)
