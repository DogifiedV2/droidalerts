# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "templates"), "templates"),
]
datas += collect_data_files("ttkbootstrap")
datas += collect_data_files(
    "rapidocr",
    includes=["config.yaml", "default_models.yaml", "models/*.onnx"],
)

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "droid_alerts.timers",
        "PIL._tkinter_finder",
        "rapidocr.inference_engine.onnxruntime",
        "ttkbootstrap",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "onnxruntime.quantization",
        "onnxruntime.tools",
        "onnxruntime.transformers",
        "rapidocr.inference_engine.mnn",
        "rapidocr.inference_engine.openvino",
        "rapidocr.inference_engine.paddle",
        "rapidocr.inference_engine.pytorch",
        "rapidocr.inference_engine.tensorrt",
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
    icon=str(ROOT / "assets" / "signals_icon.png"),
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
