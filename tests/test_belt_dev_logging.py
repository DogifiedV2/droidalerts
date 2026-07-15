from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.belt.dev_logging import BeltDevLogger
from droid_alerts.config import AppConfig
from droid_alerts.diagnostics import create_support_bundle


class BeltDevLoggerTests(unittest.TestCase):
    def test_enabled_logger_writes_json_and_rate_limited_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (
                patch("droid_alerts.belt.dev_logging.belt_dev_dir", return_value=root / "belt_dev"),
                patch("droid_alerts.belt.dev_logging.data_dir", return_value=root),
            ):
                logger = BeltDevLogger(True)
                logger.log("scan", ocr_seconds=5.0)
                frame = np.zeros((20, 30, 3), dtype=np.uint8)
                first = logger.save_frame(frame, frame_number=1, now=10.0)
                second = logger.save_frame(frame, frame_number=2, now=11.0)

                self.assertEqual("frame_000001.png", first)
                self.assertEqual("", second)
                self.assertTrue((logger.session_dir / first).exists())
                record = json.loads(logger.log_path.read_text(encoding="utf-8"))
                self.assertEqual("scan", record["event"])
                self.assertEqual(5.0, record["ocr_seconds"])
                self.assertTrue(logger.relative_path().startswith("belt_dev/session_"))

    def test_disabled_logger_creates_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch(
                "droid_alerts.belt.dev_logging.belt_dev_dir",
                return_value=root / "belt_dev",
            ):
                logger = BeltDevLogger(False)
                logger.log("scan")

            self.assertFalse((root / "belt_dev").exists())


class BeltDevSupportBundleTests(unittest.TestCase):
    def test_support_bundle_includes_latest_belt_session_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "data"
            belt_dev = data / "belt_dev"
            old_session = belt_dev / "session_old"
            new_session = belt_dev / "session_new"
            old_session.mkdir(parents=True)
            new_session.mkdir(parents=True)
            (old_session / "belt_dev.jsonl").write_text('{"event":"old"}\n', encoding="utf-8")
            new_log = new_session / "belt_dev.jsonl"
            new_log.write_text('{"event":"new"}\n', encoding="utf-8")
            for index in range(6):
                (new_session / f"frame_{index:06d}.png").write_bytes(b"png")
            # Ensure the intended session wins even on coarse timestamp filesystems.
            old_log = old_session / "belt_dev.jsonl"
            old_log.touch()
            new_log.touch()
            old_log_mtime = old_log.stat().st_mtime - 10
            old_log.chmod(0o644)
            import os

            os.utime(old_log, (old_log_mtime, old_log_mtime))

            empty = root / "empty"
            empty.mkdir()
            with (
                patch("droid_alerts.diagnostics.data_dir", return_value=data),
                patch("droid_alerts.diagnostics.belt_dev_dir", return_value=belt_dev),
                patch("droid_alerts.diagnostics.logs_dir", return_value=empty),
                patch("droid_alerts.diagnostics.debug_dir", return_value=empty),
                patch("droid_alerts.diagnostics.calibration_path", return_value=empty / "calibration.json"),
                patch("droid_alerts.diagnostics.belt_regions_path", return_value=empty / "belt_regions.json"),
                patch("droid_alerts.diagnostics._safe_monitors", return_value=[]),
            ):
                bundle_path = create_support_bundle(AppConfig())

            with zipfile.ZipFile(bundle_path) as bundle:
                names = bundle.namelist()
                self.assertIn("belt_dev/belt_dev.jsonl", names)
                self.assertIn('"event":"new"', bundle.read("belt_dev/belt_dev.jsonl").decode())
                self.assertEqual(
                    4,
                    len([name for name in names if name.startswith("belt_dev/frames/")]),
                )


if __name__ == "__main__":
    unittest.main()
