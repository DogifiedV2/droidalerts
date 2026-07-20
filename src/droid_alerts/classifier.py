"""Classification pipeline for Droid Tycoon alert rows.

All pixel constants here (icon columns 14-72, rarity ROI 180-410/470,
spawn-phrase columns 330-720, row_height 44) are valid at the 2560x1440
reference scale. Inputs MUST be normalized via droid_alerts.normalize first.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np


DROID_TYPES = ("Diamond", "Rainbow", "Beskar", "Galactic")
RARITIES = ("Common", "Rare", "Epic", "Legendary", "Mythic")
PRIORITY_ALERTS = {
    ("Rainbow", "Epic"),
    ("Rainbow", "Legendary"),
    ("Beskar", "Epic"),
    ("Beskar", "Legendary"),
    ("Beskar", "Mythic"),
    ("Diamond", "Mythic"),
    ("Rainbow", "Mythic"),
    ("Galactic", "Common"),
    ("Galactic", "Rare"),
    ("Galactic", "Epic"),
    ("Galactic", "Legendary"),
    ("Galactic", "Mythic"),
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
    "Galactic": 230,
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
    # Word-shape template score for the *detected* rarity (not the argmax).
    # Only gates Mythic alerts; defaults to 1.0 so non-Mythic paths are unaffected.
    shape_score: float = 1.0

    @property
    def is_priority(self) -> bool:
        return (self.droid, self.rarity) in PRIORITY_ALERTS

    @property
    def should_alert(self) -> bool:
        # Every FP defense lives in the detector (spawn-line gate, dual-word
        # veto, shape floor, text confirmation), so a detection that reaches
        # here and is a priority combo should alert. The per-combo score
        # gates that used to live here blocked ZERO of the 13 live FPs in the
        # 2026-07-08 debug batch while sitting right on top of real alerts
        # (three real Mythic rows measured shape 0.401-0.433 against the old
        # 0.40 floor), and they made the UI show "Priority=yes / Alerted=no".
        return self.is_priority

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
class DroidWordTemplate:
    droid: str
    path: Path
    image: np.ndarray


@dataclass(frozen=True)
class RarityCandidate:
    rarity: str
    score: float
    box: tuple[int, int, int, int]
    template_name: str


def read_image(path: str | Path) -> np.ndarray:
    image = _read_cv_image(path, cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(path)
    return image


def _read_cv_image(path: str | Path, flags: int) -> np.ndarray | None:
    """Read an image without passing its path through OpenCV's Windows APIs.

    Some OpenCV Windows builds cannot open paths containing non-ASCII
    characters. Python's file APIs are Unicode-safe, so read the bytes there
    and let OpenCV decode the in-memory image instead.
    """
    try:
        encoded = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    except OSError:
        return None
    if encoded.size == 0:
        return None
    return cv2.imdecode(encoded, flags)


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
        image = _read_cv_image(path, cv2.IMREAD_GRAYSCALE)
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
        image = _read_cv_image(path, cv2.IMREAD_GRAYSCALE)
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
        image = _read_cv_image(path, cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            continue
        templates.append(EdgeTemplate(path=path, image=image))
    return templates


def load_droid_word_templates(template_dir: str | Path) -> dict[str, list[DroidWordTemplate]]:
    template_dir = Path(template_dir)
    templates: dict[str, list[DroidWordTemplate]] = {droid: [] for droid in DROID_TYPES}
    if not template_dir.exists():
        return templates

    for path in sorted(template_dir.glob("*.png")):
        droid = path.stem.split("__", 1)[0]
        if droid not in DROID_TYPES:
            continue
        image = _read_cv_image(path, cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            continue
        templates[droid].append(DroidWordTemplate(droid=droid, path=path, image=image))
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


def droid_word_text_profile(row: np.ndarray) -> dict[str, int]:
    """Dark-outlined, glyph-sized text components in the droid-word columns.

    The droid NAME ('Diamond'/'Rainbow'/'Beskar'/'Galactic' + 'Droid') is rendered in the
    droid's colors, sits on the backdrop, and is far larger than the icon -
    scenery blobs can't imitate letter-shaped outlined text (same filtering
    that made rarity classification background-proof). Columns 30-240 stop
    short of the rarity word so e.g. a cyan '(Rare)' can't inflate Diamond.
    """
    h, w = row.shape[:2]
    win = row[:, 30 : min(w, 240)]
    if win.size == 0:
        return {
            "cyan": 0,
            "purple": 0,
            "gray": 0,
            "colored_total": 0,
            "strong_families": 0,
        }
    hsv = cv2.cvtColor(win, cv2.COLOR_BGR2HSV)
    gray_img = cv2.cvtColor(win, cv2.COLOR_BGR2GRAY)
    hue, sat, val = cv2.split(hsv)
    edge_near = cv2.dilate(cv2.Canny(gray_img, 35, 125), np.ones((3, 3), np.uint8)) > 0
    dark_near = cv2.dilate((gray_img < 95).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    gate = edge_near & dark_near

    def text_shaped_count(mask: np.ndarray) -> int:
        component_mask = mask.astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(component_mask, 8)
        keep = np.zeros_like(component_mask, dtype=bool)
        for i in range(1, n):
            x, y, cw, ch, area = (int(v) for v in stats[i])
            if area >= 8 and cw >= 2 and 3 <= ch <= 30 and area <= 2600:
                keep |= labels == i
        return int((mask & keep).sum())

    colored = (sat >= 110) & (val >= 140) & gate
    families = {
        "cyan": colored & (hue >= 78) & (hue <= 105),
        "green": colored & (hue >= 38) & (hue < 78),
        "yellow": colored & (hue >= 24) & (hue < 38),
        "orange": colored & (hue >= 5) & (hue < 24),
        "magenta": colored & ((hue >= 155) | (hue <= 4)),
        "purple": colored & (hue >= 122) & (hue < 155),
    }
    counts = {name: text_shaped_count(mask) for name, mask in families.items()}
    return {
        "cyan": counts["cyan"],
        "purple": counts["purple"],
        "gray": text_shaped_count((sat < 55) & (val > 150) & gate),
        "colored_total": sum(counts.values()),
        "strong_families": sum(1 for v in counts.values() if v >= 60),
    }


def droid_word_color_mask(row: np.ndarray, droid: str) -> np.ndarray:
    """Binary, colour-segmented mask of the icon/name area for word matching.

    Unlike grayscale edges, this drops scenery that does not share the target
    family's text colour. The component limits were measured across the 149
    valid rows in the 2026-07-10 1.1.5 debug batch.
    """
    # The newer Galactic HUD style is substantially wider than the original
    # Diamond/Rainbow/Beskar words (the supplied Epic capture reaches x=327).
    # Keep the proven legacy window for every other family, but leave enough
    # room to match the complete literal "Galactic Droid" word.
    x_end = 360 if droid == "Galactic" else 270
    win = row[:, 20 : min(row.shape[1], x_end)]
    if win.size == 0:
        return np.zeros((0, 0), dtype=np.uint8)
    hsv = cv2.cvtColor(win, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(win, cv2.COLOR_BGR2GRAY)
    hue, sat, val = cv2.split(hsv)
    edge_near = cv2.dilate(cv2.Canny(gray, 35, 125), np.ones((3, 3), np.uint8)) > 0
    dark_near = cv2.dilate((gray < 95).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    gate = edge_near & dark_near
    if droid == "Diamond":
        raw = (hue >= 78) & (hue <= 112) & (sat > 55) & (val > 95) & gate
    elif droid == "Rainbow":
        raw = (sat >= 100) & (val >= 120) & gate
    elif droid == "Galactic":
        # The announced #9200E0 renders around OpenCV hue 140. Preserve the
        # anti-aliased edge range measured across the live Galactic chat
        # captures; the literal word-shape template below prevents other
        # purple HUD text from becoming a Galactic verdict.
        raw = (hue >= 125) & (hue <= 155) & (sat >= 110) & (val >= 120) & gate
    else:
        raw = (sat < 60) & (val > 140) & gate

    label_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(raw.astype(np.uint8), 8)
    output = np.zeros_like(raw, dtype=np.uint8)
    for label in range(1, label_count):
        _x, y, component_w, component_h, area = (int(value) for value in stats[label])
        # Bold Galactic glyphs can touch after capture scaling, joining an
        # entire word into one component. The old 40px cap erased those words
        # before literal template matching. Galactic still requires both its
        # narrow purple range and a word-shape match, so retaining the joined
        # component does not weaken the other droid classifiers.
        maximum_width = 240 if droid == "Galactic" else 40
        maximum_area = 8000 if droid == "Galactic" else 2600
        inside_row = (
            y >= 0 and y + component_h <= raw.shape[0]
            if droid == "Galactic"
            else y > 1 and y + component_h < raw.shape[0] - 1
        )
        if (
            area >= 6
            and component_w >= 2
            and 3 <= component_h <= 38
            and component_w <= maximum_width
            and area <= maximum_area
            and inside_row
        ):
            output[labels == label] = 255
    return output


def droid_word_shape_score(
    row: np.ndarray,
    droid: str,
    templates_by_droid: dict[str, list[DroidWordTemplate]],
) -> float:
    mask = droid_word_color_mask(row, droid)
    if mask.size == 0:
        return 0.0
    best = 0.0
    for template in templates_by_droid.get(droid, []):
        # Fortnite now renders Galactic chat text at multiple horizontal/UI
        # sizes that are not fully predicted by the screen-resolution scale.
        # Multi-scale matching is restricted to the Galactic literal-word
        # path; legacy family behavior and thresholds remain unchanged.
        scale_factors = (
            (0.70, 0.80, 0.90, 1.0, 1.10, 1.20, 1.35, 1.50, 1.70)
            if droid == "Galactic"
            else (1.0,)
        )
        for scale_factor in scale_factors:
            if scale_factor == 1.0:
                candidate = template.image
            else:
                candidate = cv2.resize(
                    template.image,
                    None,
                    fx=scale_factor,
                    fy=scale_factor,
                    interpolation=cv2.INTER_NEAREST,
                )
            if candidate.shape[0] > mask.shape[0] or candidate.shape[1] > mask.shape[1]:
                continue
            result = cv2.matchTemplate(mask, candidate, cv2.TM_CCOEFF_NORMED)
            best = max(best, float(result.max()))
    return best


def classify_galactic_droid_word(
    row: np.ndarray,
    templates_by_droid: dict[str, list[DroidWordTemplate]],
    *,
    shape_threshold: float = 0.50,
    minimum_purple_pixels: int = 250,
) -> tuple[str, float] | None:
    """Return Galactic only when live colour and literal word shape agree.

    Purple alone is unsafe because Epic/Mythic rarity words and scenery use
    nearby hues. With no installed Galactic templates this path stays dormant.
    """

    if not templates_by_droid.get("Galactic"):
        return None
    profile = droid_word_text_profile(row)
    purple = profile["purple"]
    if purple < minimum_purple_pixels:
        return None
    shape = droid_word_shape_score(row, "Galactic", templates_by_droid)
    if shape < shape_threshold:
        return None
    return "Galactic", min(0.99, max(shape, purple / 900.0))


def classify_beskar_droid_word(
    row: np.ndarray,
    templates_by_droid: dict[str, list[DroidWordTemplate]],
    *,
    shape_threshold: float = 0.50,
    minimum_gray_pixels: int = 550,
) -> tuple[str, float] | None:
    """Recover scaled Beskar words when the gray pixel count is just low.

    The generic word classifier deliberately requires 700 gray pixels. The
    supplied Epic capture measures below that after screen normalization, but
    still has a strong literal Beskar template match. Requiring both signals
    preserves the existing gray custom-name false-positive protection.
    """

    if not templates_by_droid.get("Beskar"):
        return None
    gray = droid_word_text_profile(row)["gray"]
    if gray < minimum_gray_pixels:
        return None
    shape = droid_word_shape_score(row, "Beskar", templates_by_droid)
    if shape < shape_threshold:
        return None
    return "Beskar", min(0.99, max(shape, gray / 900.0))


def classify_droid_word(row: np.ndarray) -> tuple[str, float] | None:
    """Droid family from the droid-word text; None when evidence is weak.

    The ordering and floors are measured across the 149 valid rows in the
    2026-07-10 1.1.5 batch: Diamond words carry dominant cyan, Rainbow words
    span at least four hue families, and Beskar words carry dominant gray.
    """
    p = droid_word_text_profile(row)
    # Resolve the dominant single-colour words before looking at background
    # colour diversity. The old ordering let a multicolour prop behind a real
    # Diamond word turn the row into Rainbow, while unrelated cyan scenery in
    # the old icon fallback turned gray Beskar words into Diamond.
    if p["gray"] >= 700 and p["gray"] > p["cyan"]:
        # A white nameplate can cover a Rainbow word, leaving only the
        # multicolour letters around it. Keep that measured exception, but do
        # not let unrelated icon-window colour override a gray word.
        if p["colored_total"] >= 900 and p["strong_families"] >= 3:
            return "Rainbow", min(0.99, p["colored_total"] / 1200.0)
        return "Beskar", min(0.99, p["gray"] / 900.0)
    if (
        p["cyan"] >= 700
        # Orange scenery (a gold hexagon sign) behind a real Diamond word can
        # push cyan under the 60%-of-colored ratio (measured 0.56 on a live
        # 2026-07-08 FP: the verdict fell through to the icon path, misread
        # Beskar, and alerted Beskar Legendary on a Diamond Legendary row).
        # An absolute cyan count this high is Diamond regardless: real Diamond
        # rows measure 1385-1553, every non-Diamond live row <=425.
        and (p["cyan"] >= 0.6 * max(1, p["colored_total"]) or p["cyan"] >= 1200)
    ):
        return "Diamond", min(0.99, p["cyan"] / 1000.0)
    # Slightly clipped 1080p Rainbow rows measured 752 coloured pixels in the
    # new 1.1.5 report batch. Four distinct text-colour families are still a
    # stronger signal than the weak icon fallback that misread them as Beskar.
    if p["strong_families"] >= 4 and p["colored_total"] >= 700:
        return "Rainbow", min(0.99, p["colored_total"] / 1200.0)
    return None


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


def has_spawn_line_phrase(
    image: np.ndarray,
    y: int,
    templates: list[EdgeTemplate],
    *,
    row_height: int,
    threshold: float = 0.38,
    search_x: tuple[int, int] = (300, 640),
) -> bool:
    """Confirm the row carries the literal "spawned at the (Sandcrawler)" text.

    The generic white-text gate (has_spawn_phrase_structure) only counts white
    edge pixels, so it also passes milestone / craft / chat / promo rows
    ("<player> crafted a Rainbow Droid", "reached Rebirth 12", chat lines, a
    shop card) whose orange usernames and gold badges inflate the Legendary
    color count -> eight live FPs 2026-07-08. The real spawn phrase is a fixed
    string at a fixed position; a column-constrained edge-template match near
    the row cleanly separates it (measured on the live batch: every non-spawn
    Legendary upload scores <=0.337, every real Legendary row >=0.428).

    Only Legendary is gated - that is where every observed non-spawn FP landed
    (orange = Legendary's hue). Epic/Mythic rows can be legitimately occluded by
    shop UI over the phrase (a real Beskar Epic measured 0.263) and would be
    lost by this gate, so they keep the looser white-text gate only. Returns
    True when no template is installed so a missing asset never blocks alerts.
    """
    if not templates:
        return True
    x0, x1 = search_x
    if image.shape[1] <= x0 + 4:
        return True
    edge = preprocess_for_template(image[:, x0 : min(image.shape[1], x1)])
    lo = max(0, y - 12)
    for template in templates:
        t = template.image
        if t.shape[0] > edge.shape[0] or t.shape[1] > edge.shape[1]:
            continue
        result = cv2.matchTemplate(edge, t, cv2.TM_CCOEFF_NORMED)
        hi = min(result.shape[0], y + 13)
        if lo < hi and float(result[lo:hi].max()) >= threshold:
            return True
    return False


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
    # Real rarity words are stroked with a dark outline (measured: 100% of
    # their colored pixels are dark-adjacent; background color floods ~70%).
    dark_near = cv2.dilate((gray < 95).astype(np.uint8), np.ones((5, 5), np.uint8), iterations=1) > 0
    colored_gate = edge_near & dark_near

    return {
        "Common": int(((sat < 35) & (val >= 145) & (val <= 235) & edge_near).sum()),
        "Rare": int(((hue >= 86) & (hue <= 102) & (sat >= 160) & (val >= 170) & colored_gate).sum()),
        "Epic": int(((hue >= 123) & (hue <= 139) & (sat >= 160) & (val >= 170) & colored_gate).sum()),
        "Legendary": int(((hue >= 10) & (hue <= 23) & (sat >= 160) & (val >= 170) & colored_gate).sum()),
        "Mythic": int(((hue >= 154) & (hue <= 174) & (sat >= 160) & (val >= 170) & colored_gate).sum()),
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
    dark_near = cv2.dilate((gray < 95).astype(np.uint8), np.ones((5, 5), np.uint8), iterations=1) > 0
    colored_gate = edge_near & dark_near
    masks = {
        "Common": (sat < 35) & (val >= 145) & (val <= 235) & edge_near,
        "Rare": (hue >= 86) & (hue <= 102) & (sat >= 160) & (val >= 170) & colored_gate,
        "Epic": (hue >= 123) & (hue <= 139) & (sat >= 160) & (val >= 170) & colored_gate,
        "Legendary": (hue >= 10) & (hue <= 23) & (sat >= 160) & (val >= 170) & colored_gate,
        "Mythic": (hue >= 154) & (hue <= 174) & (sat >= 160) & (val >= 170) & colored_gate,
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
                # Glyphs are 13-26px tall at reference scale (a blur-merged
                # word stays under ~30); HUD coins (~55px) and billboard
                # blobs are taller and must not count as rarity text.
                and component_h <= 30
                and area <= max_area
                # Saturated rarity glyphs remain <=35px wide after scale
                # normalization. Wider or boundary-touching components in the
                # 1.1.5 report batch were solid orange/magenta scenery, not
                # letters. Common stays permissive because its low-saturation
                # mask can merge glyphs after downscale blur.
                and (rarity == "Common" or component_w <= 35)
                and (
                    rarity == "Common"
                    or not (touches_top or touches_bottom or touches_left or touches_right)
                )
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


def galactic_rarity_text_override(
    raw_rarity: str,
    text_verdict: tuple[str, float, float, str],
    shape_verdict: tuple[str, float, float, str],
    text_counts: dict[str, int],
) -> tuple[str, float, float, str] | None:
    """Correct Galactic rarity colors polluted by scenery or adjacent rows.

    Galactic has no fixed family ROI templates. Its broad raw-color crop can
    therefore let orange/purple scenery outvote the actual outlined rarity
    word. Prefer a conflicting rarity only when text-shaped color and word
    shape corroborate it, with two measured exceptions for Galactic Rare:
    raw Legendary is a recurring orange-background failure, while raw Common
    needs both a Rare-shaped word and meaningful cyan dominance.
    """
    text_rarity, _text_score, _text_margin, text_source = text_verdict
    shape_rarity, shape_score, _shape_margin, shape_source = shape_verdict
    colored = {"Rare", "Epic", "Legendary", "Mythic"}

    def result(rarity: str, reason: str) -> tuple[str, float, float, str]:
        count = text_counts[rarity]
        threshold = RARITY_COLOR_THRESHOLDS[rarity]
        raw_text_count = text_counts.get(raw_rarity, 0)
        score = min(0.99, count / threshold)
        margin = min(0.99, max(0, count - raw_text_count) / threshold)
        return (
            rarity,
            float(score),
            float(margin),
            f"galactic-text-override[{reason}]:{text_source};{shape_source}",
        )

    # Strongest general case: the colored glyph count clears its own floor,
    # its literal word shape agrees, and it beats text-shaped pixels for the
    # raw winner. This also corrects the rarer reverse errors (Rare reported
    # over a real Epic/Legendary word).
    if (
        shape_rarity in colored
        and shape_rarity != raw_rarity
        and shape_score >= 0.40
        and text_counts[shape_rarity] >= RARITY_COLOR_THRESHOLDS[shape_rarity]
        and text_counts[shape_rarity] >= text_counts.get(raw_rarity, 0) * 1.20
    ):
        return result(shape_rarity, "shape+color")

    # Orange scenery repeatedly makes a real cyan Rare word look Legendary.
    # A full-strength Rare text verdict is independently background-filtered.
    if raw_rarity == "Legendary" and text_rarity == "Rare":
        return result("Rare", "legendary-background")
    if (
        raw_rarity == "Legendary"
        and text_rarity == "Unknown"
        and text_counts["Rare"] >= 620
        and shape_rarity == "Rare"
        and shape_score >= 0.50
    ):
        return result("Rare", "legendary-background-near-floor")

    # Very strong Mythic text survives even when orange scenery dominates the
    # raw crop. The 1000px floor stays above the measured conflicting text on
    # genuine Legendary rows.
    if (
        raw_rarity == "Legendary"
        and text_rarity == "Mythic"
        and text_counts["Mythic"] >= 1000
    ):
        return result("Mythic", "legendary-background-mythic")

    # Common can win from the white spawn phrase. Require either an unusually
    # strong colored word or independent Rare word-shape evidence before
    # replacing it, preserving all reviewed genuine Common rows.
    if raw_rarity == "Common":
        if (
            shape_rarity == "Rare"
            and shape_score >= 0.40
            and text_counts["Rare"] >= 600
            and text_counts["Rare"] >= text_counts["Common"] * 0.60
        ):
            return result("Rare", "common-phrase+rare-shape")
        strong_floor = {"Legendary": 750, "Mythic": 1000}.get(text_rarity)
        if (
            strong_floor is not None
            and text_counts[text_rarity] >= strong_floor
            and text_counts[text_rarity] >= text_counts["Common"] * 1.20
        ):
            return result(text_rarity, "common-phrase+strong-color")

    return None


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
    word_matches: list[RarityCandidate] | None = None,
) -> tuple[str, float, float, str]:
    if not templates and word_matches is None:
        return "Unknown", 0.0, 0.0, "shape:no-templates"

    row_center = y + (row_height // 2)
    matches = [
        match
        for match in rarity_candidates(image, templates, threshold, matches=word_matches)
        if abs(((match.box[1] + match.box[3]) // 2) - row_center) <= 18
    ]
    if not matches:
        return "Unknown", 0.0, 0.0, "shape:no-match"

    ranked = sorted(matches, key=lambda match: match.score, reverse=True)
    best = ranked[0]
    second = ranked[1].score if len(ranked) > 1 else 0.0
    margin = best.score - second
    return best.rarity, float(best.score), float(margin), f"shape:{best.template_name}"


def rescue_weak_color_rarity(
    image: np.ndarray,
    y: int,
    droid: str,
    shape_templates: list[Template],
    *,
    row_height: int,
    word_matches: list["RarityCandidate"] | None = None,
) -> tuple[str, float, float, str] | None:
    """Optional "Extra checks" fallback for washed-out colors (Windows HDR,
    night-light, driver vibrance filters): tone mapping clips bright rarity
    text toward white, so the strict color masks count too few pixels and the
    row gets dropped even though the glyphs are intact. Rescue the row only
    when two independent weak signals agree on the same rarity:
      - the text-shaped color count still picks it, just under its floor
        (>=300 px; the HDR ultrawide Diamond Rare capture measures 501 vs
        2500+ on SDR machines);
      - the rarity word-shape template (grayscale edges, immune to color
        shift) matches the same rarity at >=0.50 (real words measure
        0.49-0.63; measured background fakes stay <=0.29).
    Runs only after normal color counting returned Unknown, so verdicts on
    healthy captures are untouched."""
    best_rarity: str | None = None
    best_count = 0
    best_second = 0
    for dy in (-4, -2, 0, 2, 4):
        counts = rarity_text_color_counts(image, y + dy, droid, row_height=row_height)
        colored = {rarity: count for rarity, count in counts.items() if rarity != "Common"}
        rarity = max(colored, key=lambda r: colored[r])
        if colored[rarity] > best_count:
            best_rarity = rarity
            best_count = colored[rarity]
            best_second = max(count for r, count in colored.items() if r != rarity)
    if best_rarity is None or best_count < 300 or best_second > best_count * 0.25:
        return None
    shape_rarity, shape_score, _margin, shape_source = classify_rarity_word_shape(
        image, y, shape_templates, row_height=row_height, word_matches=word_matches
    )
    if shape_rarity != best_rarity or shape_score < 0.50:
        return None
    score = min(0.99, 0.60 + shape_score * 0.5)
    margin = min(0.99, 1.0 - best_second / best_count)
    return best_rarity, float(score), float(margin), (
        f"extra:color:{best_rarity}:{best_count}+{shape_source}:{shape_score:.2f}"
    )


def classify_rarity_roi(
    image: np.ndarray,
    y: int,
    droid: str,
    templates_by_droid: dict[str, list[RarityRoiTemplate]],
    shape_templates: list[Template] | None = None,
    *,
    row_height: int,
    word_matches: list[RarityCandidate] | None = None,
) -> tuple[str, float, float, str]:
    templates = templates_by_droid.get(droid, [])
    if not templates:
        verdict = classify_rarity_color(image, y, droid, row_height=row_height)
        if droid == "Galactic" and verdict[0] != "Unknown":
            text_verdict = classify_rarity_text_color(
                image,
                y,
                droid,
                row_height=row_height,
            )
            text_counts = rarity_text_color_counts(image, y, droid, row_height=row_height)
            shape_verdict = classify_rarity_word_shape(
                image,
                y,
                shape_templates or [],
                row_height=row_height,
                word_matches=word_matches,
            )
            override = galactic_rarity_text_override(
                verdict[0], text_verdict, shape_verdict, text_counts
            )
            if override is not None:
                return override

        if droid == "Galactic" and verdict[0] not in {"Unknown", "Common"}:
            # Galactic currently has no family-specific rarity ROI templates,
            # so its normal verdict comes directly from raw color totals. A
            # large cyan/blue prop behind a real white "(Common)" word can
            # therefore clear the Rare floor even though almost none of those
            # pixels have text-like edges. Prefer a strong Common text verdict
            # over that unsupported colored-background verdict. Real Galactic
            # Rare/Epic/Legendary/Mythic rows retain strong text-colored pixels
            # for their own rarity and do not enter this branch.
            text_rarity, text_score, text_margin, text_source = text_verdict
            colored_word_supported = (
                text_counts[verdict[0]] >= RARITY_COLOR_THRESHOLDS[verdict[0]]
            )
            if (
                text_rarity == "Common"
                and text_score >= 0.86
                and text_margin >= 0.30
                and not colored_word_supported
            ):
                return (
                    "Common",
                    text_score,
                    text_margin,
                    f"galactic-text-over-background:{text_source}",
                )
        if verdict[0] != "Unknown" or droid != "Galactic":
            return verdict

        # The compact Galactic Rare capture retains a clear cyan rarity word
        # but lands just below the global 650px Rare floor after resampling.
        # Rescue only when Rare is the dominant saturated color and nearby
        # Common pixels do not outnumber it by more than 25%; that excludes
        # white spawn text and adjacent Common rows from becoming Rare.
        counts = rarity_color_counts(image, y, droid, row_height=row_height)
        rare_count = counts["Rare"]
        other_colored = max(counts[name] for name in ("Epic", "Legendary", "Mythic"))
        if (
            rare_count >= int(RARITY_COLOR_THRESHOLDS["Rare"] * 0.80)
            and rare_count >= other_colored * 1.5
            and counts["Common"] <= rare_count * 1.25
        ):
            score = min(0.99, rare_count / RARITY_COLOR_THRESHOLDS["Rare"])
            margin = min(0.99, (rare_count - other_colored) / RARITY_COLOR_THRESHOLDS["Rare"])
            return "Rare", float(score), float(margin), f"galactic-weak-color:Rare:{rare_count}"
        return verdict

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
        word_matches=word_matches,
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

    # Priority rarities decided by color counts need corroboration; measured
    # 2026-07-07 on live debug uploads (Downloads tt batch), three FP modes:
    #   - Epic was exempt from confirmation entirely: a purple mystery-box
    #     card + white banner text alerted Beskar Epic with no spawn line.
    #   - A billboard's literal "LEGENDARY" badge passes the text-color
    #     confirm (it IS orange outlined text), but no rarity-word template
    #     matches anywhere near the row (own-shape 0.00; every real alert
    #     row measures >=0.21) -> own-shape floor 0.15.
    #   - Orange shop cards behind a real "(Rare)" row outvoted Rare on raw
    #     color; only there does a SECOND rarity's text count clear its own
    #     floor while beating the winner on word shape -> dual-word veto.
    if color_rarity in {"Epic", "Legendary", "Mythic"}:
        own_shape: float | None = None
        if word_matches is not None:
            own_shape = rarity_word_shape_score_from_matches(
                word_matches, y, color_rarity, row_height=row_height
            )
        elif shape_templates:
            own_shape = rarity_word_shape_score(
                image, y, color_rarity, shape_templates, row_height=row_height
            )
        text_confirms = text_rarity == color_rarity and text_score >= 0.75
        shape_confirms = (
            own_shape >= 0.50
            if own_shape is not None
            else (shape_rarity == color_rarity and shape_score >= 0.50)
        )
        veto = None
        # Shape matching alone is not enough to confirm Legendary/Mythic: a
        # large orange panel in the 1.1.5 report batch scored 0.54 against a
        # Legendary word template despite containing no Legendary word. Epic
        # keeps the old shape fallback because three real, partially occluded
        # Epic rows in that batch lose too many colour components after
        # downscale normalization.
        if color_rarity in {"Legendary", "Mythic"} and not text_confirms:
            veto = "unconfirmed-text"
        elif color_rarity == "Epic" and not text_confirms and not shape_confirms:
            veto = "unconfirmed"
        elif own_shape is not None and own_shape < 0.15:
            veto = f"shape-floor:{own_shape:.2f}"
        elif own_shape is not None:
            text_counts = rarity_text_color_counts(image, y, droid, row_height=row_height)
            for other in RARITIES:
                if other == color_rarity:
                    continue
                # Common joins the veto at a raised floor: the white spawn
                # phrase bleeds 200-620 Common px into every row crop, but a
                # literal "(Common)" word measures 1616 (live FP 2026-07-08:
                # a real Beskar Common spawn in front of an orange COMPLETE
                # quest banner alerted Beskar Legendary at color 2597).
                floor = 900 if other == "Common" else RARITY_COLOR_THRESHOLDS[other]
                if text_counts[other] < floor:
                    continue
                other_shape = rarity_word_shape_score_from_matches(
                    word_matches, y, other, row_height=row_height
                )
                if other_shape >= own_shape:
                    veto = f"dual-word:{other}:{text_counts[other]}:{other_shape:.2f}"
                    break
        if veto is not None:
            return (
                "Unknown",
                color_score,
                color_margin,
                f"unconfirmed-priority-color[{veto}]:{color_source};{text_source};{shape_source}",
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


def _word_search_edges(image: np.ndarray) -> np.ndarray:
    """Edge map for rarity-word template search, restricted to the columns
    where the word can appear (center-x <= 430, widest template ~250px).
    Cuts matchTemplate cost ~2x vs the full 845px band."""
    return preprocess_for_template(image[:, : min(image.shape[1], 560)])


def collect_word_matches(
    image: np.ndarray,
    templates: list[Template],
    *,
    min_score: float = 0.20,
    per_template_cap: int = 48,
) -> list[RarityCandidate]:
    """One matchTemplate pass over the band for ALL rarity-word templates.

    Every consumer (row seeding, word-shape argmax, per-rarity shape gate)
    filters this shared list instead of re-scanning. The scan dominated
    per-frame cost when repeated per row. Raw matches, no NMS: the Mythic
    alert gate needs per-rarity maxima that NMS would suppress.
    """
    edge = _word_search_edges(image)
    matches: list[RarityCandidate] = []
    for template in templates:
        templ = template.image
        if templ.shape[0] > edge.shape[0] or templ.shape[1] > edge.shape[1]:
            continue
        result = cv2.matchTemplate(edge, templ, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= min_score)
        if ys.size == 0:
            continue
        scores = result[ys, xs]
        if ys.size > per_template_cap:
            keep = np.argpartition(scores, -per_template_cap)[-per_template_cap:]
            ys, xs, scores = ys[keep], xs[keep], scores[keep]
        for x, y, score in zip(xs.tolist(), ys.tolist(), scores.tolist()):
            center_x = x + templ.shape[1] // 2
            if center_x < 175 or center_x > 430:
                continue
            matches.append(
                RarityCandidate(
                    rarity=template.rarity,
                    score=float(score),
                    box=(x, y, x + templ.shape[1], y + templ.shape[0]),
                    template_name=template.path.name,
                )
            )
    return matches


def rarity_word_shape_score(
    image: np.ndarray,
    y: int,
    rarity: str,
    templates: list[Template],
    *,
    row_height: int,
) -> float:
    """Best word-shape template score for one specific rarity near a row.

    Unlike classify_rarity_word_shape (argmax across rarities), this answers
    "how strongly does THIS rarity's word appear here", which is needed because a
    busy background can push another rarity's template above the true one.
    """
    matches = collect_word_matches(image, [t for t in templates if t.rarity == rarity])
    return rarity_word_shape_score_from_matches(matches, y, rarity, row_height=row_height)


def rarity_word_shape_score_from_matches(
    matches: list[RarityCandidate],
    y: int,
    rarity: str,
    *,
    row_height: int,
) -> float:
    row_center = y + row_height // 2
    best = 0.0
    for match in matches:
        if match.rarity != rarity:
            continue
        # 26px: detected rows can sit ~15px off the true row top, and word
        # centers land low in the row; stays under the 33px row spacing so
        # an adjacent row's word can't be borrowed.
        if abs(((match.box[1] + match.box[3]) // 2) - row_center) > 26:
            continue
        best = max(best, match.score)
    return best


def rarity_candidates(
    image: np.ndarray,
    templates: list[Template],
    threshold: float,
    *,
    matches: list[RarityCandidate] | None = None,
) -> list[RarityCandidate]:
    if matches is None:
        matches = collect_word_matches(image, templates, min_score=threshold)
    candidates = [match for match in matches if match.score >= threshold]

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
    matches: list[RarityCandidate] | None = None,
) -> list[int]:
    h = image.shape[0]
    rows: list[tuple[int, float]] = []
    for rarity_match in rarity_candidates(image, templates, threshold, matches=matches):
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
        extra_checks: bool = False,
    ) -> None:
        self.templates = load_templates(template_dir)
        self.rarity_roi_templates = load_rarity_roi_templates(Path(template_dir) / "rarity_rois")
        self.droid_word_templates = load_droid_word_templates(Path(template_dir) / "droid_words")
        self.crafted_phrase_templates = load_edge_templates(Path(template_dir) / "crafted_phrases")
        self.spawn_line_templates = load_edge_templates(Path(template_dir) / "spawn_line")
        self.rarity_threshold = rarity_threshold
        self.droid_threshold = droid_threshold
        self.row_height = row_height
        self.extra_checks = extra_checks
        # Rows that looked like real alerts but were dropped, refreshed each
        # detect() call; the watcher surfaces these in debug mode.
        self.last_rejections: list[dict] = []

    def detect(self, image: np.ndarray, extra_row_ys: list[int] | None = None) -> list[Detection]:
        h, w = image.shape[:2]
        self.last_rejections = []
        # Single template pass per frame; every downstream consumer filters
        # this list. min_score 0.20 covers the lowest threshold in use.
        word_matches = collect_word_matches(image, self.templates, min_score=0.20)
        row_ys = row_positions_from_rarity_candidates(
            image,
            self.templates,
            threshold=self.rarity_threshold,
            row_height=self.row_height,
            matches=word_matches,
        )
        # Extra seeds (e.g. from the resolution-relative row finder) go through
        # the exact same per-row gates; template-seeded rows take precedence.
        for extra_y in extra_row_ys or []:
            extra_y = max(0, min(h - self.row_height, int(extra_y)))
            if all(abs(extra_y - y) > 26 for y in row_ys):
                row_ys.append(extra_y)
        candidates: list[Detection] = []

        def reject(y: int, reason: str, droid: str = "?", detail: str = "") -> None:
            self.last_rejections.append(
                {"y": int(y), "droid": droid, "reason": reason, "detail": detail}
            )

        for y in sorted(row_ys):
            row = image[y : y + self.row_height, :]
            if has_crafted_phrase(row, self.crafted_phrase_templates):
                reject(y, "crafted-phrase")
                continue
            galactic_word_verdict = None
            if not has_spawn_phrase_structure(row):
                # Resampling around 0.75x can move a real spawn line just
                # below the generic 700-pixel phrase floor. Only relax that
                # floor when the independent colour + literal Galactic word
                # recognizer agrees, so other chat/HUD text keeps the stricter
                # false-positive protection.
                galactic_word_verdict = classify_galactic_droid_word(
                    row, self.droid_word_templates
                )
                if galactic_word_verdict is None or not has_spawn_phrase_structure(
                    row, min_white_edge_pixels=600
                ):
                    continue

            # Word-text verdict first: background-proof and unambiguous when
            # it fires. Legacy icon-window path (plus icon-structure gate)
            # only for rows without strong word evidence.
            word_verdict = (
                galactic_word_verdict
                or classify_galactic_droid_word(row, self.droid_word_templates)
                or classify_droid_word(row)
                or classify_beskar_droid_word(row, self.droid_word_templates)
            )
            if word_verdict is not None:
                droid, droid_score = word_verdict
                if droid_score < self.droid_threshold:
                    reject(y, "weak-droid-word", droid, f"score={droid_score:.2f}")
                    continue
                # Gray custom names can resemble Beskar. When a strong icon
                # verdict disagrees, require the literal Beskar word shape.
                # Very strong gray text remains a safe occlusion fallback (one
                # valid report row has a nameplate over most of the word).
                if droid == "Beskar":
                    icon_droid, icon_score = best_droid_type(row)
                    profile = droid_word_text_profile(row)
                    if icon_droid != "Beskar" and icon_score >= 0.75:
                        word_shape = droid_word_shape_score(row, droid, self.droid_word_templates)
                        if word_shape < 0.40 and profile["gray"] < 1800:
                            reject(
                                y,
                                "conflicting-droid-word",
                                droid,
                                f"icon={icon_droid}:{icon_score:.2f};shape={word_shape:.2f}",
                            )
                            continue
            else:
                droid, droid_score = best_droid_type(row)
                if droid_score < self.droid_threshold:
                    reject(y, "weak-droid-icon", droid, f"score={droid_score:.2f}")
                    continue
                if not has_droid_icon_structure(row, droid):
                    reject(y, "no-icon-structure", droid)
                    continue

            rarity, rarity_score, rarity_margin, template_name = classify_rarity_roi(
                image,
                y,
                droid,
                self.rarity_roi_templates,
                self.templates,
                row_height=self.row_height,
                word_matches=word_matches,
            )
            # Galactic Rare regularly lands just under the global cyan floor
            # after 0.75x normalization. Its rescue already requires both a
            # dominant text-shaped color and the literal rarity-word shape,
            # so keep that high-confidence recall path active even when the
            # broader HDR/washed-out option is disabled.
            if rarity == "Unknown" and (self.extra_checks or droid == "Galactic"):
                rescued = rescue_weak_color_rarity(
                    image,
                    y,
                    droid,
                    self.templates,
                    row_height=self.row_height,
                    word_matches=word_matches,
                )
                if rescued is not None:
                    rarity, rarity_score, rarity_margin, template_name = rescued
            if rarity == "Unknown":
                reject(y, "unknown-rarity", droid, template_name)
                continue

            # A priority word must be bound to a phrase-derived row seed. A
            # 44px candidate window overlaps the next 32-33px chat line, so the
            # old generic white-edge gate could borrow a real spawn phrase for
            # an adjacent rebirth/crafting line. Every real alert in both the
            # existing fixtures and the 149-positive 1.1.5 batch sits within
            # 13px of its phrase seed; use a small safety margin for rounding.
            if (droid, rarity) in PRIORITY_ALERTS and (
                not extra_row_ys or min(abs(y - phrase_y) for phrase_y in extra_row_ys) > 16
            ):
                reject(y, "no-aligned-spawn-phrase", droid, rarity)
                continue

            # Legendary-only spawn-line confirmation: orange usernames/badges
            # on craft/rebirth/chat/promo rows read as Legendary text but the
            # row is not a spawn line. Require the literal "spawned at the"
            # phrase in its fixed position (measured clean on the 2026-07-08
            # live batch). Epic/Mythic keep the looser white-text gate.
            if rarity == "Legendary" and not has_spawn_line_phrase(
                image, y, self.spawn_line_templates, row_height=self.row_height
            ):
                reject(y, "no-spawn-line-phrase", droid, "Legendary")
                continue

            score = (rarity_score * 0.72) + (droid_score * 0.28)
            shape_score = 1.0
            if rarity == "Mythic":
                shape_score = rarity_word_shape_score_from_matches(
                    word_matches, y, rarity, row_height=self.row_height
                )
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
                    shape_score=float(shape_score),
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
