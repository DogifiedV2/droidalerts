from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "src"))
sys.path.insert(0, str(BASE_DIR / "tests"))

import main as app_main
import run_eval
from droid_alerts.startup_splash import StartupSplash
from droid_alerts import gui


class FakeVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class StartupSplashTests(unittest.TestCase):
    def make_splash(self) -> StartupSplash:
        return StartupSplash(Mock(), Mock(), FakeVar("Loading tracker…"))

    def test_status_is_updated_and_flushed(self) -> None:
        splash = self.make_splash()

        splash.set_status("Loading dashboard…")

        self.assertEqual(splash.status_var.get(), "Loading dashboard…")
        splash.window.update_idletasks.assert_called_once_with()

    def test_blocking_startup_task_keeps_splash_responsive(self) -> None:
        splash = self.make_splash()

        result = splash.run_task(lambda: (time.sleep(0.04), 42)[1])

        self.assertEqual(result, 42)
        self.assertGreaterEqual(splash.window.update.call_count, 1)

    def test_task_failure_is_propagated(self) -> None:
        splash = self.make_splash()

        def fail() -> None:
            raise RuntimeError("startup failed")

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            splash.run_task(fail)

    def test_dashboard_reveal_does_not_dispatch_modal_startup_callbacks(self) -> None:
        root = Mock()
        splash = Mock(root=root)
        config = SimpleNamespace(ui_theme="signal_dark")
        app = SimpleNamespace(
            _page_prime_after_id=None,
            _unprimed_tabs=[],
            schedule_startup_prompts=Mock(),
        )

        with (
            patch.object(gui, "set_dpi_awareness"),
            patch.object(gui, "load_config", return_value=config),
            patch.object(gui, "DroidAlertsApp", return_value=app) as app_type,
        ):
            gui.run_gui(startup_splash=splash)

        app_type.assert_called_once_with(
            root,
            config=config,
            defer_startup_prompts=True,
        )
        root.update.assert_called_once_with()
        self.assertGreaterEqual(root.update_idletasks.call_count, 1)
        splash.close.assert_called_once_with()
        app.schedule_startup_prompts.assert_called_once_with(
            first_delay_ms=100,
        )
        root.mainloop.assert_called_once_with()


class CommandExitStatusTests(unittest.TestCase):
    def test_test_command_propagates_evaluation_status(self):
        for status in (0, 1):
            with self.subTest(status=status), patch.object(
                sys, "argv", ["main.py", "test"]
            ), patch.object(run_eval, "main", return_value=status):
                self.assertEqual(status, app_main.main())

    def test_evaluation_fails_when_a_manifest_fixture_is_missing(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(run_eval, "FIXTURES_DIR", Path(folder) / "fixtures"),
            patch.object(run_eval, "RESULTS_DIR", Path(folder) / "results"),
            patch.object(run_eval, "load_manifest", return_value={"missing.png": {"rows": []}}),
            patch.object(run_eval, "Pipeline"),
            patch.object(run_eval, "templates_dir", return_value=Path(folder)),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(1, run_eval.main())


if __name__ == "__main__":
    unittest.main()
