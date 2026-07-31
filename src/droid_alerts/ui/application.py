from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QRect, QTimer, QUrl, Qt
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from .. import __version__
from ..capture import set_dpi_awareness
from ..config import assets_dir
from .app_controller import AppController
from .belt_controller import BeltController
from .capture_controller import CaptureController
from .dashboard_controller import DashboardController
from .deals_controller import DealsController
from .diagnostics_controller import DiagnosticsController
from .history_controller import HistoryController
from .overlays import close_all_overlays
from .runtime import ApplicationRuntime
from .settings_controller import SettingsController


DEFAULT_WINDOW_WIDTH = 1470
DEFAULT_WINDOW_HEIGHT = 1040
MINIMUM_WINDOW_WIDTH = 980
MINIMUM_WINDOW_HEIGHT = 650


def qml_dir() -> Path:
    return Path(__file__).resolve().parent / "qml"


def initial_window_geometry(available: QRect) -> QRect:
    width = min(
        DEFAULT_WINDOW_WIDTH,
        max(MINIMUM_WINDOW_WIDTH, available.width() - 32),
    )
    height = min(
        DEFAULT_WINDOW_HEIGHT,
        max(MINIMUM_WINDOW_HEIGHT, available.height() - 40),
    )
    return QRect(
        available.left() + (available.width() - width) // 2,
        available.top() + (available.height() - height) // 2,
        width,
        height,
    )


def run_gui(*, startup_splash=None) -> None:
    """Create the PySide6/Qt Quick application and enter its event loop."""
    set_dpi_awareness()
    QQuickStyle.setStyle("Basic")
    QCoreApplication.setOrganizationName("Droid Alerts")
    QCoreApplication.setApplicationName("Droid Alerts")
    QCoreApplication.setApplicationVersion(__version__)
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setFont(
        QFont("Segoe UI" if sys.platform == "win32" else "Avenir Next", 10)
    )

    icon_path = assets_dir() / "signals_icon.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    runtime = ApplicationRuntime()
    if os.environ.get("DROID_ALERTS_QML_ADVANCED") == "1":
        runtime.config.advanced_mode = True
    capture = CaptureController(runtime)
    dashboard = DashboardController(runtime, capture)
    belt = BeltController(runtime, capture, dashboard)
    deals = DealsController(runtime, dashboard)
    history = HistoryController(runtime)
    diagnostics = DiagnosticsController(
        runtime,
        capture,
        history_refresh=history.refresh,
    )
    settings = SettingsController(runtime, dashboard, capture)
    controller = AppController(runtime, dashboard, belt)

    def page_changed(page: str) -> None:
        if page == "belt":
            belt.pageOpened()
        elif page == "deals":
            deals.pageOpened()

    controller.pageChanged.connect(page_changed)
    dashboard.historyChanged.connect(history.refresh)
    belt.historyChanged.connect(history.refresh)
    deals.historyChanged.connect(history.refresh)
    settings.storageSettingsChanged.connect(diagnostics.storageSettingsChanged)
    runtime.register_shutdown(history.shutdown)
    runtime.register_shutdown(close_all_overlays)
    from ..timers import hide_droid_timers

    runtime.register_shutdown(hide_droid_timers)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(qml_dir()))
    context = engine.rootContext()
    context.setContextProperty("appController", controller)
    context.setContextProperty("captureController", capture)
    context.setContextProperty("dashboardController", dashboard)
    context.setContextProperty("beltController", belt)
    context.setContextProperty("dealsController", deals)
    context.setContextProperty("historyController", history)
    context.setContextProperty("diagnosticsController", diagnostics)
    context.setContextProperty("settingsController", settings)
    context.setContextProperty("dialogController", runtime.dialogs)
    context.setContextProperty(
        "appIconUrl",
        QUrl.fromLocalFile(str(icon_path)) if icon_path.is_file() else QUrl(),
    )

    engine.load(QUrl.fromLocalFile(str(qml_dir() / "Main.qml")))
    if not engine.rootObjects():
        runtime.shutdown()
        raise RuntimeError("The Droid Alerts QML interface could not be loaded.")

    window = engine.rootObjects()[0]
    runtime.main_window = window
    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is not None:
        window.setGeometry(initial_window_geometry(screen.availableGeometry()))
    if icon_path.is_file():
        window.setIcon(QIcon(str(icon_path)))

    if startup_splash is not None:
        try:
            startup_splash.close()
        except Exception:
            try:
                startup_splash.destroy()
            except Exception:
                pass

    runtime.start()
    if runtime.config.droid_timers_enabled:
        from ..timers import show_droid_timers

        show_droid_timers(
            runtime.config,
            monitor=capture.current_monitor(),
            on_reminder=dashboard.handleTimerReminder,
        )
    if runtime.config.start_watcher_on_launch:
        QTimer.singleShot(800, dashboard.startWatcher)
    if os.environ.get("DROID_ALERTS_SKIP_STARTUP_PROMPTS") != "1":
        QTimer.singleShot(700, controller.startupPrompts)
    preview_page = os.environ.get("DROID_ALERTS_QML_PAGE", "").strip()
    if preview_page:
        controller.selectPage(preview_page)
    preview_dialog = os.environ.get("DROID_ALERTS_QML_DIALOG", "").strip()
    if preview_dialog == "confirm":
        QTimer.singleShot(
            100,
            lambda: runtime.dialogs.confirm(
                "Confirm alert setup",
                "Use this display and send a test alert?",
                note="Nothing is shared unless the selected channel is enabled.",
                accept_text="Use display",
            ),
        )
    elif preview_dialog == "rules":
        QTimer.singleShot(
            100,
            lambda: runtime.dialogs.rules(
                "Belt Alert Rules",
                "Choose the minimum tier for each droid.",
                [
                    {
                        "id": name,
                        "label": name,
                        "detail": "Alert threshold",
                        "value": "Epic",
                    }
                    for name in ("ARG", "BD-1", "C1-10P", "Chopper", "GONK")
                ],
                [
                    {"id": "Off", "label": "Off"},
                    {"id": "Epic", "label": "Epic"},
                    {"id": "Legendary", "label": "Legendary"},
                    {"id": "Mythic", "label": "Mythic"},
                ],
            ),
        )
    elif preview_dialog == "channel":
        QTimer.singleShot(
            100,
            lambda: runtime.dialogs.channel_settings(
                "Configure Discord alerts",
                "Connect Discord and choose which alerts it should receive.",
                [
                    {
                        "id": "webhook",
                        "label": "Webhook URL",
                        "value": "https://discord.com/api/webhooks/…",
                    }
                ],
                [
                    {
                        "id": "chat:Beskar:Mythic",
                        "label": "Beskar · Mythic",
                        "selected": True,
                    },
                    {
                        "id": "chat:Galactic:Mythic",
                        "label": "Galactic · Mythic",
                        "selected": True,
                    },
                    {
                        "id": "limited_deals",
                        "label": "Limited Deal alerts",
                        "selected": False,
                    },
                ],
            ),
        )

    screenshot_path = os.environ.get("DROID_ALERTS_QML_SCREENSHOT", "").strip()
    if screenshot_path:
        def capture_window() -> None:
            window.grabWindow().save(screenshot_path)

        QTimer.singleShot(1200, capture_window)
    try:
        test_quit_ms = int(os.environ.get("DROID_ALERTS_TEST_QUIT_MS", "0"))
    except ValueError:
        test_quit_ms = 0
    if test_quit_ms > 0:
        QTimer.singleShot(test_quit_ms, app.quit)

    app.aboutToQuit.connect(runtime.shutdown)
    if owns_app or startup_splash is not None:
        app.exec()


__all__ = ["initial_window_geometry", "qml_dir", "run_gui"]
