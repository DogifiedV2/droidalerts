from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import classifier
from .capture import PixelBox
from .config import CALIBRATION_FILE, config_dir
from .normalize import estimate_scale, normalize_band
from .row_finder import find_candidate_rows

# Position-notes "safer" auto box for standard wide captures.
AUTO_BOX_PERCENT = {"left": 0.0, "top": 0.47, "width": 0.33, "height": 0.16}

# Compact 4:3-ish captures place the same chat alert row higher on screen.
# Measured on a 1439x1079 capture: alert text y=413-469 (38.3%-43.5%).
COMPACT_ASPECT_MAX = 1.50
COMPACT_AUTO_BOX_PERCENT = {"left": 0.0, "top": 0.36, "width": 0.33, "height": 0.16}

# 3440-wide ultrawide captures place the chat alert row above the standard
# wide profile. Measured from a 3440x1392 capture where the old 47% top started
# below the alert row.
ULTRAWIDE_ASPECT_MIN = 2.20
ULTRAWIDE_AUTO_BOX_PERCENT = {"left": 0.0, "top": 0.40, "width": 0.33, "height": 0.16}


class NeedsCalibration(Exception):
    """Raised when auto/manual region placement has repeatedly failed validation."""


def auto_box_percent(screen_width: int, screen_height: int) -> PixelBox:
    ratios = auto_box_ratios(screen_width, screen_height)
    return PixelBox(
        left=int(round(screen_width * ratios["left"])),
        top=int(round(screen_height * ratios["top"])),
        width=max(1, int(round(screen_width * ratios["width"]))),
        height=max(1, int(round(screen_height * ratios["height"]))),
    )


def auto_box_ratios(screen_width: int, screen_height: int) -> dict[str, float]:
    profile = auto_box_profile(screen_width, screen_height)
    if profile == "compact":
        return COMPACT_AUTO_BOX_PERCENT
    if profile == "ultrawide":
        return ULTRAWIDE_AUTO_BOX_PERCENT
    return AUTO_BOX_PERCENT


def auto_box_profile(screen_width: int, screen_height: int) -> str:
    aspect = screen_width / max(1, screen_height)
    if aspect <= COMPACT_ASPECT_MAX:
        return "compact"
    if aspect >= ULTRAWIDE_ASPECT_MIN:
        return "ultrawide"
    return "wide"


@dataclass
class ValidationResult:
    rows_found: int
    evidence_rows: int

    @property
    def ok(self) -> bool:
        return self.evidence_rows >= 1


def validate_region(
    band_bgr: np.ndarray,
    templates: list[classifier.Template],
    *,
    screen_height: int | None = None,
    screen_width: int | None = None,
    rarity_threshold: float = 0.35,
) -> ValidationResult:
    """Position-notes recommendation: do not rely on percentages alone.

    Requires at least one candidate row showing icon-color-blob evidence plus
    rarity-word template evidence on the normalized band. Note: an empty chat
    box legitimately fails this. Callers must only count failures on frames
    that contain candidate rows.
    """
    candidates = find_candidate_rows(band_bgr)
    scale, _method = estimate_scale(
        screen_height=screen_height, screen_width=screen_width, candidates=candidates
    )
    normalized = normalize_band(band_bgr, scale)
    norm_candidates = find_candidate_rows(normalized.image)
    if not norm_candidates:
        return ValidationResult(rows_found=0, evidence_rows=0)

    rarity_matches = classifier.rarity_candidates(normalized.image, templates, rarity_threshold)
    evidence_rows = 0
    for candidate in norm_candidates:
        center = (candidate.y0 + candidate.y1) // 2
        has_rarity = any(
            abs(((m.box[1] + m.box[3]) // 2) - center) <= 22 for m in rarity_matches
        )
        if not has_rarity:
            continue
        y0 = max(0, center - 22)
        row = normalized.image[y0 : y0 + 44, :]
        droid, score = classifier.best_droid_type(row)
        if score >= 0.08 or classifier.has_spawn_phrase_structure(row):
            evidence_rows += 1
    return ValidationResult(rows_found=len(norm_candidates), evidence_rows=evidence_rows)


@dataclass
class Calibration:
    mode: str = "auto"  # "auto" | "manual"
    ratios: dict[str, float] = field(default_factory=lambda: dict(AUTO_BOX_PERCENT))
    monitor_signature: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, monitor_key: str | None = None) -> "Calibration":
        path = calibration_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                return cls()
            profiles = data.get("profiles")
            if isinstance(profiles, dict):
                profile = profiles.get(monitor_key or "default") or profiles.get("default")
                if profile is None and monitor_key is None and profiles:
                    profile = next(iter(profiles.values()))
                data = profile if isinstance(profile, dict) else {}

            ratios = data.get("ratios")
            if not isinstance(ratios, dict):
                ratios = {}
            signature = data.get("monitor_signature")
            if not isinstance(signature, dict):
                signature = {}
            calibration = cls(
                mode=str(data.get("mode", "auto")),
                monitor_signature={str(k): int(v) for k, v in signature.items()},
            )
            keys = ("left", "top", "width", "height")
            if all(key in ratios for key in keys):
                calibration.ratios = {key: float(ratios[key]) for key in keys}
                if not calibration.ratios_valid():
                    calibration.mode = "auto"
                    calibration.ratios = dict(AUTO_BOX_PERCENT)
            else:
                calibration.mode = "auto"
            return calibration
        except (OSError, json.JSONDecodeError, TypeError, ValueError, OverflowError):
            return cls()

    def save(self, monitor_key: str | None = None) -> None:
        path = calibration_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        profiles: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            if isinstance(existing.get("profiles"), dict):
                profiles = dict(existing["profiles"])
            elif isinstance(existing, dict) and existing.get("ratios"):
                profiles["default"] = {
                    key: existing[key]
                    for key in ("mode", "ratios", "monitor_signature")
                    if key in existing
                }
        profiles[monitor_key or "default"] = self.to_dict()
        payload = {"version": 2, "profiles": profiles}
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "ratios": self.ratios,
            "monitor_signature": self.monitor_signature,
        }

    def ratios_valid(self) -> bool:
        try:
            r = self.ratios
            return (
                0.0 <= r["left"] < 1.0
                and 0.0 <= r["top"] < 1.0
                and 0.01 <= r["width"] <= 1.0 - r["left"]
                and 0.01 <= r["height"] <= 1.0 - r["top"]
            )
        except (KeyError, TypeError, ValueError):
            return False


def calibration_path() -> Path:
    return config_dir() / CALIBRATION_FILE


class RegionResolver:
    """Resolves the capture region each session.

    Manual calibration is stored as percent ratios (the actual source of
    truth, so it survives resolution
    changes; the monitor signature only marks when re-validation is due.
    """

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        *,
        max_failures: int = 30,
        monitor_key: str | None = None,
    ) -> None:
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.max_failures = max_failures
        self.monitor_key = monitor_key
        self.calibration = Calibration.load(monitor_key)
        self.consecutive_failures = 0
        self.signature_changed = self._signature_changed()

    def _signature_changed(self) -> bool:
        sig = self.calibration.monitor_signature
        if not sig:
            return False
        return sig.get("width") != self.screen_width or sig.get("height") != self.screen_height

    def resolve(self) -> tuple[PixelBox, str]:
        if self.calibration.mode == "manual" and self.calibration.ratios_valid():
            r = self.calibration.ratios
            box = PixelBox(
                left=int(round(self.screen_width * r["left"])),
                top=int(round(self.screen_height * r["top"])),
                width=max(1, int(round(self.screen_width * r["width"]))),
                height=max(1, int(round(self.screen_height * r["height"]))),
            )
            source = "manual(rescaled)" if self.signature_changed else "manual"
            return box, source
        profile = auto_box_profile(self.screen_width, self.screen_height)
        source = "auto" if profile == "wide" else f"auto({profile})"
        return auto_box_percent(self.screen_width, self.screen_height), source

    def record_validation(self, ok: bool) -> None:
        """Call only on frames that actually contained candidate rows."""
        if ok:
            self.consecutive_failures = 0
            if self.signature_changed:
                self.calibration.monitor_signature = {
                    "width": self.screen_width,
                    "height": self.screen_height,
                }
                self.calibration.save(self.monitor_key)
                self.signature_changed = False
            return
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_failures:
            raise NeedsCalibration(
                f"{self.consecutive_failures} consecutive row-bearing frames failed validation; "
                "run: python main.py calibrate"
            )
