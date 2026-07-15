from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.ocr import RapidOcrEngine
from rapidocr import ModelType, OCRVersion


class RapidOcrConfigurationTests(unittest.TestCase):
    def test_windows_configuration_uses_proven_hybrid_models_and_two_threads(self):
        received = {}
        rapidocr_module = types.ModuleType("rapidocr")

        class FakeRapidOCR:
            def __init__(self, *, params):
                received.update(params)

        rapidocr_module.RapidOCR = FakeRapidOCR
        rapidocr_module.ModelType = ModelType
        rapidocr_module.OCRVersion = OCRVersion
        with (
            patch.dict(sys.modules, {"rapidocr": rapidocr_module}),
            patch("droid_alerts.belt.ocr.sys.platform", "win32"),
        ):
            engine = RapidOcrEngine()

        self.assertEqual(ModelType.SMALL, received["Det.model_type"])
        self.assertEqual(ModelType.TINY, received["Rec.model_type"])
        self.assertEqual(OCRVersion.PPOCRV6, received["Det.ocr_version"])
        self.assertEqual(OCRVersion.PPOCRV6, received["Rec.ocr_version"])
        self.assertEqual(13, received["Global.width_height_ratio"])
        self.assertEqual(2, received["EngineConfig.onnxruntime.intra_op_num_threads"])
        self.assertEqual(1, received["EngineConfig.onnxruntime.inter_op_num_threads"])
        self.assertEqual("small", engine.engine_params["Det.model_type"])
        self.assertEqual("tiny", engine.engine_params["Rec.model_type"])
        self.assertEqual("PP-OCRv6", engine.engine_params["Rec.ocr_version"])


if __name__ == "__main__":
    unittest.main()
