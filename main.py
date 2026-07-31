from __future__ import annotations

import argparse
import importlib.util
import multiprocessing
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "src"))


def _source_dependencies_ready(splash=None) -> bool:
    """Install missing source dependencies before loading Qt."""
    if getattr(sys, "frozen", False):
        return True
    checker_path = BASE_DIR / "tools" / "ensure_dependencies.py"
    if not checker_path.is_file():
        return True
    spec = importlib.util.spec_from_file_location(
        "droid_alerts_dependency_check", checker_path
    )
    if spec is None or spec.loader is None:
        return False
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    callback = module.main
    return (splash.run_task(callback) if splash is not None else callback()) == 0


def main() -> int | None:
    parser = argparse.ArgumentParser(prog="droid-alerts", description="Cross-PC Droid Tycoon alert detector.")
    sub = parser.add_subparsers(dest="command")

    watch = sub.add_parser("watch", help="Run the live watcher.")
    watch.add_argument("--debug", action="store_true", help="Verbose output + ROI/overlay dumps.")

    sub.add_parser("gui", help="Open the interactive GUI.")

    calibrate = sub.add_parser("calibrate", help="Drag-select the alert region (saved as percent ratios).")
    calibrate.add_argument("--capture-delay", type=float, default=0.0)
    calibrate.add_argument("--reset", action="store_true", help="Revert to automatic region detection.")

    sub.add_parser("build-templates", help="Rebuild templates/ from training_data/current_ui/.")

    test = sub.add_parser("test", help="Run the fixture evaluation harness.")
    test.add_argument("--verbose", action="store_true")
    test.add_argument("--dump-unlabeled", action="store_true", help="Dump per-candidate crops for review.")

    args = parser.parse_args()
    command = args.command or "gui"

    if command == "watch":
        from droid_alerts.config import load_config
        from droid_alerts.watcher import run_watch

        config = load_config()
        run_watch(debug=args.debug, config=config)
    elif command == "gui":
        splash = None
        try:
            if not _source_dependencies_ready():
                raise SystemExit("Droid Alerts dependencies could not be installed.")
            from droid_alerts.platform_ui import set_windows_app_identity

            set_windows_app_identity()
            from droid_alerts.startup_splash import create_startup_splash

            splash = create_startup_splash()
            if splash is not None:
                splash.set_status("Loading dashboard…")
            run_gui = _load_gui()
            run_gui(startup_splash=splash)
        except BaseException:
            if splash is not None:
                splash.destroy()
            raise
    elif command == "calibrate":
        from droid_alerts.calibrate_cli import run_calibrate

        run_calibrate(capture_delay=max(0.0, args.capture_delay), reset=args.reset)
    elif command == "build-templates":
        sys.path.insert(0, str(BASE_DIR / "tools"))
        import build_templates

        build_templates.build_templates(
            BASE_DIR / "training_data" / "current_ui", BASE_DIR / "templates"
        )
    elif command == "test":
        sys.path.insert(0, str(BASE_DIR / "tests"))
        import run_eval

        return run_eval.main(verbose=args.verbose, dump_unlabeled=args.dump_unlabeled)


def _load_gui():
    from droid_alerts.ui import run_gui

    return run_gui


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
