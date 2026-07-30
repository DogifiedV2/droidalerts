from __future__ import annotations

from dataclasses import replace
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))
sys.path.insert(0, str(BASE_DIR / "tests"))

from resolution_matrix import RESOLUTION_CASES
from test_belt_template_recognition import (
    ART_HEIGHT,
    ART_WIDTH,
    ART_X,
    ART_Y,
    CARD_WIDTH,
    HEIGHT,
    synthetic_index,
)

from droid_alerts.belt.models import CardCandidate, CardContext, CardFrameResult
from droid_alerts.belt.names import DROID_NAMES
from droid_alerts.belt.region import DEFAULT_REGION
from droid_alerts.belt.scale_recognition import HybridCardRecognizer
from droid_alerts.belt.template_recognition import identity_features
from droid_alerts.belt.tracking import BeltTracker


TARGETS = ("R2", "GONK")


def distinct_art(kind: int) -> np.ndarray:
    image = np.zeros((ART_HEIGHT, ART_WIDTH, 3), dtype=np.uint8)
    if kind == 0:
        for x in range(8, ART_WIDTH, 20):
            cv2.rectangle(
                image,
                (x, 5),
                (x + 7, ART_HEIGHT - 6),
                (255, 255, 255),
                -1,
            )
        cv2.circle(
            image,
            (ART_WIDTH // 2, ART_HEIGHT // 2),
            32,
            (0, 0, 255),
            -1,
        )
    else:
        for y in range(8, ART_HEIGHT, 18):
            cv2.rectangle(
                image,
                (5, y),
                (ART_WIDTH - 6, y + 6),
                (255, 255, 255),
                -1,
            )
        cv2.line(
            image,
            (5, ART_HEIGHT - 5),
            (ART_WIDTH - 5, 5),
            (0, 255, 0),
            12,
        )
    return image


def distinct_card(kind: int) -> np.ndarray:
    image = np.full((HEIGHT, CARD_WIDTH, 3), (92, 83, 78), dtype=np.uint8)
    cv2.rectangle(
        image,
        (12, 5),
        (CARD_WIDTH - 13, HEIGHT - 8),
        (125, 120, 130),
        8,
    )
    image[ART_Y : ART_Y + ART_HEIGHT, ART_X : ART_X + ART_WIDTH] = distinct_art(
        kind
    )
    cv2.rectangle(
        image,
        (ART_X - 5, 163),
        (ART_X + ART_WIDTH + 5, 220),
        (9, 9, 12),
        -1,
    )
    cv2.rectangle(
        image,
        (ART_X - 5, 163),
        (ART_X + ART_WIDTH + 5, 220),
        (150, 150, 158),
        2,
    )
    return image


def distinct_index():
    index = synthetic_index()
    descriptors = np.zeros_like(index.identity_hog)
    for kind, name in enumerate(TARGETS):
        descriptors[DROID_NAMES.index(name)] = identity_features(distinct_art(kind))
    return replace(index, identity_hog=descriptors)


def belt_frame(
    screen_width: int,
    screen_height: int,
    *,
    displacement_ratio: float = 0.0,
) -> np.ndarray:
    height = round(screen_height * DEFAULT_REGION.height)
    width = round(screen_width * DEFAULT_REGION.width)
    frame = np.full((height, width, 3), (110, 95, 80), dtype=np.uint8)
    card_heights = (
        round(screen_height * 0.105),
        round(screen_height * 0.24),
    )
    anchors = (
        round(width * 0.08),
        round(width * 0.58),
    )
    for kind, (card_height, anchor) in enumerate(zip(card_heights, anchors)):
        source = distinct_card(kind)
        card_width = round(source.shape[1] * card_height / source.shape[0])
        interpolation = (
            cv2.INTER_AREA if card_height < source.shape[0] else cv2.INTER_CUBIC
        )
        resized = cv2.resize(
            source,
            (card_width, card_height),
            interpolation=interpolation,
        )
        x = anchor + round(card_width * displacement_ratio)
        y = max(2, (height - card_height) // 2)
        frame[y : y + card_height, x : x + card_width] = resized
    return frame


def accepted_candidate(
    name: str,
    *,
    box: tuple[int, int, int, int] = (20, 10, 80, 100),
    accepted: bool = True,
    reason: str = "accepted_template",
) -> CardCandidate:
    context = CardContext(
        art_box=(30, 20, 50, 50),
        card_box=box,
        nameplate_dark_fraction=0.8,
        art_standard_deviation=40.0,
        art_edge_density=0.1,
        frame_line_ratio=0.5,
        accepted=accepted,
        reason=reason,
    )
    return CardCandidate(
        canonical_name=name,
        raw_text=f"template:{name}",
        identity_confidence=0.96,
        name_box=(30, 70, 50, 10),
        context=context,
        accepted=accepted,
        reason=reason,
        raw_best_similarity=0.92,
        identity_margin=0.09,
    )


class BeltResolutionMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = distinct_index()

    def test_belt_uses_every_shared_chat_alert_resolution(self):
        expected = {
            (1280, 720),
            (1366, 768),
            (1600, 900),
            (1920, 1080),
            (2560, 1440),
            (3840, 2160),
            (1280, 800),
            (1920, 1200),
            (3440, 1440),
            (1440, 1040),
            (1728, 1117),
        }

        self.assertEqual(expected, {case.size for case in RESOLUTION_CASES})

    def test_two_scales_track_at_every_chat_alert_resolution_without_one_frame_alerts(self):
        for case in RESOLUTION_CASES:
            with self.subTest(resolution=case.name):
                first_frame = belt_frame(case.width, case.height)
                second_frame = belt_frame(
                    case.width,
                    case.height,
                    displacement_ratio=0.16,
                )
                recognizer = HybridCardRecognizer(
                    self.index,
                    load_learned_model=False,
                )
                first_result = recognizer.analyze(
                    first_frame,
                    now=0.0,
                    force_scale=True,
                )
                second_result = recognizer.analyze(
                    second_frame,
                    now=1.0,
                    force_scale=True,
                )
                self.assertEqual(
                    list(TARGETS),
                    [item.match.name for item in first_result.observations],
                )
                self.assertEqual(
                    ["Epic", "Common"],
                    [item.rarity for item in first_result.observations],
                )
                self.assertEqual(
                    list(TARGETS),
                    [item.match.name for item in second_result.observations],
                )

                tracker = BeltTracker(
                    confirmation_hits=4,
                    slow_confirmation_hits=2,
                    slow_cadence_seconds=0.70,
                    slow_minimum_confidence=0.90,
                    timeout_seconds=8.0,
                    minimum_template_displacement_ratio=0.10,
                )
                first_update = tracker.update(
                    first_result.observations,
                    0.0,
                    first_frame.shape[1],
                )
                self.assertEqual([], first_update.events)
                second_update = tracker.update(
                    second_result.observations,
                    1.0,
                    second_frame.shape[1],
                )
                self.assertEqual(
                    list(TARGETS),
                    [event.track.name for event in second_update.events],
                )
                self.assertTrue(
                    all(
                        event.track.confirmation_mode == "slow-cadence"
                        for event in second_update.events
                    )
                )

    def test_hybrid_runs_scale_scan_periodically_and_respects_cpu_budget(self):
        frame = np.zeros((120, 240, 3), dtype=np.uint8)
        fast = MagicMock()
        fast.index = distinct_index()
        fast.analyze.return_value = CardFrameResult(
            (),
            {"card_window_count": 0},
        )
        scale = MagicMock()
        scale.analyze.return_value = CardFrameResult(
            (),
            {"card_window_count": 0},
        )
        recognizer = HybridCardRecognizer(
            fast_recognizer=fast,
            scale_recognizer=scale,
            scale_scan_interval_seconds=0.5,
            maximum_scale_cpu_fraction=0.25,
        )

        first = recognizer.analyze(frame, now=0.0)
        second = recognizer.analyze(frame, now=0.2)
        third = recognizer.analyze(frame, now=0.5)

        self.assertTrue(first.diagnostics["scale_scan_ran"])
        self.assertFalse(second.diagnostics["scale_scan_ran"])
        self.assertTrue(third.diagnostics["scale_scan_ran"])
        self.assertEqual(3, fast.analyze.call_count)
        self.assertEqual(2, scale.analyze.call_count)

    def test_conflicting_fast_and_scale_identities_both_abstain(self):
        frame = np.zeros((120, 240, 3), dtype=np.uint8)
        fast = MagicMock()
        fast.index = distinct_index()
        fast.analyze.return_value = CardFrameResult(
            (accepted_candidate("R2"),),
            {"card_window_count": 1},
        )
        scale = MagicMock()
        scale.analyze.return_value = CardFrameResult(
            (accepted_candidate("GONK"),),
            {"card_window_count": 1},
        )
        recognizer = HybridCardRecognizer(
            fast_recognizer=fast,
            scale_recognizer=scale,
        )

        result = recognizer.analyze(frame, now=0.0)

        self.assertEqual([], result.observations)
        self.assertEqual(
            {"conflicting_detector_identity"},
            {item.reason for item in result.candidates},
        )

    def test_strong_learned_conflict_vetoes_overlapping_fast_result(self):
        frame = np.zeros((120, 240, 3), dtype=np.uint8)
        fast = MagicMock()
        fast.index = distinct_index()
        fast.analyze.return_value = CardFrameResult(
            (accepted_candidate("R2"),),
            {"card_window_count": 1},
        )
        scale = MagicMock()
        scale.analyze.return_value = CardFrameResult(
            (
                accepted_candidate(
                    "R2",
                    accepted=False,
                    reason="learned_identity_conflict",
                ),
            ),
            {"card_window_count": 1},
        )
        recognizer = HybridCardRecognizer(
            fast_recognizer=fast,
            scale_recognizer=scale,
        )

        result = recognizer.analyze(frame, now=0.0)

        self.assertEqual([], result.observations)
        self.assertTrue(
            all(
                item.reason == "learned_identity_conflict"
                for item in result.candidates
            )
        )


if __name__ == "__main__":
    unittest.main()
