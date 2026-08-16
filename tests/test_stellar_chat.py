from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import cv2


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.classifier import (  # noqa: E402
    build_scaled_droid_word_templates,
    classify_stellar_droid_word,
    load_droid_word_templates,
)
from droid_alerts.pipeline import Pipeline  # noqa: E402


class StellarChatRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_dir = BASE_DIR / "tests" / "stellar_fixtures"
        cls.manifest = json.loads(
            (cls.fixture_dir / "manifest.json").read_text(encoding="utf-8")
        )
        cls.templates = load_droid_word_templates(
            BASE_DIR / "templates" / "droid_words"
        )
        cls.scaled_templates = build_scaled_droid_word_templates(cls.templates)
        cls.pipeline = Pipeline(BASE_DIR / "templates")

    def _detect_case(self, case: dict, image=None):
        if image is None:
            image = cv2.imread(
                str(self.fixture_dir / case["file"]), cv2.IMREAD_COLOR
            )
        self.assertIsNotNone(image)
        if case.get("known_scale") is not None:
            return self.pipeline.detect(image, known_scale=case["known_scale"])
        width, height = case["source_screen"]
        return self.pipeline.detect(
            image,
            screen_width=width,
            screen_height=height,
        )

    def test_stellar_word_templates_are_bundled(self):
        stellar = self.templates["Stellar"]

        self.assertGreaterEqual(len(stellar), 9)
        self.assertTrue(all(template.path.is_file() for template in stellar))

    def test_tight_crop_keeps_both_stellar_word_shapes(self):
        case = next(
            item
            for item in self.manifest["cases"]
            if item["mode"] == "word_crop"
        )
        image = cv2.imread(str(self.fixture_dir / case["file"]), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)

        for row_y in case["stellar_word_rows"]:
            with self.subTest(row_y=row_y):
                verdict = classify_stellar_droid_word(
                    image[row_y : row_y + 44],
                    self.templates,
                    scaled_templates=self.scaled_templates,
                )
                self.assertIsNotNone(verdict)
                self.assertEqual("Stellar", verdict[0])

    def test_supplied_bands_detect_all_stellar_rows(self):
        for case in self.manifest["cases"]:
            if case["mode"] != "band":
                continue
            with self.subTest(file=case["file"]):
                result = self._detect_case(case)
                detected = [
                    [item.droid, item.rarity]
                    for item in result.detections
                    if item.droid == "Stellar"
                ]
                self.assertEqual(case["expected_stellar"], detected)
                for item in result.detections:
                    if item.droid != "Stellar":
                        continue
                    if item.rarity in {"Common", "Rare"}:
                        self.assertFalse(item.should_alert)
                    else:
                        self.assertTrue(item.should_alert)

    def test_representative_bands_survive_larger_resolution_variants(self):
        filenames = {
            "image_01_auto_chat_band.png",
            "image_04_auto_chat_band.png",
            "image_05_auto_chat_band.png",
        }
        for case in self.manifest["cases"]:
            if case["file"] not in filenames:
                continue
            image = cv2.imread(str(self.fixture_dir / case["file"]), cv2.IMREAD_COLOR)
            enlarged = cv2.resize(
                image,
                None,
                fx=1.25,
                fy=1.25,
                interpolation=cv2.INTER_CUBIC,
            )
            enlarged_case = dict(case)
            if case.get("source_screen"):
                width, height = case["source_screen"]
                enlarged_case["source_screen"] = [
                    round(width * 1.25),
                    round(height * 1.25),
                ]

            with self.subTest(file=case["file"]):
                result = self._detect_case(enlarged_case, enlarged)
                detected = [
                    [item.droid, item.rarity]
                    for item in result.detections
                    if item.droid == "Stellar"
                ]
                self.assertEqual(case["expected_stellar"], detected)

    def test_existing_galactic_rows_do_not_become_stellar(self):
        fixture_dir = BASE_DIR / "tests" / "galactic_fixtures"
        for filename, scale in (
            ("galactic_epic_large_scale_100.png", 1.0),
            ("mixed_beskar_epic_galactic_mythic_scale_083.png", 0.83),
            ("galactic_common_red_background_scale_100.png", 1.0),
        ):
            with self.subTest(filename=filename):
                image = cv2.imread(str(fixture_dir / filename), cv2.IMREAD_COLOR)
                result = self.pipeline.detect(image, known_scale=scale)
                self.assertNotIn(
                    "Stellar",
                    {item.droid for item in result.detections},
                )


if __name__ == "__main__":
    unittest.main()
