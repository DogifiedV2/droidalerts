from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from droid_alerts.belt.dev_logging import request_belt_miss_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preserve the previous fifteen seconds from an active Belt Tracker "
            "dev-mode session."
        )
    )
    parser.add_argument(
        "note",
        nargs="?",
        default="",
        help="Optional note describing the card that was missed.",
    )
    arguments = parser.parse_args()
    path = request_belt_miss_report(arguments.note)
    print(f"Miss report requested: {path}")
    print("Keep Belt Tracker dev mode running until the request is saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
