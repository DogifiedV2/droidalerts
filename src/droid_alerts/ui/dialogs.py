from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import QObject, Slot

from .state import StateObject


DialogCallback = Callable[[dict[str, Any] | None], None]
DialogActionCallback = Callable[[dict[str, Any]], None]


class DialogController(StateObject):
    """Presents dialogs through the shared QML overlay."""

    def __init__(self, *, parent: QObject | None = None) -> None:
        super().__init__(
            {
                "visible": False,
                "kind": "message",
                "tone": "info",
                "title": "",
                "eyebrow": "Droid Alerts",
                "icon": "info",
                "message": "",
                "note": "",
                "acceptText": "OK",
                "cancelText": "",
                "actionText": "",
                "fields": [],
                "options": [],
                "choices": [],
                "linkText": "",
                "linkUrl": "",
            },
            parent=parent,
        )
        self._callback: DialogCallback | None = None
        self._action_callback: DialogActionCallback | None = None

    def show_message(
        self,
        title: str,
        message: str,
        *,
        tone: str = "info",
        note: str = "",
        accept_text: str = "Close",
        link: tuple[str, str] | None = None,
        callback: DialogCallback | None = None,
    ) -> None:
        self._open(
            kind="message",
            title=title,
            message=message,
            tone=tone,
            note=note,
            accept_text=accept_text,
            cancel_text="",
            link=link,
            callback=callback,
        )

    def confirm(
        self,
        title: str,
        message: str,
        *,
        tone: str = "info",
        note: str = "",
        accept_text: str = "Continue",
        cancel_text: str = "Cancel",
        link: tuple[str, str] | None = None,
        callback: DialogCallback | None = None,
    ) -> None:
        self._open(
            kind="confirm",
            title=title,
            message=message,
            tone=tone,
            note=note,
            accept_text=accept_text,
            cancel_text=cancel_text,
            link=link,
            callback=callback,
        )

    def form(
        self,
        title: str,
        message: str,
        fields: Sequence[Mapping[str, Any]],
        *,
        note: str = "",
        accept_text: str = "Save",
        cancel_text: str = "Cancel",
        callback: DialogCallback | None = None,
    ) -> None:
        self._open(
            kind="form",
            title=title,
            message=message,
            tone="info",
            note=note,
            accept_text=accept_text,
            cancel_text=cancel_text,
            fields=fields,
            callback=callback,
        )

    def choices(
        self,
        title: str,
        message: str,
        options: Sequence[Mapping[str, Any]],
        *,
        note: str = "",
        multi: bool = False,
        accept_text: str = "Save",
        cancel_text: str = "Cancel",
        callback: DialogCallback | None = None,
    ) -> None:
        self._open(
            kind="multi-choice" if multi else "choice",
            title=title,
            message=message,
            tone="info",
            note=note,
            accept_text=accept_text,
            cancel_text=cancel_text,
            options=options,
            callback=callback,
        )

    def rules(
        self,
        title: str,
        message: str,
        options: Sequence[Mapping[str, Any]],
        choices: Sequence[Mapping[str, Any]],
        *,
        note: str = "",
        accept_text: str = "Save",
        cancel_text: str = "Cancel",
        action_text: str = "",
        action_callback: DialogActionCallback | None = None,
        callback: DialogCallback | None = None,
    ) -> None:
        self._open(
            kind="rules",
            title=title,
            message=message,
            tone="info",
            note=note,
            accept_text=accept_text,
            cancel_text=cancel_text,
            options=options,
            choices=choices,
            action_text=action_text,
            action_callback=action_callback,
            callback=callback,
        )

    def channel_settings(
        self,
        title: str,
        message: str,
        fields: Sequence[Mapping[str, Any]],
        options: Sequence[Mapping[str, Any]],
        *,
        note: str = "",
        accept_text: str = "Save",
        cancel_text: str = "Cancel",
        link: tuple[str, str] | None = None,
        callback: DialogCallback | None = None,
    ) -> None:
        self._open(
            kind="channel",
            title=title,
            message=message,
            tone="info",
            note=note,
            accept_text=accept_text,
            cancel_text=cancel_text,
            fields=fields,
            options=options,
            link=link,
            callback=callback,
        )

    @staticmethod
    def _presentation(kind: str, tone: str, title: str) -> tuple[str, str]:
        lowered = title.casefold()
        if tone == "danger":
            return "Attention", "warning"
        if "timer" in lowered:
            return "Overlay", "clock"
        if any(
            word in lowered
            for word in ("capture", "display", "monitor", "window", "device")
        ):
            return "Capture", "monitor"
        if any(
            word in lowered
            for word in ("discord", "ntfy", "pushover", "phone", "alert")
        ):
            return "Alerts", "bell"
        if "belt" in lowered:
            return "Belt tracker", "belt"
        if "deal" in lowered:
            return "Limited deals", "deals"
        if any(word in lowered for word in ("history", "storage", "export", "data")):
            return "Data", "history"
        if kind in {"form", "choice", "multi-choice", "rules", "channel"}:
            return "Settings", "settings"
        return "Droid Alerts", "info"

    def _open(
        self,
        *,
        kind: str,
        title: str,
        message: str,
        tone: str,
        note: str,
        accept_text: str,
        cancel_text: str,
        fields: Sequence[Mapping[str, Any]] = (),
        options: Sequence[Mapping[str, Any]] = (),
        choices: Sequence[Mapping[str, Any]] = (),
        action_text: str = "",
        action_callback: DialogActionCallback | None = None,
        link: tuple[str, str] | None = None,
        callback: DialogCallback | None = None,
    ) -> None:
        self._callback = callback
        self._action_callback = action_callback
        eyebrow, icon = self._presentation(kind, tone, title)
        self.replace_state(
            {
                "visible": True,
                "kind": kind,
                "tone": tone,
                "title": title,
                "eyebrow": eyebrow,
                "icon": icon,
                "message": message,
                "note": note,
                "acceptText": accept_text,
                "cancelText": cancel_text,
                "actionText": action_text,
                "fields": [dict(field) for field in fields],
                "options": [dict(option) for option in options],
                "choices": [dict(choice) for choice in choices],
                "linkText": link[0] if link else "",
                "linkUrl": link[1] if link else "",
            }
        )

    @Slot("QVariantMap")
    def accept(self, payload: Mapping[str, Any] | None = None) -> None:
        callback = self._callback
        self._callback = None
        self._action_callback = None
        self.update_state(visible=False)
        if callback is not None:
            callback(dict(payload or {}))

    @Slot()
    def cancel(self) -> None:
        callback = self._callback
        self._callback = None
        self._action_callback = None
        self.update_state(visible=False)
        if callback is not None:
            callback(None)

    @Slot("QVariantMap")
    def action(self, payload: Mapping[str, Any] | None = None) -> None:
        callback = self._action_callback
        if callback is not None:
            callback(dict(payload or {}))
