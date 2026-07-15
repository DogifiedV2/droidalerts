from __future__ import annotations

DROID_NAMES = (
    "MOUSE", "PIT", "GONK", "CB", "R3", "R5", "R8", "IMPERIAL PROBE",
    "B1 BATTLE", "DRK-1 PROBE", "ID10", "BDX EXPLORER", "ARG",
    "SENATE HOVERCAM", "BU-4D", "BAL-CORE", "ROLL-R", "2BB", "A-LT",
    "R4", "R9", "B1 SECURITY", "NAV-EX", "VECT-ARM", "HOV-R",
    "GROUNDMECH", "LO", "AMP WALKER", "SEN-TRI", "OPTI-POD", "BB",
    "R2", "R6", "TRAK-R", "ORB-WALKER", "GUNRUNNER", "UTIL-TEC",
    "B1 HEAVY", "B2 SUPER", "B2 HEAVY", "STRIKE-ORB", "HAUL-R",
    "LNG-SHOT", "PROTO-ROLLER", "MECHA-DROID", "MONO-WLKR", "BB9",
    "R7", "B2-RP", "CYCLO-GRAV", "OPTI-STRK", "SNOW MOUSE", "RIC",
    "LOADLIFTER", "LEP", "RIC-1200", "DRFT-R", "CYCLENS", "MO-TRAK",
    "TRI-TEK", "IG", "KX",
)

# Common/Rare/Epic/Legendary/Mythic is an intrinsic droid class, not a visual
# card attribute. Keep it keyed by the canonical template identity so live
# recognition only has to classify the changing Default/Gold/Diamond/Rainbow/
# Beskar tier. The existing event schema calls this value ``card_rarity`` for
# compatibility with saved history and API consumers.
DROID_CLASS_BY_NAME = {
    "2BB": "Rare",
    "A-LT": "Rare",
    "AMP WALKER": "Epic",
    "ARG": "Rare",
    "B1 BATTLE": "Common",
    "B1 HEAVY": "Epic",
    "B1 SECURITY": "Rare",
    "B2 HEAVY": "Epic",
    "B2 SUPER": "Epic",
    "B2-RP": "Legendary",
    "BAL-CORE": "Rare",
    "BB": "Epic",
    "BB9": "Legendary",
    "BDX EXPLORER": "Rare",
    "BU-4D": "Rare",
    "CB": "Common",
    "CYCLENS": "Mythic",
    "CYCLO-GRAV": "Legendary",
    "DRFT-R": "Mythic",
    "DRK-1 PROBE": "Common",
    "GONK": "Common",
    "GROUNDMECH": "Epic",
    "GUNRUNNER": "Epic",
    "HAUL-R": "Epic",
    "HOV-R": "Rare",
    "ID10": "Common",
    "IG": "Mythic",
    "IMPERIAL PROBE": "Common",
    "KX": "Mythic",
    "LEP": "Mythic",
    "LNG-SHOT": "Epic",
    "LO": "Epic",
    "LOADLIFTER": "Mythic",
    "MECHA-DROID": "Legendary",
    "MONO-WLKR": "Legendary",
    "MO-TRAK": "Mythic",
    "MOUSE": "Common",
    "NAV-EX": "Rare",
    "OPTI-POD": "Epic",
    "OPTI-STRK": "Legendary",
    "ORB-WALKER": "Epic",
    "PIT": "Common",
    "PROTO-ROLLER": "Legendary",
    "R2": "Epic",
    "R3": "Common",
    "R4": "Rare",
    "R5": "Common",
    "R6": "Epic",
    "R7": "Legendary",
    "R8": "Common",
    "R9": "Rare",
    "RIC": "Mythic",
    "RIC-1200": "Mythic",
    "ROLL-R": "Rare",
    "SENATE HOVERCAM": "Rare",
    "SEN-TRI": "Epic",
    "SNOW MOUSE": "Mythic",
    "STRIKE-ORB": "Epic",
    "TRAK-R": "Epic",
    "TRI-TEK": "Mythic",
    "UTIL-TEC": "Epic",
    "VECT-ARM": "Rare",
}

if frozenset(DROID_CLASS_BY_NAME) != frozenset(DROID_NAMES):
    raise RuntimeError("Every belt droid must have exactly one fixed class")


def droid_class(name: object) -> str:
    """Return the fixed Common-through-Mythic class for a canonical identity."""

    return DROID_CLASS_BY_NAME.get(str(name or "").strip().upper(), "")
