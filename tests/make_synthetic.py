"""Generate chat-detection screenshots for the shared resolution matrix.

The source is treated as a real fullscreen capture. It is resized uniformly at
Fortnite's HUD scale, then aligned so the source and target chat auto-regions
share the same top edge. This avoids the aspect-ratio stretching used by the
old ultrawide-only generator.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))
sys.path.insert(0, str(BASE_DIR / "tests"))

from droid_alerts.normalize import scale_from_screen  # noqa: E402
from droid_alerts.region import auto_box_percent  # noqa: E402
from resolution_matrix import RESOLUTION_CASES  # noqa: E402


DEFAULT_SOURCE = BASE_DIR / "tests" / "fixtures" / "root_screenshots" / "image.png"
DEFAULT_OUT = BASE_DIR / "tests" / "fixtures" / "synthetic"


def render_at_resolution(
    source: np.ndarray,
    *,
    target_width: int,
    target_height: int,
) -> np.ndarray:
    """Render one source screenshot without stretching its HUD pixels."""

    source_height, source_width = source.shape[:2]
    source_scale = scale_from_screen(source_height, source_width)
    target_scale = scale_from_screen(target_height, target_width)
    ratio = target_scale / source_scale
    interpolation = cv2.INTER_AREA if ratio < 1.0 else cv2.INTER_CUBIC
    scaled = cv2.resize(source, None, fx=ratio, fy=ratio, interpolation=interpolation)

    source_box = auto_box_percent(source_width, source_height)
    target_box = auto_box_percent(target_width, target_height)
    target_y = target_box.top - round(source_box.top * ratio)

    # Edge-replicated padding gives a stable background outside the resized
    # source. The alert/chat pixels themselves are copied only once and remain
    # uniformly scaled.
    canvas = np.empty((target_height, target_width, 3), dtype=np.uint8)
    canvas[:] = scaled[min(max(-target_y, 0), scaled.shape[0] - 1), -1]
    source_y = max(0, -target_y)
    dest_y = max(0, target_y)
    copy_height = min(scaled.shape[0] - source_y, target_height - dest_y)
    copy_width = min(scaled.shape[1], target_width)
    if copy_height > 0 and copy_width > 0:
        canvas[dest_y : dest_y + copy_height, :copy_width] = scaled[
            source_y : source_y + copy_height, :copy_width
        ]
        if copy_width < target_width:
            canvas[dest_y : dest_y + copy_height, copy_width:] = scaled[
                source_y : source_y + copy_height, copy_width - 1 : copy_width
            ]
        if dest_y > 0:
            canvas[:dest_y] = canvas[dest_y : dest_y + 1]
        if dest_y + copy_height < target_height:
            canvas[dest_y + copy_height :] = canvas[
                dest_y + copy_height - 1 : dest_y + copy_height
            ]
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate chat fixtures for every supported resolution."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    source = cv2.imread(str(args.source), cv2.IMREAD_COLOR)
    if source is None:
        raise FileNotFoundError(
            f"Source screenshot not found: {args.source}. Pass --source to a fullscreen capture."
        )

    args.out.mkdir(parents=True, exist_ok=True)
    for case in RESOLUTION_CASES:
        rendered = render_at_resolution(
            source,
            target_width=case.width,
            target_height=case.height,
        )
        output = args.out / f"image_{case.width}x{case.height}.png"
        if not cv2.imwrite(str(output), rendered):
            raise OSError(f"Could not write {output}")
        print(f"wrote {output.name} ({case.profile})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
