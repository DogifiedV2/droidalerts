from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from ..config import templates_dir
from .names import DROID_NAMES


MODEL_FILE = "belt_identity.onnx"
MANIFEST_FILE = "belt_identity.json"
UNKNOWN_IDENTITY = "UNKNOWN"
EXPECTED_CLASSES = tuple(DROID_NAMES) + (UNKNOWN_IDENTITY,)


def belt_identity_model_path() -> Path:
    return templates_dir() / MODEL_FILE


def belt_identity_manifest_path() -> Path:
    return templates_dir() / MANIFEST_FILE


@dataclass(frozen=True)
class LearnedIdentityResult:
    name: str
    confidence: float
    runner_up_name: str
    margin: float


class LearnedIdentityModel:
    """Small, CPU-only ONNX classifier used to corroborate card artwork.

    The model is deliberately independent from the HOG template library. A
    disagreement can therefore make the detector abstain instead of turning
    one visual failure mode into a confident alert.
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        manifest_path: str | Path | None = None,
    ) -> None:
        self.model_path = (
            Path(model_path) if model_path is not None else belt_identity_model_path()
        )
        self.manifest_path = (
            Path(manifest_path)
            if manifest_path is not None
            else belt_identity_manifest_path()
        )
        manifest = self._load_manifest()
        self.classes = tuple(str(value) for value in manifest["classes"])
        self.input_size = int(manifest["input_size"])
        self.batch_size = int(manifest.get("batch_size", 1))
        self.mean = np.asarray(manifest["mean"], dtype=np.float32).reshape(1, 1, 3)
        self.standard_deviation = np.asarray(
            manifest["standard_deviation"],
            dtype=np.float32,
        ).reshape(1, 1, 3)
        if self.classes != EXPECTED_CLASSES:
            raise RuntimeError("Belt identity model classes do not match the droid list")
        if not 48 <= self.input_size <= 512:
            raise RuntimeError("Belt identity model input size is invalid")
        if not 1 <= self.batch_size <= 64:
            raise RuntimeError("Belt identity model batch size is invalid")
        if (
            self.mean.shape != (1, 1, 3)
            or self.standard_deviation.shape != (1, 1, 3)
            or not np.all(np.isfinite(self.mean))
            or not np.all(np.isfinite(self.standard_deviation))
            or np.any(self.standard_deviation <= 0)
        ):
            raise RuntimeError("Belt identity model normalization is invalid")

        try:
            self.net = cv2.dnn.readNetFromONNX(str(self.model_path))
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        except cv2.error as exc:
            raise RuntimeError(
                f"Belt identity model could not be loaded: {self.model_path}"
            ) from exc

    def predict(self, artwork: Sequence[np.ndarray]) -> list[LearnedIdentityResult]:
        if not artwork:
            return []
        tensors = [self._prepare(image) for image in artwork]
        results: list[LearnedIdentityResult] = []
        for offset in range(0, len(tensors), self.batch_size):
            chunk = tensors[offset : offset + self.batch_size]
            real_count = len(chunk)
            if real_count < self.batch_size:
                chunk.extend(
                    np.zeros_like(chunk[0])
                    for _ in range(self.batch_size - real_count)
                )
            blob = np.ascontiguousarray(np.stack(chunk), dtype=np.float32)
            try:
                self.net.setInput(blob)
                logits = np.asarray(self.net.forward(), dtype=np.float32)
            except cv2.error as exc:
                raise RuntimeError("Belt identity model inference failed") from exc
            if logits.shape != (self.batch_size, len(self.classes)):
                raise RuntimeError(
                    "Belt identity model returned an unexpected output shape"
                )
            for row in logits[:real_count]:
                probabilities = _softmax(row)
                order = np.argsort(probabilities)[::-1]
                best_index = int(order[0])
                runner_up_index = int(order[1])
                best_confidence = float(probabilities[best_index])
                results.append(
                    LearnedIdentityResult(
                        name=self.classes[best_index],
                        confidence=best_confidence,
                        runner_up_name=self.classes[runner_up_index],
                        margin=best_confidence
                        - float(probabilities[runner_up_index]),
                    )
                )
        return results

    def _prepare(self, image_bgr: np.ndarray) -> np.ndarray:
        if (
            not isinstance(image_bgr, np.ndarray)
            or image_bgr.ndim != 3
            or image_bgr.shape[2] != 3
            or image_bgr.size == 0
        ):
            raise ValueError("Belt identity artwork must be a non-empty BGR image")
        resized = cv2.resize(
            image_bgr,
            (self.input_size, self.input_size),
            interpolation=(
                cv2.INTER_AREA
                if max(image_bgr.shape[:2]) > self.input_size
                else cv2.INTER_CUBIC
            ),
        )
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        normalized = (rgb - self.mean) / self.standard_deviation
        return np.transpose(normalized, (2, 0, 1))

    def _load_manifest(self) -> dict[str, object]:
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Belt identity manifest could not be loaded: {self.manifest_path}"
            ) from exc
        if not isinstance(raw, dict):
            raise RuntimeError("Belt identity manifest must be a JSON object")
        required = {
            "version",
            "classes",
            "input_size",
            "mean",
            "standard_deviation",
        }
        if not required.issubset(raw):
            raise RuntimeError("Belt identity manifest is incomplete")
        if int(raw["version"]) != 1:
            raise RuntimeError("Belt identity manifest version is unsupported")
        expected_hash = str(raw.get("sha256", "")).strip().casefold()
        if expected_hash:
            try:
                actual_hash = hashlib.sha256(self.model_path.read_bytes()).hexdigest()
            except OSError as exc:
                raise RuntimeError(
                    f"Belt identity model could not be read: {self.model_path}"
                ) from exc
            if actual_hash != expected_hash:
                raise RuntimeError("Belt identity model checksum does not match its manifest")
        return raw


def _softmax(logits: np.ndarray) -> np.ndarray:
    values = np.asarray(logits, dtype=np.float32).reshape(-1)
    values = values - float(np.max(values))
    exponentials = np.exp(values)
    total = float(np.sum(exponentials))
    if not np.isfinite(total) or total <= 0:
        return np.full(values.shape, 1.0 / max(1, len(values)), dtype=np.float32)
    return exponentials / total
