from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AuxiliaryAlertSchedule:
    next_rebirth_available_scan_at: float = 0.0
    last_rebirth_available_alert_at: float = float("-inf")
    next_rebirth_ready_scan_at: float = 0.0
    next_cb23_mission_scan_at: float = 0.0
    last_cb23_mission_alert_at: float = float("-inf")

    def rebirth_available_due(self, now: float, interval: float) -> bool:
        if now < self.next_rebirth_available_scan_at:
            return False
        self.next_rebirth_available_scan_at = now + interval
        return True

    def rebirth_ready_due(self, now: float, interval: float) -> bool:
        if now < self.next_rebirth_ready_scan_at:
            return False
        self.next_rebirth_ready_scan_at = now + interval
        return True

    def cb23_due(self, now: float, interval: float) -> bool:
        if now < self.next_cb23_mission_scan_at:
            return False
        self.next_cb23_mission_scan_at = now + interval
        return True

    def can_alert_rebirth_available(self, now: float, cooldown: float) -> bool:
        if now - self.last_rebirth_available_alert_at < cooldown:
            return False
        self.last_rebirth_available_alert_at = now
        return True

    def can_alert_cb23(self, now: float, cooldown: float) -> bool:
        if now - self.last_cb23_mission_alert_at < cooldown:
            return False
        self.last_cb23_mission_alert_at = now
        return True
