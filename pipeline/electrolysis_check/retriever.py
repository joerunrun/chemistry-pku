"""Retrieve the closest correct and wrong electrolysis reference images."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
FEATURES_NPZ = DATA_DIR / "features.npz"
METADATA_JSON = DATA_DIR / "metadata.json"

MAX_SIDE = 1000
MIN_MATCHES = 4
MAX_QUERY_DESCRIPTORS = max(200, int(os.environ.get("CHEM_ELECTROLYSIS_MAX_QUERY_DESCRIPTORS", "100000")))
MAX_DB_DESCRIPTORS_PER_SAMPLE = max(200, int(os.environ.get("CHEM_ELECTROLYSIS_MAX_DB_DESCRIPTORS_PER_SAMPLE", "100000")))
AKAZE = cv2.AKAZE_create()
BF = cv2.BFMatcher(cv2.NORM_HAMMING)

_db: tuple[dict[str, Any], dict[str, np.ndarray], dict[str, np.ndarray]] | None = None


def _load_db() -> tuple[dict[str, Any], dict[str, np.ndarray], dict[str, np.ndarray]]:
    global _db
    if _db is not None:
        return _db
    if not METADATA_JSON.exists() or not FEATURES_NPZ.exists():
        raise FileNotFoundError("电解特征库未建立，请先运行 python -m pipeline.electrolysis_check.build_db")
    metadata = json.loads(METADATA_JSON.read_text(encoding="utf-8"))
    arrays: dict[str, np.ndarray] = {}
    with np.load(FEATURES_NPZ, allow_pickle=False) as npz:
        for key in npz.files:
            arrays[key] = npz[key].copy()
    flat = _build_flat_index(metadata, arrays)
    _db = (metadata, arrays, flat)
    return _db


def _build_flat_index(
    metadata: dict[str, Any],
    arrays: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    descriptors: list[np.ndarray] = []
    sample_indices: list[np.ndarray] = []
    for idx, sample in enumerate(metadata["samples"]):
        desc = arrays.get(sample["desc_key"])
        pts = arrays.get(sample["pts_key"])
        if desc is None or pts is None or len(desc) < MIN_MATCHES:
            continue
        desc = _sample_descriptors(desc, MAX_DB_DESCRIPTORS_PER_SAMPLE)
        descriptors.append(desc)
        sample_indices.append(np.full(len(desc), idx, dtype=np.int32))
    if not descriptors:
        return {
            "desc": np.zeros((0, 61), dtype=np.uint8),
            "sample_index": np.zeros((0,), dtype=np.int32),
        }
    return {
        "desc": np.concatenate(descriptors, axis=0),
        "sample_index": np.concatenate(sample_indices, axis=0),
    }


def _preprocess(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    if max(h, w) <= MAX_SIDE:
        working = image.copy()
    else:
        scale = MAX_SIDE / max(h, w)
        working = cv2.resize(
            image,
            (max(1, round(w * scale)), max(1, round(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _sample_descriptors(desc: np.ndarray, max_count: int) -> np.ndarray:
    if len(desc) <= max_count:
        return desc
    indices = np.linspace(0, len(desc) - 1, max_count, dtype=np.int32)
    return desc[indices]


def _extract(image_bytes: bytes) -> tuple[np.ndarray, np.ndarray | None]:
    data = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        return np.zeros((0, 2), dtype=np.float32), None
    gray = _preprocess(image)
    keypoints, descriptors = AKAZE.detectAndCompute(gray, None)
    if not keypoints or descriptors is None:
        return np.zeros((0, 2), dtype=np.float32), None
    return np.array([kp.pt for kp in keypoints], dtype=np.float32), _sample_descriptors(
        descriptors,
        MAX_QUERY_DESCRIPTORS,
    )


def _score_samples(
    query_desc: np.ndarray,
    samples: list[dict[str, Any]],
    flat: dict[str, np.ndarray],
) -> list[tuple[float, int, dict[str, Any]]]:
    db_desc = flat["desc"]
    sample_index = flat["sample_index"]
    if db_desc.shape[0] < MIN_MATCHES:
        return []

    raw_matches = BF.knnMatch(db_desc, query_desc, k=2)
    counts = np.zeros(len(samples), dtype=np.int32)
    for row_idx, pair in enumerate(raw_matches):
        if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance:
            counts[int(sample_index[row_idx])] += 1

    scores: list[tuple[float, int, dict[str, Any]]] = []
    for idx, good in enumerate(counts):
        sample = samples[idx]
        score = int(good) / (1.0 + sample["keypoint_count"] * 0.0005)
        scores.append((score, int(good), sample))
    return scores


def retrieve_pair(image_bytes: bytes, *, include_images: bool = True) -> dict[str, Any]:
    started = time.perf_counter()
    query_pts, query_desc = _extract(image_bytes)
    if query_desc is None or len(query_pts) < MIN_MATCHES:
        return {
            "correct": None,
            "wrong": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    metadata, _arrays, flat = _load_db()
    best: dict[str, tuple[float, int, dict[str, Any]] | None] = {
        "correct": None,
        "wrong": None,
    }

    for score, match_count, sample in _score_samples(query_desc, metadata["samples"], flat):
        label = sample["label"]
        if label not in best:
            continue
        current = best[label]
        if current is None or score > current[0]:
            best[label] = (score, match_count, sample)

    results: dict[str, Any] = {}
    for label, item in best.items():
        if item is None:
            results[label] = None
            continue
        score, match_count, sample = item
        image_path = Path(sample["image_path"])
        image_jpeg = image_path.read_bytes() if include_images and image_path.exists() else b""
        results[label] = {
            "sample_id": sample["sample_id"],
            "label": label,
            "image_file": sample["image_file"],
            "image_jpeg": image_jpeg,
            "description": sample.get("description", ""),
            "match_count": int(match_count),
            "score": round(float(score), 2),
        }

    results["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    print(
        "[electrolysis.retrieve] "
        f"correct={results['correct']['image_file'] if results['correct'] else None}, "
        f"wrong={results['wrong']['image_file'] if results['wrong'] else None}, "
        f"{results['elapsed_ms']:.0f} ms"
    )
    return results


def reload_db() -> None:
    global _db
    _db = None
    _load_db()
    print(f"[electrolysis.retrieve] db reloaded: {_db[0]['sample_count']} samples")
