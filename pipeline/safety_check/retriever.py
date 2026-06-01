"""
在线检索：新图来 → 提 AKAZE → 匹配特征库 → 返回 top-k 参考图 + 整改意见。
数据库只加载一次，后续调用复用缓存。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from pipeline.resource_paths import preferred_runtime_dir

HERE = Path(__file__).resolve().parent
DATA_DIR = preferred_runtime_dir("safety_check_data", HERE / "data")
IMAGES_DIR = DATA_DIR / "images"
FEATURES_NPZ = DATA_DIR / "features.npz"
METADATA_JSON = DATA_DIR / "metadata.json"

MAX_SIDE = 1000
MIN_MATCHES = 4
MAX_QUERY_DESCRIPTORS = max(200, int(os.environ.get("CHEM_SAFETY_MAX_QUERY_DESCRIPTORS", "1000")))
MAX_DB_DESCRIPTORS_PER_SAMPLE = max(200, int(os.environ.get("CHEM_SAFETY_MAX_DB_DESCRIPTORS_PER_SAMPLE", "1000")))
AKAZE = cv2.AKAZE_create()
BF = cv2.BFMatcher(cv2.NORM_HAMMING)

# 模块级缓存，避免每次检索都重新加载 37 万 keypoints
_db: tuple[dict, dict[str, np.ndarray], dict[str, np.ndarray]] | None = None


def _load_db() -> tuple[dict, dict[str, np.ndarray], dict[str, np.ndarray]]:
    global _db
    if _db is not None:
        return _db
    if not METADATA_JSON.exists() or not FEATURES_NPZ.exists():
        raise FileNotFoundError("特征库未建立，先运行 build_db.py")
    meta = json.loads(METADATA_JSON.read_text(encoding="utf-8"))
    arrays = {}
    with np.load(FEATURES_NPZ, allow_pickle=False) as npz:
        for key in npz.files:
            arrays[key] = npz[key].copy()
    flat = _build_flat_index(meta, arrays)
    _db = (meta, arrays, flat)
    return _db


def _build_flat_index(meta: dict, arrays: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    descriptors: list[np.ndarray] = []
    sample_indices: list[np.ndarray] = []
    for idx, sample in enumerate(meta["samples"]):
        desc = arrays.get(sample["desc_key"])
        pts = arrays.get(sample["pts_key"])
        if desc is None or pts is None or desc.shape[0] < MIN_MATCHES:
            continue
        desc = _sample_descriptors(desc, MAX_DB_DESCRIPTORS_PER_SAMPLE)
        descriptors.append(desc)
        sample_indices.append(np.full(desc.shape[0], idx, dtype=np.int32))
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
        working = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))),
                             interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _sample_descriptors(desc: np.ndarray, max_count: int) -> np.ndarray:
    if desc.shape[0] <= max_count:
        return desc
    indices = np.linspace(0, desc.shape[0] - 1, max_count, dtype=np.int32)
    return desc[indices]


def _extract(image_bytes: bytes) -> tuple[np.ndarray, np.ndarray | None]:
    data = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return np.zeros((0, 2), dtype=np.float32), None
    gray = _preprocess(img)
    kps, desc = AKAZE.detectAndCompute(gray, None)
    if not kps or desc is None:
        return np.zeros((0, 2), dtype=np.float32), None
    return np.array([kp.pt for kp in kps], dtype=np.float32), _sample_descriptors(desc, MAX_QUERY_DESCRIPTORS)


def _score_samples(
    query_desc: np.ndarray,
    samples: list[dict[str, Any]],
    flat: dict[str, np.ndarray],
) -> list[tuple[float, dict[str, Any], int]]:
    db_desc = flat["desc"]
    sample_index = flat["sample_index"]
    if db_desc.shape[0] < MIN_MATCHES:
        return []

    raw = BF.knnMatch(db_desc, query_desc, k=2)
    counts = np.zeros(len(samples), dtype=np.int32)
    for row_idx, pair in enumerate(raw):
        if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance:
            counts[int(sample_index[row_idx])] += 1

    scores: list[tuple[float, dict[str, Any], int]] = []
    for idx, good in enumerate(counts):
        sample = samples[idx]
        if good >= MIN_MATCHES:
            score = int(good) / (1.0 + sample["keypoint_count"] * 0.0005)
        else:
            score = 0.0
        scores.append((score, sample, int(good)))
    return scores


def retrieve(image_bytes: bytes, top_k: int = 5, *, include_images: bool = True) -> list[dict[str, Any]]:
    """输入新图 JPEG bytes，返回 top-k 相似参考图的信息列表。"""
    started = time.perf_counter()
    query_pts, query_desc = _extract(image_bytes)
    if query_desc is None or len(query_pts) < MIN_MATCHES:
        return []

    meta, _arrays, flat = _load_db()
    samples = meta["samples"]

    scores = _score_samples(query_desc, samples, flat)
    scores.sort(key=lambda x: x[0], reverse=True)

    seen_entries: set[int] = set()
    results: list[dict] = []

    for score, s, match_count in scores:
        ei = s["entry_index"]
        if ei in seen_entries:
            continue
        seen_entries.add(ei)

        img_path = IMAGES_DIR / s["image_file"]
        jpeg_bytes = img_path.read_bytes() if include_images and img_path.exists() else b""

        results.append({
            "entry_index": ei,
            "image_file": s["image_file"],
            "image_jpeg": jpeg_bytes,
            "title": s["title"],
            "rectification": s["rectification"],
            "match_count": int(match_count),
            "score": round(score, 2),
        })

        if len(results) >= top_k:
            break

    elapsed = (time.perf_counter() - started) * 1000
    print(f"[retrieve] matched {len(results)} entries in {elapsed:.0f} ms")
    return results


def reload_db() -> None:
    """强制重新加载数据库（离线建库后调用）。"""
    global _db
    _db = None
    _load_db()
    print(f"[retrieve] db reloaded: {_db[0]['sample_count']} samples")
