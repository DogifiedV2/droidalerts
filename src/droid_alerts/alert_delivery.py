from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping

from .logging_io import append_event_safely, timestamp
from .notifications import AlertDelivery, DeliveryResult


@dataclass(frozen=True)
class DeliveryExecution:
    result: DeliveryResult
    attempts: int


def delivery_retryable(message: str) -> bool:
    """Retry transient network/server failures, but not permanent client errors."""
    text = str(message).lower()
    non_retryable = ("http 400", "http 401", "http 403", "http 404", "credential", "unauthorized")
    if any(marker in text for marker in non_retryable):
        return False
    return any(
        marker in text
        for marker in ("timeout", "timed out", "tempor", "connection", "http 408", "http 429", "http 5")
    )


def execute_alert_delivery(
    delivery: AlertDelivery,
    *,
    wait_before_retry: Callable[[float], bool] | None = None,
    max_attempts: int = 2,
) -> DeliveryExecution:
    result: DeliveryResult | None = None
    attempts = 0
    attempt_limit = max(1, int(max_attempts))
    for attempts in range(1, attempt_limit + 1):
        try:
            result = delivery.target(*delivery.args, **delivery.kwargs)
        except Exception as exc:
            result = DeliveryResult(delivery.label, False, str(exc))
        if (
            result.success
            or attempts == attempt_limit
            or not delivery_retryable(result.message)
        ):
            break
        if wait_before_retry is not None and wait_before_retry(1.5):
            break
    assert result is not None
    return DeliveryExecution(result, attempts)


def build_delivery_event(
    execution: DeliveryExecution,
    source_event: Mapping[str, object],
    *,
    channel: str | None = None,
    source: str | None = None,
    rarity: str | None = None,
    extra_fields: Mapping[str, object] | None = None,
) -> dict[str, object]:
    result = execution.result
    detail = result.message
    if execution.attempts > 1:
        detail += f" after {execution.attempts} attempts"
    event: dict[str, object] = {
        "ts": timestamp(),
        "event_type": "delivery",
        "channel": channel or result.channel,
        "success": result.success,
        "detail": detail,
        "droid": source_event.get("droid", ""),
        "rarity": source_event.get("rarity", "") if rarity is None else rarity,
        "alerted": True,
        "is_priority": True,
        "score": source_event.get("score"),
        "source": source_event.get("source", "") if source is None else source,
    }
    event.update(extra_fields or {})
    return event


def persist_delivery_event(
    event: dict[str, object],
    *,
    on_error: Callable[[Exception], None] | None = None,
) -> bool:
    return append_event_safely(event, on_error=on_error)
