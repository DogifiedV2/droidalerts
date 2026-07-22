from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
MANIFEST = BASE_DIR / "tests" / "data_report_manifest.json"


class DataReportManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
        cls.entries = cls.payload["entries"]

    def test_every_in_scope_submission_has_one_reviewed_label(self) -> None:
        ids = [entry["submissionId"] for entry in self.entries]

        self.assertEqual(1623, len(ids))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            {"Beskar", "Galactic", "Rainbow"},
            {entry["droid"] for entry in self.entries},
        )
        self.assertTrue(
            all(entry["status"] in {"real", "false", "uncertain"} for entry in self.entries)
        )

    def test_declared_review_summary_matches_entries(self) -> None:
        counts = Counter(entry["status"] for entry in self.entries)

        self.assertEqual(
            {key: counts[key] for key in ("real", "false", "uncertain")},
            self.payload["reviewSummary"],
        )

    def test_correction_lists_match_final_status(self) -> None:
        statuses = {entry["submissionId"]: entry["status"] for entry in self.entries}
        corrections = self.payload["corrections"]

        self.assertTrue(all(statuses[item] == "real" for item in corrections["falseToReal"]))
        self.assertTrue(all(statuses[item] == "false" for item in corrections["realToFalse"]))
        self.assertEqual(len(corrections["realToFalse"]), len(set(corrections["realToFalse"])))


if __name__ == "__main__":
    unittest.main()
