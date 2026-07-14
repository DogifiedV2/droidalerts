from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.classifier import load_templates, read_image


def _write_png(path: Path) -> None:
    image = np.full((12, 24, 3), 127, dtype=np.uint8)
    success, encoded = cv2.imencode(".png", image)
    assert success
    path.write_bytes(encoded.tobytes())


class UnicodeImagePathTests(unittest.TestCase):
    def test_runtime_images_load_from_unicode_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            template_dir = Path(temporary_dir) / "Русский_日本語_العربية"
            template_dir.mkdir()
            template_path = template_dir / "Common__01.png"
            _write_png(template_path)

            # Runtime reads must not pass Unicode paths to OpenCV's Windows
            # filesystem API; decoding bytes in memory works in every locale.
            with patch("droid_alerts.classifier.cv2.imread", side_effect=AssertionError("cv2.imread used")):
                image = read_image(template_path)
                templates = load_templates(template_dir)

            self.assertEqual(image.shape, (12, 24, 3))
            self.assertEqual(len(templates), 1)
            self.assertEqual(templates[0].rarity, "Common")
            self.assertEqual(templates[0].image.shape, (12, 24))


if __name__ == "__main__":
    unittest.main()
