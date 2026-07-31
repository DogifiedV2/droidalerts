from __future__ import annotations

import argparse
from pathlib import Path
import sys

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from droid_alerts.belt.dev_capture import latest_dev_session
from droid_alerts.belt.dev_logging import belt_dev_dir
from droid_alerts.belt.dev_review import (
    CARD_FAMILIES,
    load_track_manifest,
    pending_track_manifests,
    predicted_track_label,
    record_track_review,
    track_frame_paths,
)
from droid_alerts.belt.names import DROID_NAMES


def _contact_sheet(
    manifest: dict[str, object],
    *,
    heading: str,
) -> np.ndarray:
    loaded: list[tuple[np.ndarray, str]] = []
    for image_path, frame in track_frame_paths(manifest):
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        loaded.append(
            (
                image,
                f"f{frame.get('frame', '?')} q{float(frame.get('quality_score') or 0):.2f}",
            )
        )
    if not loaded:
        canvas = np.full((360, 900, 3), 28, dtype=np.uint8)
    else:
        tile_height = 330
        tiles: list[np.ndarray] = []
        for image, label in loaded:
            scale = min(1.0, tile_height / max(1, image.shape[0]))
            resized = cv2.resize(
                image,
                (
                    max(1, round(image.shape[1] * scale)),
                    max(1, round(image.shape[0] * scale)),
                ),
                interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC,
            )
            tile = np.full(
                (tile_height + 34, max(180, resized.shape[1]), 3),
                28,
                dtype=np.uint8,
            )
            tile[: resized.shape[0], : resized.shape[1]] = resized
            cv2.putText(
                tile,
                label,
                (8, tile_height + 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (235, 235, 235),
                1,
                cv2.LINE_AA,
            )
            tiles.append(tile)
        canvas = np.concatenate(tiles, axis=1)
        if canvas.shape[1] > 1500:
            scale = 1500 / canvas.shape[1]
            canvas = cv2.resize(
                canvas,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA,
            )
    banner = np.full((92, max(900, canvas.shape[1]), 3), 28, dtype=np.uint8)
    cv2.putText(
        banner,
        heading,
        (12, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (240, 240, 240),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        banner,
        "A accept  E edit  U unknown  X unreadable  N not-card  D duplicate  M mixed-track  S skip  Q quit  1-6 family",
        (12, 68),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.50,
        (210, 210, 210),
        1,
        cv2.LINE_AA,
    )
    if canvas.shape[1] < banner.shape[1]:
        padded = np.full(
            (canvas.shape[0], banner.shape[1], 3),
            28,
            dtype=np.uint8,
        )
        padded[:, : canvas.shape[1]] = canvas
        canvas = padded
    return np.concatenate((banner, canvas), axis=0)


def _prompt_label(
    predicted_name: str,
    predicted_family: str,
) -> tuple[str, str] | None:
    typed_name = input(f"Droid name [{predicted_name}]: ").strip()
    requested_name = typed_name or predicted_name
    name = next(
        (
            candidate
            for candidate in DROID_NAMES
            if candidate.casefold() == requested_name.casefold()
        ),
        "",
    )
    if not name:
        print("Unknown droid name.")
        return None
    typed_family = input(
        f"Family {', '.join(CARD_FAMILIES)} [{predicted_family}]: "
    ).strip()
    requested_family = typed_family or predicted_family
    family = next(
        (
            candidate
            for candidate in CARD_FAMILIES
            if candidate.casefold() == requested_family.casefold()
        ),
        "",
    )
    if not family:
        print("Unknown family.")
        return None
    return name, family


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review physical tracks from a Blueprint Collection session."
    )
    parser.add_argument(
        "session",
        nargs="?",
        type=Path,
        help="Session folder. Defaults to the newest local session.",
    )
    arguments = parser.parse_args()
    session = arguments.session
    if session is None:
        session = latest_dev_session(belt_dev_dir())
        if session is None:
            parser.error("No Blueprint Collection sessions were found")
    session = session.expanduser().resolve()
    pending = pending_track_manifests(session)
    if not pending:
        print("No unreviewed physical tracks remain.")
        return 0

    window = "Blueprint Collection review"
    for index, manifest_path in enumerate(pending, start=1):
        manifest = load_track_manifest(manifest_path)
        name, family = predicted_track_label(manifest)
        while True:
            heading = (
                f"{index}/{len(pending)} track {manifest.get('physical_track_id')}  "
                f"predicted: {name or 'UNKNOWN'}  family: {family or 'UNKNOWN'}"
            )
            cv2.imshow(window, _contact_sheet(manifest, heading=heading))
            key = cv2.waitKey(0) & 0xFF
            if ord("1") <= key <= ord("6"):
                family = CARD_FAMILIES[key - ord("1")]
                continue
            if key in {27, ord("q")}:
                cv2.destroyAllWindows()
                return 0
            if key == ord("s"):
                break
            if key == ord("u"):
                record_track_review(
                    manifest_path,
                    decision="unknown_identity",
                )
                break
            if key == ord("x"):
                record_track_review(
                    manifest_path,
                    decision="unreadable",
                )
                break
            if key == ord("n"):
                record_track_review(manifest_path, decision="not_card")
                break
            if key == ord("d"):
                record_track_review(manifest_path, decision="duplicate")
                break
            if key == ord("m"):
                record_track_review(manifest_path, decision="mixed_track")
                break
            if key == ord("e"):
                cv2.destroyWindow(window)
                label = _prompt_label(name, family)
                if label is not None:
                    name, family = label
                continue
            if key == ord("a"):
                if name not in DROID_NAMES or family not in CARD_FAMILIES:
                    print("Set an exact droid name and family before accepting.")
                    continue
                output = record_track_review(
                    manifest_path,
                    decision="confirmed",
                    name=name,
                    family=family,
                )
                print(f"Confirmed: {output}")
                break
    cv2.destroyAllWindows()
    print(f"Review ledger: {session / 'reviewed.jsonl'}")
    print(f"Identity-only reviewed samples: {session / 'confirmed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
