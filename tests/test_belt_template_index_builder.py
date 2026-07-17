from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.template_recognition import BeltTemplateIndex
from tools.build_belt_template_index import augment_index


class BeltTemplateIndexBuilderTests(unittest.TestCase):
    def test_partial_library_augments_complete_index_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            confirmed = root / "confirmed" / "r2"
            confirmed.mkdir(parents=True)
            image = np.full((180, 170, 3), (65, 80, 110), dtype=np.uint8)
            generator = np.random.default_rng(42)
            art = generator.integers(0, 256, (100, 100, 3), dtype=np.uint8)
            image[14:114, 28:128] = art
            cv2.rectangle(image, (20, 120), (145, 155), (8, 8, 10), -1)
            image_path = confirmed / "sample.png"
            cv2.imwrite(str(image_path), image)
            (confirmed / "sample.json").write_text(
                json.dumps(
                    {
                        "name": "R2",
                        "family": "Default",
                        "art_box_in_crop": [28, 14, 100, 100],
                        "quality_score": 1.0,
                    }
                ),
                encoding="utf-8",
            )
            output = root / "augmented.npz"

            first_manifest = augment_index(
                root / "confirmed",
                BASE_DIR / "templates" / "belt_blueprints.npz",
                output,
            )
            first = BeltTemplateIndex.load(output)
            first_identity_count = first.identity_hog.shape[0]
            first_family_count = first.family_histograms.shape[0]
            second_manifest = augment_index(root / "confirmed", output, output)
            second = BeltTemplateIndex.load(output)

            self.assertEqual(1, first_manifest["added_identity_templates"])
            self.assertEqual(1, first_manifest["added_family_templates"])
            self.assertEqual(0, second_manifest["added_identity_templates"])
            self.assertEqual(0, second_manifest["added_family_templates"])
            self.assertEqual(first_identity_count, second.identity_hog.shape[0])
            self.assertEqual(first_family_count, second.family_histograms.shape[0])
            self.assertEqual(
                first_manifest["confirmed_samples"],
                second_manifest["confirmed_samples"],
            )


if __name__ == "__main__":
    unittest.main()
