"""Classification pipeline for Droid Tycoon alert rows.

All pixel constants here (icon columns 14-72, rarity ROI 180-410/470,
spawn-phrase columns 330-720, row_height 44) are valid at the 2560x1440
reference scale. Inputs MUST be normalized via toolv2.normalize first.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np


DROID_TYPES = ("Diamond", "Rainbow", "Beskar")
RARITIES = ("Common", "Rare", "Epic", "Legendary", "Mythic")
PRIORITY_ALERTS = {
    ("Beskar", "Epic"),
    ("Beskar", "Legendary"),
    ("Beskar", "Mythic"),
    ("Diamond", "Mythic"),
    ("Rainbow", "Mythic"),
}

RARITY_COLOR_THRESHOLDS = {
    "Common": 700,
    "Rare": 650,
    "Epic": 550,
    "Legendary": 700,
    "Mythic": 600,
}

RARITY_COLOR_X_START = {
    "Beskar": 215,
    "Diamond": 230,
    "Rainbow": 230,
}


@dataclass(frozen=True)
class Detection:
    droid: str
    rarity: str
    row_box: tuple[int, int, int, int]
    droid_score: float
    rarity_score: float
    rarity_margin: float
    score: float
    source: str

    @property
    def is_priority(self) -> bool:
        return (self.droid, self.rarity) in PRIORITY_ALERTS

    @property
    def should_alert(self) -> bool:
        if not self.is_priority:
            return False
        if self.rarity == "Mythic":
            if self.droid == "Diamond":
                return self.droid_score >= 0.55 and self.rarity_score >= 0.62 and self.rarity_margin >= 0.08
            if self.droid == "Rainbow":
                return self.droid_score >= 0.85 and self.rarity_score >= 0.55 and self.rarity_margin >= 0.08
            if self.droid == "Beskar":
                return self.droid_score >= 0.10 and self.rarity_score >= 0.66 and self.rarity_margin >= 0.08
            return self.droid_score >= 0.70 and self.rarity_score >= 0.66 and self.rarity_margin >= 0.08
        if self.droid == "Beskar" and self.rarity == "Legendary":
            return self.droid_score >= 0.10 and self.rarity_score >= 0.55
        if self.droid == "Beskar" and self.rarity == "Epic":
            return self.droid_score >= 0.10 and self.rarity_score >= 0.55 and self.rarity_margin >= 0.06
        return self.score >= 0.70

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["is_priority"] = self.is_priority
        data["should_alert"] = self.should_alert
        return data


@dataclass(frozen=True)
class Template:
    rarity: str
    path: Path
    image: np.ndarray


@dataclass(frozen=True)
class RarityRoiTemplate:
    droid: str
    rarity: str
    path: Path
    image: np.ndarray


@dataclass(frozen=True)
class EdgeTemplate:
    path: Path
    image: np.ndarray


@dataclass(frozen=True)
class RarityCandidate:
    rarity: str
    score: float
    box: tuple[int, int, int, int]
    template_name: str


def read_image(path: str | Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(path)
    return image


def save_json(path: str | Path, data: object) -> None:
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def preprocess_for_template(image: np.ndarray, height: int | None = None) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 45, 145)
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8), iterations=1)
    if height is not None and edges.shape[0] != height:
        scale = height / max(1, edges.shape[0])
        width = max(1, int(round(edges.shape[1] * scale)))
        edges = cv2.resize(edges, (width, height), interpolation=cv2.INTER_AREA)
    return edges


def trim_blank_edges(mask: np.ndarray, pad: int = 2) -> np.ndarray:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        return mask
    x1 = max(0, int(xs.min()) - pad)
    x2 = min(mask.shape[1], int(xs.max()) + pad + 1)
    y1 = max(0, int(ys.min()) - pad)
    y2 = min(mask.shape[0], int(ys.max()) + pad + 1)
    return mask[y1:y2, x1:x2]


def load_templates(template_dir: str | Path) -> list[Template]:
    template_dir = Path(template_dir)
    templates: list[Template] = []
    for path in sorted(template_dir.glob("*.png")):
        rarity = path.stem.split("__", 1)[0]
        if rarity not in RARITIES:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            continue
        templates.append(Template(rarity=rarity, path=path, image=image))
    if not templates:
        raise FileNotFoundError(f"No templates found in {template_dir}. Add new screenshots and rebuild templates first.")
    return templates


def load_rarity_roi_templates(template_dir: str | Path) -> dict[str, list[RarityRoiTemplate]]:
    template_dir = Path(template_dir)
    templates: dict[str, list[RarityRoiTemplate]] = {droid: [] for droid in DROID_TYPES}
    if not template_dir.exists():
        return templates

    for path in sorted(template_dir.glob("*.png")):
        parts = path.stem.split("__")
        if len(parts) < 3:
            continue
        droid, rarity = parts[0], parts[1]
        if droid not in DROID_TYPES or rarity not in RARITIES:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            continue
        templates[droid].append(RarityRoiTemplate(droid=droid, rarity=rarity, path=path, image=image))

    return templates


def load_edge_templates(template_dir: str | Path) -> list[EdgeTemplate]:
    template_dir = Path(template_dir)
    if not template_dir.exists():
        return []

    templates: list[EdgeTemplate] = []
    for path in sorted(template_dir.glob("*.png")):
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            continue
        templates.append(EdgeTemplate(path=path, image=image))
    return templates


def color_scores(row: np.ndarray) -> dict[str, float]:
    """Classify droid family using the stable left-side icon/text colors.

    Window anchored to the detected icon start; at the reference offset
    (x0=14) this is exactly the original fixed 20:72 window.
    """
    _h, w = row.shape[:2]
    x0 = _icon_x_start(row)
    left = row[:, x0 + 6 : min(w, x0 + 58)]
    hsv = cv2.cvtColor(left, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
    hue, sat, val = cv2.split(hsv)

    edges = cv2.Canny(gray, 35, 125)
    edge_near = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1) > 0

    bright_colored = (sat > 55) & (val > 105) & edge_near
    bright_gray = (sat < 38) & (val > 125) & edge_near
    colored_count = int(bright_colored.sum())
    gray_count = int(bright_gray.sum())

    cyan = bright_colored & (hue >= 78) & (hue <= 105)
    cyan_count = int(cyan.sum())

    hue_bins: dict[int, int] = {}
    ys, xs = np.where(bright_colored)
    for value in hue[ys, xs].tolist():
        bucket = int(value) // 15
        hue_bins[bucket] = hue_bins.get(bucket, 0) + 1
    strong_bins = sum(1 for count in hue_bins.values() if count >= 25)

    diamond = min(1.0, cyan_count / 55.0) * min(1.0, cyan_count / max(1, colored_count) / 0.42)
    rainbow = min(1.0, max(0, strong_bins - 3) / 4.0) * min(1.0, colored_count / 250.0)
    beskar = min(1.0, gray_count / 65.0) * (1.0 - min(0.8, colored_count / 160.0))

    return {
        "Diamond": float(diamond),
        "Rainbow": float(rainbow),
        "Beskar": float(beskar),
    }


def best_droid_type(row: np.ndarray) -> tuple[str, float]:
    scores = color_scores(row)
    if scores["Rainbow"] >= 0.75:
        return "Rainbow", scores["Rainbow"]
    if scores["Diamond"] >= 0.50:
        return "Diamond", scores["Diamond"]
    if scores["Beskar"] >= 0.08:
        return "Beskar", scores["Beskar"]
    droid, score = max(scores.items(), key=lambda item: item[1])
    return droid, score


def _icon_x_start(row: np.ndarray, default: int = 14) -> int:
    """Anchor the icon window to the row's leftmost dense foreground.

    The fixed x=14 assumed the capture region starts at the alert's left
    edge; an auto-detected region may start further left (icon drifts right
    by up to ~35px). Falls back to the reference offset when nothing dense
    is found.
    """
    probe = row[:, :130]
    if probe.size == 0:
        return default
    hsv = cv2.cvtColor(probe, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(probe, cv2.COLOR_BGR2GRAY)
    _hue, sat, val = cv2.split(hsv)
    edges = cv2.Canny(gray, 35, 125)
    edge_near = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1) > 0
    # Bright icon pixels only: the translucent backdrop's left edge is a
    # dimmer gray transition and must not anchor the window.
    fg = ((sat > 55) & (val > 95) & edge_near) | ((sat < 55) & (val > 150) & edge_near)
    colsum = fg.sum(axis=0)
    window = np.convolve(colsum, np.ones(8), mode="valid")
    dense = np.where(window >= 60)[0]
    if dense.size == 0:
        return default
    return max(0, int(dense[0]))


def has_droid_icon_structure(row: np.ndarray, droid: str) -> bool:
    x0 = _icon_x_start(row)
    icon = row[:, x0 : x0 + 38]
    if icon.size == 0:
        return False

    hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY)
    _hue, sat, val = cv2.split(hsv)
    edges = cv2.Canny(gray, 35, 125)
    edge_near = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1) > 0
    colored = (sat > 55) & (val > 95) & edge_near
    light_gray = (sat < 55) & (val > 120) & edge_near

    colored_count = int(colored.sum())
    gray_count = int(light_gray.sum())
    if droid == "Beskar":
        icon_shape = colored | light_gray
        left_noise = int(icon_shape[:, :10].sum())
        middle_body = int(icon_shape[:, 10:26].sum())
        right_body = int(icon_shape[:, 26:].sum())
        total_body = colored_count + gray_count
        # 170 (was 200): downscale blur on faded rows costs ~30% edge pixels
        # (measured 285 native -> 195 at 1080p) while non-icon rows stay far below.
        return total_body >= 160 and (middle_body >= 170 or (right_body >= 160 and left_noise <= 25))
    return colored_count >= 180


def has_crafted_phrase(
    row: np.ndarray,
    templates: list[EdgeTemplate],
    *,
    threshold: float = 0.85,
    max_x: int = 320,
) -> bool:
    if not templates:
        return False

    edge = preprocess_for_template(row)
    for template in templates:
        if template.image.shape[0] > edge.shape[0] or template.image.shape[1] > edge.shape[1]:
            continue
        result = cv2.matchTemplate(edge, template.image, cv2.TM_CCOEFF_NORMED)
        _min_score, max_score, _min_loc, max_loc = cv2.minMaxLoc(result)
        if max_score >= threshold and max_loc[0] <= max_x:
            return True
    return False


def has_spawn_phrase_structure(row: np.ndarray, *, min_white_edge_pixels: int = 700) -> bool:
    _h, w = row.shape[:2]
    if w <= 340:
        return False

    phrase = row[:, 330 : min(w, 720)]
    hsv = cv2.cvtColor(phrase, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(phrase, cv2.COLOR_BGR2GRAY)
    _hue, sat, val = cv2.split(hsv)
    edges = cv2.Canny(gray, 35, 125)
    edge_near = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1) > 0
    white_text_edges = (sat < 55) & (val > 165) & edge_near
    return int(white_text_edges.sum()) >= min_white_edge_pixels


def fixed_rarity_roi(image: np.ndarray, y: int, *, row_height: int, x1: int = 180, x2: int = 410) -> np.ndarray:
    h, w = image.shape[:2]
    y = max(0, min(max(0, h - row_height), y))
    left = max(0, min(w - 1, x1))
    right = max(left + 1, min(w, x2))
    crop = image[y : y + row_height, left:right]
    target_width = x2 - x1
    if crop.shape[0] != row_height or crop.shape[1] != target_width:
        crop = cv2.resize(crop, (target_width, row_height), interpolation=cv2.INTER_AREA)
    return preprocess_for_template(crop)


def rarity_color_counts(image: np.ndarray, y: int, droid: str, *, row_height: int) -> dict[str, int]:
    h, w = image.shape[:2]
    y1 = max(0, min(h, y - 3))
    y2 = max(y1 + 1, min(h, y + row_height + 4))
    x1 = max(0, min(w - 1, RARITY_COLOR_X_START.get(droid, 225)))
    x2 = max(x1 + 1, min(w, 470))
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return {rarity: 0 for rarity in RARITIES}

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    hue, sat, val = cv2.split(hsv)
    edges = cv2.Canny(gray, 35, 125)
    edge_near = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1) > 0

    return {
        "Common": int(((sat < 35) & (val >= 145) & (val <= 235) & edge_near).sum()),
        "Rare": int(((hue >= 86) & (hue <= 102) & (sat >= 160) & (val >= 170) & edge_near).sum()),
        "Epic": int(((hue >= 123) & (hue <= 139) & (sat >= 160) & (val >= 170) & edge_near).sum()),
        "Legendary": int(((hue >= 10) & (hue <= 23) & (sat >= 160) & (val >= 170) & edge_near).sum()),
        "Mythic": int(((hue >= 154) & (hue <= 174) & (sat >= 160) & (val >= 170) & edge_near).sum()),
    }


def rarity_text_color_counts(image: np.ndarray, y: int, droid: str, *, row_height: int) -> dict[str, int]:
    h, w = image.shape[:2]
    y1 = max(0, min(h, y - 3))
    y2 = max(y1 + 1, min(h, y + row_height + 4))
    x1 = max(0, min(w - 1, RARITY_COLOR_X_START.get(droid, 225)))
    x2 = max(x1 + 1, min(w, 470))
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return {rarity: 0 for rarity in RARITIES}

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    hue, sat, val = cv2.split(hsv)
    edges = cv2.Canny(gray, 35, 125)
    edge_near = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1) > 0
    masks = {
        "Common": (sat < 35) & (val >= 145) & (val <= 235) & edge_near,
        "Rare": (hue >= 86) & (hue <= 102) & (sat >= 160) & (val >= 170) & edge_near,
        "Epic": (hue >= 123) & (hue <= 139) & (sat >= 160) & (val >= 170) & edge_near,
        "Legendary": (hue >= 10) & (hue <= 23) & (sat >= 160) & (val >= 170) & edge_near,
        "Mythic": (hue >= 154) & (hue <= 174) & (sat >= 160) & (val >= 170) & edge_near,
    }

    crop_h, crop_w = crop.shape[:2]
    counts: dict[str, int] = {}
    for rarity, mask in masks.items():
        component_mask = mask.astype("uint8")
        label_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(component_mask, 8)
        text_mask = np.zeros_like(component_mask, dtype=bool)
        # Resampling blur can merge a whole rarity word into one component.
        # The tight cap only matters for Common, whose low-saturation mask
        # floods on bright backdrops; saturated color masks rarely hit scenery.
        max_area = 900 if rarity == "Common" else 2600
        for label in range(1, label_count):
            x, component_y, component_w, component_h, area = (int(value) for value in stats[label])
            touches_top = component_y <= 1
            touches_bottom = component_y + component_h >= crop_h - 1
            touches_left = x <= 1
            touches_right = x + component_w >= crop_w - 1
            text_shaped = (
                area >= 8
                and component_w >= 2
                and component_h >= 3
                and area <= max_area
                and not (touches_top and touches_bottom)
                and not (touches_left and touches_right)
            )
            if text_shaped:
                text_mask |= labels == label
        counts[rarity] = int((mask & text_mask).sum())
    return counts


def classify_rarity_text_color(
    image: np.ndarray,
    y: int,
    droid: str,
    *,
    row_height: int,
) -> tuple[str, float, float, str]:
    counts = rarity_text_color_counts(image, y, droid, row_height=row_height)
    best_rarity = max(RARITIES, key=lambda rarity: counts[rarity])
    best_count = counts[best_rarity]
    threshold = RARITY_COLOR_THRESHOLDS[best_rarity]
    if best_count < threshold:
        return "Unknown", min(0.99, best_count / threshold), 0.0, f"text-color:{best_rarity}:{best_count}"
    second = sorted((counts[rarity] for rarity in RARITIES if rarity != best_rarity), reverse=True)[0]
    score = min(0.99, best_count / threshold)
    margin = min(0.99, max(0, best_count - second) / threshold)
    return best_rarity, float(score), float(margin), f"text-color:{best_rarity}:{best_count}"


def classify_rarity_color(
    image: np.ndarray,
    y: int,
    droid: str,
    *,
    row_height: int,
) -> tuple[str, float, float, str]:
    counts = rarity_color_counts(image, y, droid, row_height=row_height)
    colored_rarities = ("Rare", "Epic", "Legendary", "Mythic")
    best_colored = max(colored_rarities, key=lambda rarity: counts[rarity])
    best_colored_count = counts[best_colored]
    colored_threshold = RARITY_COLOR_THRESHOLDS[best_colored]
    common_count = counts["Common"]
    common_dominates_colored = (
        common_count >= RARITY_COLOR_THRESHOLDS["Common"]
        and common_count >= best_colored_count * 2.0
    )
    common_conflicts_with_legendary = (
        best_colored == "Legendary"
        and common_count >= RARITY_COLOR_THRESHOLDS["Common"]
        and common_count >= best_colored_count * 0.70
    )
    common_conflicts_with_mythic = (
        best_colored == "Mythic"
        and common_count >= RARITY_COLOR_THRESHOLDS["Common"]
        and common_count >= best_colored_count * 0.70
    )
    if (
        best_colored_count >= colored_threshold
        and not common_dominates_colored
        and not common_conflicts_with_legendary
        and not common_conflicts_with_mythic
    ):
        second_colored = max(counts[rarity] for rarity in colored_rarities if rarity != best_colored)
        score = min(0.99, best_colored_count / colored_threshold)
        margin = min(0.99, max(0, best_colored_count - second_colored) / colored_threshold)
        return (
            best_colored,
            float(score),
            float(margin),
            f"color:{best_colored}:{best_colored_count}",
        )

    if (
        common_count >= RARITY_COLOR_THRESHOLDS["Common"]
        and (
            common_dominates_colored
            or common_conflicts_with_legendary
            or common_conflicts_with_mythic
            or best_colored_count < 500
        )
    ):
        score = min(0.99, common_count / RARITY_COLOR_THRESHOLDS["Common"])
        margin = min(0.99, max(0, common_count - best_colored_count) / RARITY_COLOR_THRESHOLDS["Common"])
        return "Common", float(score), float(margin), f"color:Common:{common_count}"

    best_rarity = max(RARITIES, key=lambda rarity: counts[rarity])
    second = sorted(counts.values(), reverse=True)[1]
    score = min(0.99, counts[best_rarity] / max(1, RARITY_COLOR_THRESHOLDS[best_rarity]))
    margin = min(0.99, max(0, counts[best_rarity] - second) / max(1, RARITY_COLOR_THRESHOLDS[best_rarity]))
    return "Unknown", float(score), float(margin), f"weak-color:{best_rarity}:{counts[best_rarity]}"


def classify_rarity_word_shape(
    image: np.ndarray,
    y: int,
    templates: list[Template],
    *,
    row_height: int,
    threshold: float = 0.35,
) -> tuple[str, float, float, str]:
    if not templates:
        return "Unknown", 0.0, 0.0, "shape:no-templates"

    row_center = y + (row_height // 2)
    matches = [
        match
        for match in rarity_candidates(image, templates, threshold)
        if abs(((match.box[1] + match.box[3]) // 2) - row_center) <= 18
    ]
    if not matches:
        return "Unknown", 0.0, 0.0, "shape:no-match"

    ranked = sorted(matches, key=lambda match: match.score, reverse=True)
    best = ranked[0]
    second = ranked[1].score if len(ranked) > 1 else 0.0
    margin = best.score - second
    return best.rarity, float(best.score), float(margin), f"shape:{best.template_name}"


def classify_rarity_roi(
    image: np.ndarray,
    y: int,
    droid: str,
    templates_by_droid: dict[str, list[RarityRoiTemplate]],
    shape_templates: list[Template] | None = None,
    *,
    row_height: int,
) -> tuple[str, float, float, str]:
    templates = templates_by_droid.get(droid, [])
    if not templates:
        return classify_rarity_color(image, y, droid, row_height=row_height)

    best_by_rarity: dict[str, tuple[float, str]] = {}
    for dy in range(-6, 7):
        roi = fixed_rarity_roi(image, y + dy, row_height=row_height)
        for template in templates:
            if roi.shape != template.image.shape:
                continue
            score = float(cv2.matchTemplate(roi, template.image, cv2.TM_CCOEFF_NORMED)[0, 0])
            previous = best_by_rarity.get(template.rarity)
            if previous is None or score > previous[0]:
                best_by_rarity[template.rarity] = (score, template.path.name)

    if not best_by_rarity:
        return "Unknown", 0.0, 0.0, "roi-template-mismatch"

    ranked = sorted(best_by_rarity.items(), key=lambda item: item[1][0], reverse=True)
    rarity, (score, template_name) = ranked[0]
    second = ranked[1][1][0] if len(ranked) > 1 else 0.0
    margin = score - second

    color_rarity, color_score, color_margin, color_source = classify_rarity_color(
        image,
        y,
        droid,
        row_height=row_height,
    )
    text_rarity, text_score, text_margin, text_source = classify_rarity_text_color(
        image,
        y,
        droid,
        row_height=row_height,
    )
    shape_rarity, shape_score, _shape_margin, shape_source = classify_rarity_word_shape(
        image,
        y,
        shape_templates or [],
        row_height=row_height,
    )
    if (
        color_rarity in {"Legendary", "Mythic"}
        and text_rarity != "Unknown"
        and text_rarity != color_rarity
        and text_score >= 0.86
        and text_margin >= 0.20
        and shape_rarity == text_rarity
        and shape_score >= 0.40
    ):
        return text_rarity, text_score, text_margin, f"{text_source};{shape_source}"

    # Raw color counts inflate Common on bright low-saturation backgrounds
    # (sand) when the alert backdrop is faint; the text-shaped analysis
    # filters those regions out, so trust a strong colored-text verdict.
    # Row seeds can sit a few px off, so take the best-margin verdict over a
    # small vertical scan (mirrors the template path's dy search).
    if color_rarity == "Common":
        best = (text_rarity, text_score, text_margin, text_source)
        for dy in (-4, -2, 2, 4):
            scan = classify_rarity_text_color(image, y + dy, droid, row_height=row_height)
            if scan[0] != "Unknown" and (scan[2], scan[1]) > (best[2], best[1]):
                best = scan
        scan_rarity, scan_score, scan_margin, scan_source = best
        if (
            scan_rarity in {"Rare", "Epic", "Legendary", "Mythic"}
            and scan_score >= 0.86
            and scan_margin >= 0.30
        ):
            confirmed = scan_rarity not in {"Legendary", "Mythic"} or (
                (shape_rarity == scan_rarity and shape_score >= 0.40) or scan_margin >= 0.55
            )
            if confirmed:
                return scan_rarity, scan_score, scan_margin, f"text-over-common:{scan_source}"

    unconfirmed_priority_color = (
        color_rarity in {"Legendary", "Mythic"}
        and not (text_rarity == color_rarity and text_score >= 0.75)
        and not (shape_rarity == color_rarity and shape_score >= 0.50)
    )
    if unconfirmed_priority_color:
        return (
            "Unknown",
            color_score,
            color_margin,
            f"unconfirmed-priority-color:{color_source};{text_source};{shape_source}",
        )

    # Common's color score saturates on any row (white words + bright sand),
    # so it may not overturn a template verdict with a decisive margin.
    # Measured on image.png rows: a genuine colored word matches its template
    # at >=0.72 with margin >=0.30; a Common row's best wrong-template match
    # stays below both. Only then may the template stand against Common.
    common_overriding_decisive_template = (
        color_rarity == "Common" and rarity != "Common" and score >= 0.72 and margin >= 0.30
    )
    if (
        color_rarity != "Unknown"
        and color_rarity != rarity
        and color_score >= 0.86
        and score < 0.85
        and not common_overriding_decisive_template
    ):
        return color_rarity, color_score, color_margin, color_source

    if score < 0.55 or (margin < 0.04 and score < 0.85):
        if color_rarity != "Unknown":
            return color_rarity, color_score, color_margin, color_source
        return "Unknown", float(score), float(margin), f"weak-roi:{template_name};{color_source}"

    return rarity, float(score), float(margin), template_name


def rarity_candidates(image: np.ndarray, templates: list[Template], threshold: float) -> list[RarityCandidate]:
    edge = preprocess_for_template(image)
    candidates: list[RarityCandidate] = []
    for template in templates:
        templ = template.image
        if templ.shape[0] > edge.shape[0] or templ.shape[1] > edge.shape[1]:
            continue
        result = cv2.matchTemplate(edge, templ, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= threshold)
        for x, y in zip(xs.tolist(), ys.tolist()):
            center_x = x + templ.shape[1] // 2
            if center_x < 175 or center_x > 430:
                continue
            candidates.append(
                RarityCandidate(
                    rarity=template.rarity,
                    score=float(result[y, x]),
                    box=(x, y, x + templ.shape[1], y + templ.shape[0]),
                    template_name=template.path.name,
                )
            )

    selected: list[RarityCandidate] = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        cx = (candidate.box[0] + candidate.box[2]) // 2
        cy = (candidate.box[1] + candidate.box[3]) // 2
        if any(
            abs(cx - (other.box[0] + other.box[2]) // 2) <= 38
            and abs(cy - (other.box[1] + other.box[3]) // 2) <= 16
            for other in selected
        ):
            continue
        selected.append(candidate)
    return selected


def non_max_rows(candidates: list[Detection], y_distance: int = 18) -> list[Detection]:
    selected: list[Detection] = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        cy = (candidate.row_box[1] + candidate.row_box[3]) // 2
        if any(abs(cy - (row.row_box[1] + row.row_box[3]) // 2) <= y_distance for row in selected):
            continue
        selected.append(candidate)
    selected.sort(key=lambda item: item.row_box[1])
    return selected


def row_positions_from_rarity_candidates(
    image: np.ndarray,
    templates: list[Template],
    *,
    threshold: float,
    row_height: int,
) -> list[int]:
    h = image.shape[0]
    rows: list[tuple[int, float]] = []
    for rarity_match in rarity_candidates(image, templates, threshold):
        _rx1, ry1, _rx2, ry2 = rarity_match.box
        center_y = (ry1 + ry2) // 2
        y = max(0, min(h - row_height, center_y - row_height // 2))
        rows.append((y, rarity_match.score))

    selected: list[tuple[int, float]] = []
    for y, score in sorted(rows, key=lambda item: item[1], reverse=True):
        if any(abs(y - other_y) <= 18 for other_y, _other_score in selected):
            continue
        selected.append((y, score))
    return [y for y, _score in sorted(selected)]


class DroidVisualDetector:
    def __init__(
        self,
        template_dir: str | Path,
        *,
        rarity_threshold: float = 0.35,
        droid_threshold: float = 0.15,
        row_height: int = 44,
    ) -> None:
        self.templates = load_templates(template_dir)
        self.rarity_roi_templates = load_rarity_roi_templates(Path(template_dir) / "rarity_rois")
        self.crafted_phrase_templates = load_edge_templates(Path(template_dir) / "crafted_phrases")
        self.rarity_threshold = rarity_threshold
        self.droid_threshold = droid_threshold
        self.row_height = row_height

    def detect(self, image: np.ndarray, extra_row_ys: list[int] | None = None) -> list[Detection]:
        h, w = image.shape[:2]
        row_ys = row_positions_from_rarity_candidates(
            image,
            self.templates,
            threshold=self.rarity_threshold,
            row_height=self.row_height,
        )
        # Extra seeds (e.g. from the resolution-relative row finder) go through
        # the exact same per-row gates; template-seeded rows take precedence.
        for extra_y in extra_row_ys or []:
            extra_y = max(0, min(h - self.row_height, int(extra_y)))
            if all(abs(extra_y - y) > 26 for y in row_ys):
                row_ys.append(extra_y)
        candidates: list[Detection] = []
        for y in sorted(row_ys):
            row = image[y : y + self.row_height, :]
            if has_crafted_phrase(row, self.crafted_phrase_templates):
                continue
            if not has_spawn_phrase_structure(row):
                continue

            droid, droid_score = best_droid_type(row)
            if droid_score < self.droid_threshold:
                continue
            if not has_droid_icon_structure(row, droid):
                continue

            rarity, rarity_score, rarity_margin, template_name = classify_rarity_roi(
                image,
                y,
                droid,
                self.rarity_roi_templates,
                self.templates,
                row_height=self.row_height,
            )
            if rarity == "Unknown":
                continue

            score = (rarity_score * 0.72) + (droid_score * 0.28)
            candidates.append(
                Detection(
                    droid=droid,
                    rarity=rarity,
                    row_box=(0, y, w, y + self.row_height),
                    droid_score=float(droid_score),
                    rarity_score=float(rarity_score),
                    rarity_margin=float(rarity_margin),
                    score=float(score),
                    source=f"roi:{template_name}",
                )
            )

        # 26px: wide enough to merge duplicate seeds of the same physical row
        # (~20px offsets), narrow enough to keep adjacent rows (~33px spacing).
        return non_max_rows(candidates, y_distance=26)


# Reference-scale column constants, also expressed as %-of-row-width for
# non-gating drift diagnostics (reference band width ~845px at 2560x1440).
COLUMN_CONSTANTS = {
    "icon_x": (14, 72),
    "rarity_roi_x": (180, 410),
    "rarity_color_x_end": (470, 470),
    "spawn_phrase_x": (330, 720),
}


def column_drift_report(band_width: int) -> dict[str, dict[str, float]]:
    """Log each fixed column as a fraction of the normalized band width.

    Purely diagnostic: if the normalized band width drifts far from the
    ~845px reference, these fractions flag it without gating detection.
    """
    report: dict[str, dict[str, float]] = {}
    for name, (x1, x2) in COLUMN_CONSTANTS.items():
        report[name] = {
            "x1_frac": round(x1 / max(1, band_width), 4),
            "x2_frac": round(x2 / max(1, band_width), 4),
            "band_width": float(band_width),
        }
    return report


def draw_detections(image: np.ndarray, detections: list[Detection]) -> np.ndarray:
    output = image.copy()
    colors = {
        "Diamond": (255, 220, 0),
        "Rainbow": (255, 0, 255),
        "Beskar": (220, 220, 220),
    }
    for detection in detections:
        x1, y1, x2, y2 = detection.row_box
        color = colors.get(detection.droid, (0, 255, 255))
        cv2.rectangle(output, (x1, y1), (x2 - 1, y2 - 1), color, 2)
        label = f"{detection.droid} {detection.rarity} {detection.score:.2f}"
        cv2.putText(output, label, (max(0, x1 + 6), max(16, y1 + 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    return output
