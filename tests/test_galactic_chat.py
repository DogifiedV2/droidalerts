from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.classifier import (  # noqa: E402
    classify_galactic_droid_word,
    load_droid_word_templates,
)
from droid_alerts.pipeline import Pipeline  # noqa: E402


SUPPLIED_CAPTURE_CASES = (
    (
        "galactic_stack_common_common_rare_scale_068.png",
        0.68,
        [("Galactic", "Common"), ("Galactic", "Common"), ("Galactic", "Rare")],
    ),
    (
        "mixed_beskar_rare_galactic_legendary_scale_074.png",
        0.74,
        [("Beskar", "Rare"), ("Galactic", "Legendary")],
    ),
    (
        "mixed_stack_beskar_galactic_diamond_common_scale_0615.png",
        0.615,
        [("Beskar", "Common"), ("Galactic", "Common"), ("Diamond", "Common")],
    ),
    (
        "mixed_stack_beskar_common_galactic_rare_common_scale_055.png",
        0.55,
        [("Beskar", "Common"), ("Galactic", "Rare"), ("Galactic", "Common")],
    ),
    (
        "galactic_epic_large_scale_100.png",
        1.0,
        [("Galactic", "Epic")],
    ),
    (
        "mixed_beskar_epic_galactic_mythic_scale_083.png",
        0.83,
        [("Beskar", "Epic"), ("Galactic", "Mythic")],
    ),
    (
        "galactic_common_red_background_scale_100.png",
        1.0,
        [("Galactic", "Common")],
    ),
    (
        "galactic_common_blue_background_false_rare_scale_100.png",
        1.0,
        [("Galactic", "Common")],
    ),
    (
        "galactic_epic_compact_scale_050.png",
        0.5,
        [("Galactic", "Epic")],
    ),
    (
        "galactic_rare_busy_background_scale_050.png",
        0.5,
        [("Galactic", "Rare")],
    ),
    (
        "galactic_common_timer_overlap_scale_050.png",
        0.5,
        [("Galactic", "Common")],
    ),
    (
        "galactic_rare_timer_overlap_scale_050.png",
        0.5,
        [("Galactic", "Rare")],
    ),
    (
        "mixed_beskar_rare_galactic_legendary_region_scale_065.png",
        0.65,
        [("Beskar", "Rare"), ("Galactic", "Legendary")],
    ),
)


TRAINING_REVIEW_CASES = (
    (
        "training_0793b08e_galacticrare.png",
        (1440, 1080),
        ("Galactic", "Rare"),
        ("Galactic", "Legendary"),
    ),
    (
        "training_3d1e09d9_galacticrare.png",
        (2560, 1080),
        ("Galactic", "Rare"),
        ("Galactic", "Common"),
    ),
    (
        "training_32a487db_galacticrare.png",
        (3440, 1440),
        ("Galactic", "Rare"),
        ("Galactic", "Epic"),
    ),
    (
        "training_7644b4ff_galacticlegendary.png",
        (2560, 1080),
        ("Galactic", "Legendary"),
        ("Galactic", "Rare"),
    ),
)


class GalacticChatRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = load_droid_word_templates(BASE_DIR / "templates" / "droid_words")
        cls.pipeline = Pipeline(BASE_DIR / "templates")

    def test_live_galactic_word_templates_are_bundled(self):
        galactic = self.templates["Galactic"]

        self.assertGreaterEqual(len(galactic), 10)
        self.assertTrue(all(template.path.is_file() for template in galactic))

    def test_literal_galactic_word_shape_and_color_are_accepted(self):
        template = self.templates["Galactic"][1].image
        row = np.zeros((44, 300, 3), dtype=np.uint8)
        y, x = 14, 35
        target = row[y : y + template.shape[0], x : x + template.shape[1]]
        target[template > 0] = (224, 0, 146)  # BGR for #9200E0.

        verdict = classify_galactic_droid_word(row, self.templates)

        self.assertIsNotNone(verdict)
        self.assertEqual("Galactic", verdict[0])

    def test_held_out_common_capture_detects_at_live_scale(self):
        fixture = (
            BASE_DIR
            / "tests"
            / "galactic_fixtures"
            / "held_out_common_scale_075.png"
        )
        image = cv2.imread(str(fixture), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)

        result = self.pipeline.detect(image, known_scale=0.75)

        self.assertEqual(
            [("Galactic", "Common")],
            [(detection.droid, detection.rarity) for detection in result.detections],
        )

    def test_supplied_missed_alert_captures_detect_every_expected_row(self):
        fixture_dir = BASE_DIR / "tests" / "galactic_fixtures"
        for filename, scale, expected in SUPPLIED_CAPTURE_CASES:
            with self.subTest(filename=filename):
                image = cv2.imread(str(fixture_dir / filename), cv2.IMREAD_COLOR)
                self.assertIsNotNone(image)

                result = self.pipeline.detect(image, known_scale=scale)
                detected = [(item.droid, item.rarity) for item in result.detections]

                self.assertEqual(expected, detected)
                self.assertEqual(
                    [pair for pair in expected if pair[0] == "Galactic" or pair == ("Beskar", "Epic")],
                    [(item.droid, item.rarity) for item in result.detections if item.should_alert],
                )

    def test_supplied_captures_survive_larger_resolution_variants(self):
        fixture_dir = BASE_DIR / "tests" / "galactic_fixtures"
        for filename, scale, expected in SUPPLIED_CAPTURE_CASES:
            image = cv2.imread(str(fixture_dir / filename), cv2.IMREAD_COLOR)
            self.assertIsNotNone(image)
            enlarged = cv2.resize(image, None, fx=1.25, fy=1.25, interpolation=cv2.INTER_CUBIC)

            with self.subTest(filename=filename, resolution_scale=1.25):
                result = self.pipeline.detect(enlarged, known_scale=scale * 1.25)
                self.assertEqual(
                    expected,
                    [(item.droid, item.rarity) for item in result.detections],
                )

    def test_reviewed_galactic_false_detections_are_corrected(self):
        fixture_dir = BASE_DIR / "tests" / "galactic_fixtures"
        for filename, screen, expected, rejected in TRAINING_REVIEW_CASES:
            with self.subTest(filename=filename):
                image = cv2.imread(str(fixture_dir / filename), cv2.IMREAD_COLOR)
                self.assertIsNotNone(image)

                result = self.pipeline.detect(
                    image,
                    screen_width=screen[0],
                    screen_height=screen[1],
                )
                detected = [(item.droid, item.rarity) for item in result.detections]

                self.assertIn(expected, detected)
                self.assertNotIn(rejected, detected)

    def test_reviewed_compact_galactic_rare_row_is_not_missed(self):
        fixture = (
            BASE_DIR
            / "tests"
            / "galactic_fixtures"
            / "training_recall_71235c34_stack.png"
        )
        image = cv2.imread(str(fixture), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)

        result = self.pipeline.detect(image, screen_width=2560, screen_height=1080)

        self.assertEqual(
            [("Galactic", "Common"), ("Galactic", "Rare"), ("Galactic", "Common")],
            [(item.droid, item.rarity) for item in result.detections],
        )

    def test_supplied_beskar_epic_training_template_is_bundled(self):
        template = (
            BASE_DIR
            / "templates"
            / "rarity_rois"
            / "Beskar__Epic__BE_user_20260719.png"
        )

        self.assertTrue(template.is_file())

    def test_other_purple_words_do_not_become_galactic(self):
        for text in ("EPIC", "MYTHIC", "PURPLE SHOP"):
            with self.subTest(text=text):
                row = np.zeros((44, 300, 3), dtype=np.uint8)
                cv2.putText(
                    row,
                    text,
                    (35, 29),
                    cv2.FONT_HERSHEY_DUPLEX,
                    0.7,
                    (224, 0, 146),
                    2,
                    cv2.LINE_AA,
                )

                self.assertIsNone(classify_galactic_droid_word(row, self.templates))


if __name__ == "__main__":
    unittest.main()
