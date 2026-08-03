from __future__ import annotations

import io
import json
import ssl
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts import notifications, updater
from droid_alerts.network import certifi_ssl_context


class BundledCertificateTests(unittest.TestCase):
    def test_shared_context_loads_the_bundled_ca_store(self):
        context = certifi_ssl_context()

        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        self.assertTrue(context.check_hostname)
        self.assertTrue(context.get_ca_certs())

    def test_update_check_uses_the_bundled_ca_context(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = (
            "https://github.com/DogifiedV2/droidalerts/releases/tag/1.3.7"
        )
        expected_context = object()

        with (
            patch.object(
                notifications,
                "certifi_ssl_context",
                return_value=expected_context,
            ),
            patch.object(
                notifications.urllib.request,
                "urlopen",
                return_value=response,
            ) as urlopen,
        ):
            release = notifications.latest_release_info("DogifiedV2/droidalerts")

        self.assertEqual("1.3.7", release["tag"])
        self.assertEqual(
            "https://github.com/DogifiedV2/droidalerts/releases/download/1.3.7/"
            "DroidAlerts-Windows.zip",
            release["package_zip_url"],
        )
        self.assertEqual("HEAD", urlopen.call_args.args[0].method)
        self.assertIs(expected_context, urlopen.call_args.kwargs["context"])

    def test_update_check_explains_github_rate_limits(self):
        error = notifications.urllib.error.HTTPError(
            "https://github.com/DogifiedV2/droidalerts/releases/latest",
            403,
            "rate limit exceeded",
            {},
            None,
        )
        with patch.object(
            notifications.urllib.request,
            "urlopen",
            side_effect=error,
        ):
            with self.assertRaisesRegex(RuntimeError, "temporarily limited"):
                notifications.latest_release_info("DogifiedV2/droidalerts")

    def test_update_download_uses_the_bundled_ca_context(self):
        expected_context = object()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(updater, "data_dir", return_value=Path(directory)),
            patch.object(
                updater,
                "certifi_ssl_context",
                return_value=expected_context,
            ),
            patch.object(
                updater.urllib.request,
                "urlopen",
                side_effect=RuntimeError("stop after opening"),
            ) as urlopen,
        ):
            with self.assertRaisesRegex(RuntimeError, "stop after opening"):
                updater.download_and_install_update(
                    "https://example.test/DroidAlerts.zip",
                    "1.3.7",
                )

        self.assertIs(expected_context, urlopen.call_args.kwargs["context"])


class SourceUpdaterInstallTests(unittest.TestCase):
    def test_update_archive_rejects_path_traversal(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("../escape.txt", "nope")
        payload.seek(0)

        with tempfile.TemporaryDirectory() as folder, zipfile.ZipFile(payload) as archive:
            with self.assertRaises(RuntimeError):
                updater._safe_extract(archive, Path(folder) / "extract")

    def make_release(self, root: Path) -> Path:
        source = root / "release"
        for directory in ("src/droid_alerts", "assets", "templates"):
            (source / directory).mkdir(parents=True)
        (source / "main.py").write_text("new main", encoding="utf-8")
        (source / "src/droid_alerts/new.py").write_text("new", encoding="utf-8")
        (source / "assets/icon.txt").write_text("new icon", encoding="utf-8")
        (source / "templates/current.txt").write_text("current", encoding="utf-8")
        return source

    def test_managed_directories_are_replaced_and_local_paths_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = self.make_release(root)
            target = root / "target"
            (target / "src/droid_alerts").mkdir(parents=True)
            (target / "src/droid_alerts/old.py").write_text("stale", encoding="utf-8")
            (target / "templates").mkdir(parents=True)
            (target / "templates/stale.txt").write_text("stale", encoding="utf-8")
            for name in ("config", "data", "training_data", ".git", ".idea", "build", "dist"):
                (target / name).mkdir(parents=True)
                (target / name / "sentinel").write_text(name, encoding="utf-8")

            count = updater._copy_update_files(source, target)

            self.assertGreaterEqual(count, 4)
            self.assertFalse((target / "src/droid_alerts/old.py").exists())
            self.assertFalse((target / "templates/stale.txt").exists())
            self.assertEqual("new", (target / "src/droid_alerts/new.py").read_text())
            self.assertEqual("new main", (target / "main.py").read_text())
            for name in ("config", "data", "training_data", ".git", ".idea", "build", "dist"):
                self.assertEqual(name, (target / name / "sentinel").read_text())

    def test_mid_swap_failure_restores_complete_managed_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = self.make_release(root)
            target = root / "target"
            for relative, contents in {
                "main.py": "old main",
                "src/droid_alerts/old.py": "old src",
                "assets/old.txt": "old asset",
                "templates/old.txt": "old template",
            }.items():
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents, encoding="utf-8")
            original = {
                path.relative_to(target).as_posix(): path.read_bytes()
                for path in target.rglob("*") if path.is_file()
            }
            real_move = updater.shutil.move
            calls = 0

            def flaky_move(src, dst, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise OSError("injected swap failure")
                return real_move(src, dst, *args, **kwargs)

            with patch.object(updater.shutil, "move", side_effect=flaky_move):
                with self.assertRaisesRegex(RuntimeError, "previous files restored"):
                    updater._copy_update_files(source, target)

            restored = {
                path.relative_to(target).as_posix(): path.read_bytes()
                for path in target.rglob("*") if path.is_file()
            }
            self.assertEqual(original, restored)

    def test_missing_required_release_fails_before_target_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "release"
            source.mkdir()
            (source / "main.py").write_text("new", encoding="utf-8")
            target = root / "target"
            target.mkdir()
            sentinel = target / "main.py"
            sentinel.write_text("old", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "missing required path"):
                updater._copy_update_files(source, target)
            self.assertEqual("old", sentinel.read_text())

    def test_managed_symlink_cannot_escape_release_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = self.make_release(root)
            outside = root / "credential.txt"
            outside.write_text("secret", encoding="utf-8")
            link = source / "templates/leak.txt"
            try:
                link.symlink_to(outside)
            except OSError:
                self.skipTest("symlinks are unavailable")
            target = root / "target"
            with self.assertRaisesRegex(RuntimeError, "unsafe managed path"):
                updater._copy_update_files(source, target)
            self.assertFalse((target / "templates").exists())


if __name__ == "__main__":
    unittest.main()
