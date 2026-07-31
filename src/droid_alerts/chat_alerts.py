from __future__ import annotations


# User-selectable chat detections, in the order shown throughout the UI.
CHAT_ALERT_COMBOS: tuple[tuple[str, str], ...] = (
    ("Rainbow", "Epic"),
    ("Rainbow", "Legendary"),
    ("Rainbow", "Mythic"),
    ("Beskar", "Epic"),
    ("Beskar", "Legendary"),
    ("Beskar", "Mythic"),
    ("Galactic", "Epic"),
    ("Galactic", "Legendary"),
    ("Galactic", "Mythic"),
    ("Diamond", "Mythic"),
)

PRIORITY_ALERTS = frozenset(CHAT_ALERT_COMBOS)

# These retired combinations must not be surfaced even when an older config
# still contains them or the visual classifier recognizes their rarity text.
REMOVED_CHAT_DETECTIONS = frozenset(
    {
        ("Galactic", "Common"),
        ("Galactic", "Rare"),
    }
)

