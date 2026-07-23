from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.config import templates_dir
from droid_alerts.pipeline import Pipeline, PipelineResult


@dataclass(frozen=True)
class BenchmarkFixture:
    name: str
    path: Path | None
    scale: float


@dataclass
class MatchTemplateStats:
    calls: int = 0
    seconds: float = 0.0


FIXTURES = (
    BenchmarkFixture(
        "beskar_epic",
        BASE_DIR
        / "tests"
        / "galactic_fixtures"
        / "review_resolution_beskar_epic_reference_114332fb.png",
        1.0,
    ),
    BenchmarkFixture(
        "galactic_epic",
        BASE_DIR / "tests" / "galactic_fixtures" / "galactic_epic_large_scale_100.png",
        1.0,
    ),
    BenchmarkFixture(
        "mixed_beskar_galactic",
        BASE_DIR
        / "tests"
        / "galactic_fixtures"
        / "mixed_beskar_epic_galactic_mythic_scale_083.png",
        0.83,
    ),
    BenchmarkFixture("empty", None, 1.0),
)


@contextmanager
def count_match_template():
    stats = MatchTemplateStats()
    original = cv2.matchTemplate

    def measured(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            stats.calls += 1
            stats.seconds += time.perf_counter() - started

    cv2.matchTemplate = measured
    try:
        yield stats
    finally:
        cv2.matchTemplate = original


def result_signature(result: PipelineResult) -> dict[str, object]:
    """Behavioral detector signature suitable for before/after parity checks."""

    return {
        "detections": Counter((item.droid, item.rarity) for item in result.detections),
        "rows": [item.row_box for item in result.detections],
        "sources": [item.source for item in result.detections],
        "scores": [
            (item.droid_score, item.rarity_score, item.rarity_margin, item.score, item.shape_score)
            for item in result.detections
        ],
        "rejections": [
            (item.get("y"), item.get("droid"), item.get("reason"), item.get("detail"))
            for item in result.rejections
        ],
        "phrase_rows": list(result.phrase_row_boxes),
    }


def load_images() -> list[tuple[BenchmarkFixture, np.ndarray]]:
    loaded: list[tuple[BenchmarkFixture, np.ndarray]] = []
    reference_shape: tuple[int, int, int] | None = None
    for fixture in FIXTURES:
        if fixture.path is None:
            continue
        image = cv2.imread(str(fixture.path), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(f"Could not load benchmark fixture: {fixture.path}")
        reference_shape = reference_shape or image.shape
        loaded.append((fixture, image))
    assert reference_shape is not None
    empty_fixture = next(item for item in FIXTURES if item.path is None)
    loaded.append((empty_fixture, np.zeros(reference_shape, dtype=np.uint8)))
    return loaded


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure priority chat detector work.")
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()
    iterations = max(5, args.iterations)
    pipeline = Pipeline(templates_dir())

    print(f"chat detector benchmark: {iterations} measured iterations after warmup")
    for fixture, image in load_images():
        pipeline.detect(image, known_scale=fixture.scale)
        totals: list[float] = []
        match_times: list[float] = []
        call_counts: list[int] = []
        signature = None
        for _ in range(iterations):
            started = time.perf_counter()
            with count_match_template() as stats:
                result = pipeline.detect(image, known_scale=fixture.scale)
            totals.append(time.perf_counter() - started)
            match_times.append(stats.seconds)
            call_counts.append(stats.calls)
            current_signature = result_signature(result)
            if signature is None:
                signature = current_signature
            elif current_signature != signature:
                raise RuntimeError(f"Non-deterministic result for {fixture.name}")
        print(
            f"{fixture.name:24} total={statistics.median(totals) * 1000:8.2f}ms "
            f"matchTemplate={statistics.median(match_times) * 1000:8.2f}ms "
            f"calls={int(statistics.median(call_counts)):4d} "
            f"detections={dict(signature['detections'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
