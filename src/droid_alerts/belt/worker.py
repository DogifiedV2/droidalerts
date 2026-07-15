from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..capture import PixelBox
from .watcher import run_belt_watcher


def run_belt_worker_process(
    monitor_index: int,
    region: PixelBox,
    target_names: Iterable[str],
    stop_event: Any,
    status_queue: Any,
    dev_mode: bool = False,
) -> None:
    """Run Belt Tracker outside the GUI process.

    RapidOCR's ONNX session construction can hold Python's GIL for many
    seconds on Windows. A process keeps that startup work from starving Tk's
    event loop while still forwarding the watcher's normal status messages.
    """

    try:
        run_belt_watcher(
            monitor_index,
            region,
            target_names=target_names,
            stop_event=stop_event,
            status_callback=status_queue.put,
            dev_mode=dev_mode,
        )
    except Exception as exc:
        status_queue.put(
            {
                "type": "error",
                "message": f"Belt Tracker process failed: {exc}",
            }
        )
