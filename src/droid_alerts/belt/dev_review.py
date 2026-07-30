from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil

from .names import DROID_NAMES, droid_class


CARD_FAMILIES = ("Default", "Gold", "Diamond", "Rainbow", "Beskar", "Galactic")
REVIEW_DECISIONS = (
    "confirmed",
    "unknown_identity",
    "unreadable",
    "not_card",
    "duplicate",
    "mixed_track",
)


def pending_track_manifests(session_dir: str | Path) -> list[Path]:
    session = Path(session_dir)
    return [
        path
        for path in sorted((session / "tracks").glob("track_*/manifest.json"))
        if _label_status(path) != "reviewed"
    ]


def load_track_manifest(path: str | Path) -> dict[str, object]:
    manifest_path = Path(path)
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Track manifest is not an object: {manifest_path}")
    value["_manifest_path"] = str(manifest_path)
    return value


def predicted_track_label(
    manifest: dict[str, object],
) -> tuple[str, str]:
    summary = manifest.get("summary")
    if not isinstance(summary, dict):
        return "", ""
    names = summary.get("predicted_names")
    families = summary.get("predicted_families")
    return _majority(names), _majority(families)


def track_frame_paths(
    manifest: dict[str, object],
) -> list[tuple[Path, dict[str, object]]]:
    manifest_path = Path(str(manifest.get("_manifest_path") or ""))
    frames = manifest.get("frames")
    if not manifest_path.is_file() or not isinstance(frames, list):
        return []
    result: list[tuple[Path, dict[str, object]]] = []
    for item in frames:
        if not isinstance(item, dict):
            continue
        image = manifest_path.parent / str(item.get("image") or "")
        if image.is_file():
            result.append((image, item))
    return result


def record_track_review(
    manifest_path: str | Path,
    *,
    decision: str,
    name: str = "",
    family: str = "",
    note: str = "",
    selected_image: str = "",
) -> Path | None:
    """Store a human label and optionally create an identity-only sample."""

    path = Path(manifest_path).resolve()
    manifest = load_track_manifest(path)
    decision = str(decision).strip()
    if decision not in REVIEW_DECISIONS:
        raise ValueError(f"Unknown Belt review decision: {decision}")
    name = str(name).strip()
    family = _normalize_family(family)
    if decision == "confirmed":
        if name not in DROID_NAMES:
            raise ValueError(f"Unknown droid name: {name}")
        if family not in CARD_FAMILIES:
            raise ValueError(f"Unknown card family: {family}")
    else:
        name = ""
        family = ""

    reviewed_at = datetime.now(timezone.utc).isoformat()
    review = {
        "decision": decision,
        "name": name,
        "family": family,
        "rarity": droid_class(name) if name else "",
        "note": str(note).strip()[:500],
        "reviewed_at": reviewed_at,
        "label_source": "human_review",
    }
    manifest["label_status"] = "reviewed"
    manifest["review"] = review
    _write_json_atomic(path, _without_private_keys(manifest))

    session_dir = path.parents[2]
    ledger_record = {
        "track_manifest": str(path.relative_to(session_dir)),
        **review,
    }
    output: Path | None = None
    if decision == "confirmed":
        image_path, frame = _select_frame(
            manifest,
            requested=selected_image,
        )
        output = _write_confirmed_sample(
            session_dir,
            manifest,
            image_path,
            frame,
            review,
        )
        ledger_record["confirmed_metadata"] = str(
            output.relative_to(session_dir)
        )
    _append_jsonl(session_dir / "reviewed.jsonl", ledger_record)
    return output


def _write_confirmed_sample(
    session_dir: Path,
    manifest: dict[str, object],
    source_image: Path,
    frame: dict[str, object],
    review: dict[str, object],
) -> Path:
    name = str(review["name"])
    family = str(review["family"])
    physical_track_id = int(manifest.get("physical_track_id") or 0)
    sample_id = f"{session_dir.name}_track_{physical_track_id:06d}"
    destination = session_dir / "confirmed" / _slug(name)
    destination.mkdir(parents=True, exist_ok=True)
    image_path = destination / f"{sample_id}.png"
    metadata_path = destination / f"{sample_id}.json"
    shutil.copy2(source_image, image_path)

    candidate = frame.get("candidate")
    if not isinstance(candidate, dict):
        candidate = {}
    metadata = {
        "sample_id": sample_id,
        "image_file": image_path.name,
        "name": name,
        "family": family,
        "rarity": droid_class(name),
        "label_source": "human_review",
        "review_decision": "confirmed",
        "reviewed_at": review["reviewed_at"],
        "use_for_identity": True,
        # Family training remains opt-in because one correct crop can still be
        # an appearance outlier for the global family classifier.
        "use_for_family": False,
        "physical_track_id": physical_track_id,
        "source_session": session_dir.name,
        "source_frame_number": frame.get("frame"),
        "source_frame_shape": frame.get("source_frame_shape", []),
        "card_box": frame.get("card_box", []),
        "art_box_in_crop": frame.get("art_box_in_crop", []),
        "quality_score": frame.get("quality_score", 0.0),
        "original_prediction": {
            "name": candidate.get("name", ""),
            "family": candidate.get("family", ""),
            "rarity": candidate.get("rarity", ""),
            "raw_best_similarity": candidate.get("raw_best_similarity", 0.0),
            "runner_up_identity": candidate.get("runner_up_identity", ""),
            "identity_margin": candidate.get("identity_margin", 0.0),
            "accepted": candidate.get("accepted", False),
            "reason": candidate.get("reason", ""),
        },
        "training_status": "human_reviewed_identity_only",
    }
    _write_json_atomic(metadata_path, metadata)
    return metadata_path


def _select_frame(
    manifest: dict[str, object],
    *,
    requested: str,
) -> tuple[Path, dict[str, object]]:
    available = track_frame_paths(manifest)
    if not available:
        raise ValueError("The reviewed track has no saved crops")
    if requested:
        for path, frame in available:
            if path.name == requested or str(path) == requested:
                return path, frame
        raise ValueError(f"Selected crop is not part of this track: {requested}")
    return max(
        available,
        key=lambda item: float(item[1].get("quality_score") or 0.0),
    )


def _label_status(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return ""
    return str(value.get("label_status") or "") if isinstance(value, dict) else ""


def _majority(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    entries: list[tuple[int, str]] = []
    for key, count in value.items():
        try:
            entries.append((int(count), str(key)))
        except (TypeError, ValueError):
            continue
    if not entries:
        return ""
    return max(entries, key=lambda item: (item[0], item[1]))[1]


def _normalize_family(value: str) -> str:
    candidate = str(value).strip()
    return next(
        (
            family
            for family in CARD_FAMILIES
            if family.casefold() == candidate.casefold()
        ),
        candidate,
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") or "unknown"


def _without_private_keys(value: dict[str, object]) -> dict[str, object]:
    return {
        key: item
        for key, item in value.items()
        if not str(key).startswith("_")
    }


def _append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def _write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
