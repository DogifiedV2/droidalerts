from __future__ import annotations

from dataclasses import dataclass, field

from .matching import NameMatch

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DroidObservation:
    match: NameMatch
    identity_confidence: float
    box: tuple[int, int, int, int]
    family: str = ""
    family_confidence: float = 0.0
    rarity: str = ""
    rarity_confidence: float = 0.0


@dataclass(frozen=True)
class CardContext:
    art_box: tuple[int, int, int, int]
    card_box: tuple[int, int, int, int]
    nameplate_dark_fraction: float
    art_standard_deviation: float
    art_edge_density: float
    frame_line_ratio: float
    accepted: bool
    reason: str


@dataclass(frozen=True)
class CardCandidate:
    canonical_name: str
    raw_text: str
    identity_confidence: float
    name_box: tuple[int, int, int, int]
    context: CardContext
    accepted: bool
    reason: str
    family: str = ""
    family_confidence: float = 0.0
    rarity: str = ""
    rarity_confidence: float = 0.0
    raw_best_similarity: float = 0.0
    runner_up_identity: str = ""
    identity_margin: float = 0.0
    family_best_similarity: float = 0.0
    runner_up_family: str = ""
    family_margin: float = 0.0

    @property
    def identity(self) -> str:
        return self.canonical_name if self.accepted else UNKNOWN

    def to_droid_observation(self) -> DroidObservation | None:
        if not self.accepted:
            return None
        return DroidObservation(
            NameMatch(name=self.canonical_name, score=1.0, raw_text=self.raw_text),
            self.identity_confidence,
            self.context.card_box,
            self.family,
            self.family_confidence,
            self.rarity,
            self.rarity_confidence,
        )


@dataclass(frozen=True)
class CardFrameResult:
    candidates: tuple[CardCandidate, ...]
    diagnostics: dict[str, object] = field(default_factory=dict)

    @property
    def observations(self) -> list[DroidObservation]:
        return [
            observation
            for candidate in self.candidates
            if (observation := candidate.to_droid_observation()) is not None
        ]
