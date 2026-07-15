from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..capture import PixelBox
from .watcher import run_belt_watcher


def run_belt_worker_process(
    monitor_index: int,
    region: PixelBox,
    target_tiers: Mapping[str, str],
    stop_event: Any,
    status_queue: Any,
    dev_mode: bool = False,
    collect_template_samples: bool = False,
    idle_scan_fps: int = 4,
    active_scan_fps: int = 8,
) -> None:
    """Run Belt Tracker outside the GUI process.

    A process keeps capture and recognition work from starving Tk's event
    loop while still forwarding the watcher's normal status messages.
    """

    try:
        run_belt_watcher(
            monitor_index,
            region,
            target_tiers=target_tiers,
            stop_event=stop_event,
            status_callback=status_queue.put,
            dev_mode=dev_mode,
            collect_template_samples=collect_template_samples,
            idle_scan_fps=idle_scan_fps,
            active_scan_fps=active_scan_fps,
        )
    except Exception as exc:
        status_queue.put(
            {
                "type": "error",
                "message": f"Belt Tracker process failed: {exc}",
            }
        )
