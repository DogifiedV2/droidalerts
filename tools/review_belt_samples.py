from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from droid_alerts.belt.names import DROID_NAMES, droid_class


FAMILIES = ("Default", "Gold", "Diamond", "Rainbow", "Beskar")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") or "unknown"


def _load_metadata(path: Path) -> dict[str, object]:
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError(f"Metadata is not an object: {path}")
    image_path = path.parent / str(metadata["image_file"])
    if not image_path.is_file():
        raise ValueError(f"Image is missing: {image_path}")
    metadata["_source_metadata"] = str(path)
    metadata["_source_image"] = str(image_path)
    return metadata


def _write_ledger(root: Path, record: dict[str, object]) -> None:
    path = root / "reviewed.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def record_decision(
    root: Path,
    metadata: dict[str, object],
    *,
    decision: str,
    name: str = "",
    family: str = "",
) -> Path | None:
    reviewed_at = datetime.now(timezone.utc).isoformat()
    source_metadata = Path(str(metadata["_source_metadata"]))
    source_image = Path(str(metadata["_source_image"]))
    ledger_record = {
        "reviewed_at": reviewed_at,
        "decision": decision,
        "source_metadata": str(source_metadata),
        "source_image": str(source_image),
        "predicted_name": str(metadata.get("name", "")),
        "predicted_family": str(metadata.get("family", "")),
        "name": name,
        "family": family,
    }
    if decision != "confirmed":
        _write_ledger(root, ledger_record)
        return None
    if name not in DROID_NAMES:
        raise ValueError(f"Unknown droid name: {name}")
    if family not in FAMILIES:
        raise ValueError(f"Unknown card family: {family}")

    destination = root / "confirmed" / _slug(name)
    destination.mkdir(parents=True, exist_ok=True)
    sample_id = str(metadata.get("sample_id") or source_image.stem)
    image_path = destination / f"{sample_id}.png"
    metadata_path = destination / f"{sample_id}.json"
    suffix = 1
    while image_path.exists() or metadata_path.exists():
        image_path = destination / f"{sample_id}_{suffix}.png"
        metadata_path = destination / f"{sample_id}_{suffix}.json"
        suffix += 1
    shutil.copy2(source_image, image_path)
    confirmed = {
        key: value
        for key, value in metadata.items()
        if not str(key).startswith("_")
    }
    confirmed.update(
        {
            "image_file": image_path.name,
            "name": name,
            "family": family,
            "rarity": droid_class(name),
            "label_source": "human_review",
            "review_decision": "confirmed",
            "reviewed_at": reviewed_at,
            "use_for_identity": True,
            # A single correct family crop can still be an appearance outlier
            # that harms unrelated cards. Family training is opt-in only after
            # a developer curates several diverse examples.
            "use_for_family": False,
            "original_prediction": {
                "name": metadata.get("name", ""),
                "family": metadata.get("family", ""),
                "rarity": metadata.get("rarity", ""),
            },
        }
    )
    temporary = metadata_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(confirmed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(metadata_path)
    ledger_record["confirmed_metadata"] = str(metadata_path)
    _write_ledger(root, ledger_record)
    return metadata_path


def _reviewed_sources(root: Path) -> set[str]:
    ledger = root / "reviewed.jsonl"
    if not ledger.is_file():
        return set()
    reviewed = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            reviewed.add(str(record["source_metadata"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return reviewed


def _display_image(image: np.ndarray, lines: list[str]) -> np.ndarray:
    maximum_width = 1200
    maximum_height = 760
    scale = min(
        1.0,
        maximum_width / max(1, image.shape[1]),
        maximum_height / max(1, image.shape[0]),
    )
    if scale < 1.0:
        image = cv2.resize(
            image,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_AREA,
        )
    banner_height = 32 * len(lines) + 14
    canvas = np.full(
        (image.shape[0] + banner_height, max(760, image.shape[1]), 3),
        28,
        dtype=np.uint8,
    )
    canvas[banner_height : banner_height + image.shape[0], : image.shape[1]] = image
    for index, line in enumerate(lines):
        cv2.putText(
            canvas,
            line,
            (12, 28 + index * 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (235, 235, 235),
            1,
            cv2.LINE_AA,
        )
    return canvas


def _prompt_label(
    predicted_name: str,
    predicted_family: str,
) -> tuple[str, str] | None:
    name = input(f"Droid name [{predicted_name}]: ").strip() or predicted_name
    if name not in DROID_NAMES:
        print("Unknown droid name. Use the exact in-app spelling.")
        return None
    family = input(
        f"Family {', '.join(FAMILIES)} [{predicted_family}]: "
    ).strip() or predicted_family
    normalized = next(
        (item for item in FAMILIES if item.casefold() == family.casefold()),
        "",
    )
    if not normalized:
        print("Unknown family.")
        return None
    return name, normalized


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Review Belt Tracker video predictions before they are allowed "
            "into the confirmed template library."
        )
    )
    parser.add_argument(
        "review_root",
        type=Path,
        help="Extraction folder containing detections/ and review/.",
    )
    arguments = parser.parse_args()
    root = arguments.review_root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"Review folder does not exist: {root}")
    reviewed = _reviewed_sources(root)
    metadata_paths = sorted(
        list((root / "detections").glob("*/*.json"))
        + list((root / "review").glob("**/*.json"))
    )
    pending = [path for path in metadata_paths if str(path) not in reviewed]
    if not pending:
        print("No unreviewed samples remain.")
        return 0

    print("Keys: A accept, E edit, N not a card, U unknown, S skip, Q quit")
    print("Keys 1-5 choose Default, Gold, Diamond, Rainbow, or Beskar")
    window = "Belt sample review"
    for index, metadata_path in enumerate(pending, start=1):
        try:
            metadata = _load_metadata(metadata_path)
            image = cv2.imread(str(metadata["_source_image"]))
            if image is None:
                raise ValueError(f"Unreadable image: {metadata['_source_image']}")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(exc)
            continue
        name = str(metadata.get("name", ""))
        family = str(metadata.get("family", ""))
        while True:
            display = _display_image(
                image,
                [
                    f"{index}/{len(pending)} predicted: {name}  family: {family or 'UNKNOWN'}",
                    "A accept  E edit  N not-card  U unknown  S skip  Q quit  1-5 family",
                ],
            )
            cv2.imshow(window, display)
            key = cv2.waitKey(0) & 0xFF
            if ord("1") <= key <= ord("5"):
                family = FAMILIES[key - ord("1")]
                continue
            if key in {ord("q"), 27}:
                cv2.destroyAllWindows()
                return 0
            if key == ord("s"):
                break
            if key == ord("n"):
                record_decision(root, metadata, decision="not_card")
                break
            if key == ord("u"):
                record_decision(root, metadata, decision="unknown_identity")
                break
            if key == ord("e"):
                cv2.destroyWindow(window)
                label = _prompt_label(name, family)
                if label is not None:
                    name, family = label
                continue
            if key == ord("a"):
                if name not in DROID_NAMES or family not in FAMILIES:
                    print("Set both an exact droid name and family before accepting.")
                    continue
                path = record_decision(
                    root,
                    metadata,
                    decision="confirmed",
                    name=name,
                    family=family,
                )
                print(f"Confirmed: {path}")
                break
    cv2.destroyAllWindows()
    print(f"Confirmed templates: {root / 'confirmed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
