from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.recognition import CardCandidate, CardContext
from droid_alerts.belt.names import droid_class
from droid_alerts.belt.sample_collection import BeltTemplateSampleCollector


def card_candidate(
    frame: np.ndarray,
    *,
    name: str = "MOUSE",
    x: int = 390,
    pattern: int = 0,
) -> CardCandidate:
    y, name_width, name_height = 190, 70, 30
    card_width = max(name_width + 2 * name_height, 6 * name_height)
    left = round(x - 1.2 * name_height)
    top = round(y - 5.0 * name_height)
    right = left + card_width
    art_bottom = round(y - 0.1 * name_height)
    bottom = round(y + 1.6 * name_height)

    if pattern == 0:
        for column in range(max(0, left), min(frame.shape[1], right)):
            value = (column - left) % 256
            frame[max(0, top) : min(frame.shape[0], art_bottom), column] = (value, value, value)
    elif pattern == 1:
        for column in range(max(0, left), min(frame.shape[1], right)):
            value = 255 - ((column - left) % 256)
            frame[max(0, top) : min(frame.shape[0], art_bottom), column] = (value, value, value)
    else:
        art = frame[max(0, top) : min(frame.shape[0], art_bottom), max(0, left) : min(frame.shape[1], right)]
        if art.size:
            yy, xx = np.indices(art.shape[:2])
            mask = ((xx // (3 + pattern)) + (yy // (4 + pattern))) % 2 == 0
            art[mask] = (240, 30 + pattern * 10, 180)
            art[~mask] = (20, 220, 40 + pattern * 10)

    if 0 <= left < frame.shape[1] and 0 <= top < frame.shape[0]:
        cv2.rectangle(
            frame,
            (max(0, left), max(0, y - 8)),
            (min(frame.shape[1] - 1, right - 1), min(frame.shape[0] - 1, bottom - 1)),
            (12, 12, 15),
            -1,
        )
    context = CardContext(
        art_box=(max(0, left), max(0, top), max(0, min(frame.shape[1], right) - max(0, left)), max(0, art_bottom - max(0, top))),
        card_box=(max(0, left), max(0, top), max(0, min(frame.shape[1], right) - max(0, left)), max(0, bottom - max(0, top))),
        nameplate_dark_fraction=0.90,
        art_standard_deviation=62.0,
        art_edge_density=0.08,
        frame_line_ratio=0.95,
        accepted=True,
        reason="accepted_exact",
    )
    return CardCandidate(
        canonical_name=name,
        raw_text=name,
        ocr_confidence=0.99,
        name_box=(x, y, name_width, name_height),
        context=context,
        accepted=True,
        reason="accepted_exact",
        family="Gold",
        family_confidence=0.96,
        rarity=droid_class(name),
        rarity_confidence=1.0,
        raw_best_similarity=0.94,
        runner_up_identity="R4" if name != "R4" else "R5",
        identity_margin=0.08,
    )


def track_event(kind: str, track_id: int, name: str = "MOUSE"):
    return SimpleNamespace(kind=kind, track=SimpleNamespace(id=track_id, name=name))


def collect_appearance(
    collector: BeltTemplateSampleCollector,
    *,
    track_id: int,
    pattern: int,
    name: str = "MOUSE",
) -> list:
    for read in range(3):
        frame = np.full((280, 900, 3), 25, dtype=np.uint8)
        candidate = card_candidate(frame, name=name, x=350 + read * 20, pattern=pattern)
        collector.observe(
            frame,
            (candidate,),
            {0: track_id},
            now=float(track_id * 10 + read),
            frame_number=track_id * 10 + read,
        )
    collector.process_events((track_event("entered", track_id, name),))
    return collector.process_events((track_event("exited", track_id, name),))


class BeltTemplateSampleCollectorTests(unittest.TestCase):
    def test_saves_only_the_best_complete_crop_for_a_confirmed_appearance(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = BeltTemplateSampleCollector(directory)

            for read, x in enumerate((120, 390, 650), start=1):
                frame = np.full((280, 900, 3), 25, dtype=np.uint8)
                candidate = card_candidate(frame, x=x, pattern=2)
                collector.observe(
                    frame,
                    (candidate,),
                    {0: 7},
                    now=float(read),
                    frame_number=read,
                )
            collector.process_events((track_event("entered", 7),))
            updates = collector.process_events((track_event("exited", 7),))

            self.assertEqual(["saved"], [update.action for update in updates])
            images = list((Path(directory) / "detections" / "mouse").glob("*.png"))
            metadata_files = list((Path(directory) / "detections" / "mouse").glob("*.json"))
            self.assertEqual(1, len(images))
            self.assertEqual(1, len(metadata_files))
            metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
            self.assertEqual(2, metadata["frame_number"])
            self.assertEqual(3, metadata["strong_label_reads"])
            self.assertEqual({"MOUSE": 3}, metadata["observed_names"])
            self.assertEqual("Gold", metadata["family"])
            self.assertEqual("Common", metadata["rarity"])
            self.assertEqual(0.94, metadata["raw_best_similarity"])
            self.assertEqual("R4", metadata["runner_up_identity"])
            self.assertEqual(0.08, metadata["identity_margin"])
            self.assertEqual(1, collector.total_samples)

    def test_cap_and_duplicate_index_persist_across_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = BeltTemplateSampleCollector(
                directory,
                max_samples_per_droid=2,
                duplicate_hash_distance=6,
            )
            collect_appearance(collector, track_id=1, pattern=0)
            collect_appearance(collector, track_id=2, pattern=1)
            collect_appearance(collector, track_id=3, pattern=3)

            folder = Path(directory) / "detections" / "mouse"
            self.assertEqual(2, len(list(folder.glob("*.png"))))
            self.assertEqual(2, collector.total_samples)

            reloaded = BeltTemplateSampleCollector(
                directory,
                max_samples_per_droid=2,
                duplicate_hash_distance=6,
            )
            updates = collect_appearance(reloaded, track_id=4, pattern=3)
            self.assertIn(updates[0].action, {"duplicate", "replaced", "capped"})
            self.assertEqual(2, reloaded.total_samples)
            self.assertEqual(2, len(list(folder.glob("*.png"))))
            self.assertEqual(2, len(list(folder.glob("*.json"))))

    def test_conflicting_track_names_never_enter_confirmed_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = BeltTemplateSampleCollector(directory)
            names = ("MOUSE", "R4", "MOUSE", "MOUSE")
            for read, name in enumerate(names):
                frame = np.full((280, 900, 3), 25, dtype=np.uint8)
                candidate = card_candidate(frame, name=name, pattern=2)
                collector.observe(
                    frame,
                    (candidate,),
                    {0: 9},
                    now=float(read),
                    frame_number=read,
                )
            collector.process_events((track_event("entered", 9, "MOUSE"),))
            updates = collector.process_events((track_event("exited", 9, "MOUSE"),))

            self.assertEqual([], list((Path(directory) / "detections").glob("*/*.png")))
            self.assertEqual(["reviewed"], [update.action for update in updates])
            review_metadata = next((Path(directory) / "review").glob("*.json"))
            metadata = json.loads(review_metadata.read_text(encoding="utf-8"))
            self.assertEqual("conflicting_track_names", metadata["review_reason"])
            self.assertEqual({"MOUSE": 3, "R4": 1}, metadata["observed_names"])
            self.assertEqual("MOUSE", metadata["confirmed_name"])
            self.assertEqual("MOUSE", metadata["name"])
            self.assertEqual("MOUSE", metadata["detected_name"])

    def test_cards_touching_any_frame_edge_are_not_saved(self):
        edge_positions = {
            "left": (10, 190),
            "right": (890, 190),
            "top": (390, 100),
            "bottom": (390, 250),
        }
        for edge, (x, y) in edge_positions.items():
            with self.subTest(edge=edge), tempfile.TemporaryDirectory() as directory:
                collector = BeltTemplateSampleCollector(directory)
                for read in range(3):
                    frame = np.full((280, 900, 3), 25, dtype=np.uint8)
                    candidate = card_candidate(frame, x=x, pattern=2)
                    if y != 190:
                        name_x, _name_y, name_width, name_height = candidate.name_box
                        context = candidate.context
                        top = round(y - 5.0 * name_height)
                        bottom = round(y + 1.6 * name_height)
                        candidate = replace(
                            candidate,
                            name_box=(name_x, y, name_width, name_height),
                            context=replace(
                                context,
                                card_box=(
                                    context.card_box[0],
                                    max(0, top),
                                    context.card_box[2],
                                    bottom - max(0, top),
                                ),
                            ),
                        )
                    collector.observe(
                        frame,
                        (candidate,),
                        {0: 3},
                        now=float(read),
                        frame_number=read,
                    )
                collector.process_events((track_event("entered", 3),))
                updates = collector.process_events((track_event("exited", 3),))

                self.assertEqual([], updates)
                self.assertEqual(
                    [],
                    list((Path(directory) / "detections").glob("*/*.png")),
                )


if __name__ == "__main__":
    unittest.main()
