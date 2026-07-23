from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.alert_delivery import (
    DeliveryExecution,
    build_delivery_event,
    execute_alert_delivery,
    persist_delivery_event,
)
from droid_alerts.logging_io import append_event_safely
from droid_alerts.notifications import AlertDelivery, DeliveryResult


class SharedAlertDeliveryTests(unittest.TestCase):
    def delivery(self, target) -> AlertDelivery:
        return AlertDelivery("Discord", target, (), {})

    def test_transient_failure_retries_once(self):
        target = Mock(side_effect=[
            DeliveryResult("Discord", False, "HTTP 503"),
            DeliveryResult("Discord", True, "Delivered"),
        ])
        waits = []
        execution = execute_alert_delivery(
            self.delivery(target), wait_before_retry=lambda seconds: waits.append(seconds) or False
        )
        self.assertTrue(execution.result.success)
        self.assertEqual(2, execution.attempts)
        self.assertEqual([1.5], waits)

    def test_permanent_failure_is_not_retried(self):
        target = Mock(return_value=DeliveryResult("Discord", False, "HTTP 401 unauthorized"))
        execution = execute_alert_delivery(self.delivery(target))
        self.assertEqual(1, execution.attempts)
        target.assert_called_once()

    def test_retry_can_be_disabled_for_single_attempt_callers(self):
        target = Mock(return_value=DeliveryResult("Discord", False, "HTTP 503"))
        waits = []

        execution = execute_alert_delivery(
            self.delivery(target),
            max_attempts=1,
            wait_before_retry=lambda seconds: waits.append(seconds) or False,
        )

        self.assertEqual(1, execution.attempts)
        target.assert_called_once()
        self.assertEqual([], waits)

    def test_event_shape_is_stable_for_each_alert_source(self):
        execution = DeliveryExecution(DeliveryResult("Discord", True, "Delivered"), 1)
        for source in ("chat", "rebirth-alert", "rebirth_ready", "cb23-mission", "belt_tracker", "limited_deal"):
            with self.subTest(source=source):
                event = build_delivery_event(
                    execution,
                    {"droid": "R2", "rarity": "Mythic", "score": 0.9, "source": source},
                    extra_fields={"rebirth_level": 3} if source == "rebirth_ready" else None,
                )
                self.assertEqual(
                    {"event_type", "ts", "channel", "success", "detail", "droid", "rarity", "alerted", "is_priority", "score", "source"}
                    | ({"rebirth_level"} if source == "rebirth_ready" else set()),
                    set(event),
                )
                self.assertEqual(source, event["source"])

    def test_log_failure_does_not_change_delivery_event(self):
        event = {"event_type": "delivery", "success": True}
        errors = []
        with patch("droid_alerts.alert_delivery.append_event_safely", return_value=False) as append:
            result = persist_delivery_event(event, on_error=errors.append)
        self.assertFalse(result)
        append.assert_called_once_with(event, on_error=errors.append)
        self.assertTrue(event["success"])


class SafeEventLoggingTests(unittest.TestCase):
    def test_safe_writer_reports_success(self):
        event = {"event_type": "test"}
        with patch("droid_alerts.logging_io.append_event") as append:
            self.assertTrue(append_event_safely(event, filename="other.jsonl"))

        append.assert_called_once_with(event, filename="other.jsonl")

    def test_safe_writer_reports_failure_and_invokes_callback(self):
        on_error = Mock()
        error = OSError("disk full")
        with patch("droid_alerts.logging_io.append_event", side_effect=error):
            self.assertFalse(append_event_safely({"event_type": "test"}, on_error=on_error))

        on_error.assert_called_once_with(error)


if __name__ == "__main__":
    unittest.main()
