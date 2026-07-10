from __future__ import annotations

import subprocess
import sys


FORTNITE_PROCESS = "FortniteClient-Win64-Shipping.exe"


def is_game_running() -> bool | None:
    if sys.platform != "win32":
        return None
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {FORTNITE_PROCESS}", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return FORTNITE_PROCESS.lower() in result.stdout.lower()
