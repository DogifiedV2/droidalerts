from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.events import log_track_event
from droid_alerts.belt.region import DEFAULT_REGION, RelativeRegion, load_region, save_region
from droid_alerts.belt.targets import BELT_FAMILY_ORDER, belt_family_meets_minimum, normalize_belt_target_tiers
from droid_alerts.capture import MonitorDescriptor, PixelBox
from droid_alerts.config import AppConfig
from droid_alerts.popup import _calculate_popup_layout


class BeltConfigAndRegionTests(unittest.TestCase):
    def test_config_round_trip_normalizes_targets_and_preserves_overlay(self):
        config = AppConfig.from_dict(
            {
                "belt_overlay_enabled": False,
                "belt_dev_mode": True,
                "belt_template_collection_enabled": True,
                "belt_cpu_warning_confirmed": True,
                "belt_region_guide_confirmed": True,
                "belt_target_names": ["GONK"],
                "belt_target_tiers": {
                    " r2 ": "gold",
                    "GONK": "Beskar",
                    "not-a-droid": "Default",
                    "IG": "not-a-tier",
                },
            }
        )

        self.assertFalse(config.belt_overlay_enabled)
        self.assertTrue(config.belt_dev_mode)
        self.assertTrue(config.belt_template_collection_enabled)
        self.assertTrue(config.belt_cpu_warning_confirmed)
        self.assertTrue(config.belt_region_guide_confirmed)
        self.assertEqual({"GONK": "Beskar", "R2": "Gold"}, config.belt_target_tiers)
        restored = AppConfig.from_dict(config.to_dict())
        self.assertEqual(config.belt_target_tiers, restored.belt_target_tiers)
        self.assertNotIn("belt_target_names", config.to_dict())
        self.assertTrue(restored.belt_cpu_warning_confirmed)
        self.assertTrue(restored.belt_region_guide_confirmed)
        self.assertTrue(restored.belt_dev_mode)
        self.assertTrue(restored.belt_template_collection_enabled)

    def test_belt_family_threshold_uses_requested_progression(self):
        self.assertEqual(
            ("Default", "Gold", "Diamond", "Rainbow", "Beskar", "Galactic"),
            BELT_FAMILY_ORDER,
        )
        self.assertTrue(belt_family_meets_minimum("Gold", "Gold"))
        self.assertTrue(belt_family_meets_minimum("Beskar", "Gold"))
        self.assertTrue(belt_family_meets_minimum("Galactic", "Beskar"))
        self.assertFalse(belt_family_meets_minimum("Beskar", "Galactic"))
        self.assertTrue(belt_family_meets_minimum("Rainbow", "Diamond"))
        self.assertFalse(belt_family_meets_minimum("Gold", "Diamond"))
        self.assertTrue(belt_family_meets_minimum("", "Default"))
        self.assertFalse(belt_family_meets_minimum("", "Gold"))

    def test_target_normalization_keeps_only_real_droids_and_tiers(self):
        self.assertEqual(
            {"R2": "Galactic", "CYCLENS": "Gold", "IG": "Default"},
            normalize_belt_target_tiers(
                {
                    "ig": "default",
                    " cyclens ": "GOLD",
                    "unknown": "Beskar",
                    "R2": "galactic",
                }
            ),
        )

    def test_belt_events_are_written_to_shared_history(self):
        track = SimpleNamespace(
            id=7,
            name="R2",
            confidence=0.93,
            raw_text="R2",
            family="Diamond",
            family_confidence=0.94,
            rarity="Common",
            rarity_confidence=0.91,
            box=(10.0, 20.0, 30.0, 40.0),
        )
        event = SimpleNamespace(kind="entered", track=track)

        with patch("droid_alerts.logging_io.append_event") as append:
            record = log_track_event(event, alerted=True)

        self.assertEqual("belt_entered", record["event_type"])
        self.assertEqual("belt_tracker", record["source"])
        self.assertEqual("Diamond Common", record["rarity"])
        self.assertEqual("Diamond", record["card_family"])
        self.assertEqual("Common", record["card_rarity"])
        self.assertEqual(0.91, record["rarity_confidence"])
        self.assertTrue(record["alerted"])
        append.assert_called_once_with(record, filename="events.jsonl")

    def test_regions_are_saved_independently_for_each_monitor(self):
        first = MonitorDescriptor(
            index=1, left=0, top=0, width=1920, height=1080, unique_id="first"
        )
        second = MonitorDescriptor(
            index=2, left=1920, top=0, width=2560, height=1440, unique_id="second"
        )
        first_region = RelativeRegion(0.1, 0.2, 0.7, 0.6)
        second_region = RelativeRegion(0.05, 0.1, 0.8, 0.75)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regions.json"
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                save_region(first, first_region)
                save_region(second, second_region)
                self.assertEqual(first_region, load_region(first))
                self.assertEqual(second_region, load_region(second))

    def test_relative_region_scales_with_the_same_monitor(self):
        original = MonitorDescriptor(
            index=1, left=0, top=0, width=1920, height=1080, unique_id="game"
        )
        resized = MonitorDescriptor(
            index=2, left=0, top=0, width=2560, height=1440, unique_id="game"
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regions.json"
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                save_region(
                    original,
                    RelativeRegion.from_pixels(PixelBox(192, 108, 960, 540), original),
                )
                loaded = load_region(resized)

        self.assertIsNotNone(loaded)
        self.assertEqual(PixelBox(256, 144, 1280, 720), loaded.to_pixels(resized))

    def test_legacy_name_strip_region_is_not_reused(self):
        monitor = MonitorDescriptor(index=1, left=0, top=0, width=1728, height=1117)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regions.json"
            path.write_text(
                json.dumps(
                    {
                        monitor.key: {
                            "left": 0.01,
                            "top": 0.27,
                            "width": 0.98,
                            "height": 0.29,
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("droid_alerts.belt.region.regions_path", return_value=path):
                self.assertEqual(DEFAULT_REGION, load_region(monitor))


class BeltAlertPresentationTests(unittest.TestCase):
    def test_popup_layout_keeps_card_and_icon_inside_monitor_at_every_scale(self):
        for screen_width, screen_height in ((800, 600), (1280, 720), (1920, 1080)):
            for scale in (0.7, 1.0, 1.5):
                with self.subTest(screen=(screen_width, screen_height), scale=scale):
                    layout = _calculate_popup_layout(
                        screen_width,
                        screen_height,
                        scale,
                        icon_width=128,
                        icon_height=128,
                    )
                    self.assertLessEqual(layout.width + layout.margin * 2, screen_width)
                    self.assertLessEqual(layout.height + layout.margin * 2, screen_height)
                    self.assertLessEqual(layout.panel_width, layout.width)
                    self.assertLessEqual(layout.panel_height, layout.height)
                    if layout.show_icon:
                        self.assertEqual(
                            layout.width,
                            layout.panel_width + 128 - 34 + 22,
                        )


if __name__ == "__main__":
    unittest.main()
