from __future__ import annotations

import json
import ssl
import sys
import tempfile
import unittest
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
        response.read.return_value = json.dumps(
            {
                "tag_name": "1.3.7",
                "name": "1.3.7",
                "html_url": "https://github.com/DogifiedV2/droidalerts/releases/tag/1.3.7",
                "zipball_url": "https://api.github.com/repos/DogifiedV2/droidalerts/zipball/1.3.7",
                "assets": [],
            }
        ).encode("utf-8")
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
        self.assertIs(expected_context, urlopen.call_args.kwargs["context"])

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


if __name__ == "__main__":
    unittest.main()
