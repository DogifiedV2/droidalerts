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


class GalacticChatRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = load_droid_word_templates(BASE_DIR / "templates" / "droid_words")

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

        result = Pipeline(BASE_DIR / "templates").detect(image, known_scale=0.75)

        self.assertEqual(
            [("Galactic", "Common")],
            [(detection.droid, detection.rarity) for detection in result.detections],
        )

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
