from __future__ import annotations

import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.alerts import AlertPolicy
from droid_alerts.classifier import Detection
from droid_alerts.config import AppConfig
from droid_alerts.gui import ALERT_COMBOS
from droid_alerts.notifications import alert_title, alert_type_id, discord_color, event_text


def _detection(droid: str = "Diamond", rarity: str = "Mythic") -> Detection:
    return Detection(
        droid=droid,
        rarity=rarity,
        row_box=(0, 0, 845, 44),
        droid_score=0.99,
        rarity_score=0.99,
        rarity_margin=0.99,
        score=0.99,
        source="test",
        # This used to silently suppress Mythic alerts even though the logs
        # showed the row as a priority detection.
        shape_score=0.0,
    )


def _source_detection(
    *, source: str, droid: str = "Beskar", rarity: str = "Mythic"
) -> Detection:
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


def main() -> int:
    config = AppConfig()
    policy = AlertPolicy(config)
    priority = _detection()

    failures: list[str] = []
    if not priority.should_alert:
        failures.append("priority detection should be marked alertable")
    if not policy.should_alert(priority, "diamond-mythic-row"):
        failures.append("first priority detection should fire")
    if policy.should_alert(priority, "diamond-mythic-row"):
        failures.append("duplicate row hash should still be deduped")

    disabled_config = AppConfig(alert_targets=[])
    if AlertPolicy(disabled_config).should_alert(priority, "disabled-row"):
        failures.append("disabled target should not fire")

    rainbow_epic = _detection("Rainbow", "Epic")
    if not rainbow_epic.should_alert:
        failures.append("Rainbow Epic should be an alertable priority combo")
    rainbow_legendary = _detection("Rainbow", "Legendary")
    if not rainbow_legendary.should_alert:
        failures.append("Rainbow Legendary should be an alertable priority combo")
    default_config = AppConfig()
    if ("Rainbow", "Epic") in default_config.targets:
        failures.append("Rainbow Epic should be disabled by default")
    if ("Rainbow", "Legendary") in default_config.targets:
        failures.append("Rainbow Legendary should be disabled by default")
    expected_first_slots = (
        ("Rainbow", "Epic"),
        ("Rainbow", "Legendary"),
        ("Rainbow", "Mythic"),
    )
    if ALERT_COMBOS[:3] != expected_first_slots:
        failures.append("Rainbow priorities should occupy the first toggle group")
    enabled_epic_config = AppConfig(alert_targets=[["Rainbow", "Epic"]])
    if not AlertPolicy(enabled_epic_config).should_alert(rainbow_epic, "rainbow-epic-row"):
        failures.append("enabled Rainbow Epic target should fire")
    enabled_config = AppConfig(alert_targets=[["Rainbow", "Legendary"]])
    if not AlertPolicy(enabled_config).should_alert(rainbow_legendary, "rainbow-legendary-row"):
        failures.append("enabled Rainbow Legendary target should fire")

    galactic_combos = {
        ("Galactic", "Epic"),
        ("Galactic", "Legendary"),
        ("Galactic", "Mythic"),
    }
    if not galactic_combos.issubset(set(ALERT_COMBOS)):
        failures.append("supported Galactic priority toggles should be available")
    removed_galactic = {("Galactic", "Common"), ("Galactic", "Rare")}
    if removed_galactic & set(ALERT_COMBOS):
        failures.append("retired Galactic Common/Rare toggles should not be available")
    default_galactic = set(galactic_combos)
    if (galactic_combos & default_config.targets) != default_galactic:
        failures.append("only Galactic Epic, Legendary, and Mythic should be on by default")
    migrated_defaults = AppConfig.from_dict(
        {
            "alert_targets": [
                ["Beskar", "Epic"],
                ["Beskar", "Legendary"],
                ["Diamond", "Mythic"],
                ["Rainbow", "Mythic"],
                ["Beskar", "Mythic"],
            ]
        }
    )
    if not default_galactic.issubset(migrated_defaults.targets):
        failures.append("legacy default selections should enable the new Galactic defaults")
    retired_targets = AppConfig.from_dict(
        {
            "alert_targets": [
                ["Galactic", "Common"],
                ["Galactic", "Rare"],
                ["Galactic", "Epic"],
            ]
        }
    )
    if retired_targets.targets != {("Galactic", "Epic")}:
        failures.append("saved Galactic Common/Rare targets should be removed on load")
    retired_only = AppConfig.from_dict(
        {"alert_targets": [["Galactic", "Common"], ["Galactic", "Rare"]]}
    )
    if retired_only.targets:
        failures.append("a retired-only selection should migrate to no targets")
    for droid, rarity in removed_galactic:
        if _detection(droid, rarity).should_alert:
            failures.append(f"retired {droid} {rarity} should not be alertable")
    for droid, rarity in galactic_combos:
        detection = _detection(droid, rarity)
        if not detection.should_alert:
            failures.append(f"{droid} {rarity} should be an alertable priority combo")
        galactic_config = AppConfig(alert_targets=[[droid, rarity]])
        if not AlertPolicy(galactic_config).should_alert(
            detection, f"{droid.lower()}-{rarity.lower()}-row"
        ):
            failures.append(f"enabled {droid} {rarity} target should fire")
        if discord_color(detection) != 0x9200E0:
            failures.append(f"{droid} {rarity} should use the Galactic alert color")

    non_priority = _detection("Diamond", "Legendary")
    if non_priority.should_alert:
        failures.append("non-priority combo should not be marked alertable")
    if AlertPolicy(config).should_alert(non_priority, "diamond-legendary-row"):
        failures.append("non-target combo should not fire")

    if failures:
        print("alert policy failures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("alert policy OK: priority detections fire, disabled/duplicate rows stay quiet")
    return 0


class AlertPolicyGuardTests(unittest.TestCase):
    def test_alert_policy_guard(self):
        self.assertEqual(0, main())


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
        self.assertEqual("rebirth_ready", alert_type_id(_source_detection(source="rebirth-ready")))
        self.assertEqual("rebirth_available", alert_type_id(_source_detection(source="rebirth-alert")))
        self.assertEqual("belt_tracker", alert_type_id(_source_detection(source="belt-tracker")))
        self.assertEqual("limited_deals", alert_type_id(_source_detection(source="limited-deal")))
        self.assertEqual(
            "timer:mythic",
            alert_type_id(
                _source_detection(
                    source="timer-reminder",
                    droid="Mythic Timer",
                    rarity="60",
                )
            ),
        )
        self.assertEqual("chat:Beskar:Mythic", alert_type_id(_source_detection(source="watcher")))

    def test_timer_reminder_has_channel_friendly_text(self):
        detection = _source_detection(
            source="timer-reminder",
            droid="Mythic Timer",
            rarity="60",
        )

        self.assertEqual("Mythic Timer in 60 seconds", event_text(detection))
        self.assertEqual("Droid Alerts Timer Reminder", alert_title(detection))
        self.assertEqual(0xFF3FA8, discord_color(detection))


if __name__ == "__main__":
    unittest.main()
