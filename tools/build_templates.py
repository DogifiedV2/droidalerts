"""Rebuild ToolV2 templates from training_data/current_ui screenshots.

Template builder using the project's crop geometry and preprocessing. Additions:
  - crops from the five real annotated captures (IMG_6604-6608), which fill
    the Rainbow/Diamond Mythic and Beskar Legendary gaps;
  - overlay-line cleanup for those captures (the old run drew vivid debug
    rectangles exactly on the row bounds);
  - templates/manifest.json recording reference-scale metadata.

All sources are at reference scale (44px rows / 2560x1440 capture); this was
verified by measuring text-band heights (15-20px) and row spacing (~33px)
against the original training crops.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.classifier import preprocess_for_template, trim_blank_edges  # noqa: E402
from droid_alerts.config import REFERENCE_ROW_HEIGHT_PX, REFERENCE_SCREEN_HEIGHT  # noqa: E402
from droid_alerts.overlay_cleanup import clean_overlay  # noqa: E402

RARITY_ROI_X1 = 180
RARITY_ROI_X2 = 410
ROW_HEIGHT = REFERENCE_ROW_HEIGHT_PX

# Sources whose old debug overlays must be erased before cropping.
OVERLAYED_SOURCES = {"IMG_6604.png", "IMG_6605.png", "IMG_6606.png", "IMG_6607.png", "IMG_6608.png"}

# (rarity, source, (x1, y1, x2, y2)): rarity-word shape templates.
REFERENCE_CROPS: list[tuple[str, str, tuple[int, int, int, int]]] = [
    ("Common", "beskarcommon.png", (180, 20, 410, 64)),
    ("Common", "beskarcommonepicmythic.png", (180, 40, 410, 84)),
    ("Common", "diamondcommon.png", (180, 18, 410, 62)),
    ("Common", "diamondcommon2.png", (180, 38, 410, 82)),
    ("Common", "rainbowcommon.png", (180, 13, 410, 57)),
    ("Common", "rainbowepic1.png", (180, 55, 410, 99)),
    ("Common", "IMG_6605.png", (180, 93, 410, 137)),
    ("Common", "IMG_6605.png", (180, 127, 410, 171)),
    ("Common", "IMG_6608.png", (180, 189, 410, 233)),
    ("Rare", "beskarrare1.png", (180, 23, 410, 67)),
    ("Rare", "beskarrare2.png", (180, 43, 410, 87)),
    ("Rare", "beskarrare3.png", (180, 22, 410, 66)),
    ("Rare", "beskarrare_live_222501.png", (180, 98, 410, 142)),
    ("Rare", "diamond_rare_20260702.png", (180, 65, 410, 109)),
    ("Rare", "IMG_6607.png", (180, 156, 410, 200)),
    ("Epic", "beskarcommonepicmythic.png", (180, 72, 410, 116)),
    ("Epic", "diamondepic.png", (180, 48, 410, 92)),
    ("Epic", "rainbowepic.png", (180, 43, 410, 87)),
    ("Epic", "rainbowepic1.png", (180, 24, 410, 68)),
    ("Epic", "rainbowlegendaries.png", (180, 107, 410, 151)),
    ("Legendary", "rainbowlegendary.png", (180, 7, 430, 51)),
    ("Legendary", "rainbowlegendaries.png", (180, 43, 430, 87)),
    ("Legendary", "rainbowlegendaries.png", (180, 75, 430, 119)),
    ("Legendary", "IMG_6605.png", (180, 156, 430, 200)),
    ("Legendary", "IMG_6606.png", (180, 156, 430, 200)),
    ("Mythic", "beskarcommonepicmythic.png", (180, 104, 410, 148)),
    ("Mythic", "IMG_6604.png", (180, 93, 410, 137)),
    ("Mythic", "IMG_6607.png", (180, 189, 410, 233)),
    ("Mythic", "IMG_6608.png", (180, 157, 410, 201)),
]

# (droid, rarity, name, source, row_top_y): full rarity-ROI templates.
RARITY_ROI_CROPS: list[tuple[str, str, str, str, int]] = [
    ("Beskar", "Common", "BC_beskarcommon", "beskarcommon.png", 20),
    ("Beskar", "Common", "BC_stack", "beskarcommonepicmythic.png", 40),
    ("Beskar", "Common", "BC_img6605", "IMG_6605.png", 127),
    ("Beskar", "Rare", "BR_1", "beskarrare1.png", 23),
    ("Beskar", "Rare", "BR_2", "beskarrare2.png", 43),
    ("Beskar", "Rare", "BR_3", "beskarrare3.png", 22),
    ("Beskar", "Rare", "BR_live_222501", "beskarrare_live_222501.png", 98),
    ("Beskar", "Epic", "BE_stack", "beskarcommonepicmythic.png", 72),
    ("Beskar", "Legendary", "BL_img6605", "IMG_6605.png", 156),
    ("Beskar", "Legendary", "BL_img6606", "IMG_6606.png", 156),
    ("Beskar", "Mythic", "BM_stack", "beskarcommonepicmythic.png", 104),
    ("Diamond", "Common", "DC_1", "diamondcommon.png", 18),
    ("Diamond", "Common", "DC_2", "diamondcommon2.png", 38),
    ("Diamond", "Common", "DC_rainbowepic1", "rainbowepic1.png", 55),
    ("Diamond", "Rare", "DR_1", "diamond_rare_20260702.png", 65),
    ("Diamond", "Rare", "DR_img6607", "IMG_6607.png", 156),
    ("Diamond", "Epic", "DE_1", "diamondepic.png", 48),
    ("Diamond", "Mythic", "DM_img6607", "IMG_6607.png", 189),
    ("Rainbow", "Common", "RC_1", "rainbowcommon.png", 13),
    ("Rainbow", "Common", "RC_img6605", "IMG_6605.png", 93),
    ("Rainbow", "Common", "RC_img6608", "IMG_6608.png", 189),
    ("Rainbow", "Epic", "RE_1", "rainbowepic.png", 43),
    ("Rainbow", "Epic", "RE_2", "rainbowepic1.png", 24),
    ("Rainbow", "Epic", "RE_legendaries", "rainbowlegendaries.png", 107),
    ("Rainbow", "Legendary", "RL_standalone", "rainbowlegendary.png", 7),
    ("Rainbow", "Legendary", "RL_1", "rainbowlegendaries.png", 43),
    ("Rainbow", "Legendary", "RL_2", "rainbowlegendaries.png", 75),
    ("Rainbow", "Mythic", "RM_img6604", "IMG_6604.png", 93),
    ("Rainbow", "Mythic", "RM_img6608", "IMG_6608.png", 157),
]

CRAFTED_PHRASE_CROPS: list[tuple[str, str, tuple[int, int, int, int]]] = []


def read_source(source_root: Path, relative_path: str) -> tuple[Path, np.ndarray]:
    source_path = source_root / relative_path
    image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(source_path)
    if source_path.name in OVERLAYED_SOURCES:
        image = clean_overlay(image)
    return source_path, image


def clear_pngs(path: Path, *, preserve_prefixes: tuple[str, ...] = ()) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for old in path.glob("*.png"):
        if old.name.startswith(preserve_prefixes):
            continue
        old.unlink()


def build_templates(source_root: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_pngs(out_dir)
    roi_dir = out_dir / "rarity_rois"
    # Galactic prototypes are built from the separately reviewed debug-data
    # corpus. A normal current_ui rebuild must not silently delete them.
    clear_pngs(roi_dir, preserve_prefixes=("Galactic__",))
    crafted_dir = out_dir / "crafted_phrases"
    clear_pngs(crafted_dir)

    written: list[dict[str, object]] = []

    for index, (rarity, relative_path, box) in enumerate(REFERENCE_CROPS, start=1):
        source_path, image = read_source(source_root, relative_path)
        x1, y1, x2, y2 = box
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            raise ValueError(f"Empty reference crop for {rarity}: {source_path} {box}")
        templ = preprocess_for_template(crop, height=42)
        templ = trim_blank_edges(templ, pad=2)
        out_path = out_dir / f"{rarity}__{index:02d}.png"
        cv2.imwrite(str(out_path), templ)
        written.append({"kind": "rarity_word", "file": out_path.name, "source": relative_path, "box": list(box)})
        print(f"wrote {out_path.name} from {source_path.name} {box}")

    for droid, rarity, name, relative_path, y in RARITY_ROI_CROPS:
        source_path, image = read_source(source_root, relative_path)
        y = max(0, min(image.shape[0] - ROW_HEIGHT, y))
        crop = image[y : y + ROW_HEIGHT, RARITY_ROI_X1:RARITY_ROI_X2]
        if crop.size == 0:
            raise ValueError(f"Empty rarity ROI for {droid} {rarity}: {source_path} row {y}")
        templ = preprocess_for_template(crop)
        out_path = roi_dir / f"{droid}__{rarity}__{name}.png"
        cv2.imwrite(str(out_path), templ)
        written.append({"kind": "rarity_roi", "file": f"rarity_rois/{out_path.name}", "source": relative_path, "row_y": y})
        print(f"wrote rarity_rois/{out_path.name} from {source_path.name} row {y}")

    for name, relative_path, box in CRAFTED_PHRASE_CROPS:
        source_path, image = read_source(source_root, relative_path)
        x1, y1, x2, y2 = box
        crop = image[y1:y2, x1:x2]
        templ = trim_blank_edges(preprocess_for_template(crop), pad=2)
        out_path = crafted_dir / f"{name}.png"
        cv2.imwrite(str(out_path), templ)
        written.append({"kind": "crafted_phrase", "file": f"crafted_phrases/{out_path.name}", "source": relative_path, "box": list(box)})

    manifest = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reference_row_height_px": ROW_HEIGHT,
        "reference_screen_height": REFERENCE_SCREEN_HEIGHT,
        "rarity_roi_x": [RARITY_ROI_X1, RARITY_ROI_X2],
        "note": "All templates are at reference scale; normalize inputs before matching.",
        "known_gaps": ["Beskar Mythic has no real full-row capture yet (template from synthetic stack only)."],
        "templates": written,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote manifest.json ({len(written)} templates)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ToolV2 visual templates.")
    parser.add_argument("--source-root", type=Path, default=BASE_DIR / "training_data" / "current_ui")
    parser.add_argument("--out-dir", type=Path, default=BASE_DIR / "templates")
    args = parser.parse_args()
    build_templates(args.source_root, args.out_dir)


if __name__ == "__main__":
    main()
