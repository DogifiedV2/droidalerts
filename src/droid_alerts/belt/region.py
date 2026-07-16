from __future__ import annotations

import json
from dataclasses import asdict, dataclass
import math
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

    def is_valid(self) -> bool:
        values = (self.left, self.top, self.width, self.height)
        return (
            self.version >= 2
            and all(math.isfinite(value) for value in values)
            and 0.0 <= self.left < 1.0
            and 0.0 <= self.top < 1.0
            and 0.01 <= self.width <= 1.0 - self.left
            and 0.01 <= self.height <= 1.0 - self.top
        )

    def to_pixels(self, monitor: Monitor) -> PixelBox:
        left = max(0, round(self.left * monitor.width))
        top = max(0, round(self.top * monitor.height))
        return PixelBox(
            left=left,
            top=top,
            width=max(
                1,
                min(monitor.width - left, max(20, round(self.width * monitor.width))),
            ),
            height=max(
                1,
                min(monitor.height - top, max(20, round(self.height * monitor.height))),
            ),
        )

    @classmethod
    def from_pixels(cls, box: PixelBox, monitor: Monitor) -> "RelativeRegion":
        return cls(
            box.left / monitor.width,
            box.top / monitor.height,
            box.width / monitor.width,
            box.height / monitor.height,
        )


DEFAULT_REGION = RelativeRegion(
    left=0.16608796296296297,
    top=0.13249776186213072,
    width=0.7008101851851852,
    height=0.36884512085944493,
)


def load_region(monitor: Monitor) -> RelativeRegion:
    path = regions_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return DEFAULT_REGION
        item = raw.get(monitor.key)
        if not isinstance(item, dict) or int(item.get("version", 1)) < 2:
            return DEFAULT_REGION
        region = RelativeRegion(**item)
        return region if region.is_valid() else DEFAULT_REGION
    except (OSError, OverflowError, ValueError, TypeError):
        return DEFAULT_REGION


def save_region(monitor: Monitor, region: RelativeRegion) -> None:
    path = regions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError, TypeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw[monitor.key] = asdict(region)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
