# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
import rapidocr
from rapidocr.inference_engine.base import FileInfo
from rapidocr.utils.download_models import download_task
from rapidocr.utils.typings import EngineType, ModelType, OCRVersion, TaskType


ROOT = Path(SPECPATH)
RAPIDOCR_MODELS = Path(rapidocr.__file__).resolve().parent / "models"

# RapidOCR wheels bundle the default small recognizer only. Belt Tracker uses
# the much faster PP-OCRv6 tiny recognizer, so fetch its checksum-verified model
# while dependencies are already being prepared and bundle it into the release.
download_task(
    RAPIDOCR_MODELS,
    FileInfo(
        EngineType.ONNXRUNTIME,
        OCRVersion.PPOCRV6,
        TaskType.REC,
        "ch",
        ModelType.TINY,
    ),
)

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "templates"), "templates"),
]
datas += collect_data_files("ttkbootstrap")
datas += collect_data_files(
    "rapidocr",
    includes=[
        "config.yaml",
        "default_models.yaml",
        "models/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "models/PP-OCRv6_det_small.onnx",
        "models/PP-OCRv6_rec_tiny.onnx",
    ],
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
