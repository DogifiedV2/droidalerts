from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..classifier import Detection
from ..config import AppConfig, data_dir
from ..limited_deals import (
    LIMITED_DEAL_ALERT_FAMILY_ORDER,
    LIMITED_DEAL_DROIDS,
    LIMITED_DEAL_PRIORITY_COMBOS,
    LimitedDeal,
    LimitedDealService,
    LimitedDealStatus,
    limited_deal_matches,
    limited_deal_target_label,
    normalize_limited_deal_priority_alerts,
    normalize_limited_deal_target_tiers,
)
from ..logging_io import append_event_safely, timestamp
from ..notifications import event_text
from .dashboard_controller import DashboardController
from .runtime import ApplicationRuntime
from .state import StateObject


class DealsController(StateObject):
    """Loads Limited Deals and manages their alert rules."""

    historyChanged = Signal()

    def __init__(
        self,
        runtime: ApplicationRuntime,
        dashboard: DashboardController,
        *,
        parent: QObject | None = None,
    ) -> None:
        self.runtime = runtime
        self.dashboard = dashboard
        self.service: LimitedDealService | None = None
        self.current_deal: LimitedDeal | None = None
        self._portrait: Path | None = None
        self._error = ""
        super().__init__({}, parent=parent)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        runtime.configChanged.connect(self._rules_changed)
        runtime.register_shutdown(self.shutdown)
        self.start()
        self.refresh()

    def start(self) -> None:
        url = self.runtime.config.limited_deal_url.strip()
        if not url:
            self._error = "Limited deal unavailable"
            return
        service = LimitedDealService(
            url,
            data_dir() / "limited_deal_cache.json",
            lambda status: self.runtime.dispatcher.post(
                lambda value=status: self._handle_status(value)
            ),
        )
        self.service = service
        service.start()

    def _priority_rows(self) -> list[dict[str, Any]]:
        selected = {
            tuple(value)
            for value in normalize_limited_deal_priority_alerts(
                self.runtime.config.limited_deal_priority_alerts
            )
        }
        return [
            {
                "id": f"{family}|{rarity}",
                "family": family,
                "rarity": rarity,
                "label": f"{family} {rarity}",
                "enabled": (family, rarity) in selected,
                "tone": family.lower(),
            }
            for family, rarity in LIMITED_DEAL_PRIORITY_COMBOS
        ]

    def _target_rows(self) -> list[dict[str, str]]:
        tiers = normalize_limited_deal_target_tiers(
            self.runtime.config.limited_deal_target_tiers
        )
        return [
            {
                "id": str(droid.id),
                "droid": droid.name,
                "rarity": droid.rarity,
                "minimum": limited_deal_target_label(tiers[str(droid.id)]),
                "tone": tiers[str(droid.id)].lower(),
            }
            for droid in LIMITED_DEAL_DROIDS
            if str(droid.id) in tiers
        ]

    def _countdown(self) -> str:
        deal = self.current_deal
        if deal is None:
            return "--:--:--"
        try:
            end = datetime.fromisoformat(deal.ends_at.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return "--:--:--"
        remaining = max(
            0,
            int((end - datetime.now(timezone.utc)).total_seconds() + 0.999),
        )
        hours, remainder = divmod(remaining, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @Slot()
    def refresh(self) -> None:
        deal = self.current_deal
        target_rows = self._target_rows()
        self.replace_state(
            {
                "available": deal is not None,
                "offer": (
                    f"{deal.mutation} {deal.rarity} · {deal.droid}"
                    if deal is not None
                    else self._error or "Getting limited deal…"
                ),
                "countdown": self._countdown(),
                "portrait": (
                    self._portrait.resolve().as_uri()
                    if self._portrait is not None and self._portrait.is_file()
                    else ""
                ),
                "priorityRows": self._priority_rows(),
                "targets": target_rows,
                "targetCount": len(target_rows),
            }
        )

    def _handle_status(self, status: LimitedDealStatus) -> None:
        deal = (
            status.deal
            if status.deal is not None and status.deal.is_current()
            else None
        )
        self.current_deal = deal
        self._portrait = status.portrait_path if deal is not None else None
        self._error = status.error
        if status.error:
            self.runtime.detailChanged.emit(f"Limited Deal check failed: {status.error}")
        self._evaluate_current()
        self.refresh()

    def _evaluate_current(self) -> None:
        service, deal = self.service, self.current_deal
        if service is None or deal is None or not deal.is_current():
            return
        config = AppConfig.from_dict(self.runtime.config.to_dict())
        if not limited_deal_matches(
            deal,
            config.limited_deal_priority_alerts,
            config.limited_deal_target_tiers,
        ):
            return
        if service.was_alerted(deal):
            return
        service.mark_alerted(deal)
        detection = Detection(
            droid=deal.droid,
            rarity=f"{deal.mutation} {deal.rarity}",
            row_box=(0, 0, 0, 0),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="limited-deal",
            shape_score=1.0,
        )
        event = {
            "ts": timestamp(),
            "event_type": "limited_deal",
            "source": "limited_deal",
            "droid": deal.droid,
            "droid_id": deal.droid_id,
            "rarity": detection.rarity,
            "deal_mutation": deal.mutation,
            "droid_rarity": deal.rarity,
            "starts_at": deal.starts_at,
            "ends_at": deal.ends_at,
            "alerted": True,
            "is_priority": True,
            "detail": "Matched Limited Deal alert rules",
        }
        append_event_safely(
            event,
            on_error=lambda exc: print(f"[LOG] Failed to write limited_deal: {exc}"),
        )
        self.runtime.detailChanged.emit(event_text(detection))
        self.dashboard.dispatch_detection(
            detection,
            source="limited_deal",
            rarity=detection.rarity,
            extra_fields={"starts_at": deal.starts_at},
        )
        self.historyChanged.emit()

    @Slot(str, bool)
    def setPriority(self, combo_id: str, enabled: bool) -> None:
        selected = {
            tuple(value)
            for value in normalize_limited_deal_priority_alerts(
                self.runtime.config.limited_deal_priority_alerts
            )
        }
        parts = tuple(combo_id.split("|", 1))
        if len(parts) != 2 or parts not in LIMITED_DEAL_PRIORITY_COMBOS:
            return
        if enabled:
            selected.add(parts)
        else:
            selected.discard(parts)
        values = [
            list(combo)
            for combo in LIMITED_DEAL_PRIORITY_COMBOS
            if combo in selected
        ]
        self.runtime.update_config(limited_deal_priority_alerts=values)
        self.refresh()

    @Slot(bool)
    def selectAllPriorities(self, selected: bool) -> None:
        values = (
            [list(combo) for combo in LIMITED_DEAL_PRIORITY_COMBOS]
            if selected
            else []
        )
        self.runtime.update_config(limited_deal_priority_alerts=values)
        self.refresh()

    @Slot()
    def chooseTargets(self) -> None:
        rules = normalize_limited_deal_target_tiers(
            self.runtime.config.limited_deal_target_tiers
        )
        choices = [{"id": "", "label": "Off"}] + [
            {"id": family, "label": limited_deal_target_label(family)}
            for family in LIMITED_DEAL_ALERT_FAMILY_ORDER
        ]
        self.runtime.dialogs.rules(
            "Custom Droid Alerts",
            "Choose the minimum Limited Deal mutation for each droid.",
            [
                {
                    "id": str(droid.id),
                    "label": droid.name,
                    "detail": droid.rarity,
                    "value": rules.get(str(droid.id), ""),
                }
                for droid in LIMITED_DEAL_DROIDS
            ],
            choices,
            callback=self._save_targets,
        )

    def _save_targets(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        values = payload.get("values", {})
        rules = normalize_limited_deal_target_tiers(
            values if isinstance(values, dict) else {}
        )
        self.runtime.update_config(limited_deal_target_tiers=rules)
        self.runtime.detailChanged.emit("Limited Deal droid rules saved")
        self.refresh()

    def _rules_changed(self) -> None:
        self._evaluate_current()
        self.refresh()

    @Slot()
    def pageOpened(self) -> None:
        if self.runtime.config.limited_deals_intro_shown:
            return
        self.runtime.dialogs.confirm(
            "Limited Deals",
            "Get alerted when the rotating deal matches something you want.",
            note=(
                "Choose a mutation and rarity combination, or set a minimum "
                "mutation for individual droids. New offers normally appear "
                "about 10 seconds after each hour."
            ),
            accept_text="Got it",
            cancel_text="",
            callback=self._intro_response,
        )

    def _intro_response(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        self.runtime.update_config(limited_deals_intro_shown=True)

    def shutdown(self) -> None:
        self._timer.stop()
        if self.service is not None:
            self.service.stop()
            self.service = None
