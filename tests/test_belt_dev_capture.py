from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))
sys.path.insert(0, str(BASE_DIR / "tests"))

from resolution_matrix import RESOLUTION_CASES

from droid_alerts.belt.dev_capture import (
    BeltDevCaptureRecorder,
    export_dev_session,
)
from droid_alerts.belt.dev_review import record_track_review
from droid_alerts.belt.models import CardCandidate, CardContext
from droid_alerts.belt.region import DEFAULT_REGION


def card_candidate(
    frame: np.ndarray,
    *,
    x: int,
    y: int = 30,
    width: int = 120,
    height: int = 180,
    name: str = "R2",
    family: str = "Gold",
    accepted: bool = True,
    reason: str = "accepted_template",
) -> CardCandidate:
    right = min(frame.shape[1], x + width)
    bottom = min(frame.shape[0], y + height)
    if right > max(0, x) and bottom > max(0, y):
        crop = frame[max(0, y) : bottom, max(0, x) : right]
        yy, xx = np.indices(crop.shape[:2])
        crop[(xx // 8 + yy // 11) % 2 == 0] = (230, 70, 30)
        crop[(xx // 8 + yy // 11) % 2 == 1] = (25, 180, 220)
    art_box = (
        x + round(width * 0.15),
        y + round(height * 0.08),
        round(width * 0.70),
        round(height * 0.56),
    )
    context = CardContext(
        art_box=art_box,
        card_box=(x, y, width, height),
        nameplate_dark_fraction=0.86,
        art_standard_deviation=55.0,
        art_edge_density=0.13,
        frame_line_ratio=0.50,
        accepted=accepted,
        reason=reason,
    )
    return CardCandidate(
        canonical_name=name,
        raw_text=f"template:{name}",
        identity_confidence=0.96,
        name_box=(x + 20, y + 120, 70, 18),
        context=context,
        accepted=accepted,
        reason=reason,
        family=family if accepted else "",
        family_confidence=0.92 if accepted else 0.0,
        rarity="Epic",
        rarity_confidence=1.0,
        raw_best_similarity=0.91 if accepted else 0.78,
        runner_up_identity="R4",
        identity_margin=0.09 if accepted else 0.02,
    )


def track_event(track_id: int = 7):
    return SimpleNamespace(
        kind="entered",
        track=SimpleNamespace(
            id=track_id,
            name="R2",
            family="Gold",
            rarity="Epic",
            confidence=0.96,
        ),
    )


class BeltDevCaptureTests(unittest.TestCase):
    def test_groups_real_scans_into_one_review_only_physical_track(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session_test"
            recorder = BeltDevCaptureRecorder(
                session,
                track_timeout_seconds=2.0,
                maximum_saved_crops=3,
            )
            for frame_number, (now, x) in enumerate(
                ((0.0, 40), (1.0, 80), (2.0, 120)),
                start=1,
            ):
                frame = np.full((260, 640, 3), 20, dtype=np.uint8)
                candidate = card_candidate(frame, x=x)
                recorder.observe(
                    frame,
                    (candidate,),
                    {0: 7},
                    now=now,
                    frame_number=frame_number,
                )
            recorder.record_tracker_event(track_event(), alerted=True)
            recorder.observe(
                np.zeros((260, 640, 3), dtype=np.uint8),
                (),
                {},
                now=5.1,
                frame_number=4,
            )
            status = recorder.close()

            self.assertEqual(1, status["written_tracks"])
            manifest_path = next(
                (session / "tracks").glob("track_*/manifest.json")
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("unreviewed", manifest["label_status"])
            self.assertEqual(
                "never_auto_promote",
                manifest["training_status"],
            )
            self.assertEqual(3, manifest["summary"]["observation_count"])
            self.assertEqual(3, manifest["summary"]["accepted_count"])
            self.assertEqual({"R2": 3}, manifest["summary"]["predicted_names"])
            self.assertEqual([7], manifest["production_track_ids"])
            self.assertTrue(manifest["tracker_events"][0]["alerted"])
            self.assertGreaterEqual(len(manifest["frames"]), 2)

    def test_rejected_identity_candidates_are_saved_for_review(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session_rejected"
            recorder = BeltDevCaptureRecorder(session)
            for frame_number, x in enumerate((70, 100), start=1):
                frame = np.full((260, 640, 3), 20, dtype=np.uint8)
                recorder.observe(
                    frame,
                    (
                        card_candidate(
                            frame,
                            x=x,
                            accepted=False,
                            reason="ambiguous_template_identity",
                        ),
                    ),
                    {},
                    now=float(frame_number),
                    frame_number=frame_number,
                )
            recorder.close()

            manifest_path = next(
                (session / "tracks").glob("track_*/manifest.json")
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(0, manifest["summary"]["accepted_count"])
            self.assertEqual(
                {"ambiguous_template_identity": 2},
                manifest["summary"]["rejection_reasons"],
            )
            self.assertTrue(manifest["frames"])

    def test_static_rejected_hud_hypothesis_does_not_fill_review_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session_static"
            recorder = BeltDevCaptureRecorder(session)
            for frame_number in range(1, 7):
                frame = np.full((260, 640, 3), 20, dtype=np.uint8)
                recorder.observe(
                    frame,
                    (
                        card_candidate(
                            frame,
                            x=70,
                            accepted=False,
                            reason="low_template_similarity",
                        ),
                    ),
                    {},
                    now=float(frame_number),
                    frame_number=frame_number,
                )
            status = recorder.close()

            self.assertEqual(0, status["written_tracks"])
            self.assertEqual(1, status["filtered_tracks"])
            self.assertEqual(
                [],
                list((session / "tracks").glob("track_*/manifest.json")),
            )

    def test_slow_cpu_timeout_keeps_two_real_scans_in_one_track(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session_slow"
            recorder = BeltDevCaptureRecorder(
                session,
                track_timeout_seconds=5.0,
            )
            first = np.full((260, 640, 3), 20, dtype=np.uint8)
            recorder.observe(
                first,
                (card_candidate(first, x=60),),
                {0: 2},
                now=0.0,
                frame_number=1,
                track_timeout_seconds=10.0,
            )
            second = np.full((260, 640, 3), 20, dtype=np.uint8)
            recorder.observe(
                second,
                (card_candidate(second, x=160),),
                {0: 2},
                now=8.0,
                frame_number=2,
                track_timeout_seconds=10.0,
            )
            recorder.close()

            manifests = list(
                (session / "tracks").glob("track_*/manifest.json")
            )
            self.assertEqual(1, len(manifests))
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(2, manifest["summary"]["observation_count"])

    def test_changed_production_id_is_preserved_as_duplicate_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session_duplicate"
            recorder = BeltDevCaptureRecorder(session)
            first = np.full((260, 640, 3), 20, dtype=np.uint8)
            recorder.observe(
                first,
                (card_candidate(first, x=60),),
                {0: 10},
                now=0.0,
                frame_number=1,
            )
            second = np.full((260, 640, 3), 20, dtype=np.uint8)
            recorder.observe(
                second,
                (card_candidate(second, x=90),),
                {0: 11},
                now=1.0,
                frame_number=2,
            )
            recorder.close()

            manifest_path = next(
                (session / "tracks").glob("track_*/manifest.json")
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual([10, 11], manifest["production_track_ids"])

    def test_different_art_does_not_merge_at_the_same_belt_position(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session_art_split"
            recorder = BeltDevCaptureRecorder(session)
            for frame_number, x in ((1, 60), (2, 100)):
                frame = np.full((260, 640, 3), 20, dtype=np.uint8)
                candidate = card_candidate(
                    frame,
                    x=x,
                    accepted=False,
                    reason="ambiguous_template_identity",
                )
                recorder.observe(
                    frame,
                    (candidate,),
                    {},
                    now=float(frame_number),
                    frame_number=frame_number,
                )
            for frame_number, x in ((3, 65), (4, 105)):
                frame = np.full((260, 640, 3), 20, dtype=np.uint8)
                candidate = card_candidate(
                    frame,
                    x=x,
                    name="ARG",
                    accepted=False,
                    reason="ambiguous_template_identity",
                )
                art_x, art_y, art_width, art_height = candidate.context.art_box
                art = frame[
                    art_y : art_y + art_height,
                    art_x : art_x + art_width,
                ]
                art[:] = (15, 15, 15)
                cv2.circle(
                    art,
                    (art.shape[1] // 2, art.shape[0] // 2),
                    max(4, min(art.shape[:2]) // 3),
                    (250, 250, 250),
                    -1,
                )
                recorder.observe(
                    frame,
                    (candidate,),
                    {},
                    now=float(frame_number),
                    frame_number=frame_number,
                )
            recorder.close()

            manifests = list(
                (session / "tracks").glob("track_*/manifest.json")
            )
            self.assertEqual(2, len(manifests))

    def test_dev_capture_crops_at_every_shared_chat_alert_resolution(self):
        for case in RESOLUTION_CASES:
            with self.subTest(resolution=case.name), tempfile.TemporaryDirectory() as directory:
                belt_width = round(case.width * DEFAULT_REGION.width)
                belt_height = round(case.height * DEFAULT_REGION.height)
                frame = np.full(
                    (belt_height, belt_width, 3),
                    22,
                    dtype=np.uint8,
                )
                card_height = max(80, round(belt_height * 0.58))
                card_width = max(55, round(card_height * 0.68))
                x = round(belt_width * 0.18)
                y = max(2, round((belt_height - card_height) * 0.5))
                recorder = BeltDevCaptureRecorder(
                    Path(directory) / f"session_{case.name}",
                    maximum_saved_crops=1,
                )
                recorder.observe(
                    frame,
                    (
                        card_candidate(
                            frame,
                            x=x,
                            y=y,
                            width=card_width,
                            height=card_height,
                        ),
                    ),
                    {0: 1},
                    now=0.0,
                    frame_number=1,
                )
                recorder.close()

                manifest_path = next(
                    (Path(directory) / f"session_{case.name}" / "tracks").glob(
                        "track_*/manifest.json"
                    )
                )
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                self.assertEqual(
                    [belt_height, belt_width, 3],
                    manifest["frames"][0]["source_frame_shape"],
                )
                image = cv2.imread(
                    str(
                        manifest_path.parent
                        / manifest["frames"][0]["image"]
                    )
                )
                self.assertIsNotNone(image)
                self.assertGreaterEqual(image.shape[0], 8)
                self.assertGreaterEqual(image.shape[1], 8)

    def test_human_review_creates_identity_only_training_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session_review"
            recorder = BeltDevCaptureRecorder(session)
            frame = np.full((260, 640, 3), 20, dtype=np.uint8)
            recorder.observe(
                frame,
                (card_candidate(frame, x=80),),
                {0: 1},
                now=0.0,
                frame_number=1,
            )
            recorder.close()
            manifest_path = next(
                (session / "tracks").glob("track_*/manifest.json")
            )

            confirmed_path = record_track_review(
                manifest_path,
                decision="confirmed",
                name="R2",
                family="Gold",
            )

            self.assertIsNotNone(confirmed_path)
            confirmed = json.loads(
                confirmed_path.read_text(encoding="utf-8")
            )
            self.assertTrue(confirmed["use_for_identity"])
            self.assertFalse(confirmed["use_for_family"])
            self.assertEqual("human_review", confirmed["label_source"])
            reviewed = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            self.assertEqual("reviewed", reviewed["label_status"])
            self.assertEqual("confirmed", reviewed["review"]["decision"])

    def test_mixed_track_review_cannot_enter_training_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session_mixed"
            recorder = BeltDevCaptureRecorder(session)
            frame = np.full((260, 640, 3), 20, dtype=np.uint8)
            recorder.observe(
                frame,
                (card_candidate(frame, x=80),),
                {0: 1},
                now=0.0,
                frame_number=1,
            )
            recorder.close()
            manifest_path = next(
                (session / "tracks").glob("track_*/manifest.json")
            )

            output = record_track_review(
                manifest_path,
                decision="mixed_track",
            )

            self.assertIsNone(output)
            self.assertEqual(
                [],
                list((session / "confirmed").glob("**/*.json")),
            )

    def test_complete_session_exports_to_shareable_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session_export"
            session.mkdir()
            (session / "capture_manifest.json").write_text(
                '{"version": 1}\n',
                encoding="utf-8",
            )
            track = session / "tracks" / "track_000001"
            track.mkdir(parents=True)
            (track / "manifest.json").write_text(
                '{"label_status": "unreviewed"}\n',
                encoding="utf-8",
            )

            output = export_dev_session(
                session,
                Path(directory) / "exports" / "belt.zip",
            )

            with zipfile.ZipFile(output) as archive:
                self.assertIn("README.txt", archive.namelist())
                self.assertIn(
                    "session/capture_manifest.json",
                    archive.namelist(),
                )
                self.assertIn(
                    "session/tracks/track_000001/manifest.json",
                    archive.namelist(),
                )

    def test_disabled_capture_creates_no_files(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "disabled"
            recorder = BeltDevCaptureRecorder(session, enabled=False)
            frame = np.zeros((200, 400, 3), dtype=np.uint8)
            recorder.observe(
                frame,
                (card_candidate(frame, x=20, height=140),),
                {},
                now=0.0,
                frame_number=1,
            )
            recorder.close()

            self.assertFalse(session.exists())


if __name__ == "__main__":
    unittest.main()
