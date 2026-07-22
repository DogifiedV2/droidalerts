"""Regenerate FALSE_DETECTION_DATA_REPORT.md from reviewed ground truth."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
MANIFEST = BASE_DIR / "tests" / "data_report_manifest.json"
OUTPUT = BASE_DIR / "FALSE_DETECTION_DATA_REPORT.md"
FAMILIES = ("Beskar", "Galactic", "Rainbow")
RARITIES = {
    "Beskar": ("Epic", "Legendary", "Mythic"),
    "Galactic": ("Common", "Rare", "Epic", "Legendary", "Mythic"),
    "Rainbow": ("Epic", "Legendary", "Mythic"),
}


def row_counts(entries: list[dict[str, object]]) -> Counter[tuple[str, str]]:
    return Counter((str(entry["droid"]), str(entry["status"])) for entry in entries)


def category_counts(entries: list[dict[str, object]]) -> Counter[tuple[str, str, str]]:
    return Counter(
        (str(entry["droid"]), str(entry["rarity"]), str(entry["status"]))
        for entry in entries
    )


def generate() -> str:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    entries = payload["entries"]
    by_family = row_counts(entries)
    by_category = category_counts(entries)
    summary = Counter(str(entry["status"]) for entry in entries)
    root = Path(payload["sourceRootHint"])
    false_to_real = payload["corrections"]["falseToReal"]
    real_to_false = payload["corrections"]["realToFalse"]

    lines = [
        "# Droid Alerts Detection Classification Report",
        "",
        f"**Dataset:** `{root}`  ",
        "**Scope:** Beskar, Galactic, and Rainbow chat detections  ",
        "**Canonical ground truth:** `tests/data_report_manifest.json`",
        "",
        "## Classification rule",
        "",
        "- **Real:** The captured image visibly contains the exact droid family and rarity encoded by the detection folder.",
        "- **False:** That exact family/rarity is absent, even if another valid alert is visible.",
        "- **Uncertain:** The image is too obscured to label reliably and is excluded from scoring.",
        "",
        "## Review correction",
        "",
        "The original report treated recorded alert metadata as truth. Visual review found metadata-label contradictions, so the reviewed manifest is now authoritative. "
        f"It contains {len(false_to_real) + len(real_to_false)} corrected labels: "
        f"{len(false_to_real)} false-to-real and {len(real_to_false)} real-to-false.",
        "",
        "## Overall summary",
        "",
        "| Family | Reviewed | Real | False | Uncertain |",
        "|---|---:|---:|---:|---:|",
    ]
    for family in FAMILIES:
        real = by_family[(family, "real")]
        false = by_family[(family, "false")]
        uncertain = by_family[(family, "uncertain")]
        lines.append(f"| {family} | {real + false + uncertain} | {real} | {false} | {uncertain} |")
    lines.append(
        f"| **Total** | **{len(entries)}** | **{summary['real']}** | **{summary['false']}** | **{summary['uncertain']}** |"
    )

    lines.extend(["", "## Per-category results", ""])
    for family in FAMILIES:
        lines.extend(
            [
                f"### {family}",
                "",
                "| Detection | Reviewed | Real | False | Uncertain |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for rarity in RARITIES[family]:
            real = by_category[(family, rarity, "real")]
            false = by_category[(family, rarity, "false")]
            uncertain = by_category[(family, rarity, "uncertain")]
            lines.append(
                f"| {family} {rarity} | {real + false + uncertain} | {real} | {false} | {uncertain} |"
            )
        lines.append("")

    lines.extend(
        [
            "## False-detection locations",
            "",
            "Each row is a reviewed negative for the exact recorded target. Paths are relative to the dataset root above.",
            "",
        ]
    )
    for family in FAMILIES:
        false_entries = [
            entry for entry in entries if entry["droid"] == family and entry["status"] == "false"
        ]
        false_entries.sort(key=lambda entry: (entry["rarity"], entry["relativePath"]))
        lines.extend(
            [
                f"### {family} false detections ({len(false_entries)})",
                "",
                "| # | Recorded target | Submission | Resolution | Folder |",
                "|---:|---|---|---:|---|",
            ]
        )
        for index, entry in enumerate(false_entries, start=1):
            width, height = entry["resolution"]
            lines.append(
                f"| {index} | {entry['droid']} {entry['rarity']} | `{entry['submissionId']}` | "
                f"{width}x{height} | `{entry['relativePath']}` |"
            )
        lines.append("")

    uncertain = [entry for entry in entries if entry["status"] == "uncertain"]
    lines.extend(
        [
            "## Uncertain detections requiring human review",
            "",
            "| Recorded target | Submission | Resolution | Folder |",
            "|---|---|---:|---|",
        ]
    )
    for entry in uncertain:
        width, height = entry["resolution"]
        lines.append(
            f"| {entry['droid']} {entry['rarity']} | `{entry['submissionId']}` | "
            f"{width}x{height} | `{entry['relativePath']}` |"
        )

    lines.extend(
        [
            "",
            "## Verification use",
            "",
            "```bash",
            "PYTHONPATH=src python3 tests/run_debug_batch_eval.py '/Users/rubenvancraenenbroeck/Downloads/data 2'",
            "```",
            "",
            "The evaluator requires every real target to remain detected, every false target to be absent, and skips uncertain rows. The 106 Diamond Mythic submissions remain outside this report's requested scope.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    OUTPUT.write_text(generate(), encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
