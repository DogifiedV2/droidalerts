from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from tools.review_belt_samples import record_decision


class BeltSampleReviewTests(unittest.TestCase):
    def test_confirmed_review_is_identity_only_until_family_is_curated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_image = root / "source.png"
            source_metadata = root / "source.json"
            cv2.imwrite(
                str(source_image),
                np.full((120, 100, 3), 80, dtype=np.uint8),
            )
            source_metadata.write_text("{}\n", encoding="utf-8")
            metadata = {
                "_source_metadata": str(source_metadata),
                "_source_image": str(source_image),
                "sample_id": "sample",
                "image_file": source_image.name,
                "name": "R8",
                "family": "Gold",
                "art_box_in_crop": [20, 10, 60, 60],
            }

            confirmed_path = record_decision(
                root,
                metadata,
                decision="confirmed",
                name="R8",
                family="Gold",
            )

            self.assertIsNotNone(confirmed_path)
            confirmed = json.loads(
                confirmed_path.read_text(encoding="utf-8")
            )
            self.assertTrue(confirmed["use_for_identity"])
            self.assertFalse(confirmed["use_for_family"])


if __name__ == "__main__":
    unittest.main()
