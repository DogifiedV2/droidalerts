from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

import unittest

from droid_alerts.belt.matching import NameMatch
from droid_alerts.belt.ocr import DroidObservation
from droid_alerts.belt.tracking import BeltTracker


def observation(
    name,
    x,
    *,
    y=80,
    width=60,
    height=18,
    confidence=0.9,
    score=1.0,
    family="",
    family_confidence=0.9,
    rarity="",
    rarity_confidence=0.9,
    raw_text=None,
):
    return DroidObservation(
        NameMatch(name, score, raw_text or name),
        confidence,
        (x, y, width, height),
        family=family,
        family_confidence=family_confidence,
        rarity=rarity,
        rarity_confidence=rarity_confidence,
    )


class TrackingTests(unittest.TestCase):
    def test_update_exposes_each_accepted_observations_physical_track_id(self):
        tracker = BeltTracker()

        first = tracker.update(
            [observation("MOUSE", 30), observation("R4", 180)],
            0.0,
            500,
        )
        second = tracker.update(
            [observation("MOUSE", 55), observation("R4", 205)],
            0.2,
            500,
        )

        self.assertEqual({0: 1, 1: 2}, first.observation_track_ids)
        self.assertEqual(first.observation_track_ids, second.observation_track_ids)

    def test_card_attributes_confirm_with_name_without_delaying_entry(self):
        tracker = BeltTracker()
        families = ("Beskar", "", "Beskar", "Beskar")
        rarities = ("Rare", "", "Rare", "Rare")
        for index, (family, rarity) in enumerate(zip(families, rarities)):
            confirmed = tracker.update(
                [
                    observation(
                        "BAL-CORE",
                        20 + index * 20,
                        family=family,
                        rarity=rarity,
                    )
                ],
                index * 0.2,
                400,
            )

        self.assertEqual(["entered"], [event.kind for event in confirmed.events])
        self.assertEqual("Beskar", confirmed.events[0].track.family)
        self.assertEqual("Rare", confirmed.events[0].track.rarity)
        self.assertAlmostEqual(0.9, confirmed.events[0].track.family_confidence)
        self.assertAlmostEqual(0.9, confirmed.events[0].track.rarity_confidence)

        missing = BeltTracker()
        for index in range(4):
            result = missing.update([observation("R2", 20)], index * 0.2, 400)
        self.assertEqual(["entered"], [event.kind for event in result.events])
        self.assertEqual("", result.events[0].track.family)
        self.assertEqual("", result.events[0].track.rarity)

    def test_confirmed_attributes_are_not_replaced_by_later_conflicting_reads(self):
        tracker = BeltTracker()
        for index in range(4):
            tracker.update(
                [
                    observation(
                        "R8",
                        20 + index * 20,
                        family="Diamond",
                        rarity="Common",
                    )
                ],
                index * 0.2,
                400,
            )

        result = tracker.update(
            [observation("R8", 100, family="Rainbow", rarity="Mythic")],
            0.8,
            400,
        )

        self.assertEqual("Diamond", result.tracks[0].family)
        self.assertEqual("Common", result.tracks[0].rarity)

    def test_single_attribute_read_is_not_published_and_later_majority_can_win(self):
        tracker = BeltTracker()
        reads = (
            ("Gold", "Mythic"),
            ("", ""),
            ("", ""),
            ("", ""),
        )
        for index, (family, rarity) in enumerate(reads):
            result = tracker.update(
                [
                    observation(
                        "R2",
                        20 + index * 20,
                        family=family,
                        rarity=rarity,
                    )
                ],
                index * 0.2,
                400,
            )

        self.assertEqual(["entered"], [event.kind for event in result.events])
        self.assertEqual("", result.events[0].track.family)
        self.assertEqual("", result.events[0].track.rarity)

        tracker.update(
            [observation("R2", 100, family="Default", rarity="Common")],
            0.8,
            400,
        )
        corrected = tracker.update(
            [observation("R2", 120, family="Default", rarity="Common")],
            1.0,
            400,
        )

        self.assertEqual("Default", corrected.tracks[0].family)
        self.assertEqual("Common", corrected.tracks[0].rarity)

    def test_default_emits_immediately_on_fourth_vote_without_motion_or_gate(self):
        tracker = BeltTracker()
        updates = [
            tracker.update([observation("GONK", 20)], index * 0.2, 400)
            for index in range(3)
        ]
        self.assertTrue(all(not update.tracks and not update.events for update in updates))

        confirmed = tracker.update([observation("GONK", 20)], 0.6, 400)
        self.assertEqual(["GONK"], [track.name for track in confirmed.tracks])
        self.assertEqual(["entered"], [event.kind for event in confirmed.events])
        self.assertEqual("GONK", confirmed.events[0].track.name)
        self.assertEqual([], tracker.update([observation("GONK", 20)], 0.8, 400).events)

    def test_geometry_association_does_not_split_alternating_labels(self):
        tracker = BeltTracker()
        events = []
        alternating = ("MO-TRAK", "OPTI-STRK") * 3
        for index, (name, x) in enumerate(zip(alternating, (20, 50, 80, 110, 140, 170))):
            update = tracker.update([observation(name, x)], index * 0.15, 600)
            events.extend(update.events)

        self.assertEqual([], events)
        self.assertEqual(1, len(tracker._tracks))
        self.assertFalse(tracker._tracks[0].confirmed)

        # Four matching rolling votes eventually settle the same physical
        # track and emit exactly one event immediately on confirmation.
        for offset, x in enumerate((200, 230, 260, 290), start=6):
            update = tracker.update([observation("MO-TRAK", x)], offset * 0.15, 600)
            events.extend(update.events)
        self.assertEqual(["entered"], [event.kind for event in events])
        self.assertEqual(1, len(tracker._tracks))
        self.assertEqual(1, tracker._tracks[0].id)
        self.assertEqual("MO-TRAK", tracker._tracks[0].name)

        conflict = tracker.update([observation("OPTI-STRK", 320)], 1.5, 600)
        self.assertEqual([], conflict.events)
        self.assertEqual(["MO-TRAK"], [track.name for track in conflict.tracks])
        self.assertEqual(1, len(tracker._tracks))

    def test_duplicate_conflicting_observations_are_one_vote_and_one_track(self):
        tracker = BeltTracker()
        for index, x in enumerate((20, 55, 90, 125, 160, 195)):
            first_name, second_name = (
                ("MO-TRAK", "OPTI-STRK")
                if index % 2 == 0
                else ("OPTI-STRK", "MO-TRAK")
            )
            update = tracker.update(
                [
                    observation(first_name, x, confidence=0.91),
                    observation(second_name, x + 1, confidence=0.90),
                ],
                index * 0.2,
                500,
            )
            self.assertEqual([], update.events)
        self.assertEqual(1, len(tracker._tracks))
        self.assertFalse(tracker._tracks[0].confirmed)
        self.assertEqual(5, len(tracker._tracks[0].identity_votes))

    def test_stationary_observations_confirm_without_a_motion_requirement(self):
        tracker = BeltTracker()
        events = []
        for index, x in enumerate((168, 171, 169, 172, 167)):
            update = tracker.update([observation("R6", x)], index * 0.15, 400)
            events.extend(update.events)
        self.assertEqual(["entered"], [event.kind for event in events])
        self.assertEqual(1, len(tracker._tracks))
        self.assertTrue(tracker._tracks[0].confirmed)

    def test_multiple_stationary_cards_all_emit_on_the_confirmation_pass(self):
        tracker = BeltTracker()
        cards = [
            observation("NAV-EX", 20),
            observation("RIC-1200", 220),
            observation("R5", 420),
        ]

        for index in range(3):
            update = tracker.update(cards, index * 0.2, 600)
            self.assertEqual([], update.events)

        confirmed = tracker.update(cards, 0.6, 600)
        self.assertEqual(
            ["NAV-EX", "RIC-1200", "R5"],
            [event.track.name for event in confirmed.events],
        )
        self.assertEqual([], tracker.update(cards, 0.8, 600).events)

    def test_confirmed_identity_is_immutable_when_later_labels_conflict(self):
        tracker = BeltTracker()
        for index, x in enumerate((20, 60, 100, 140)):
            confirmed = tracker.update([observation("GONK", x)], index * 0.2, 400)
        self.assertEqual(["GONK"], [track.name for track in confirmed.tracks])
        self.assertEqual(["entered"], [event.kind for event in confirmed.events])

        bad_label = tracker.update([observation("R6", 180)], 0.8, 400)
        self.assertEqual([], bad_label.events)
        self.assertEqual(["GONK"], [track.name for track in bad_label.tracks])
        self.assertEqual(1, len(tracker._tracks))

        correct_label = tracker.update([observation("GONK", 180)], 1.0, 400)
        self.assertEqual([], correct_label.events)
        self.assertEqual("GONK", correct_label.tracks[0].name)

    def test_repeated_template_identity_can_replace_a_stale_same_slot_card(self):
        tracker = BeltTracker()
        for index in range(4):
            tracker.update(
                [observation("GONK", 100, raw_text="template:GONK")],
                index / 8,
                400,
            )

        entered = []
        for index in range(8):
            update = tracker.update(
                [observation("R6", 100, raw_text="template:R6")],
                0.5 + index / 8,
                400,
            )
            entered.extend(
                event.track.name for event in update.events if event.kind == "entered"
            )

        self.assertEqual(["R6"], entered)
        self.assertIn("GONK", [track.name for track in tracker._tracks])
        self.assertIn("R6", [track.name for track in tracker._tracks])

    def test_conflicting_names_cannot_keep_an_entered_identity_alive(self):
        tracker = BeltTracker(
            confirmation_hits=2,
            confirmation_window=3,
            timeout_seconds=0.4,
        )
        tracker.update([observation("GONK", 20)], 0.0, 400)
        entered = tracker.update([observation("GONK", 100)], 0.1, 400)
        self.assertEqual(["entered"], [event.kind for event in entered.events])

        tracker.update([observation("R6", 220)], 0.25, 400)
        tracker.update([observation("R6", 260)], 0.4, 400)
        expired = tracker.update([observation("R6", 300)], 0.55, 400)

        self.assertEqual(["exited"], [event.kind for event in expired.events])
        self.assertEqual("GONK", expired.events[0].track.name)

    def test_adjacent_different_card_is_not_quarantined(self):
        tracker = BeltTracker()
        for index, x in enumerate((20, 60, 100, 140)):
            tracker.update([observation("GONK", x)], index * 0.2, 600)

        tracker.update(
            [observation("GONK", 180), observation("R6", 380)],
            0.8,
            600,
        )

        self.assertEqual(2, len(tracker._tracks))
        self.assertEqual("GONK", tracker._tracks[0].name)
        self.assertEqual(["R6"], list(tracker._tracks[1].identity_votes))

    def test_direction_does_not_delay_confirmation(self):
        tracker = BeltTracker()
        events = []
        for index, x in enumerate((250, 210, 170, 130)):
            update = tracker.update([observation("GONK", x)], index * 0.15, 400)
            events.extend(update.events)

        self.assertEqual(["entered"], [event.kind for event in events])
        self.assertTrue(tracker._tracks[0].confirmed)

    def test_two_nearby_cards_are_associated_by_predicted_geometry_not_name(self):
        tracker = BeltTracker(
            confirmation_hits=2,
            confirmation_window=3,
        )
        tracker.update([observation("A", 20), observation("B", 240)], 0.0, 600)
        tracker.update([observation("B", 60), observation("A", 280)], 0.2, 600)
        result = tracker.update([observation("A", 100), observation("B", 320)], 0.4, 600)

        self.assertEqual(2, len(result.tracks))
        self.assertEqual([1, 2], [track.id for track in result.tracks])
        self.assertEqual(["A", "B"], [track.name for track in result.tracks])
        self.assertEqual(["entered", "entered"], [event.kind for event in result.events])

    def test_same_named_cards_remain_separate_physical_tracks(self):
        tracker = BeltTracker(
            confirmation_hits=2,
            confirmation_window=3,
        )
        tracker.update([observation("R6", 20), observation("R6", 260)], 0.0, 600)
        confirmed = tracker.update([observation("R6", 50), observation("R6", 290)], 0.2, 600)
        result = tracker.update([observation("R6", 80), observation("R6", 320)], 0.4, 600)

        self.assertEqual(2, len(result.tracks))
        self.assertNotEqual(result.tracks[0].id, result.tracks[1].id)
        self.assertEqual(["entered", "entered"], [event.kind for event in confirmed.events])
        self.assertEqual([], result.events)

    def test_entered_track_predicts_then_emits_one_exit_on_timeout(self):
        tracker = BeltTracker(
            confirmation_hits=2,
            confirmation_window=3,
            timeout_seconds=0.5,
        )
        tracker.update([observation("GONK", 20)], 0.0, 400)
        entered = tracker.update([observation("GONK", 100)], 0.1, 400)
        self.assertEqual(["entered"], [event.kind for event in entered.events])
        tracker.update([observation("GONK", 180)], 0.2, 400)

        predicted = tracker.predict(0.4, 400)
        self.assertGreater(predicted.tracks[0].box[0], 180)
        expired = tracker.predict(0.71, 400)
        self.assertEqual(["exited"], [event.kind for event in expired.events])
        self.assertEqual([], tracker.predict(0.9, 400).events)

    def test_slow_ocr_cadence_extends_prediction_through_next_result(self):
        tracker = BeltTracker(confirmation_hits=2, confirmation_window=3)
        tracker.update([observation("GONK", 20)], 0.0, 1_000)
        tracker.update([observation("GONK", 220)], 1.0, 1_000)

        predicted = tracker.predict(1.9, 1_000)

        # A fixed 0.75s horizon stopped at x=325 with the smoothed velocity.
        # The adaptive horizon continues following the moving card.
        self.assertGreater(predicted.tracks[0].box[0], 325)

    def test_point_three_fps_keeps_order_and_confirms_moving_cards(self):
        tracker = BeltTracker(timeout_seconds=8.0)
        scans = (
            (
                observation("A", 100, width=180, height=70),
                observation("B", 500, width=180, height=70),
                observation("C", 900, width=180, height=70),
            ),
            (
                observation("NEW-1", 100, width=180, height=70),
                observation("A", 500, width=180, height=70),
                observation("B", 900, width=180, height=70),
                observation("C", 1_300, width=180, height=70),
            ),
            (
                observation("NEW-2", 100, width=180, height=70),
                observation("NEW-1", 500, width=180, height=70),
                observation("A", 900, width=180, height=70),
                observation("B", 1_300, width=180, height=70),
                observation("C", 1_700, width=180, height=70),
            ),
        )

        events = []
        for index, scan in enumerate(scans):
            events.extend(tracker.update(scan, index * (10.0 / 3.0), 2_000).events)

        self.assertEqual(["A", "B", "C"], [event.track.name for event in events])
        self.assertTrue(all(event.track.confirmation_mode == "slow-cadence" for event in events))

    def test_slow_cadence_confirmation_requires_three_strong_consecutive_reads(self):
        weak = BeltTracker(timeout_seconds=8.0)
        for index in range(3):
            update = weak.update(
                [observation("GONK", 100 + index * 300, confidence=0.75)],
                index * 2.0,
                1_200,
            )
        self.assertEqual([], update.events)

        standard = weak.update(
            [observation("GONK", 1_000, confidence=0.75)],
            6.0,
            1_200,
        )
        self.assertEqual(["entered"], [event.kind for event in standard.events])
        self.assertEqual("standard", standard.events[0].track.confirmation_mode)

        noisy = BeltTracker(timeout_seconds=8.0)
        names = ("R6", "GONK", "R6")
        for index, name in enumerate(names):
            update = noisy.update(
                [observation(name, 100 + index * 300)],
                index * 2.0,
                1_200,
            )
        self.assertEqual([], update.events)

    def test_normal_cadence_still_requires_four_of_five_reads(self):
        tracker = BeltTracker()
        for index in range(3):
            update = tracker.update([observation("R8", 100 + index * 20)], index * 0.3, 800)
        self.assertEqual([], update.events)

        confirmed = tracker.update([observation("R8", 160)], 0.9, 800)
        self.assertEqual(["entered"], [event.kind for event in confirmed.events])
        self.assertEqual("standard", confirmed.events[0].track.confirmation_mode)

    def test_outside_prediction_cannot_discard_video_style_votes(self):
        tracker = BeltTracker(timeout_seconds=2.0)
        for index, x in enumerate((100, 120, 140)):
            update = tracker.update(
                [observation("R5", x, width=160, height=80)],
                index * 0.1,
                600,
            )
            self.assertEqual([], update.events)

        # Reproduce the moving-belt failure: a noisy velocity extrapolates the
        # track outside even though its last real observation is centered.
        tracker._tracks[0].velocity_x = 5_000.0
        predicted = tracker.predict(0.3, 600)
        self.assertEqual([], predicted.events)
        self.assertEqual(1, len(tracker._tracks))
        self.assertEqual(["R5", "R5", "R5"], list(tracker._tracks[0].identity_votes))

        confirmed = tracker.update(
            [observation("R5", 160, width=160, height=80)],
            0.4,
            600,
        )
        self.assertEqual(["entered"], [event.kind for event in confirmed.events])
        self.assertEqual(1, confirmed.events[0].track.id)

    def test_different_exact_card_cannot_take_over_distant_unconfirmed_track(self):
        tracker = BeltTracker()
        for index, x in enumerate((500, 520, 540)):
            tracker.update(
                [observation("GONK", x, width=203, height=80)],
                index * 0.4,
                2_000,
            )

        # This reproduces the video merge: the broad generic gate is about
        # 406px, but CYCLO-GRAV is a separate card 278px from GONK.
        tracker.update(
            [observation("CYCLO-GRAV", 262, width=203, height=80)],
            1.2,
            2_000,
        )
        self.assertEqual(2, len(tracker._tracks))
        self.assertEqual(
            [["GONK", "GONK", "GONK"], ["CYCLO-GRAV"]],
            [list(track.identity_votes) for track in tracker._tracks],
        )

        confirmed = tracker.update(
            [observation("GONK", 560, width=203, height=80)],
            1.6,
            2_000,
        )
        self.assertEqual(["entered"], [event.kind for event in confirmed.events])
        self.assertEqual("GONK", confirmed.events[0].track.name)

    def test_sliver_overlap_cannot_bypass_different_exact_card_gate(self):
        tracker = BeltTracker()
        for index in range(3):
            tracker.update(
                [observation("GONK", 0, width=200, height=80)],
                index * 0.2,
                1_000,
            )

        # The boxes overlap by one pixel, but their centers are farther apart
        # than the 0.75-card-width conflicting-name gate.
        tracker.update(
            [observation("CYCLO-GRAV", 199, width=200, height=80)],
            0.6,
            1_000,
        )

        self.assertEqual(2, len(tracker._tracks))
        self.assertEqual(
            [["GONK", "GONK", "GONK"], ["CYCLO-GRAV"]],
            [list(track.identity_votes) for track in tracker._tracks],
        )

    def test_one_wrong_first_vote_can_still_follow_the_same_moving_card(self):
        tracker = BeltTracker()
        tracker.update(
            [observation("R6", 0, width=160, height=80)],
            0.0,
            1_000,
        )
        tracker.update(
            [observation("GONK", 170, width=160, height=80)],
            0.4,
            1_000,
        )

        self.assertEqual(1, len(tracker._tracks))
        self.assertEqual(["R6", "GONK"], list(tracker._tracks[0].identity_votes))

    def test_confirmed_track_waits_for_observation_timeout_not_prediction(self):
        tracker = BeltTracker(
            confirmation_hits=2,
            confirmation_window=3,
            timeout_seconds=1.0,
        )
        tracker.update([observation("ARG", 100, width=160, height=80)], 0.0, 600)
        entered = tracker.update(
            [observation("ARG", 120, width=160, height=80)],
            0.1,
            600,
        )
        self.assertEqual(["entered"], [event.kind for event in entered.events])

        tracker._tracks[0].velocity_x = 5_000.0
        predicted = tracker.predict(0.2, 600)
        self.assertEqual([], predicted.events)
        self.assertEqual(["ARG"], [track.name for track in predicted.tracks])
        self.assertEqual(tracker._tracks[0].box, predicted.tracks[0].box)
        self.assertEqual(1, len(tracker._tracks))

        expired = tracker.predict(1.11, 600)
        self.assertEqual(["exited"], [event.kind for event in expired.events])
        self.assertEqual("ARG", expired.events[0].track.name)

    def test_outside_prediction_uses_real_box_for_conflict_quarantine(self):
        tracker = BeltTracker(confirmation_hits=2, confirmation_window=3)
        tracker.update([observation("GONK", 100, width=160, height=80)], 0.0, 600)
        tracker.update([observation("GONK", 120, width=160, height=80)], 0.1, 600)
        tracker._tracks[0].velocity_x = 5_000.0

        tracker.update([observation("R6", 130, width=160, height=80)], 0.2, 600)

        self.assertEqual(1, len(tracker._tracks))
        self.assertEqual("GONK", tracker._tracks[0].name)

    def test_stationary_confirmed_track_emits_exit_after_timeout(self):
        tracker = BeltTracker(timeout_seconds=0.5)
        for index in range(4):
            confirmed = tracker.update([observation("GONK", 20)], index * 0.1, 400)
        self.assertEqual(["GONK"], [track.name for track in confirmed.tracks])
        self.assertEqual(["entered"], [event.kind for event in confirmed.events])
        self.assertEqual(["exited"], [event.kind for event in tracker.predict(0.81, 400).events])


if __name__ == "__main__":
    unittest.main()
