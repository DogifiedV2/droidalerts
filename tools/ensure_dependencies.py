"""Install source-launch dependencies only when the environment changes."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import subprocess
import sys


BASE_DIR = Path(__file__).resolve().parents[1]
REQUIREMENTS_PATH = BASE_DIR / "requirements.txt"
MARKER_PATH = BASE_DIR / "config" / ".requirements-ready"
CACHE_VERSION = "2"


def required_modules() -> tuple[str, ...]:
    modules = [
        "cv2",
        "numpy",
        "mss",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickControls2",
        "PySide6.QtWidgets",
        "certifi",
    ]
    if sys.platform == "win32":
        modules.extend(("windows_capture", "cv2_enumerate_cameras"))
    elif sys.platform == "darwin":
        modules.append("AppKit")
    return tuple(modules)


def required_distributions() -> tuple[str, ...]:
    distributions = [
        "opencv-python",
        "numpy",
        "mss",
        "PySide6-Essentials",
        "certifi",
    ]
    if sys.platform == "win32":
        distributions.extend(("windows-capture", "cv2-enumerate-cameras"))
    elif sys.platform == "darwin":
        distributions.append("pyobjc-framework-Cocoa")
    return tuple(distributions)


def dependency_key() -> str:
    digest = hashlib.sha256()
    digest.update(REQUIREMENTS_PATH.read_bytes())
    digest.update(str(Path(sys.executable).resolve()).encode("utf-8"))
    digest.update(sys.version.encode("utf-8"))
    digest.update(sys.platform.encode("ascii"))
    digest.update(CACHE_VERSION.encode("ascii"))
    for distribution in required_distributions():
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            version = "missing"
        digest.update(f"{distribution}={version}".encode("utf-8"))
    return digest.hexdigest()


def dependencies_available() -> bool:
    try:
        return all(
            importlib.util.find_spec(module) is not None for module in required_modules()
        )
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def main() -> int:
    try:
        key = dependency_key()
    except OSError as exc:
        print(f"Could not read requirements.txt: {exc}")
        return 1

    try:
        cached_key = MARKER_PATH.read_text(encoding="ascii").strip()
    except OSError:
        cached_key = ""
    if cached_key == key and dependencies_available():
        return 0

    print("Installing or updating Droid Alerts dependencies...")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(REQUIREMENTS_PATH),
            "--quiet",
            "--disable-pip-version-check",
        ],
        cwd=BASE_DIR,
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    if not dependencies_available():
        print(
            "Dependency installation completed, but required modules are still unavailable."
        )
        return 1

    try:
        MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = MARKER_PATH.with_suffix(".tmp")
        temporary_path.write_text(dependency_key() + "\n", encoding="ascii")
        os.replace(temporary_path, MARKER_PATH)
    except OSError as exc:
        # The app can still launch. A read-only marker only means pip will be
        # asked to verify the environment again on the next source launch.
        print(f"Warning: could not cache dependency state: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
