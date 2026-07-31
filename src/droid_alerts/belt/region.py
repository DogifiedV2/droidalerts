from __future__ import annotations

import json
from dataclasses import asdict, dataclass
import math
from pathlib import Path
import re

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


def _region_from_item(item: object) -> RelativeRegion | None:
    if not isinstance(item, dict):
        return None
    try:
        if int(item.get("version", 1)) < 2:
            return None
        region = RelativeRegion(**item)
    except (OverflowError, TypeError, ValueError):
        return None
    return region if region.is_valid() else None


def _saved_regions() -> dict[str, object]:
    try:
        raw = json.loads(regions_path().read_text(encoding="utf-8"))
    except (OSError, OverflowError, ValueError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load_saved_region(monitor: Monitor) -> RelativeRegion | None:
    """Return only a valid region explicitly saved for ``monitor``."""

    return _region_from_item(_saved_regions().get(monitor.key))


def _legacy_monitor_region(monitor: Monitor) -> RelativeRegion | None:
    """Find a 1.3.9 monitor entry even if Windows changed its UID suffix."""

    raw = _saved_regions()
    exact = _region_from_item(raw.get(monitor.key))
    if exact is not None:
        return exact

    candidates = [
        (key, region)
        for key, item in raw.items()
        if not str(key).startswith(("window:", "device:"))
        and (region := _region_from_item(item)) is not None
    ]
    normalized_key = re.sub(r"UID\d+", "UID*", monitor.key, flags=re.IGNORECASE)
    hardware_matches = [
        region
        for key, region in candidates
        if re.sub(r"UID\d+", "UID*", str(key), flags=re.IGNORECASE)
        == normalized_key
    ]
    if len(hardware_matches) == 1:
        return hardware_matches[0]
    if len(candidates) == 1:
        return candidates[0][1]
    return None


def migrate_legacy_monitor_region(
    source: Monitor,
    legacy_monitor: Monitor,
) -> RelativeRegion | None:
    """Copy a 1.3.9 monitor region into 1.4.0 window coordinates.

    Version 1.3.9 saved window-capture selections relative to the physical
    monitor. Version 1.4.0 saves them relative to the selected window. Keep the
    original monitor entry and add a translated window entry on first use.
    """

    if source.key == legacy_monitor.key:
        return None
    legacy = _legacy_monitor_region(legacy_monitor)
    if legacy is None:
        return None

    legacy_box = legacy.to_pixels(legacy_monitor)
    left = legacy_monitor.left + legacy_box.left - source.left
    top = legacy_monitor.top + legacy_box.top - source.top
    right = left + legacy_box.width
    bottom = top + legacy_box.height

    # A window may have moved or changed size since 1.3.9. Preserve the part
    # of the old selection that still lies inside its current capture area.
    clipped_left = max(0, left)
    clipped_top = max(0, top)
    clipped_right = min(source.width, right)
    clipped_bottom = min(source.height, bottom)
    if clipped_right <= clipped_left or clipped_bottom <= clipped_top:
        return None

    migrated = RelativeRegion.from_pixels(
        PixelBox(
            left=clipped_left,
            top=clipped_top,
            width=clipped_right - clipped_left,
            height=clipped_bottom - clipped_top,
        ),
        source,
    )
    if not migrated.is_valid():
        return None
    try:
        save_region(source, migrated)
    except OSError:
        # A read-only or temporarily locked config must not prevent tracking.
        pass
    return migrated


def load_region(
    monitor: Monitor,
    *,
    legacy_monitor: Monitor | None = None,
) -> RelativeRegion:
    saved = load_saved_region(monitor)
    if saved is not None:
        return saved
    if legacy_monitor is not None:
        migrated = migrate_legacy_monitor_region(monitor, legacy_monitor)
        if migrated is not None:
            return migrated
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
