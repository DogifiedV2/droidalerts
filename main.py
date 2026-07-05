from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(prog="toolv2", description="Cross-PC Droid Tycoon alert detector.")
    sub = parser.add_subparsers(dest="command", required=True)

    watch = sub.add_parser("watch", help="Run the live watcher.")
    watch.add_argument("--debug", action="store_true", help="Verbose output + ROI/overlay dumps.")

    calibrate = sub.add_parser("calibrate", help="Drag-select the alert region (saved as percent ratios).")
    calibrate.add_argument("--capture-delay", type=float, default=0.0)
    calibrate.add_argument("--reset", action="store_true", help="Revert to automatic region detection.")

    sub.add_parser("build-templates", help="Rebuild templates/ from training_data/current_ui/.")

    test = sub.add_parser("test", help="Run the fixture evaluation harness.")
    test.add_argument("--verbose", action="store_true")
    test.add_argument("--dump-unlabeled", action="store_true", help="Dump per-candidate crops for review.")

    args = parser.parse_args()

    if args.command == "watch":
        from toolv2.watcher import run_watch

        run_watch(debug=args.debug)
    elif args.command == "calibrate":
        from toolv2.calibrate_cli import run_calibrate

        run_calibrate(capture_delay=max(0.0, args.capture_delay), reset=args.reset)
    elif args.command == "build-templates":
        sys.path.insert(0, str(BASE_DIR / "tools"))
        import build_templates

        build_templates.build_templates(
            BASE_DIR / "training_data" / "current_ui", BASE_DIR / "templates"
        )
    elif args.command == "test":
        sys.path.insert(0, str(BASE_DIR / "tests"))
        import run_eval

        run_eval.main(verbose=args.verbose, dump_unlabeled=args.dump_unlabeled)


if __name__ == "__main__":
    main()
