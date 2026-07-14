from __future__ import annotations

import re
from dataclasses import dataclass


def normalize_text(value: str) -> str:
    value = value.upper().replace("—", "-").replace("_", "-")
    value = re.sub(r"[^A-Z0-9 -]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize_text(value))


@dataclass(frozen=True)
class NameMatch:
    name: str
    score: float
    raw_text: str
