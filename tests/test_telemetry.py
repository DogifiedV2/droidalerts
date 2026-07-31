from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.config import AppConfig
from droid_alerts.telemetry import (
    AnonymousAppTelemetryClient,
    AnonymousBeltTelemetryClient,
    AnonymousTelemetryClient,
)


INSTALL_ID = "11111111-1111-4111-8111-111111111111"


class AppTelemetryTests(unittest.TestCase):
    def test_app_heartbeat_uses_the_shared_anonymous_install_id(self):
        client = AnonymousAppTelemetryClient(
            AppConfig(anonymous_app_stats_url="https://example.test/app-heartbeat")
        )
        client._post_json = Mock(return_value={"heartbeatIntervalSeconds": 90})

        with patch("droid_alerts.telemetry.load_or_create_anonymous_install_id", return_value=INSTALL_ID):
            client._send_heartbeat()

        endpoint, payload = client._post_json.call_args.args
        self.assertEqual("https://example.test/app-heartbeat", endpoint)
        self.assertEqual(INSTALL_ID, payload["installId"])
        self.assertIn("sessionId", payload)
        self.assertIn("appVersion", payload)
        self.assertEqual(90, client._heartbeat_interval_seconds)


class ChatTelemetryTests(unittest.TestCase):
    def test_priority_settings_are_sent_first_and_only_after_changes(self):
        config = AppConfig(
            anonymous_stats_url="https://example.test/heartbeat",
            alert_targets=[
                ["Rainbow", "Mythic"],
                ["Diamond", "Mythic"],
                ["Galactic", "Rare"],
                ["Galactic", "Epic"],
            ],
        )
        client = AnonymousTelemetryClient(config)
        client._post_json = Mock(return_value={"heartbeatIntervalSeconds": 60})

        with patch("droid_alerts.telemetry.load_or_create_anonymous_install_id", return_value=INSTALL_ID):
            client._send_heartbeat()
            client._send_heartbeat()

            changed = AppConfig(
                anonymous_stats_url=config.anonymous_stats_url,
                alert_targets=[["Rainbow", "Mythic"]],
            )
            with patch.object(client, "start"):
                client.apply_config(changed)
            client._send_heartbeat()

            cleared = AppConfig(anonymous_stats_url=config.anonymous_stats_url, alert_targets=[])
            with patch.object(client, "start"):
                client.apply_config(cleared)
            client._send_heartbeat()

        payloads = [call.args[1] for call in client._post_json.call_args_list]
        self.assertEqual(
            ["diamondmythic", "galacticepic", "rainbowmythic"],
            payloads[0]["priorityAlerts"],
        )
        self.assertNotIn("priorityAlerts", payloads[1])
        self.assertEqual(["rainbowmythic"], payloads[2]["priorityAlerts"])
        self.assertEqual([], payloads[3]["priorityAlerts"])

    def test_galactic_debug_detection_upload_contains_both_pngs_and_matching_keys(self):
        config = AppConfig(
            share_debug_detections=True,
            debug_detection_upload_url="https://example.test/debug-detections",
        )
        client = AnonymousTelemetryClient(config)
        client._post_json = Mock(return_value={"ok": True})
        detection = SimpleNamespace(
            droid="Galactic",
            rarity="Legendary",
            score=0.99,
            rarity_score=0.98,
            droid_score=0.97,
            rarity_margin=0.42,
            source="test",
        )
        event = {
            "ts": "20260719_233201_904",
            "frame": 285,
            "row_hash": "abc123",
            "screen_width": 3440,
            "screen_height": 1440,
            "monitor_index": 1,
            "capture_region": {
                "source": "manual",
                "left": 0,
                "top": 616,
                "width": 1135,
                "height": 230,
            },
            "scale": 1.0,
            "scale_method": "screen",
        }

        with tempfile.TemporaryDirectory() as directory:
            roi = Path(directory) / "shared_alert_roi_test.png"
            candidate = Path(directory) / "shared_alert_roi_test_candidate_check.png"
            png = b"\x89PNG\r\n\x1a\nfixture"
            roi.write_bytes(png)
            candidate.write_bytes(png)
            with patch("droid_alerts.telemetry.load_or_create_anonymous_install_id", return_value=INSTALL_ID):
                client._send_debug_detection(
                    config.debug_detection_upload_url,
                    detection,
                    event,
                    [str(roi), str(candidate)],
                )

        endpoint, payload = client._post_json.call_args.args
        self.assertEqual(config.debug_detection_upload_url, endpoint)
        self.assertEqual("galacticlegendary", payload["detection"]["key"])
        self.assertEqual("galacticlegendary", payload["storage"]["detectionKey"])
        self.assertEqual("Galactic", payload["detection"]["droid"])
        self.assertEqual("Legendary", payload["detection"]["rarity"])
        self.assertEqual(["roi", "candidate_check"], [item["name"] for item in payload["screenshots"]])


class BeltTelemetryTests(unittest.TestCase):
    def test_belt_heartbeat_and_confirmed_counts_use_compact_cumulative_buckets(self):
        config = AppConfig(
            anonymous_belt_stats_url="https://example.test/belt-heartbeat",
            anonymous_belt_counts_url="https://example.test/belt-counts",
            belt_target_tiers={"R2": "Default", "GONK": "Gold"},
        )
        with tempfile.TemporaryDirectory() as directory:
            pending_path = Path(directory) / "pending.json"
            with patch("droid_alerts.telemetry.belt_pending_counts_path", return_value=pending_path):
                client = AnonymousBeltTelemetryClient(config)
                client._session_active = True
                client._post_json = Mock(return_value={"heartbeatIntervalSeconds": 60})
                with patch("droid_alerts.telemetry.load_or_create_anonymous_install_id", return_value=INSTALL_ID):
                    client._send_heartbeat()
                    client._send_heartbeat()
                    client.record_sighting("GONK")
                    client.record_sighting("gonk")
                    client.record_sighting("R2")
                    client.record_sighting("not-a-droid")
                    client._flush_counts()
                    calls_after_first_flush = client._post_json.call_count
                    client._flush_counts()
                    self.assertEqual(calls_after_first_flush, client._post_json.call_count)
                    client.record_sighting("GONK")
                    client._flush_counts()

        calls = client._post_json.call_args_list
        first_heartbeat = calls[0].args[1]
        second_heartbeat = calls[1].args[1]
        first_counts = calls[2].args[1]
        second_counts = calls[3].args[1]

        self.assertEqual(["GONK", "R2"], first_heartbeat["targetDroids"])
        self.assertNotIn("targetDroids", second_heartbeat)
        self.assertEqual(
            [{"droid": "GONK", "count": 2}, {"droid": "R2", "count": 1}],
            first_counts["buckets"][0]["counts"],
        )
        self.assertEqual(3, second_counts["buckets"][0]["counts"][0]["count"])
        self.assertNotIn("confidence", first_counts["buckets"][0])
        self.assertNotIn("rawText", first_counts["buckets"][0])


if __name__ == "__main__":
    unittest.main()
