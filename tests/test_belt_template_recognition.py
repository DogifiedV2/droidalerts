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

from droid_alerts.belt.names import DROID_CLASS_BY_NAME, DROID_NAMES, droid_class
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
    def test_every_template_identity_has_one_fixed_droid_class(self):
        self.assertEqual(set(DROID_NAMES), set(DROID_CLASS_BY_NAME))
        self.assertEqual("Common", droid_class("B1 BATTLE"))
        self.assertEqual("Mythic", droid_class("CYCLENS"))

    def test_production_index_covers_every_known_droid(self):
        index = BeltTemplateIndex.load()

        self.assertEqual(belt_template_index_path(), BASE_DIR / "templates" / "belt_blueprints.npz")
        self.assertEqual(tuple(DROID_NAMES), index.identity_names)
        self.assertGreaterEqual(index.identity_hog.shape[0], len(DROID_NAMES) * 6)
        self.assertEqual(5, len(index.family_labels))


class TemplateCardRecognizerTests(unittest.TestCase):
    def test_finds_multiple_cards_without_ocr(self):
        frame_height = 405
        crop_top = 71
        frame = np.full((frame_height, 820, 3), (110, 95, 80), dtype=np.uint8)
        r2_index = DROID_NAMES.index("R2")
        gonk_index = DROID_NAMES.index("GONK")
        frame[crop_top : crop_top + HEIGHT, 90 : 90 + CARD_WIDTH] = card(r2_index + 1)
        frame[crop_top : crop_top + HEIGHT, 480 : 480 + CARD_WIDTH] = card(gonk_index + 1)

        result = TemplateCardRecognizer(
            synthetic_index(),
            config=TemplateRecognitionConfig(minimum_identity_margin=0.01),
        ).analyze(frame)

        self.assertEqual(["R2", "GONK"], [item.match.name for item in result.observations])
        self.assertEqual(["Epic", "Common"], [item.rarity for item in result.observations])
        self.assertEqual([1.0, 1.0], [item.rarity_confidence for item in result.observations])
        self.assertEqual("templates", result.diagnostics["detector"])
        self.assertEqual(2, result.diagnostics["accepted_count"])
        self.assertEqual((), result.text_observations)
        self.assertTrue(all(item.raw_best_similarity > 0.8 for item in result.candidates))
        self.assertTrue(all(item.runner_up_identity for item in result.candidates))
        self.assertTrue(all(item.identity_margin > 0.01 for item in result.candidates))

    def test_partial_edge_card_waits_until_fully_visible(self):
        frame = np.full((405, 500, 3), (110, 95, 80), dtype=np.uint8)
        r2_index = DROID_NAMES.index("R2")
        partial = card(r2_index + 1)
        frame[71 : 71 + HEIGHT, : CARD_WIDTH - 35] = partial[:, 35:]

        result = TemplateCardRecognizer(synthetic_index()).analyze(frame)

        self.assertEqual([], result.observations)

    def test_card_touching_vertical_region_edge_is_rejected_as_clipped(self):
        frame = np.full((HEIGHT, 500, 3), (110, 95, 80), dtype=np.uint8)
        r2_index = DROID_NAMES.index("R2")
        frame[:, 120 : 120 + CARD_WIDTH] = card(r2_index + 1)

        result = TemplateCardRecognizer(
            synthetic_index(),
            config=TemplateRecognitionConfig(minimum_identity_margin=0.01),
        ).analyze(frame)

        self.assertEqual([], result.observations)
        self.assertTrue(
            not result.candidates
            or all(candidate.reason == "card_touches_region_edge" for candidate in result.candidates)
        )

    def test_plain_background_does_not_create_candidates(self):
        frame = np.full((HEIGHT, 900, 3), (110, 95, 80), dtype=np.uint8)

        result = TemplateCardRecognizer(synthetic_index()).analyze(frame)

        self.assertEqual([], result.observations)
        self.assertEqual(0, result.diagnostics["card_window_count"])

    def test_hud_text_and_dark_panels_do_not_create_blueprint_candidates(self):
        frame = np.full((405, 900, 3), (95, 105, 115), dtype=np.uint8)
        cv2.rectangle(frame, (80, 120), (820, 300), (8, 8, 12), -1)
        for index, text in enumerate(("R3", "2BB", "LEGENDARY", "1.10K")):
            cv2.putText(
                frame,
                text,
                (110 + index * 180, 225),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.2,
                (245, 245, 245),
                3,
                cv2.LINE_AA,
            )

        result = TemplateCardRecognizer(synthetic_index()).analyze(frame)

        self.assertEqual([], result.observations)

    def test_blurry_card_is_missed_instead_of_loosening_identity_thresholds(self):
        frame = np.full((405, 500, 3), (110, 95, 80), dtype=np.uint8)
        r2_index = DROID_NAMES.index("R2")
        blurry = cv2.GaussianBlur(card(r2_index + 1), (31, 31), 12)
        frame[71 : 71 + HEIGHT, 120 : 120 + CARD_WIDTH] = blurry

        result = TemplateCardRecognizer(synthetic_index()).analyze(frame)

        self.assertEqual([], result.observations)

    def test_near_identical_droid_templates_are_rejected_by_identity_margin(self):
        index = synthetic_index()
        identity_hog = index.identity_hog.copy()
        r2_index = DROID_NAMES.index("R2")
        gonk_index = DROID_NAMES.index("GONK")
        identity_hog[gonk_index] = identity_hog[r2_index]
        frame = np.full((405, 500, 3), (110, 95, 80), dtype=np.uint8)
        frame[71 : 71 + HEIGHT, 120 : 120 + CARD_WIDTH] = card(r2_index + 1)

        result = TemplateCardRecognizer(
            replace(index, identity_hog=identity_hog)
        ).analyze(frame)

        self.assertEqual([], result.observations)

    def test_geometry_search_handles_space_above_and_below_cards(self):
        frame_height = 405
        crop_top = 71
        frame = np.full((frame_height, 820, 3), (110, 95, 80), dtype=np.uint8)
        r2_index = DROID_NAMES.index("R2")
        gonk_index = DROID_NAMES.index("GONK")
        frame[crop_top : crop_top + HEIGHT, 90 : 90 + CARD_WIDTH] = card(r2_index + 1)
        frame[crop_top : crop_top + HEIGHT, 480 : 480 + CARD_WIDTH] = card(gonk_index + 1)

        result = TemplateCardRecognizer(
            synthetic_index(),
            config=TemplateRecognitionConfig(minimum_identity_margin=0.01),
        ).analyze(frame)

        self.assertEqual(["R2", "GONK"], [item.match.name for item in result.observations])
        self.assertEqual(crop_top, result.diagnostics["geometry_crop_top"])
        self.assertEqual(HEIGHT, result.diagnostics["geometry_crop_height"])
        self.assertAlmostEqual(crop_top / frame_height, result.diagnostics["geometry_top_ratio"], places=4)

    def test_geometry_search_handles_tight_and_bottom_heavy_regions(self):
        r2_index = DROID_NAMES.index("R2")
        gonk_index = DROID_NAMES.index("GONK")
        cases = (
            (280, 8),
            (470, 35),
        )
        for frame_height, crop_top in cases:
            with self.subTest(frame_height=frame_height, crop_top=crop_top):
                frame = np.full((frame_height, 820, 3), (110, 95, 80), dtype=np.uint8)
                frame[crop_top : crop_top + HEIGHT, 90 : 90 + CARD_WIDTH] = card(r2_index + 1)
                frame[crop_top : crop_top + HEIGHT, 480 : 480 + CARD_WIDTH] = card(
                    gonk_index + 1
                )

                result = TemplateCardRecognizer(
                    synthetic_index(),
                    config=TemplateRecognitionConfig(minimum_identity_margin=0.01),
                ).analyze(frame)

                self.assertEqual(
                    ["R2", "GONK"],
                    [item.match.name for item in result.observations],
                )
                self.assertLessEqual(
                    abs(crop_top - result.diagnostics["geometry_crop_top"]),
                    6,
                )
                self.assertLessEqual(
                    abs(HEIGHT - result.diagnostics["geometry_crop_height"]),
                    6,
                )

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

    def test_conflicting_distinctive_border_and_template_abstain(self):
        index = synthetic_index()
        histograms = np.zeros_like(index.family_histograms)
        words = np.zeros_like(index.family_words)
        words[0, 0] = 1.0
        words[3, 0] = 0.5
        recognizer = TemplateCardRecognizer(
            replace(index, family_histograms=histograms, family_words=words)
        )
        query_histogram = np.zeros(histograms.shape[1], dtype=np.float32)
        query_word = np.zeros(words.shape[1], dtype=np.float32)
        query_word[0] = 1.0
        with (
            patch(
                "droid_alerts.belt.template_recognition.classify_card_family_border",
                return_value=("Rainbow", 0.80),
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

    def test_marginal_default_family_stays_unknown(self):
        index = synthetic_index()
        histograms = np.zeros_like(index.family_histograms)
        words = np.zeros_like(index.family_words)
        words[0, 0] = 1.0
        words[1, 0] = 0.96
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
