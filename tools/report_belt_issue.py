from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from droid_alerts.belt.dev_logging import (
    BELT_ISSUE_KINDS,
    request_belt_issue_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preserve the previous fifteen seconds from an active Belt Tracker "
            "Blueprint Collection session."
        )
    )
    parser.add_argument(
        "kind",
        choices=BELT_ISSUE_KINDS,
        help="What went wrong: missed, wrong, duplicate, or other.",
    )
    parser.add_argument(
        "note",
        nargs="?",
        default="",
        help="Optional correct droid/family or a short description.",
    )
    arguments = parser.parse_args()
    path = request_belt_issue_report(arguments.kind, arguments.note)
    print(f"Belt issue report requested: {path}")
    print("Keep Blueprint Collection Mode running until the request is saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
