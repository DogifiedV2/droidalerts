from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.learned_identity import (
    EXPECTED_CLASSES,
    UNKNOWN_IDENTITY,
    LearnedIdentityModel,
)


class FakeNet:
    def __init__(self):
        self.inputs = []

    def setPreferableBackend(self, _backend):
        return None

    def setPreferableTarget(self, _target):
        return None

    def setInput(self, value):
        self.inputs.append(value.copy())

    def forward(self):
        batch_size = self.inputs[-1].shape[0]
        output = np.zeros((batch_size, len(EXPECTED_CLASSES)), dtype=np.float32)
        output[0, EXPECTED_CLASSES.index("R2")] = 8.0
        if batch_size > 1:
            output[1, EXPECTED_CLASSES.index(UNKNOWN_IDENTITY)] = 7.0
        return output


def write_model_files(root: Path, *, classes=EXPECTED_CLASSES):
    model_path = root / "model.onnx"
    model_path.write_bytes(b"fake onnx")
    manifest_path = root / "model.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "classes": list(classes),
                "input_size": 96,
                "batch_size": 2,
                "mean": [0.485, 0.456, 0.406],
                "standard_deviation": [0.229, 0.224, 0.225],
                "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return model_path, manifest_path


class LearnedIdentityModelTests(unittest.TestCase):
    def test_fixed_batch_cpu_model_pads_without_returning_padding_results(self):
        with tempfile.TemporaryDirectory() as folder:
            model_path, manifest_path = write_model_files(Path(folder))
            net = FakeNet()
            with patch(
                "droid_alerts.belt.learned_identity.cv2.dnn.readNetFromONNX",
                return_value=net,
            ):
                model = LearnedIdentityModel(model_path, manifest_path)
                results = model.predict(
                    [
                        np.zeros((60, 40, 3), dtype=np.uint8),
                        np.full((60, 40, 3), 127, dtype=np.uint8),
                        np.full((60, 40, 3), 255, dtype=np.uint8),
                    ]
                )

        self.assertEqual(["R2", UNKNOWN_IDENTITY, "R2"], [item.name for item in results])
        self.assertEqual(2, len(net.inputs))
        self.assertTrue(all(item.shape == (2, 3, 96, 96) for item in net.inputs))
        self.assertGreater(results[0].margin, 0.8)

    def test_manifest_checksum_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            model_path, manifest_path = write_model_files(Path(folder))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "checksum"):
                LearnedIdentityModel(model_path, manifest_path)

    def test_model_cannot_silently_change_the_droid_class_order(self):
        with tempfile.TemporaryDirectory() as folder:
            model_path, manifest_path = write_model_files(
                Path(folder),
                classes=tuple(reversed(EXPECTED_CLASSES)),
            )

            with self.assertRaisesRegex(RuntimeError, "classes"):
                LearnedIdentityModel(model_path, manifest_path)


if __name__ == "__main__":
    unittest.main()
