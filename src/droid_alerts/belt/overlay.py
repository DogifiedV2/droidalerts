from __future__ import annotations

from ..capture import MonitorDescriptor, MonitorInfo, PixelBox
from ..ui.overlays import BeltTrackOverlay


MAX_VISIBLE_LABELS = 16


class BeltOverlay:
    """Compatibility wrapper for the Qt Belt Tracker overlay."""

    def __init__(self, root=None) -> None:
        del root
        self._widget = BeltTrackOverlay()
        self._monitor: MonitorDescriptor | MonitorInfo | None = None
        self._region: PixelBox | None = None

    def configure(
        self,
        monitor: MonitorDescriptor | MonitorInfo,
        region: PixelBox,
    ) -> None:
        self._monitor = monitor
        self._region = region
        self._widget.show_tracks(monitor, region, [])

    def update_tracks(self, tracks: list[dict[str, object]]) -> None:
        if self._monitor is None or self._region is None:
            return
        self._widget.show_tracks(self._monitor, self._region, tracks)

    def close(self) -> None:
        self._widget.close()
        self._monitor = None
        self._region = None


__all__ = ["BeltOverlay", "MAX_VISIBLE_LABELS"]
