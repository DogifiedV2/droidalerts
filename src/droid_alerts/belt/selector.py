from __future__ import annotations

from collections.abc import Callable

from ..capture import MonitorDescriptor, MonitorInfo, PixelBox
from ..ui.region_selector import RegionSelector as QtRegionSelector


class RegionSelector(QtRegionSelector):
    """Compatibility wrapper for the Qt belt-region selector."""

    def __init__(
        self,
        root,
        monitor: MonitorDescriptor | MonitorInfo,
        on_selected: Callable[[PixelBox], None],
        *,
        on_cancelled: Callable[[], None] | None = None,
        capture=None,
        display_monitor: MonitorDescriptor | MonitorInfo | None = None,
    ) -> None:
        del root
        super().__init__(
            monitor,
            on_selected,
            on_cancelled=on_cancelled,
            capture=capture,
            display_monitor=display_monitor,
        )
        self.window = self


__all__ = ["RegionSelector"]
