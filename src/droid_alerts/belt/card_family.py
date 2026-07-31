from __future__ import annotations

import cv2
import numpy as np

CARD_FAMILIES = ("Default", "Gold", "Diamond", "Beskar", "Rainbow", "Galactic")


def classify_card_family_border(
    frame_bgr: np.ndarray,
    name_box: tuple[int, int, int, int],
    card_box: tuple[int, int, int, int],
) -> tuple[str, float]:
    """Fallback for the distinctive Gold, Diamond, Rainbow, and Galactic frames.

    Default and Beskar are deliberately not guessed from color because both
    are low-saturation metal/gray under some lighting. The family label OCR is
    authoritative whenever it is available.
    """

    _name_x, name_y, _name_width, name_height = name_box
    card_x, _card_y, card_width, _card_height = card_box
    # A clipped card can expose unrelated colored scenery in this band.
    if name_height <= 0 or card_width < 5.0 * name_height:
        return "", 0.0

    y1 = max(0, round(name_y + 1.20 * name_height))
    y2 = min(frame_bgr.shape[0], round(name_y + 1.70 * name_height))
    x1 = max(0, round(card_x))
    x2 = min(frame_bgr.shape[1], round(card_x + card_width))
    crop = frame_bgr[y1:y2, x1:x2]
    if crop.size == 0 or crop.shape[0] < max(3, round(0.35 * name_height)):
        return "", 0.0

    hue, saturation, value = cv2.split(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
    valid = value > 60
    valid_count = int(valid.sum())
    if valid_count < max(20, round(crop.shape[0] * crop.shape[1] * 0.10)):
        return "", 0.0

    vivid = (saturation >= 70) & (value >= 100)
    vivid_count = int(vivid.sum())
    vivid_fraction = vivid_count / valid_count
    orange_fraction = float(
        ((hue >= 5) & (hue <= 35) & (saturation >= 80) & (value >= 100)).sum()
    ) / valid_count
    cyan_fraction = float(
        ((hue >= 75) & (hue <= 105) & (saturation >= 70) & (value >= 100)).sum()
    ) / valid_count
    galactic_fraction = float(
        ((hue >= 115) & (hue <= 160) & (saturation >= 70) & (value >= 100)).sum()
    ) / valid_count

    diverse_bins = 0
    hue_spread = 0
    if vivid_count:
        histogram = np.histogram(hue[vivid], bins=np.arange(0, 181, 15))[0]
        significant = max(5, round(vivid_count * 0.03))
        occupied = np.flatnonzero(histogram > significant)
        diverse_bins = len(occupied)
        bin_count = len(histogram)
        hue_spread = max(
            (
                min(abs(int(first) - int(second)), bin_count - abs(int(first) - int(second)))
                for first in occupied
                for second in occupied
            ),
            default=0,
        )

    # Galactic frames have a broad, persistent violet glow. Check this before
    # Rainbow. Galactic also occupies several hue bins, but the audited belt
    # captures kept at least half of the usable band in the violet range.
    if vivid_fraction >= 0.65 and galactic_fraction >= 0.45:
        return "Galactic", min(1.0, galactic_fraction / 0.65)

    # Gold frames pick up small red/magenta highlights as they move. Those
    # colors remain close on the circular hue wheel, unlike a real Rainbow
    # frame, which spans both warm and cool hues even when one section happens
    # to dominate the sampled band.
    if vivid_fraction >= 0.35 and diverse_bins >= 3 and hue_spread >= 4:
        return "Rainbow", min(1.0, vivid_fraction / 0.60)
    if orange_fraction >= 0.45:
        return "Gold", min(1.0, orange_fraction / 0.75)
    if cyan_fraction >= 0.45:
        return "Diamond", min(1.0, cyan_fraction / 0.75)
    return "", 0.0

