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

    def test_latest_false_identity_crops_now_abstain(self):
        cases = (
            ("r5", "20260715_202004_937900_t00092_f002882.json", "R5"),
            ("r9", "20260715_202023_259313_t00098_f003063.json", "R9"),
            ("r9", "20260715_202050_239229_t00105_f003319.json", "R9"),
            ("r9", "20260715_202059_256915_t00106_f003406.json", "R9"),
        )
        available = [
            (SAMPLES / folder / name, wrong_label)
            for folder, name, wrong_label in cases
            if (SAMPLES / folder / name).exists()
        ]
        if not available:
            self.skipTest("latest audited false-identity crops are unavailable")

        for metadata_path, wrong_label in available:
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
                required_margin = max(
                    self.config.minimum_identity_margin,
                    0.070 if best_name in {"R3", "R9"} else 0.0,
                )
                would_emit = (
                    float(name_scores[best_index])
                    >= self.config.minimum_identity_similarity
                    and margin >= required_margin
                )

                self.assertEqual(wrong_label, metadata["detected_name"])
                self.assertFalse(would_emit and best_name == wrong_label)

    def test_desktop_frame_cannot_emit_false_epic_card(self):
        session = HANDOFF_DATA / "belt_dev" / "session_20260715_201506_418_89101"
        frame_path = session / "frame_000001.png"
        if not frame_path.exists():
            self.skipTest("latest audited desktop frame is unavailable")
        frame = cv2.imread(str(frame_path))
        self.assertIsNotNone(frame)

        result = TemplateCardRecognizer().analyze(frame)

        self.assertEqual([], result.observations)

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
