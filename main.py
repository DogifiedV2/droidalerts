from __future__ import annotations

import argparse
import multiprocessing
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(prog="droid-alerts", description="Cross-PC Droid Tycoon alert detector.")
    sub = parser.add_subparsers(dest="command")

    watch = sub.add_parser("watch", help="Run the live watcher.")
    watch.add_argument("--debug", action="store_true", help="Verbose output + ROI/overlay dumps.")
    watch.add_argument(
        "--extra-checks",
        action="store_true",
        help="Enable the washed-out color/HDR fallback checks for this run.",
    )

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
        if args.extra_checks:
            config.extra_checks = True
        run_watch(debug=args.debug, config=config)
    elif command == "gui":
        from droid_alerts.gui import run_gui

        run_gui()
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

        run_eval.main(verbose=args.verbose, dump_unlabeled=args.dump_unlabeled)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
