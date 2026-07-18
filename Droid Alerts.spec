# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "templates"), "templates"),
]
datas += collect_data_files("ttkbootstrap")

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "droid_alerts.timers",
        "PIL._tkinter_finder",
        "ttkbootstrap",
        "windows_capture",
        "windows_capture.windows_capture",
        "cv2_enumerate_cameras.windows_backend",
        "cv2_enumerate_cameras._windows_backend",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tests",
        "training_data",
    ],
    noarchive=False,
    optimize=0,
)
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
