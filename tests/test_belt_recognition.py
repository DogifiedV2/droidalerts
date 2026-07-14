from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.ocr import RapidOcrEngine, TextObservation, _parse_rapidocr_result
from droid_alerts.belt.recognition import UNKNOWN, CardRecognizer, exact_canonical_name


class StaticOcr:
    def __init__(self, observations):
        self.observations = observations
        self.frames = []

    def read(self, image_bgr):
        self.frames.append(image_bgr)
        return list(self.observations)


class ScalingOcr:
    card_input_scale = 1.5
    card_ocr_band = (0.35, 1.0)

    def __init__(self, observation):
        self.observation = observation
        self.frame_shapes = []

    def read(self, image_bgr):
        self.frame_shapes.append(image_bgr.shape)
        x, y, width, height = self.observation.box
        band_y = round(520 * self.card_ocr_band[0])
        return [
            TextObservation(
                self.observation.text,
                self.observation.confidence,
                (
                    round(x * 1.5),
                    round((y - band_y) * 1.5),
                    round(width * 1.5),
                    round(height * 1.5),
                ),
            )
        ]


def blank_frame(height=520, width=900):
    return np.full((height, width, 3), (105, 115, 125), dtype=np.uint8)


def draw_card(frame, name_box, text):
    x, y, width, height = name_box
    card_width = max(width + 2 * height, 6 * height)
    x1 = max(0, round(x - 1.2 * height))
    x2 = min(frame.shape[1] - 1, x1 + card_width)
    y1 = max(0, round(y - 5.0 * height))
    y2 = min(frame.shape[0] - 1, round(y + 1.5 * height))

    cv2.rectangle(frame, (x1, y1), (x2, y2), (75, 95, 125), -1)
    for index in range(12):
        center = (
            x1 + 12 + (index * 37) % max(20, x2 - x1 - 24),
            y1 + 12 + (index * 29) % max(20, y - y1 - 32),
        )
        color = (30 + index * 13, 180 - index * 7, 60 + index * 11)
        cv2.circle(frame, center, 4 + index % 7, color, -1)
    cv2.rectangle(frame, (x1, y - 6), (x2, y2), (12, 12, 15), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (235, 235, 235), 3)
    cv2.putText(
        frame,
        text,
        (x, y + max(height - 5, 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.35, height / 38),
        (250, 250, 250),
        2,
        cv2.LINE_AA,
    )
    return name_box


class CardRecognitionTests(unittest.TestCase):
    def test_exact_compact_lookup_never_fuzzy_creates_an_identity(self):
        self.assertEqual("DRK-1 PROBE", exact_canonical_name(" drk_1 probe "))
        self.assertIsNone(exact_canonical_name("DRK-I PROBE"))
        self.assertIsNone(exact_canonical_name("1.10K"))
        self.assertIsNone(exact_canonical_name("HUD R2"))

    def test_full_frame_keeps_all_ocr_text_but_only_accepts_exact_card(self):
        frame = blank_frame()
        name_box = draw_card(frame, (130, 270, 55, 30), "R2")
        ocr = StaticOcr(
            [
                TextObservation("1.10K", 0.99, (430, 55, 95, 26)),
                TextObservation("R2", 0.96, name_box),
            ]
        )

        result = CardRecognizer(ocr).analyze(frame)

        self.assertEqual(["1.10K", "R2"], [item.text for item in result.text_observations])
        self.assertEqual(["R2"], [item.canonical_name for item in result.candidates])
        self.assertEqual(["R2"], [item.match.name for item in result.observations])
        self.assertIs(ocr.frames[0], frame)

    def test_production_ocr_uses_configured_band_and_remaps_boxes(self):
        frame = blank_frame()
        name_box = draw_card(frame, (130, 270, 55, 30), "R2")
        ocr = ScalingOcr(TextObservation("R2", 0.98, name_box))

        result = CardRecognizer(ocr).analyze(frame)

        self.assertEqual((507, 1350, 3), ocr.frame_shapes[0])
        self.assertEqual(name_box, result.candidates[0].name_box)
        self.assertEqual(["R2"], [item.match.name for item in result.observations])

    def test_exact_hud_text_without_card_context_is_unknown(self):
        frame = blank_frame()
        hud_box = (150, 430, 55, 28)
        cv2.rectangle(frame, (110, 416), (300, 490), (12, 12, 12), -1)
        cv2.putText(frame, "R2", (150, 452), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        result = CardRecognizer(StaticOcr([TextObservation("R2", 0.99, hud_box)])).analyze(frame)

        self.assertEqual(UNKNOWN, result.candidates[0].identity)
        self.assertFalse(result.candidates[0].accepted)
        self.assertEqual([], result.observations)

    def test_only_dominant_horizontal_card_row_is_accepted(self):
        frame = blank_frame(720, 1050)
        first = draw_card(frame, (130, 290, 55, 30), "R2")
        second = draw_card(frame, (430, 294, 55, 30), "R3")
        hud_like = draw_card(frame, (760, 585, 55, 30), "R2")
        ocr = StaticOcr(
            [
                TextObservation("R2", 0.98, first),
                TextObservation("R3", 0.98, second),
                TextObservation("R2", 0.98, hud_like),
            ]
        )

        result = CardRecognizer(ocr).analyze(frame)

        self.assertEqual(["R2", "R3"], [item.match.name for item in result.observations])
        rejected = [item for item in result.candidates if not item.accepted]
        self.assertEqual(1, len(rejected))
        self.assertEqual("off_card_row", rejected[0].reason)

    def test_target_filter_uses_canonical_names_and_empty_means_all(self):
        frame = blank_frame()
        r2 = draw_card(frame, (130, 270, 55, 30), "R2")
        r3 = draw_card(frame, (430, 272, 55, 30), "R3")
        observations = [TextObservation("R2", 0.98, r2), TextObservation("R3", 0.98, r3)]

        targeted = CardRecognizer(StaticOcr(observations), target_names=("r2",)).analyze(frame)
        unfiltered = CardRecognizer(StaticOcr(observations), target_names=()).analyze(frame)

        self.assertEqual(["R2"], [item.match.name for item in targeted.observations])
        self.assertEqual(["R2", "R3"], [item.match.name for item in unfiltered.observations])

    def test_adjacent_words_can_form_only_an_exact_canonical_name(self):
        frame = blank_frame()
        combined_box = draw_card(frame, (130, 270, 210, 30), "IMPERIAL PROBE")
        left = (combined_box[0], combined_box[1], 105, combined_box[3])
        right = (combined_box[0] + 112, combined_box[1], 95, combined_box[3])
        ocr = StaticOcr(
            [
                TextObservation("IMPERIAL", 0.96, left),
                TextObservation("PROBE", 0.94, right),
            ]
        )

        result = CardRecognizer(ocr).analyze(frame)

        self.assertEqual(["IMPERIAL PROBE"], [item.match.name for item in result.observations])


class ResultObject:
    txts = ["GONK"]
    scores = [0.91]
    boxes = [[[1, 2], [31, 2], [31, 12], [1, 12]]]


class OcrCompatibilityTests(unittest.TestCase):
    def test_live_card_input_scale_uses_moving_belt_regression_value(self):
        self.assertEqual(1.25, RapidOcrEngine.card_input_scale)

    def test_current_result_object(self):
        result = _parse_rapidocr_result(ResultObject())
        self.assertEqual("GONK", result[0].text)
        self.assertEqual((1, 2, 30, 10), result[0].box)

    def test_legacy_list_result(self):
        result = _parse_rapidocr_result([[[[2, 3], [22, 3], [22, 13], [2, 13]], "R6", 0.8]])
        self.assertEqual("R6", result[0].text)
        self.assertEqual((2, 3, 20, 10), result[0].box)

    def test_runtime_caps_detector_size_and_disables_rotation_classifier(self):
        calls = []

        class FakeRapidOcr:
            def __init__(self, *, params):
                calls.append(("init", params))

            def __call__(self, image, **kwargs):
                calls.append(("call", image.shape, kwargs))
                return []

        with patch.dict("sys.modules", {"rapidocr": SimpleNamespace(RapidOCR=FakeRapidOcr)}):
            engine = RapidOcrEngine()
            engine.read(np.zeros((80, 300, 3), dtype=np.uint8))

        params = calls[0][1]
        self.assertEqual("max", params["Det.limit_type"])
        self.assertEqual(1600, params["Det.limit_side_len"])
        self.assertFalse(params["Global.use_cls"])
        self.assertEqual({"use_cls": False}, calls[1][2])


if __name__ == "__main__":
    unittest.main()
