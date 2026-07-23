from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts import classifier
from droid_alerts.classifier import (
    _DroidWordEvidence,
    _batch_normalized_correlation,
    _build_rarity_evidence,
    build_rarity_correlation_bank,
    build_scaled_droid_word_templates,
    droid_word_shape_score,
    droid_word_text_profile,
    fixed_rarity_roi,
    load_droid_word_templates,
    load_rarity_roi_templates,
    rarity_color_counts,
    rarity_text_color_counts,
)
from droid_alerts.config import templates_dir
from droid_alerts.normalize import normalize_band
from droid_alerts.row_finder import (
    analyze_phrase,
    band_has_phrase_evidence,
    phrase_row_seeds,
    phrase_text_bands,
)


def fixture_image() -> np.ndarray:
    path = (
        BASE_DIR
        / "tests"
        / "galactic_fixtures"
        / "mixed_beskar_epic_galactic_mythic_scale_083.png"
    )
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(path)
    return normalize_band(image, 0.83)


class DetectorOptimizationTests(unittest.TestCase):
    def test_normalize_band_identity_and_resized_dimensions(self) -> None:
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        self.assertIs(image, normalize_band(image, 1.01))
        self.assertEqual((50, 100, 3), normalize_band(image, 2.0).shape)

    def test_scaled_droid_word_templates_are_reused(self):
        templates = load_droid_word_templates(Path(templates_dir()) / "droid_words")
        bank = build_scaled_droid_word_templates(templates)
        row = fixture_image()[0:44]

        with patch.object(classifier.cv2, "resize", wraps=cv2.resize) as resize:
            droid_word_shape_score(
                row,
                "Galactic",
                templates,
                scaled_templates=bank,
            )
            droid_word_shape_score(
                row,
                "Galactic",
                templates,
                scaled_templates=bank,
            )

        resize.assert_not_called()
        self.assertTrue(bank["Galactic"])
        self.assertTrue(all(len(entry.images) == 9 for entry in bank["Galactic"]))

    def test_droid_and_rarity_evidence_match_uncached_helpers(self):
        image = fixture_image()
        row = image[0:44]
        word_evidence = _DroidWordEvidence(row)
        self.assertEqual(
            droid_word_text_profile(row),
            droid_word_text_profile(row, evidence=word_evidence),
        )

        rarity_evidence = _build_rarity_evidence(
            image, 0, "Galactic", row_height=44
        )
        self.assertEqual(
            rarity_color_counts(image, 0, "Galactic", row_height=44),
            rarity_color_counts(
                image,
                0,
                "Galactic",
                row_height=44,
                evidence=rarity_evidence,
            ),
        )
        self.assertEqual(
            rarity_text_color_counts(image, 0, "Galactic", row_height=44),
            rarity_text_color_counts(
                image,
                0,
                "Galactic",
                row_height=44,
                evidence=rarity_evidence,
            ),
        )

    def test_rarity_evidence_computes_hsv_and_edges_once(self):
        image = fixture_image()
        original_cvt = cv2.cvtColor
        original_canny = cv2.Canny
        with (
            patch.object(classifier.cv2, "cvtColor", wraps=original_cvt) as cvt,
            patch.object(classifier.cv2, "Canny", wraps=original_canny) as canny,
        ):
            evidence = _build_rarity_evidence(
                image, 0, "Galactic", row_height=44
            )
            rarity_color_counts(
                image, 0, "Galactic", row_height=44, evidence=evidence
            )
            rarity_text_color_counts(
                image, 0, "Galactic", row_height=44, evidence=evidence
            )

        hsv_calls = [
            call
            for call in cvt.call_args_list
            if len(call.args) > 1 and call.args[1] == cv2.COLOR_BGR2HSV
        ]
        self.assertEqual(1, len(hsv_calls))
        self.assertEqual(1, canny.call_count)

    def test_phrase_analysis_matches_public_helpers(self):
        for image in (fixture_image(), np.zeros((220, 845, 3), dtype=np.uint8)):
            with self.subTest(nonzero=bool(image.any())):
                analysis = analyze_phrase(image)
                self.assertEqual(
                    phrase_text_bands(image),
                    phrase_text_bands(image, analysis=analysis),
                )
                self.assertEqual(
                    phrase_row_seeds(image),
                    phrase_row_seeds(image, analysis=analysis),
                )
                self.assertEqual(
                    band_has_phrase_evidence(image),
                    band_has_phrase_evidence(image, analysis=analysis),
                )

    def test_vectorized_roi_correlation_matches_opencv(self):
        template_root = Path(templates_dir()) / "rarity_rois"
        templates_by_droid = load_rarity_roi_templates(template_root)
        bank = build_rarity_correlation_bank(templates_by_droid)
        real = fixture_image()
        blank = np.zeros_like(real)
        cases = ((real, 0), (real, -100), (real, real.shape[0] + 100), (blank, 0))

        for droid, groups in bank.items():
            for group in groups:
                for image, y in cases:
                    rois = [
                        fixed_rarity_roi(image, y + dy, row_height=44)
                        for dy in range(-6, 7)
                    ]
                    if any(roi.shape != group.shape for roi in rois):
                        continue
                    scores = _batch_normalized_correlation(rois, group)
                    for row_index, roi in enumerate(rois):
                        for column, template in enumerate(group.templates):
                            expected = float(
                                cv2.matchTemplate(
                                    roi,
                                    template.image,
                                    cv2.TM_CCOEFF_NORMED,
                                )[0, 0]
                            )
                            self.assertAlmostEqual(
                                expected,
                                float(scores[row_index, column]),
                                delta=3e-6,
                                msg=f"{droid}/{template.path.name}",
                            )


if __name__ == "__main__":
    unittest.main()
