from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..capture import MonitorDescriptor, MonitorInfo, PixelBox
from ..config import config_dir


Monitor = MonitorDescriptor | MonitorInfo


def regions_path() -> Path:
    return config_dir() / "belt_regions.json"


@dataclass(frozen=True)
class RelativeRegion:
    left: float
    top: float
    width: float
    height: float
    # Version 2 means the selection intentionally contains full card artwork.
    # Legacy regions often captured only the lower name strip and cannot
    # provide the context required to reject HUD/price text safely.
    version: int = 2

    def to_pixels(self, monitor: Monitor) -> PixelBox:
        return PixelBox(
            left=max(0, round(self.left * monitor.width)),
            top=max(0, round(self.top * monitor.height)),
            width=max(20, round(self.width * monitor.width)),
            height=max(20, round(self.height * monitor.height)),
        )

    @classmethod
    def from_pixels(cls, box: PixelBox, monitor: Monitor) -> "RelativeRegion":
        return cls(
            box.left / monitor.width,
            box.top / monitor.height,
            box.width / monitor.width,
            box.height / monitor.height,
        )


def load_region(monitor: Monitor) -> RelativeRegion | None:
    path = regions_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        item = raw.get(monitor.key)
        if not item or int(item.get("version", 1)) < 2:
            return None
        return RelativeRegion(**item)
    except (OSError, ValueError, TypeError):
        return None


def save_region(monitor: Monitor, region: RelativeRegion) -> None:
    path = regions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError, TypeError):
        raw = {}
    raw[monitor.key] = asdict(region)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
