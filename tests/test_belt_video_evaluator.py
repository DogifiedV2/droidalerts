from __future__ import annotations

import sys
import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from tools.evaluate_belt_videos import compare_run


def expected(time, name, family=""):
    return {
        "id": round(time * 10),
        "time": time,
        "name": name,
        "phase": "arrival",
        "family": family,
    }


def event(time, name, *, kind="entered", track_id=1, family=""):
    return {
        "event_at": time,
        "kind": kind,
        "track_id": track_id,
        "name": name,
        "family": family,
        "rarity": "",
    }


class BeltVideoEvaluatorTests(unittest.TestCase):
    def test_late_camera_repeat_does_not_consume_a_real_future_occurrence(self):
        ground_truth = [
            expected(36.0, "R5"),
            expected(64.0, "R5"),
        ]
        events = [
            event(38.0, "R5", track_id=1),
            event(65.0, "R5", track_id=2),
            event(85.0, "R5", track_id=3),
        ]

        comparison = compare_run(ground_truth, events)

        self.assertEqual(2, comparison["matched_physical_blueprints"])
        self.assertEqual(0, comparison["missed_physical_blueprints"])
        self.assertEqual(1, comparison["unexpected_or_duplicate_entries"])
        self.assertEqual(85.0, comparison["unexpected_entries"][0]["event_at"])

    def test_family_update_is_used_without_counting_another_entry(self):
        ground_truth = [expected(48.0, "MOUSE", "Diamond")]
        events = [
            event(50.5, "MOUSE", track_id=7),
            event(
                51.5,
                "MOUSE",
                kind="updated",
                track_id=7,
                family="Diamond",
            ),
        ]

        comparison = compare_run(ground_truth, events)

        self.assertEqual(1, comparison["entered_alert_events"])
        self.assertEqual(1.0, comparison["family_coverage"])
        self.assertEqual(1.0, comparison["family_accuracy_when_classified"])
        self.assertEqual([], comparison["family_errors"])

    def test_reacquisition_is_reported_but_never_counted_as_an_entry(self):
        ground_truth = [expected(10.0, "LO")]
        events = [
            event(12.0, "LO", track_id=4),
            event(30.0, "LO", kind="reacquired", track_id=4),
        ]

        comparison = compare_run(ground_truth, events)

        self.assertEqual(1, comparison["matched_physical_blueprints"])
        self.assertEqual(1, comparison["entered_alert_events"])
        self.assertEqual(1, comparison["camera_jump_reacquisitions_suppressed"])

    def test_simultaneous_same_identity_prefers_the_matching_family(self):
        ground_truth = [
            expected(0.0, "R8", "Default"),
            expected(0.0, "R8", "Gold"),
        ]
        events = [
            event(1.5, "R8", track_id=1, family="Default"),
        ]

        comparison = compare_run(ground_truth, events)

        self.assertEqual("Gold", comparison["missing"][0]["family"])
        self.assertEqual([], comparison["family_errors"])


if __name__ == "__main__":
    unittest.main()
