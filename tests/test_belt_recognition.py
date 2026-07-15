from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.ocr import TextObservation
from droid_alerts.belt.recognition import (
    UNKNOWN,
    CardRecognizer,
    classify_card_family_border,
    exact_canonical_name,
    exact_card_family,
)


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


class WidthCappedScalingOcr:
    card_input_scale = 1.5
    card_ocr_band = (0.35, 1.0)
    card_max_input_width = 600

    def __init__(self, observation):
        self.observation = observation
        self.frame_shapes = []

    def read(self, image_bgr):
        self.frame_shapes.append(image_bgr.shape)
        scale = image_bgr.shape[1] / 900
        x, y, width, height = self.observation.box
        band_y = round(520 * self.card_ocr_band[0])
        return [
            TextObservation(
                self.observation.text,
                self.observation.confidence,
                (
                    round(x * scale),
                    round((y - band_y) * scale),
                    round(width * scale),
                    round(height * scale),
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


def draw_rarity_pill(frame, name_box, rarity, *, x_offset=2.5, width_ratio=1.25):
    hues = {
        "Common": (0, 0, 190),
        "Rare": (90, 210, 220),
        "Epic": (130, 210, 220),
        "Legendary": (18, 210, 220),
        "Mythic": (165, 210, 220),
    }
    x, y, _width, height = name_box
    hsv = np.uint8([[hues[rarity]]])
    color = tuple(int(value) for value in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0])
    x1 = round(x + x_offset * height)
    y1 = round(y + 0.86 * height)
    x2 = round(x1 + width_ratio * height)
    y2 = round(y1 + 0.30 * height)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, -1)


class CardRecognitionTests(unittest.TestCase):
    def test_exact_compact_lookup_never_fuzzy_creates_an_identity(self):
        self.assertEqual("DRK-1 PROBE", exact_canonical_name(" drk_1 probe "))
        self.assertIsNone(exact_canonical_name("DRK-I PROBE"))
        self.assertIsNone(exact_canonical_name("1.10K"))
        self.assertIsNone(exact_canonical_name("HUD R2"))

    def test_card_family_accepts_joined_badge_text_without_fuzzy_guessing(self):
        self.assertEqual("Default", exact_card_family("DEFAULT RARE"))
        self.assertEqual("Beskar", exact_card_family("BESKARC"))
        self.assertEqual("Rainbow", exact_card_family("rainbow"))
        self.assertIsNone(exact_card_family("COMMON"))
        self.assertIsNone(exact_card_family("BESKXR"))

    def test_border_fallback_recognizes_only_distinctive_card_families(self):
        name_box = (100, 100, 100, 20)
        card_box = (80, 40, 200, 100)
        family_hsv = {
            "Gold": [(18, 220, 220)],
            "Diamond": [(90, 220, 220)],
            "Rainbow": [(18, 220, 220), (90, 220, 220), (135, 220, 220)],
            "Default": [(0, 0, 180)],
            "Beskar": [(90, 30, 180)],
        }
        for expected, colors in family_hsv.items():
            with self.subTest(family=expected):
                frame = blank_frame(width=400, height=240)
                x1, x2 = card_box[0], card_box[0] + card_box[2]
                y1 = round(name_box[1] + 1.20 * name_box[3])
                y2 = round(name_box[1] + 1.70 * name_box[3])
                segment_width = (x2 - x1) // len(colors)
                for index, hsv_color in enumerate(colors):
                    hsv = np.uint8([[hsv_color]])
                    bgr = tuple(
                        int(value) for value in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
                    )
                    start = x1 + index * segment_width
                    end = x2 if index == len(colors) - 1 else start + segment_width
                    frame[y1:y2, start:end] = bgr

                family, confidence = classify_card_family_border(
                    frame,
                    name_box,
                    card_box,
                )

                if expected in {"Gold", "Diamond", "Rainbow"}:
                    self.assertEqual(expected, family)
                    self.assertGreater(confidence, 0.5)
                else:
                    self.assertEqual("", family)
                    self.assertEqual(0.0, confidence)

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

    def test_ocr_width_cap_overrides_upscale_and_remaps_boxes(self):
        frame = blank_frame()
        name_box = draw_card(frame, (132, 270, 60, 30), "R2")
        ocr = WidthCappedScalingOcr(TextObservation("R2", 0.98, name_box))

        result = CardRecognizer(ocr).analyze(frame)

        self.assertEqual((225, 600, 3), ocr.frame_shapes[0])
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

    def test_identity_supplies_fixed_class_regardless_of_visible_pill(self):
        frame = blank_frame(width=1_050)
        opti = draw_card(frame, (130, 270, 150, 30), "OPTI-STRK")
        bal = draw_card(frame, (430, 270, 135, 30), "BAL-CORE")
        cb = draw_card(frame, (730, 270, 55, 30), "CB")
        draw_rarity_pill(frame, opti, "Common")
        draw_rarity_pill(frame, bal, "Mythic")
        draw_rarity_pill(frame, cb, "Legendary")
        ocr = StaticOcr(
            [
                TextObservation("OPTI-STRK", 0.99, opti),
                TextObservation("BAL-CORE", 0.99, bal),
                TextObservation("CB", 0.99, cb),
                TextObservation("GOLD", 0.88, (134, 294, 52, 13)),
                TextObservation("BESKARC", 0.92, (434, 294, 78, 13)),
                TextObservation("DEFAULT", 0.95, (734, 294, 70, 13)),
                TextObservation("COMMON", 0.99, (806, 294, 72, 13)),
            ]
        )

        result = CardRecognizer(ocr).analyze(frame)

        self.assertEqual(
            [
                ("OPTI-STRK", "Gold", "Legendary"),
                ("BAL-CORE", "Beskar", "Rare"),
                ("CB", "Default", "Common"),
            ],
            [(item.match.name, item.family, item.rarity) for item in result.observations],
        )

    def test_right_badge_does_not_change_fixed_class_or_visual_tier(self):
        frame = blank_frame()
        name_box = draw_card(frame, (130, 270, 55, 30), "R2")
        draw_rarity_pill(frame, name_box, "Common")
        result = CardRecognizer(
            StaticOcr(
                [
                    TextObservation("R2", 0.99, name_box),
                    TextObservation("COMMON", 0.99, (205, 294, 72, 13)),
                ]
            )
        ).analyze(frame)

        self.assertEqual("", result.observations[0].family)
        self.assertEqual("Epic", result.observations[0].rarity)
        self.assertEqual(1.0, result.observations[0].rarity_confidence)


if __name__ == "__main__":
    unittest.main()
