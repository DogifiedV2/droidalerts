from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.names import droid_class
from droid_alerts.belt.template_recognition import (
    BeltTemplateIndex,
    TemplateCardRecognizer,
    TemplateRecognitionConfig,
    identity_features,
)


HANDOFF_DATA = BASE_DIR / "dist" / "Droid Alerts" / "data"
SAMPLES = HANDOFF_DATA / "belt_template_samples" / "detections"
BLURRY_SESSION = (
    HANDOFF_DATA
    / "belt_dev"
    / "session_20260715_193728_684_80125"
)


@unittest.skipUnless(SAMPLES.is_dir(), "local Belt handoff samples are not available")
class BeltHandoffIdentityRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = BeltTemplateIndex.load()
        cls.config = TemplateRecognitionConfig()

    def test_known_similar_droids_abstain_instead_of_repeating_wrong_saved_labels(self):
        corrections = {
            "2bb": ("2BB", "BB"),
            "drk_1_probe": ("DRK-1 PROBE", "VECT-ARM"),
            "r3": ("R3", "R9"),
            "pit": ("PIT", "ID10"),
        }
        for folder, (wrong_label, corrected_identity) in corrections.items():
            metadata_paths = sorted((SAMPLES / folder).glob("*.json"))
            self.assertTrue(metadata_paths, folder)
            for metadata_path in metadata_paths:
                with self.subTest(sample=metadata_path.name):
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    image = cv2.imread(str(metadata_path.with_suffix(".png")))
                    self.assertIsNotNone(image)
                    x, y, width, height = metadata["art_box_in_crop"]
                    query = identity_features(image[y : y + height, x : x + width])
                    scores = self.index.identity_hog @ query
                    name_scores = np.maximum.reduceat(
                        scores,
                        self.index.identity_name_offsets[:-1],
                    )
                    order = np.argsort(name_scores)[::-1]
                    best_index, runner_up_index = (int(value) for value in order[:2])
                    best_name = self.index.identity_names[best_index]
                    margin = float(name_scores[best_index] - name_scores[runner_up_index])
                    would_emit = (
                        float(name_scores[best_index])
                        >= self.config.minimum_identity_similarity
                        and margin >= self.config.minimum_identity_margin
                    )

                    self.assertEqual(wrong_label, metadata["detected_name"])
                    self.assertTrue(
                        best_name == corrected_identity or not would_emit,
                        f"{metadata_path.name}: {best_name=} {margin=:.4f}",
                    )
                    self.assertFalse(would_emit and best_name == wrong_label)

    @unittest.skipUnless(BLURRY_SESSION.is_dir(), "local blurry Belt frames are not available")
    def test_blurry_stress_frames_emit_no_epic_or_legendary_identity(self):
        recognizer = TemplateCardRecognizer()
        high_rarity = []
        for frame_path in sorted(BLURRY_SESSION.glob("frame_*.png")):
            frame = cv2.imread(str(frame_path))
            self.assertIsNotNone(frame)
            result = recognizer.analyze(frame)
            high_rarity.extend(
                candidate.canonical_name
                for candidate in result.candidates
                if candidate.accepted
                and droid_class(candidate.canonical_name) in {"Epic", "Legendary"}
            )

        self.assertEqual([], high_rarity)


if __name__ == "__main__":
    unittest.main()
