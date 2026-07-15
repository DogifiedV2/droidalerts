from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np


def write_cv_image(
    path: str | Path,
    image: np.ndarray,
    params: Sequence[int] | None = None,
) -> Path:
    """Encode with OpenCV, then write through Python's Unicode-safe filesystem API."""
    target = Path(path)
    extension = target.suffix.lower()
    if not extension:
        raise ValueError(f"Image path has no extension: {target}")
    encode_params = [] if params is None else list(params)
    success, encoded = cv2.imencode(extension, image, encode_params)
    if not success:
        raise OSError(f"OpenCV could not encode image as {extension}: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encoded.tobytes())
    return target
