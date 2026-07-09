from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.capture import MonitorDescriptor, format_monitor_label


def main() -> int:
    primary = MonitorDescriptor(
        index=1,
        left=0,
        top=0,
        width=2560,
        height=1440,
        is_primary=True,
    )
    left = MonitorDescriptor(
        index=2,
        left=-1920,
        top=0,
        width=1920,
        height=1080,
    )
    named_right = MonitorDescriptor(
        index=3,
        left=2560,
        top=0,
        width=3440,
        height=1440,
        name="Odyssey G9",
    )

    expected = {
        format_monitor_label(primary, primary): "Monitor 1: 2560 × 1440 (Primary)",
        format_monitor_label(left, primary): "Monitor 2: 1920 × 1080 (Left)",
        format_monitor_label(named_right, primary): (
            "Monitor 3: 3440 × 1440 — Odyssey G9 (Right)"
        ),
    }
    failures = [
        f"expected {wanted!r}, got {actual!r}"
        for actual, wanted in expected.items()
        if actual != wanted
    ]
    if failures:
        print("monitor selection failures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("monitor selection OK: resolution, name, primary and position labels are clear")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
