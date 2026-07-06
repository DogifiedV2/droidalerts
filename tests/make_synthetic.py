"""Generate synthetic multi-resolution stress fixtures from image.png.

- 1920x1080 / 2560x1440: uniform resize (game UI scales with screen height).
- 3440x1440 ultrawide: right-pad with replicated edge. Real ultrawide keeps
  the left-anchored alert stack in place and widens FOV; stretching would be
  an unrealistic distortion.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE = FIXTURES / "root_screenshots" / "image.png"
OUT = FIXTURES / "synthetic"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = cv2.imread(str(SOURCE))
    if src is None:
        raise FileNotFoundError(SOURCE)

    for w, h in ((1920, 1080), (2560, 1440)):
        resized = cv2.resize(src, (w, h), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(OUT / f"image_{w}x{h}.png"), resized)
        print(f"wrote image_{w}x{h}.png")

    base = cv2.resize(src, (2560, 1440), interpolation=cv2.INTER_AREA)
    pad = np.repeat(base[:, -1:, :], 3440 - 2560, axis=1)
    ultrawide = np.concatenate([base, pad], axis=1)
    # Real 3440x1440 captures (2026-07-06) put the alert stack ~111px higher
    # than on 16:9 (row top ~43% of height vs ~50.6%), matching the top-40%
    # ultrawide auto-box. Shift content up so the synthetic agrees with the
    # measured machine instead of the old 16:9 placement.
    shift = 111
    ultrawide = np.concatenate(
        [ultrawide[shift:], np.repeat(ultrawide[-1:, :, :], shift, axis=0)], axis=0
    )
    cv2.imwrite(str(OUT / "image_3440x1440.png"), ultrawide)
    print("wrote image_3440x1440.png")


if __name__ == "__main__":
    main()
