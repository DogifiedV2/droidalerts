"""Guard: Droid Alerts must stay fully self-contained under its project folder.

Fails if any source module references OS user-data locations or resolves
paths outside the project root.
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

FORBIDDEN = (
    "LOCALAPPDATA",
    "APPDATA",
    "expanduser",
    "Path.home",
    "DroidWatcherData",
)


def main() -> int:
    failures: list[str] = []
    for path in sorted((BASE_DIR / "src" / "droid_alerts").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in text:
                failures.append(
                    f"{path.relative_to(BASE_DIR)}: contains forbidden token {token!r}"
                )

    from droid_alerts import config

    for name in ("project_root", "config_dir", "data_dir", "templates_dir", "sounds_dir", "user_sounds_dir"):
        resolved = getattr(config, name)()
        if BASE_DIR not in [resolved, *resolved.parents]:
            failures.append(f"config.{name}() resolves outside project root: {resolved}")

    if failures:
        print("PATH GUARD FAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("path guard OK: all paths project-root-relative, no OS user-data references")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
