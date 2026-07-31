from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "src"))
sys.path.insert(0, str(BASE_DIR / "tests"))

import main as app_main
import run_eval
from droid_alerts import platform_ui
from droid_alerts.startup_splash import StartupSplash


class StartupSplashTests(unittest.TestCase):
    def make_splash(self) -> StartupSplash:
        return StartupSplash(Mock(), Mock(), Mock())

    def test_blocking_startup_task_keeps_splash_responsive(self) -> None:
        splash = self.make_splash()

        result = splash.run_task(lambda: (time.sleep(0.04), 42)[1])

        self.assertEqual(result, 42)
        self.assertGreaterEqual(splash.app.processEvents.call_count, 1)

    def test_main_loads_the_modular_qt_entrypoint(self) -> None:
        from droid_alerts.ui import run_gui

        self.assertIs(run_gui, app_main._load_gui())

    def test_windows_source_launch_sets_droid_alerts_taskbar_identity(self) -> None:
        with (
            patch.object(platform_ui.sys, "platform", "win32"),
            patch.object(platform_ui.ctypes, "windll", create=True) as windll,
        ):
            platform_ui.set_windows_app_identity()

        windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with(
            platform_ui.WINDOWS_APP_USER_MODEL_ID
        )


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
