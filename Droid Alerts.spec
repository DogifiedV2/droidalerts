# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

ROOT = Path(SPECPATH)
WINDOWS_HIDDEN_IMPORTS = (
    [
        "windows_capture",
        "windows_capture.windows_capture",
        "cv2_enumerate_cameras.windows_backend",
        "cv2_enumerate_cameras._windows_backend",
    ]
    if sys.platform == "win32"
    else []
)
QT_RUNTIME_EXCLUDES = (
    "PySide6/translations/",
    "PySide6/plugins/qmltooling/",
    "PySide6/Qt/translations/",
    "PySide6/Qt/plugins/qmltooling/",
)


def keep_runtime_entry(entry):
    destination = str(entry[0]).replace("\\", "/").lstrip("./")
    return not any(
        destination == prefix.rstrip("/") or destination.startswith(prefix)
        for prefix in QT_RUNTIME_EXCLUDES
    )

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "templates"), "templates"),
    (
        str(ROOT / "src" / "droid_alerts" / "ui" / "qml"),
        "droid_alerts/ui/qml",
    ),
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "droid_alerts.timers",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickControls2",
        "PySide6.QtWidgets",
        *WINDOWS_HIDDEN_IMPORTS,
    ],
    hookspath=[str(ROOT / "tools" / "pyinstaller_hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tests",
        "training_data",
    ],
    noarchive=False,
    optimize=0,
)
# Strip unused translations and QML tooling (about 10 MiB extracted).
a.binaries = [entry for entry in a.binaries if keep_runtime_entry(entry)]
a.datas = [entry for entry in a.datas if keep_runtime_entry(entry)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Droid Alerts",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "signals_icon.ico"),
    version=str(ROOT / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Droid Alerts",
)
