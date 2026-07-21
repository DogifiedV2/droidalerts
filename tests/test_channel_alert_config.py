from __future__ import annotations

import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.classifier import Detection
from droid_alerts.config import AppConfig
from droid_alerts.notifications import alert_type_id


def detection(*, source: str, droid: str = "Beskar", rarity: str = "Mythic") -> Detection:
    return Detection(
        droid=droid,
        rarity=rarity,
        row_box=(0, 0, 1, 1),
        droid_score=1.0,
        rarity_score=1.0,
        rarity_margin=1.0,
        score=1.0,
        source=source,
        shape_score=1.0,
    )


class ChannelAlertConfigTests(unittest.TestCase):
    def test_existing_configs_allow_every_alert_on_every_channel(self):
        config = AppConfig.from_dict({})
        self.assertTrue(config.channel_allows_alert("discord", "rebirth_ready"))
        self.assertTrue(config.channel_allows_alert("ntfy", "chat:Beskar:Mythic"))

    def test_disabled_alerts_round_trip_and_only_affect_selected_channel(self):
        config = AppConfig(channel_disabled_alerts={"discord": ["rebirth_ready"]})
        restored = AppConfig.from_dict(config.to_dict())
        self.assertFalse(restored.channel_allows_alert("discord", "rebirth_ready"))
        self.assertTrue(restored.channel_allows_alert("ntfy", "rebirth_ready"))

    def test_detection_sources_map_to_stable_filter_ids(self):
        self.assertEqual("rebirth_ready", alert_type_id(detection(source="rebirth-ready")))
        self.assertEqual("rebirth_available", alert_type_id(detection(source="rebirth-alert")))
        self.assertEqual("belt_tracker", alert_type_id(detection(source="belt-tracker")))
        self.assertEqual("limited_deals", alert_type_id(detection(source="limited-deal")))
        self.assertEqual("chat:Beskar:Mythic", alert_type_id(detection(source="watcher")))


if __name__ == "__main__":
    unittest.main()
