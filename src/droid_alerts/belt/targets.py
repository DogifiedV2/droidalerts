from __future__ import annotations

from collections.abc import Mapping

from .names import DROID_NAMES


BELT_FAMILY_ORDER = ("Default", "Gold", "Diamond", "Rainbow", "Beskar")
BELT_FAMILY_RANK = {family: index for index, family in enumerate(BELT_FAMILY_ORDER)}
BELT_TARGET_LABELS = {
    family: f"{family}+" if family != BELT_FAMILY_ORDER[-1] else family
    for family in BELT_FAMILY_ORDER
}
BELT_TARGET_FAMILIES_BY_LABEL = {
    label: family for family, label in BELT_TARGET_LABELS.items()
}
_VALID_DROID_NAMES = frozenset(DROID_NAMES)
_FAMILY_BY_CASEFOLD = {family.casefold(): family for family in BELT_FAMILY_ORDER}


def normalize_belt_target_tiers(raw_targets: object) -> dict[str, str]:
    """Return valid per-droid minimum family rules in canonical display order."""

    if not isinstance(raw_targets, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for raw_name, raw_family in raw_targets.items():
        if not isinstance(raw_name, str) or not isinstance(raw_family, str):
            continue
        name = raw_name.strip().upper()
        family = _FAMILY_BY_CASEFOLD.get(raw_family.strip().casefold())
        if name in _VALID_DROID_NAMES and family is not None:
            normalized[name] = family
    return {name: normalized[name] for name in DROID_NAMES if name in normalized}


def belt_target_names(target_tiers: Mapping[str, str]) -> tuple[str, ...]:
    selected = {str(name).strip().upper() for name in target_tiers}
    return tuple(name for name in DROID_NAMES if name in selected)


def belt_family_meets_minimum(family: object, minimum_family: object) -> bool:
    """Whether a detected family satisfies a configured minimum belt tier.

    A missing family never fires a family-dependent alert. The identity can
    still appear in the overlay/history as unknown, but guessing Default here
    would turn an uncertain Gold/Beskar read into a false alert decision.
    """

    minimum = _FAMILY_BY_CASEFOLD.get(str(minimum_family or "").strip().casefold())
    if minimum is None:
        return False
    detected = _FAMILY_BY_CASEFOLD.get(str(family or "").strip().casefold())
    if detected is None:
        return False
    return BELT_FAMILY_RANK[detected] >= BELT_FAMILY_RANK[minimum]


def is_belt_alert_target(
    target_tiers: Mapping[str, str],
    droid: object,
    family: object,
) -> bool:
    name = str(droid or "").strip().upper()
    minimum = target_tiers.get(name)
    return minimum is not None and belt_family_meets_minimum(family, minimum)


def belt_target_label(family: object) -> str:
    canonical = _FAMILY_BY_CASEFOLD.get(str(family or "").strip().casefold())
    return BELT_TARGET_LABELS.get(canonical, "Off")
