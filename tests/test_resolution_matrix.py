from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))
sys.path.insert(0, str(BASE_DIR / "tests"))

from droid_alerts.normalize import scale_from_screen  # noqa: E402
from droid_alerts.pipeline import Pipeline  # noqa: E402
from droid_alerts.region import auto_box_percent, auto_box_profile  # noqa: E402
from make_synthetic import render_at_resolution  # noqa: E402
from resolution_matrix import RESOLUTION_CASES, place_inside, resize_for_screen  # noqa: E402


CHAT_CASES = (
    ("galactic_epic_large_scale_100.png", 1.0, [("Galactic", "Epic")]),
    (
        "galactic_common_blue_background_false_rare_scale_100.png",
        1.0,
        [],
    ),
    (
        "review_resolution_beskar_epic_reference_114332fb.png",
        1.0,
        [("Beskar", "Epic")],
    ),
    (
        "review_resolution_rainbow_mythic_reference_68d001f3.png",
        1.0,
        [("Rainbow", "Mythic")],
    ),
    (
        "review_resolution_galactic_legendary_987c3480.png",
        1.0,
        [("Rainbow", "Common"), ("Galactic", "Legendary")],
    ),
    (
        "review_resolution_galactic_mythic_c2956efc.png",
        1.0,
        [("Galactic", "Mythic")],
    ),
    (
        "review_resolution_beskar_legendary_41c3eeed.png",
        1.0,
        [("Rainbow", "Common"), ("Beskar", "Legendary")],
    ),
    (
        "review_resolution_beskar_mythic_719d8079.png",
        1.0,
        [("Diamond", "Common"), ("Diamond", "Common"), ("Beskar", "Mythic")],
    ),
)


def _chat_band_at_resolution(
    image: np.ndarray,
    width: int,
    height: int,
    *,
    source_scale: float,
) -> np.ndarray:
    scaled = resize_for_screen(
        image,
        source_scale=source_scale,
        target_scale=scale_from_screen(height, width),
    )
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    box = auto_box_percent(width, height)
    place_inside(scaled, frame, x=box.left, y=box.top)
    return frame[box.top : box.bottom, box.left : box.right]


class SharedResolutionMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pipeline = Pipeline(BASE_DIR / "templates")

    def test_matrix_contains_every_supported_resolution_once(self) -> None:
        expected = {
            (1280, 720),
            (1366, 768),
            (1600, 900),
            (1920, 1080),
            (2560, 1440),
            (3840, 2160),
            (1280, 800),
            (1920, 1200),
            (3440, 1440),
            (1440, 1040),
            (1728, 1117),
        }

        self.assertEqual(expected, {case.size for case in RESOLUTION_CASES})
        self.assertEqual(len(expected), len(RESOLUTION_CASES))

    def test_chat_profile_and_detection_across_shared_matrix(self) -> None:
        fixture_dir = BASE_DIR / "tests" / "galactic_fixtures"
        for filename, source_scale, expected in CHAT_CASES:
            image = cv2.imread(str(fixture_dir / filename), cv2.IMREAD_COLOR)
            self.assertIsNotNone(image)
            for case in RESOLUTION_CASES:
                with self.subTest(filename=filename, resolution=f"{case.width}x{case.height}"):
                    self.assertEqual(case.profile, auto_box_profile(case.width, case.height))
                    result = self.pipeline.detect(
                        _chat_band_at_resolution(
                            image,
                            case.width,
                            case.height,
                            source_scale=source_scale,
                        ),
                        screen_width=case.width,
                        screen_height=case.height,
                    )
                    self.assertEqual(
                        expected,
                        [(item.droid, item.rarity) for item in result.detections],
                    )

    def test_chat_fixture_generator_outputs_every_matrix_size(self) -> None:
        source = np.zeros((144, 256, 3), dtype=np.uint8)
        for case in RESOLUTION_CASES:
            with self.subTest(resolution=f"{case.width}x{case.height}"):
                rendered = render_at_resolution(
                    source,
                    target_width=case.width,
                    target_height=case.height,
                )
                self.assertEqual((case.height, case.width, 3), rendered.shape)


if __name__ == "__main__":
    unittest.main()
