from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import cv2


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.classifier import load_droid_word_templates  # noqa: E402
from droid_alerts.pipeline import Pipeline  # noqa: E402


class KyberChatRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_dir = BASE_DIR / "tests" / "kyber_fixtures"
        cls.manifest = json.loads(
            (cls.fixture_dir / "manifest.json").read_text(encoding="utf-8")
        )
        cls.templates = load_droid_word_templates(
            BASE_DIR / "templates" / "droid_words"
        )
        cls.pipeline = Pipeline(BASE_DIR / "templates")

    def _detect_case(self, case: dict, image=None, resize_factor: float = 1.0):
        if image is None:
            image = cv2.imread(
                str(self.fixture_dir / case["file"]), cv2.IMREAD_COLOR
            )
        self.assertIsNotNone(image)
        if case.get("known_scale") is not None:
            return self.pipeline.detect(
                image, known_scale=case["known_scale"] * resize_factor
            )
        width, height = case["source_screen"]
        return self.pipeline.detect(
            image,
            screen_width=round(width * resize_factor),
            screen_height=round(height * resize_factor),
        )

    @staticmethod
    def _kyber_rows(result) -> list[list[str]]:
        return [
            [item.droid, item.rarity]
            for item in result.detections
            if item.droid == "Kyber"
        ]

    def test_kyber_word_templates_are_bundled(self):
        kyber = self.templates["Kyber"]

        self.assertGreaterEqual(len(kyber), 10)
        self.assertTrue(all(template.path.is_file() for template in kyber))

    def test_supplied_bands_detect_every_kyber_row(self):
        for case in self.manifest["cases"]:
            with self.subTest(file=case["file"]):
                result = self._detect_case(case)
                self.assertEqual(case["expected_kyber"], self._kyber_rows(result))
                other_rows = [
                    [item.droid, item.rarity]
                    for item in result.detections
                    if item.droid != "Kyber"
                ]
                self.assertEqual(case.get("expected_other", []), other_rows)
                for item in result.detections:
                    if item.droid != "Kyber":
                        continue
                    self.assertEqual(
                        item.rarity in {"Epic", "Legendary", "Mythic"},
                        item.should_alert,
                    )

    def test_bands_survive_larger_resolution_variants(self):
        for case in self.manifest["cases"]:
            image = cv2.imread(str(self.fixture_dir / case["file"]), cv2.IMREAD_COLOR)
            enlarged = cv2.resize(
                image,
                None,
                fx=1.25,
                fy=1.25,
                interpolation=cv2.INTER_CUBIC,
            )
            with self.subTest(file=case["file"]):
                result = self._detect_case(case, enlarged, resize_factor=1.25)
                self.assertEqual(case["expected_kyber"], self._kyber_rows(result))

    def test_other_family_fixtures_do_not_become_kyber(self):
        for fixture_folder, pattern in (
            ("galactic_fixtures", "*.png"),
            ("stellar_fixtures", "*_band.png"),
        ):
            for path in sorted((BASE_DIR / "tests" / fixture_folder).glob(pattern)):
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                with self.subTest(file=path.name):
                    result = self.pipeline.detect(image)
                    self.assertNotIn(
                        "Kyber",
                        {item.droid for item in result.detections},
                    )


if __name__ == "__main__":
    unittest.main()
