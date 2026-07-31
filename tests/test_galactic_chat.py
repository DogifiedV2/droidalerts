from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.classifier import (  # noqa: E402
    classify_galactic_droid_word,
    collect_word_matches,
    galactic_rarity_roi_fallback,
    load_droid_word_templates,
    rarity_text_color_counts,
)
from droid_alerts.chat_alerts import REMOVED_CHAT_DETECTIONS  # noqa: E402
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


REVIEWED_FALSE_TARGET_CASES = (
    (
        "review_false_galactic_rare_blue_common_1482a3d0.png",
        (2560, 1080),
        ("Galactic", "Rare"),
    ),
    (
        "review_false_galactic_legendary_rare_ca00e765.png",
        (2560, 1440),
        ("Galactic", "Legendary"),
    ),
    (
        "review_false_galactic_mythic_rare_c5e2ba9f.png",
        (1440, 1080),
        ("Galactic", "Mythic"),
    ),
    (
        "review_false_galactic_common_rare_2928047e.png",
        (2560, 1080),
        ("Galactic", "Common"),
    ),
    (
        "review_false_beskar_epic_rare_467a79d9.png",
        (2560, 1440),
        ("Beskar", "Epic"),
    ),
    (
        "review_false_beskar_epic_common_be167e75.png",
        (2560, 1440),
        ("Beskar", "Epic"),
    ),
)


REVIEWED_RECALL_CASES = (
    (
        "review_recall_beskar_legendary_df23bdf0.png",
        (1440, 1080),
        ("Beskar", "Legendary"),
    ),
    (
        "review_recall_beskar_epic_92d17d4d.png",
        (2560, 1440),
        ("Beskar", "Epic"),
    ),
    (
        "review_recall_beskar_legendary_4f94003e.png",
        (1920, 1080),
        ("Beskar", "Legendary"),
    ),
    (
        "review_recall_beskar_legendary_c1feb426.png",
        (1920, 1080),
        ("Beskar", "Legendary"),
    ),
    (
        "review_recall_beskar_epic_c8a2e65f.png",
        (1920, 1080),
        ("Beskar", "Epic"),
    ),
)


def _without_removed_detections(
    pairs: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    return [pair for pair in pairs if pair not in REMOVED_CHAT_DETECTIONS]


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

    def test_held_out_common_capture_is_not_surfaced(self):
        fixture = (
            BASE_DIR
            / "tests"
            / "galactic_fixtures"
            / "held_out_common_scale_075.png"
        )
        image = cv2.imread(str(fixture), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)

        result = self.pipeline.detect(image, known_scale=0.75)

        self.assertEqual([], result.detections)
        self.assertTrue(
            any(rejection["reason"] == "removed-detection" for rejection in result.rejections)
        )

    def test_supplied_missed_alert_captures_detect_every_expected_row(self):
        fixture_dir = BASE_DIR / "tests" / "galactic_fixtures"
        for filename, scale, expected in SUPPLIED_CAPTURE_CASES:
            with self.subTest(filename=filename):
                image = cv2.imread(str(fixture_dir / filename), cv2.IMREAD_COLOR)
                self.assertIsNotNone(image)

                result = self.pipeline.detect(image, known_scale=scale)
                detected = [(item.droid, item.rarity) for item in result.detections]

                supported_expected = _without_removed_detections(expected)
                self.assertEqual(supported_expected, detected)
                self.assertEqual(
                    [
                        pair
                        for pair in supported_expected
                        if pair[0] == "Galactic" or pair == ("Beskar", "Epic")
                    ],
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
                    _without_removed_detections(expected),
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

                if expected in REMOVED_CHAT_DETECTIONS:
                    self.assertNotIn(expected, detected)
                else:
                    self.assertIn(expected, detected)
                self.assertNotIn(rejected, detected)
                self.assertTrue(REMOVED_CHAT_DETECTIONS.isdisjoint(detected))

    def test_reviewed_false_targets_stay_rejected(self):
        fixture_dir = BASE_DIR / "tests" / "galactic_fixtures"
        for filename, screen, rejected in REVIEWED_FALSE_TARGET_CASES:
            with self.subTest(filename=filename):
                image = cv2.imread(str(fixture_dir / filename), cv2.IMREAD_COLOR)
                self.assertIsNotNone(image)

                result = self.pipeline.detect(
                    image,
                    screen_width=screen[0],
                    screen_height=screen[1],
                )

                self.assertNotIn(
                    rejected,
                    [(item.droid, item.rarity) for item in result.detections],
                )

    def test_reviewed_compact_alerts_are_not_lost(self):
        fixture_dir = BASE_DIR / "tests" / "galactic_fixtures"
        for filename, screen, expected in REVIEWED_RECALL_CASES:
            with self.subTest(filename=filename):
                image = cv2.imread(str(fixture_dir / filename), cv2.IMREAD_COLOR)
                self.assertIsNotNone(image)

                result = self.pipeline.detect(
                    image,
                    screen_width=screen[0],
                    screen_height=screen[1],
                )

                self.assertIn(
                    expected,
                    [(item.droid, item.rarity) for item in result.detections],
                )

    def test_reviewed_compact_galactic_common_and_rare_rows_are_not_surfaced(self):
        fixture = (
            BASE_DIR
            / "tests"
            / "galactic_fixtures"
            / "training_recall_71235c34_stack.png"
        )
        image = cv2.imread(str(fixture), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)

        result = self.pipeline.detect(image, screen_width=2560, screen_height=1080)

        self.assertEqual([], result.detections)
        self.assertGreaterEqual(
            sum(
                rejection["reason"] == "removed-detection"
                for rejection in result.rejections
            ),
            1,
        )

    def test_supplied_beskar_epic_training_template_is_bundled(self):
        template = (
            BASE_DIR
            / "templates"
            / "rarity_rois"
            / "Beskar__Epic__BE_user_20260719.png"
        )

        self.assertTrue(template.is_file())

    def test_reviewed_galactic_priority_roi_prototypes_are_bundled(self):
        templates = self.pipeline.detector.rarity_roi_templates["Galactic"]
        counts = Counter(template.rarity for template in templates)
        manifest = json.loads(
            (
                BASE_DIR / "templates" / "galactic_rarity_rois_manifest.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual({"Epic": 8, "Legendary": 8, "Mythic": 8}, dict(counts))
        self.assertEqual(
            {"Epic": 146, "Legendary": 58, "Mythic": 41},
            manifest["selection"]["reviewedRows"],
        )
        self.assertTrue(all(template.image.shape == (44, 230) for template in templates))

    def test_reviewed_galactic_roi_fallback_recognizes_priority_rarities(self):
        fixture_dir = BASE_DIR / "tests" / "galactic_fixtures"
        for filename, expected in (
            ("galactic_epic_large_scale_100.png", "Epic"),
            ("review_resolution_galactic_legendary_987c3480.png", "Legendary"),
            ("review_resolution_galactic_mythic_c2956efc.png", "Mythic"),
        ):
            with self.subTest(filename=filename):
                image = cv2.imread(str(fixture_dir / filename), cv2.IMREAD_COLOR)
                result = self.pipeline.detect(
                    image,
                    screen_width=2560,
                    screen_height=1440,
                    keep_normalized=True,
                )
                detection = next(
                    item
                    for item in result.detections
                    if (item.droid, item.rarity) == ("Galactic", expected)
                )
                y = detection.row_box[1]
                word_matches = collect_word_matches(
                    result.normalized_image,
                    self.pipeline.detector.templates,
                    min_score=0.20,
                )
                fallback = galactic_rarity_roi_fallback(
                    result.normalized_image,
                    y,
                    self.pipeline.detector.rarity_roi_templates["Galactic"],
                    rarity_text_color_counts(
                        result.normalized_image,
                        y,
                        "Galactic",
                        row_height=44,
                    ),
                    word_matches,
                    row_height=44,
                )

                self.assertIsNotNone(fallback)
                self.assertEqual(expected, fallback[0])

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
