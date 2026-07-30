from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from droid_alerts.belt.dev_capture import export_dev_session, latest_dev_session
from droid_alerts.belt.dev_logging import belt_dev_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a complete Belt Tracker Developer Mode session."
    )
    parser.add_argument(
        "session",
        nargs="?",
        type=Path,
        help="Session folder. Defaults to the newest local session.",
    )
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args()
    session = arguments.session
    if session is None:
        session = latest_dev_session(belt_dev_dir())
        if session is None:
            parser.error("No Belt Developer Mode sessions were found")
    output = export_dev_session(session, arguments.output)
    print(f"Belt Developer Mode export: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
