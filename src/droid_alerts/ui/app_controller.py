from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .. import __version__
from ..notifications import (
    discord_webhook_configured,
    ntfy_configured,
    phone_alerts_configured,
)
from .belt_controller import BeltController
from .constants import (
    DISCORD_COMMUNITY_URL,
    PAGES,
    STATS_URL,
    TRACKER_URL,
    WHATS_NEW_ITEMS,
    WIKI_URL,
)
from .dashboard_controller import DashboardController
from .runtime import ApplicationRuntime
from .state import StateObject


class AppController(StateObject):
    """Controls the app shell and navigation."""

    pageChanged = Signal(str)

    def __init__(
        self,
        runtime: ApplicationRuntime,
        dashboard: DashboardController,
        belt: BeltController,
        *,
        parent: QObject | None = None,
    ) -> None:
        self.runtime = runtime
        self.dashboard = dashboard
        self.belt = belt
        self._page = "dashboard"
        self._detail = "Ready"
        self._toast_timer = QTimer()
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._hide_toast)
        super().__init__({}, parent=parent)
        runtime.detailChanged.connect(self.showToast)
        dashboard.statusChanged.connect(lambda _status: self.refresh())
        belt.statusChanged.connect(lambda _status: self.refresh())
        self.refresh()

    @staticmethod
    def _combined_status(watcher: str, belt: str) -> str:
        states = (watcher, belt)
        if "Error" in states:
            return "Error"
        if "Warning" in states:
            return "Warning"
        if "Running" in states:
            return "Running"
        if "Paused" in states:
            return "Paused"
        return "Stopped"

    @Slot()
    def refresh(self) -> None:
        title = dict(PAGES).get(self._page, "Dashboard")
        status = self._combined_status(self.dashboard.status, self.belt.status)
        self.replace_state(
            {
                **self._state,
                "page": self._page,
                "pageTitle": title,
                "pages": [
                    {"id": page_id, "label": label, "number": index}
                    for index, (page_id, label) in enumerate(PAGES, start=1)
                ],
                "status": status,
                "statusTone": {
                    "Running": "good",
                    "Warning": "warning",
                    "Error": "danger",
                    "Paused": "warning",
                }.get(status, "muted"),
                "watching": self.dashboard.is_watching(),
                "watchButton": (
                    "Stop Watching"
                    if self.dashboard.is_watching()
                    else "Start Watching"
                ),
                "version": __version__,
                "detail": self._detail,
                "toastVisible": self._state.get("toastVisible", False),
                "toastText": self._state.get("toastText", ""),
            }
        )

    @Slot(str)
    def selectPage(self, page: str) -> None:
        if page not in dict(PAGES) or page == self._page:
            return
        self._page = page
        self.refresh()
        self.pageChanged.emit(page)

    @Slot(int)
    def selectPageNumber(self, number: int) -> None:
        if 1 <= number <= len(PAGES):
            self.selectPage(PAGES[number - 1][0])

    @Slot()
    def toggleWatching(self) -> None:
        self.dashboard.toggleWatching()
        self.refresh()

    @Slot(str)
    def openLink(self, key: str) -> None:
        url = {
            "discord": DISCORD_COMMUNITY_URL,
            "tracker": TRACKER_URL,
            "wiki": WIKI_URL,
            "stats": STATS_URL,
        }.get(key)
        if url:
            self.runtime.open_url(url)

    @Slot(str)
    def showToast(self, text: str) -> None:
        self._detail = text
        self.update_state(
            detail=text,
            toastVisible=True,
            toastText=text,
        )
        self._toast_timer.start(2600)

    def _hide_toast(self) -> None:
        self.update_state(toastVisible=False)

    @Slot()
    def startupPrompts(self) -> None:
        config = self.runtime.config
        last_seen = config.last_seen_version.strip()
        completed_first_start = bool(
            config.intro_shown or config.notification_setup_prompted
        )
        fresh_install = not last_seen and not completed_first_start
        if last_seen != __version__:
            self.runtime.update_config(last_seen_version=__version__, announce=False)
        if not fresh_install and last_seen != __version__:
            self.runtime.dialogs.show_message(
                "What's new",
                "\n".join(f"• {item}" for item in WHATS_NEW_ITEMS),
                accept_text="Got it",
                callback=lambda _payload: self._continue_startup_prompts(),
            )
            return
        self._continue_startup_prompts()

    def _continue_startup_prompts(self) -> None:
        config = self.runtime.config
        completed_first_start = bool(
            config.intro_shown or config.notification_setup_prompted
        )
        if completed_first_start and not config.intro_shown:
            self.runtime.update_config(intro_shown=True, announce=False)
            config = self.runtime.config
        if not config.intro_shown and not completed_first_start:
            self.runtime.dialogs.show_message(
                "Before you start",
                "Check that Droid Alerts is watching the correct part of your Fortnite screen.",
                note=(
                    "Choose a capture source, open Diagnostics, then use Show Chat Region. "
                    "The red box should cover the droid spawn messages."
                ),
                accept_text="Okay",
                callback=self._intro_region_done,
            )
        else:
            self._maybe_offer_notifications()

    def _intro_region_done(self, _payload) -> None:
        self.runtime.dialogs.confirm(
            "Droid timers",
            "Show a small countdown strip above the game?",
            note="You can turn this off or reposition it at any time.",
            accept_text="Show timers",
            cancel_text="Not now",
            callback=self._intro_timers_done,
        )

    def _intro_timers_done(self, payload) -> None:
        enabled = payload is not None
        self.runtime.update_config(
            intro_shown=True,
            droid_timers_enabled=enabled,
            announce=False,
        )
        if enabled:
            self.dashboard.setTimersEnabled(True)
        self._maybe_offer_notifications()

    def _maybe_offer_notifications(self) -> None:
        config = self.runtime.config
        if config.notification_setup_prompted:
            return
        if (
            (config.ntfy_enabled and ntfy_configured(config))
            or (config.discord_enabled and discord_webhook_configured(config))
            or (config.phone_alerts_enabled and phone_alerts_configured(config))
        ):
            self.runtime.update_config(
                notification_setup_prompted=True,
                announce=False,
            )
            return
        self.runtime.dialogs.confirm(
            "Get alerts on your phone",
            "ntfy can push priority droid alerts to your phone for free.",
            note="Setup takes about two minutes and can also be done later.",
            accept_text="Set up ntfy",
            cancel_text="Maybe later",
            callback=self._notification_prompt_done,
        )

    def _notification_prompt_done(self, payload) -> None:
        self.runtime.update_config(
            notification_setup_prompted=True,
            announce=False,
        )
        if payload is not None:
            self.dashboard.configureChannel("ntfy")

    @Slot()
    def close(self) -> None:
        self._toast_timer.stop()
        self.runtime.shutdown()
