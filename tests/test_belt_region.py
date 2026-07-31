from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.region import (
    DEFAULT_REGION,
    RelativeRegion,
    load_region,
    load_saved_region,
    save_region,
)
from droid_alerts.capture import MonitorDescriptor, MonitorInfo, PixelBox


REFERENCE_BOX = PixelBox(287, 148, 1211, 412)


class BeltRegionDefaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monitor = MonitorDescriptor(
            index=1,
            left=0,
            top=0,
            width=1728,
            height=1117,
            unique_id="reference",
        )

    def test_missing_region_file_uses_normalized_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                region = load_region(self.monitor)

        self.assertEqual(DEFAULT_REGION, region)
        self.assertEqual(REFERENCE_BOX, region.to_pixels(self.monitor))

    def test_corrupt_region_file_uses_normalized_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regions.json"
            path.write_text("{not json", encoding="utf-8")
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                self.assertEqual(DEFAULT_REGION, load_region(self.monitor))

    def test_save_recovers_from_structurally_corrupt_region_file(self):
        custom = RelativeRegion(0.1, 0.2, 0.6, 0.5)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regions.json"
            path.write_text("[]", encoding="utf-8")
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                save_region(self.monitor, custom)
                self.assertEqual(custom, load_region(self.monitor))

    def test_legacy_and_invalid_saved_regions_use_normalized_default(self):
        cases = (
            "not-a-region",
            {
                "left": 0.01,
                "top": 0.27,
                "width": 0.98,
                "height": 0.29,
            },
            {
                "version": 2,
                "left": 0.8,
                "top": 0.1,
                "width": 0.4,
                "height": 0.4,
            },
        )
        for saved in cases:
            with self.subTest(saved=saved), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "regions.json"
                path.write_text(json.dumps({self.monitor.key: saved}), encoding="utf-8")
                with patch("droid_alerts.belt.region.regions_path", return_value=path):
                    self.assertEqual(DEFAULT_REGION, load_region(self.monitor))

    def test_valid_custom_region_overrides_default_for_only_its_monitor(self):
        custom_monitor = MonitorDescriptor(
            index=1,
            left=0,
            top=0,
            width=1920,
            height=1080,
            unique_id="custom",
        )
        other_monitor = MonitorDescriptor(
            index=2,
            left=1920,
            top=0,
            width=2560,
            height=1440,
            unique_id="other",
        )
        custom = RelativeRegion(0.10, 0.20, 0.60, 0.50)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regions.json"
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                save_region(custom_monitor, custom)
                self.assertEqual(custom, load_region(custom_monitor))
                self.assertEqual(DEFAULT_REGION, load_region(other_monitor))

    def test_legacy_monitor_region_is_migrated_to_window_coordinates(self):
        monitor = MonitorInfo(
            1920,
            0,
            2560,
            1440,
            index=2,
            key=r"id:\\?\DISPLAY#MONITOR#UID184581",
        )
        stored_monitor = MonitorInfo(
            1920,
            0,
            2560,
            1440,
            index=2,
            key=r"id:\\?\DISPLAY#MONITOR#UID184577",
        )
        window = MonitorInfo(
            2100,
            100,
            1600,
            900,
            index=2,
            key="window:fortnite",
        )
        legacy = RelativeRegion(
            left=280 / 2560,
            top=200 / 1440,
            width=800 / 2560,
            height=400 / 1440,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regions.json"
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                save_region(stored_monitor, legacy)
                migrated = load_region(window, legacy_monitor=monitor)

                self.assertEqual(PixelBox(100, 100, 800, 400), migrated.to_pixels(window))
                self.assertEqual(migrated, load_saved_region(window))
                self.assertEqual(legacy, load_saved_region(stored_monitor))

    def test_existing_window_region_wins_over_legacy_monitor_region(self):
        monitor = MonitorInfo(0, 0, 1920, 1080, key="id:legacy-monitor")
        window = MonitorInfo(100, 50, 1600, 900, key="window:fortnite")
        legacy = RelativeRegion(0.1, 0.1, 0.5, 0.4)
        current = RelativeRegion(0.2, 0.2, 0.6, 0.5)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regions.json"
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                save_region(monitor, legacy)
                save_region(window, current)

                self.assertEqual(
                    current,
                    load_region(window, legacy_monitor=monitor),
                )
                self.assertEqual(legacy, load_saved_region(monitor))

    def test_default_scales_across_resolutions(self):
        cases = (
            (1728, 1117, REFERENCE_BOX),
            (1920, 1080, PixelBox(319, 143, 1346, 398)),
            (2560, 1440, PixelBox(425, 191, 1794, 531)),
        )
        for width, height, expected in cases:
            with self.subTest(resolution=(width, height)):
                monitor = MonitorInfo(0, 0, width, height, key=f"{width}x{height}")
                self.assertEqual(expected, DEFAULT_REGION.to_pixels(monitor))

    def test_macos_and_windows_monitor_offsets_do_not_change_local_capture_scaling(self):
        # MSS on macOS and DXCam on Windows both receive monitor-local boxes;
        # global offsets are added only by capture/overlay backends.
        monitors = (
            MonitorInfo(-1728, -1117, 1728, 1117, key="mac-stacked"),
            MonitorInfo(1920, 0, 1728, 1117, key="windows-side-by-side"),
        )
        for monitor in monitors:
            with self.subTest(monitor=monitor.key):
                self.assertEqual(REFERENCE_BOX, DEFAULT_REGION.to_pixels(monitor))


if __name__ == "__main__":
    unittest.main()
