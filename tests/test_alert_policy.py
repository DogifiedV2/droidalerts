from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.alerts import AlertPolicy
from droid_alerts.classifier import Detection
from droid_alerts.config import AppConfig
from droid_alerts.gui import ALERT_COMBOS
from droid_alerts.notifications import discord_color


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
    expected_first_slots = (("Rainbow", "Epic"), ("Rainbow", "Legendary"), ("Beskar", "Epic"))
    if ALERT_COMBOS[:3] != expected_first_slots:
        failures.append("Rainbow Epic and Legendary should occupy the first two toggle slots")
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
        failures.append("all Galactic priority toggles should be available")
    if galactic_combos & default_config.targets:
        failures.append("Galactic priority alerts should be disabled by default")
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


if __name__ == "__main__":
    raise SystemExit(main())
