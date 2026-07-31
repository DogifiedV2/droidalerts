from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QObject, Property, Signal


class StateObject(QObject):
    """Exposes controller state as one QML property."""

    stateChanged = Signal()

    def __init__(
        self,
        initial: Mapping[str, Any] | None = None,
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._state: dict[str, Any] = dict(initial or {})

    def state_snapshot(self) -> dict[str, Any]:
        return dict(self._state)

    state = Property("QVariantMap", state_snapshot, notify=stateChanged)

    def replace_state(self, state: Mapping[str, Any]) -> None:
        replacement = dict(state)
        if replacement == self._state:
            return
        self._state = replacement
        self.stateChanged.emit()

    def update_state(self, **changes: Any) -> None:
        changed = False
        for key, value in changes.items():
            if self._state.get(key) != value:
                self._state[key] = value
                changed = True
        if changed:
            self.stateChanged.emit()


class UiDispatcher(QObject):
    """Queues callbacks on the Qt GUI thread."""

    dispatchRequested = Signal(object)

    def __init__(self, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._closed = False
        self.dispatchRequested.connect(self._run)

    def post(self, callback) -> None:
        if not self._closed:
            self.dispatchRequested.emit(callback)

    def close(self) -> None:
        self._closed = True

    def _run(self, callback) -> None:
        if self._closed:
            return
        try:
            callback()
        except Exception as exc:
            print(f"[GUI] Queued callback failed: {exc}")
