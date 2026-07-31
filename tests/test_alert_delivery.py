from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.alert_delivery import (
    execute_alert_delivery,
)
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

if __name__ == "__main__":
    unittest.main()
