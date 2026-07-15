from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.names import DROID_NAMES
from droid_alerts.belt.template_recognition import (
    BeltTemplateIndex,
    TemplateCardRecognizer,
    TemplateRecognitionConfig,
    belt_template_index_path,
    family_features,
    identity_features,
)


HEIGHT = 263
CARD_WIDTH = 241
ART_X = 43
ART_Y = 21
ART_WIDTH = 154
ART_HEIGHT = 144


def patterned_art(seed: int) -> np.ndarray:
    generator = np.random.default_rng(seed)
    art = generator.integers(0, 256, (ART_HEIGHT, ART_WIDTH, 3), dtype=np.uint8)
    for index in range(8):
        center = (18 + (seed * 17 + index * 29) % 120, 15 + (seed * 11 + index * 23) % 110)
        cv2.circle(
            art,
            center,
            5 + (seed + index * 3) % 18,
            ((seed * 31 + index * 47) % 255, 220 - index * 17, 35 + index * 19),
            -1,
        )
    return art


def card(seed: int) -> np.ndarray:
    image = np.full((HEIGHT, CARD_WIDTH, 3), (92, 83, 78), dtype=np.uint8)
    cv2.rectangle(image, (12, 5), (CARD_WIDTH - 13, HEIGHT - 8), (125, 120, 130), 8)
    image[ART_Y : ART_Y + ART_HEIGHT, ART_X : ART_X + ART_WIDTH] = patterned_art(seed)
    cv2.rectangle(image, (ART_X - 5, 163), (ART_X + ART_WIDTH + 5, 220), (9, 9, 12), -1)
    cv2.rectangle(image, (ART_X - 5, 163), (ART_X + ART_WIDTH + 5, 220), (150, 150, 158), 2)
    return image


def synthetic_index() -> BeltTemplateIndex:
    identity_hog = np.stack(
        [identity_features(patterned_art(index + 1)) for index, _name in enumerate(DROID_NAMES)]
    )
    family_cards = [card(index + 100) for index in range(5)]
    family_descriptors = [family_features(image) for image in family_cards]
    return BeltTemplateIndex(
        identity_hog=identity_hog,
        identity_names=tuple(DROID_NAMES),
        identity_name_offsets=np.arange(len(DROID_NAMES) + 1, dtype=np.int32),
        family_histograms=np.stack([item[0] for item in family_descriptors]),
        family_words=np.stack([item[1] for item in family_descriptors]),
        family_labels=("Default", "Gold", "Diamond", "Rainbow", "Beskar"),
        family_offsets=np.arange(6, dtype=np.int32),
        card_width_ratio=CARD_WIDTH / HEIGHT,
        art_left_ratio=ART_X / HEIGHT,
        art_top_ratio=ART_Y / HEIGHT,
        art_width_ratio=ART_WIDTH / HEIGHT,
        art_height_ratio=ART_HEIGHT / HEIGHT,
    )


class BeltTemplateIndexTests(unittest.TestCase):
    def test_production_index_covers_every_known_droid(self):
        index = BeltTemplateIndex.load()

        self.assertEqual(belt_template_index_path(), BASE_DIR / "templates" / "belt_blueprints.npz")
        self.assertEqual(tuple(DROID_NAMES), index.identity_names)
        self.assertGreaterEqual(index.identity_hog.shape[0], len(DROID_NAMES) * 6)
        self.assertEqual(5, len(index.family_labels))


class TemplateCardRecognizerTests(unittest.TestCase):
    def test_finds_multiple_cards_without_ocr(self):
        frame = np.full((HEIGHT, 820, 3), (110, 95, 80), dtype=np.uint8)
        r2_index = DROID_NAMES.index("R2")
        gonk_index = DROID_NAMES.index("GONK")
        frame[:, 90 : 90 + CARD_WIDTH] = card(r2_index + 1)
        frame[:, 480 : 480 + CARD_WIDTH] = card(gonk_index + 1)

        result = TemplateCardRecognizer(
            synthetic_index(),
            config=TemplateRecognitionConfig(minimum_identity_margin=0.01),
        ).analyze(frame)

        self.assertEqual(["R2", "GONK"], [item.match.name for item in result.observations])
        self.assertEqual("templates", result.diagnostics["detector"])
        self.assertEqual(2, result.diagnostics["accepted_count"])
        self.assertEqual((), result.text_observations)

    def test_partial_edge_card_waits_until_fully_visible(self):
        frame = np.full((HEIGHT, 500, 3), (110, 95, 80), dtype=np.uint8)
        r2_index = DROID_NAMES.index("R2")
        partial = card(r2_index + 1)
        frame[:, : CARD_WIDTH - 35] = partial[:, 35:]

        result = TemplateCardRecognizer(synthetic_index()).analyze(frame)

        self.assertEqual([], result.observations)

    def test_plain_background_does_not_create_candidates(self):
        frame = np.full((HEIGHT, 900, 3), (110, 95, 80), dtype=np.uint8)

        result = TemplateCardRecognizer(synthetic_index()).analyze(frame)

        self.assertEqual([], result.observations)
        self.assertEqual(0, result.diagnostics["card_window_count"])

    def test_marginal_beskar_match_stays_unknown(self):
        index = synthetic_index()
        histograms = np.zeros_like(index.family_histograms)
        words = np.zeros_like(index.family_words)
        words[0, 0] = 0.985
        words[4, 0] = 1.0
        recognizer = TemplateCardRecognizer(
            replace(index, family_histograms=histograms, family_words=words)
        )
        query_histogram = np.zeros(histograms.shape[1], dtype=np.float32)
        query_word = np.zeros(words.shape[1], dtype=np.float32)
        query_word[0] = 1.0

        with (
            patch(
                "droid_alerts.belt.template_recognition.classify_card_family_border",
                return_value=("", 0.0),
            ),
            patch(
                "droid_alerts.belt.template_recognition.family_features",
                return_value=(query_histogram, query_word),
            ),
        ):
            family, confidence = recognizer._classify_family(
                card(1),
                (ART_X, 168, 120, 28),
                (0, 0, CARD_WIDTH, HEIGHT),
            )

        self.assertEqual("", family)
        self.assertEqual(0.0, confidence)


if __name__ == "__main__":
    unittest.main()
