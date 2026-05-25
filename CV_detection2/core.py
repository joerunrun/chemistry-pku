from __future__ import annotations

import json
import math
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"
ANNOTATIONS_PATH = DATA_DIR / "annotations.json"
DB_JSON_PATH = DATA_DIR / "descriptor_db.json"
DB_NPZ_PATH = DATA_DIR / "descriptor_db.npz"
SIFT_DB_JSON_PATH = DATA_DIR / "sift_descriptor_db.json"
SIFT_DB_NPZ_PATH = DATA_DIR / "sift_descriptor_db.npz"

DEFAULT_MAX_SIDE = 1600
MIN_DB_KEYPOINTS = 4
MIN_GOOD_MATCHES = 8
MIN_INLIERS = 6
MIN_INLIER_RATIO = 0.25
MIN_SCALE = 0.35
MAX_SCALE = 2.8
LOWE_RATIO = 0.75
RELAXED_MIN_GOOD_MATCHES = 3
RELAXED_MIN_INLIERS = 2
RELAXED_MIN_INLIER_RATIO = 0.08
RELAXED_TOP_K_PER_LABEL = 6
RELAXED_OVERLAP_WEIGHT = 0.75
PIXEL_SCALES = [0.75, 0.9, 1.0, 1.1, 1.25]

COMPONENTS: list[dict[str, str]] = [
    {"label": "voltage_regulator", "name": "voltage_regulator", "color": "#fff200"},
    {"label": "heating_unit", "name": "heating_unit", "color": "#ff0000"},
    {"label": "distillation_core", "name": "distillation_core", "color": "#00b050"},
    {"label": "condenser_unit", "name": "condenser_unit", "color": "#00a2e8"},
    {"label": "receiving_assembly", "name": "receiving_assembly", "color": "#ff7f27"},
    {"label": "vacuum_gauge", "name": "vacuum_gauge", "color": "#a349a4"},
    {"label": "light_spot", "name": "light_spot", "color": "#ff0000"},
    {"label": "object_a", "name": "测试物体A", "color": "#ff3b30"},
    {"label": "object_b", "name": "测试物体B", "color": "#34c759"},
    {"label": "object_c", "name": "测试物体C", "color": "#007aff"},
    {"label": "object_d", "name": "测试物体D", "color": "#ffcc00"},
]

COMPONENT_LABELS = [item["label"] for item in COMPONENTS]
COMPONENT_BY_LABEL = {item["label"]: item for item in COMPONENTS}
COLOR_BGR = {
    "voltage_regulator": (0, 242, 255),
    "heating_unit": (0, 0, 255),
    "distillation_core": (80, 176, 0),
    "condenser_unit": (232, 162, 0),
    "receiving_assembly": (39, 127, 255),
    "vacuum_gauge": (164, 73, 163),
    "light_spot": (0, 0, 255),
    "object_a": (48, 59, 255),
    "object_b": (89, 199, 52),
    "object_c": (255, 122, 0),
    "object_d": (0, 204, 255),
}


class CV2Error(RuntimeError):
    pass


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 1)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_write_npz(path: Path, arrays: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            np.savez_compressed(handle, **arrays)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def decode_image(image_bytes: bytes, field_name: str = "image") -> np.ndarray:
    data = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise CV2Error(f"{field_name} image decode failed.")
    return image


def read_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise CV2Error(f"Could not read image: {path}")
    return image


def encode_image_data_url(image: np.ndarray, quality: int = 86) -> str:
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise CV2Error("Could not encode result image.")
    import base64

    payload = base64.b64encode(encoded.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{payload}"


def load_annotations() -> dict[str, Any]:
    data = read_json(ANNOTATIONS_PATH, {"version": 1, "references": []})
    if not isinstance(data, dict):
        raise CV2Error("annotations.json must be a JSON object.")
    data.setdefault("version", 1)
    data.setdefault("references", [])
    return data


def list_references() -> dict[str, Any]:
    annotations = load_annotations()
    db = read_json(DB_JSON_PATH, {"samples": [], "stats": {}})
    samples = db.get("samples", []) if isinstance(db, dict) else []
    by_reference: dict[str, dict[str, int]] = {}
    for sample in samples:
        reference_id = str(sample.get("reference_id", ""))
        label = str(sample.get("label", ""))
        if not reference_id or label not in COMPONENT_LABELS:
            continue
        by_reference.setdefault(reference_id, {})[label] = int(sample.get("keypoint_count", 0))

    references = []
    for item in annotations.get("references", []):
        reference_id = str(item.get("id", ""))
        references.append(
            {
                "id": reference_id,
                "original_filename": item.get("original_filename", ""),
                "filename": item.get("filename", ""),
                "image_size": item.get("image_size", {}),
                "labels": item.get("labels", {}),
                "keypoints": by_reference.get(reference_id, {}),
                "created_at": item.get("created_at", 0),
            }
        )
    return {"references": references, "database_stats": database_stats()}


def delete_reference_label(reference_id: str, label: str) -> dict[str, Any]:
    reference_id = reference_id.strip()
    label = label.strip()
    if not reference_id:
        raise CV2Error("Missing reference id.")
    if label not in COMPONENT_LABELS:
        raise CV2Error(f"Unsupported label: {label}")

    annotations = load_annotations()
    target_reference = None
    for reference in annotations.get("references", []):
        if str(reference.get("id", "")) == reference_id:
            target_reference = reference
            break
    if target_reference is None:
        raise CV2Error(f"Reference not found: {reference_id}")

    labels = target_reference.setdefault("labels", {})
    labels[label] = {"polygons": [], "bbox": [], "mask_area": 0}
    atomic_write_json(ANNOTATIONS_PATH, annotations)

    akaze_result = rebuild_descriptor_db()
    sift_result = None
    if SIFT_DB_JSON_PATH.exists() or SIFT_DB_NPZ_PATH.exists():
        sift_result = rebuild_sift_descriptor_db()

    return {
        "reference_id": reference_id,
        "label": label,
        "akaze_stats": akaze_result["stats"],
        "sift_stats": sift_result["stats"] if sift_result else None,
    }


def save_reference(
    *,
    image_bytes: bytes,
    original_filename: str,
    labels_payload: dict[str, Any],
    image_size_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_data_dirs()
    image = decode_image(image_bytes)
    height, width = image.shape[:2]
    if image_size_payload:
        expected_width = int(image_size_payload.get("width", width))
        expected_height = int(image_size_payload.get("height", height))
        if (expected_width, expected_height) != (width, height):
            raise CV2Error(
                f"Uploaded image is {width}x{height}, but annotation payload says "
                f"{expected_width}x{expected_height}."
            )

    reference_id = uuid.uuid4().hex[:12]
    suffix = Path(original_filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        suffix = ".jpg"
    filename = f"{reference_id}{suffix}"
    image_path = IMAGES_DIR / filename
    image_path.write_bytes(image_bytes)

    labels = normalize_labels_payload(labels_payload, width, height)
    now = time.time()
    reference = {
        "id": reference_id,
        "filename": filename,
        "original_filename": Path(original_filename).name,
        "image_size": {"width": width, "height": height},
        "labels": labels,
        "created_at": now,
    }

    annotations = load_annotations()
    annotations["references"].append(reference)
    atomic_write_json(ANNOTATIONS_PATH, annotations)
    db_result = rebuild_descriptor_db()

    return {
        "reference": reference,
        "database_stats": db_result["stats"],
        "keypoints": keypoints_for_reference(reference_id),
    }


def normalize_labels_payload(
    labels_payload: dict[str, Any],
    width: int,
    height: int,
) -> dict[str, Any]:
    labels: dict[str, Any] = {}
    for label in COMPONENT_LABELS:
        raw_polygons = labels_payload.get(label, [])
        polygons = normalize_polygons(raw_polygons, width, height, label)
        mask = polygons_to_mask(polygons, width, height)
        bbox = mask_bbox(mask)
        labels[label] = {
            "polygons": polygons,
            "bbox": bbox,
            "mask_area": int(np.count_nonzero(mask)),
        }
    return labels


def normalize_polygons(
    raw_polygons: Any,
    width: int,
    height: int,
    label: str,
) -> list[list[list[int]]]:
    if raw_polygons in (None, ""):
        return []
    if not isinstance(raw_polygons, list):
        raise CV2Error(f"{label} polygons must be a list.")
    polygons: list[list[list[int]]] = []
    for raw_polygon in raw_polygons:
        if not isinstance(raw_polygon, list):
            raise CV2Error(f"{label} polygon must be a list of points.")
        if len(raw_polygon) < 3:
            continue
        polygon: list[list[int]] = []
        for point in raw_polygon:
            if not isinstance(point, list) or len(point) != 2:
                raise CV2Error(f"{label} polygon point must be [x, y].")
            x = clamp(round(float(point[0])), 0, width - 1)
            y = clamp(round(float(point[1])), 0, height - 1)
            polygon.append([x, y])
        if len(polygon) >= 3:
            polygons.append(polygon)
    return polygons


def polygons_to_mask(
    polygons: list[list[list[int]]],
    width: int,
    height: int,
) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    for polygon in polygons:
        points = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
        cv2.fillPoly(mask, [points], 255)
    return mask


def mask_bbox(mask: np.ndarray) -> list[int]:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        return []
    return [
        int(ys.min()),
        int(xs.min()),
        int(ys.max()) + 1,
        int(xs.max()) + 1,
    ]


def keypoints_for_reference(reference_id: str) -> dict[str, int]:
    db = read_json(DB_JSON_PATH, {"samples": []})
    result = {label: 0 for label in COMPONENT_LABELS}
    for sample in db.get("samples", []):
        if sample.get("reference_id") == reference_id:
            result[str(sample.get("label"))] = int(sample.get("keypoint_count", 0))
    return result


def rebuild_descriptor_db(max_side: int = DEFAULT_MAX_SIDE) -> dict[str, Any]:
    ensure_data_dirs()
    annotations = load_annotations()
    samples: list[dict[str, Any]] = []
    unavailable_samples: list[dict[str, Any]] = []
    arrays: dict[str, np.ndarray] = {
        "__empty__": np.zeros((0,), dtype=np.float32),
    }
    build_started_at = time.perf_counter()

    for reference in annotations.get("references", []):
        reference_id = str(reference["id"])
        image_path = IMAGES_DIR / str(reference["filename"])
        if not image_path.exists():
            unavailable_samples.append(
                {
                    "reference_id": reference_id,
                    "label": "",
                    "reason": "image_missing",
                }
            )
            continue
        image = read_image(image_path)
        width = int(reference["image_size"]["width"])
        height = int(reference["image_size"]["height"])
        for label in COMPONENT_LABELS:
            label_data = reference.get("labels", {}).get(label, {})
            polygons = label_data.get("polygons", [])
            bbox = label_data.get("bbox", [])
            mask_area = int(label_data.get("mask_area", 0))
            if not polygons or not bbox or mask_area <= 0:
                unavailable_samples.append(
                    {
                        "reference_id": reference_id,
                        "label": label,
                        "reason": "empty_mask",
                        "keypoint_count": 0,
                    }
                )
                continue

            mask = polygons_to_mask(polygons, width, height)
            points, descriptors = extract_akaze_features(
                image,
                mask=mask,
                max_side=max_side,
            )
            keypoint_count = int(points.shape[0])
            if descriptors is None or keypoint_count < MIN_DB_KEYPOINTS:
                unavailable_samples.append(
                    {
                        "reference_id": reference_id,
                        "label": label,
                        "reason": "not_enough_keypoints",
                        "keypoint_count": keypoint_count,
                        "bbox": bbox,
                        "mask_area": mask_area,
                    }
                )
                continue

            sample_id = f"{reference_id}_{label}"
            points_key = f"{sample_id}_points"
            descriptors_key = f"{sample_id}_descriptors"
            arrays[points_key] = points.astype(np.float32)
            arrays[descriptors_key] = descriptors.astype(np.uint8)
            samples.append(
                {
                    "sample_id": sample_id,
                    "reference_id": reference_id,
                    "label": label,
                    "bbox": bbox,
                    "mask_area": mask_area,
                    "keypoint_count": keypoint_count,
                    "points_key": points_key,
                    "descriptors_key": descriptors_key,
                    "max_side": max_side,
                    "original_filename": reference.get("original_filename", ""),
                }
            )

    stats = compute_db_stats(samples, unavailable_samples)
    metadata = {
        "version": 1,
        "feature": "AKAZE",
        "max_side": max_side,
        "created_at": time.time(),
        "build_ms": elapsed_ms(build_started_at),
        "samples": samples,
        "unavailable_samples": unavailable_samples,
        "stats": stats,
    }
    atomic_write_npz(DB_NPZ_PATH, arrays)
    atomic_write_json(DB_JSON_PATH, metadata)
    return {"metadata": metadata, "stats": stats}


def compute_db_stats(
    samples: list[dict[str, Any]],
    unavailable_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    per_label: dict[str, dict[str, int]] = {}
    for label in COMPONENT_LABELS:
        label_samples = [sample for sample in samples if sample["label"] == label]
        label_unavailable = [
            sample for sample in unavailable_samples if sample.get("label") == label
        ]
        per_label[label] = {
            "sample_count": len(label_samples),
            "keypoint_count": sum(int(sample["keypoint_count"]) for sample in label_samples),
            "unavailable_count": len(label_unavailable),
        }
    return {
        "sample_count": len(samples),
        "total_keypoints": sum(int(sample["keypoint_count"]) for sample in samples),
        "per_label": per_label,
    }


def database_stats() -> dict[str, Any]:
    if not DB_JSON_PATH.exists():
        return {
            "sample_count": 0,
            "total_keypoints": 0,
            "per_label": {
                label: {"sample_count": 0, "keypoint_count": 0, "unavailable_count": 0}
                for label in COMPONENT_LABELS
            },
        }
    metadata = read_json(DB_JSON_PATH, {})
    return metadata.get("stats", {})


def extract_akaze_features(
    image: np.ndarray,
    *,
    mask: np.ndarray | None = None,
    max_side: int = DEFAULT_MAX_SIDE,
) -> tuple[np.ndarray, np.ndarray | None]:
    working, scale = resize_for_work(image, max_side)
    working_mask = None
    if mask is not None:
        if mask.shape[:2] != image.shape[:2]:
            raise CV2Error("Mask size does not match image size.")
        if scale != 1.0:
            working_mask = cv2.resize(
                mask,
                (working.shape[1], working.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
        else:
            working_mask = mask.copy()

    gray = prepare_gray(working)
    akaze = cv2.AKAZE_create()
    keypoints, descriptors = akaze.detectAndCompute(gray, working_mask)
    if not keypoints:
        return np.zeros((0, 2), dtype=np.float32), None
    points = np.array([kp.pt for kp in keypoints], dtype=np.float32)
    points[:, 0] /= scale
    points[:, 1] /= scale
    return points, descriptors


def prepare_gray(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def resize_for_work(image: np.ndarray, max_side: int) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    longest = max(width, height)
    if longest <= max_side:
        return image.copy(), 1.0
    scale = max_side / longest
    resized = cv2.resize(
        image,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, scale


def load_descriptor_db() -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    if not DB_JSON_PATH.exists() or not DB_NPZ_PATH.exists():
        raise CV2Error("Descriptor database not found. Add reference masks first.")
    metadata = read_json(DB_JSON_PATH, {})
    arrays: dict[str, np.ndarray] = {}
    with np.load(DB_NPZ_PATH, allow_pickle=False) as npz:
        for key in npz.files:
            arrays[key] = npz[key].copy()
    return metadata, arrays


def detect_image(
    target: np.ndarray,
    *,
    max_side: int = DEFAULT_MAX_SIDE,
    lowe_ratio: float = LOWE_RATIO,
) -> dict[str, Any]:
    total_started_at = time.perf_counter()
    metadata, arrays = load_descriptor_db()
    samples = metadata.get("samples", [])
    if not samples:
        raise CV2Error("Descriptor database has no usable samples.")

    extract_started_at = time.perf_counter()
    target_points, target_descriptors = extract_akaze_features(
        target,
        mask=None,
        max_side=max_side,
    )
    feature_extract_ms = elapsed_ms(extract_started_at)

    boxes: dict[str, list[int]] = {label: [] for label in COMPONENT_LABELS}
    detections: dict[str, dict[str, Any]] = {}
    component_ms: dict[str, float] = {}

    for label in COMPONENT_LABELS:
        component_started_at = time.perf_counter()
        label_samples = [sample for sample in samples if sample.get("label") == label]
        detection = match_label(
            label=label,
            samples=label_samples,
            arrays=arrays,
            target_points=target_points,
            target_descriptors=target_descriptors,
            target_shape=target.shape,
            lowe_ratio=lowe_ratio,
        )
        boxes[label] = detection.get("box", [])
        component_ms[label] = elapsed_ms(component_started_at)
        detection["component_ms"] = component_ms[label]
        detections[label] = detection

    return {
        "boxes": boxes,
        "detections": detections,
        "timings": {
            "feature_extract_ms": feature_extract_ms,
            "component_ms": component_ms,
            "match_total_ms": round(sum(component_ms.values()), 1),
            "total_ms": elapsed_ms(total_started_at),
        },
        "target_keypoints": int(target_points.shape[0]),
        "database_stats": metadata.get("stats", {}),
        "parameters": {
            "feature": "AKAZE",
            "max_side": max_side,
            "lowe_ratio": lowe_ratio,
            "min_good_matches": MIN_GOOD_MATCHES,
            "min_inliers": MIN_INLIERS,
            "min_inlier_ratio": MIN_INLIER_RATIO,
            "scale_range": [MIN_SCALE, MAX_SCALE],
        },
    }


def match_label(
    *,
    label: str,
    samples: list[dict[str, Any]],
    arrays: dict[str, np.ndarray],
    target_points: np.ndarray,
    target_descriptors: np.ndarray | None,
    target_shape: tuple[int, int, int],
    lowe_ratio: float,
) -> dict[str, Any]:
    if not samples:
        return empty_detection("no_reference_samples")
    if target_descriptors is None or target_points.shape[0] < 2:
        return empty_detection("not_enough_target_keypoints")

    candidates = []
    for sample in samples:
        candidate = match_sample(
            sample=sample,
            arrays=arrays,
            target_points=target_points,
            target_descriptors=target_descriptors,
            target_shape=target_shape,
            lowe_ratio=lowe_ratio,
        )
        candidates.append(candidate)

    accepted = [candidate for candidate in candidates if candidate["accepted"]]
    if accepted:
        accepted.sort(
            key=lambda item: (
                item["inliers"],
                item["inlier_ratio"],
                -item["mean_distance"],
            ),
            reverse=True,
        )
        best = accepted[0]
        return {
            "method": "akaze_affine",
            "score": best["score"],
            "good_matches": best["good_matches"],
            "inliers": best["inliers"],
            "inlier_ratio": best["inlier_ratio"],
            "mean_distance": best["mean_distance"],
            "scale": best["scale"],
            "reference_id": best["reference_id"],
            "sample_id": best["sample_id"],
            "box": best["box"],
            "failure_reason": None,
            "candidate_count": len(candidates),
        }

    candidates.sort(
        key=lambda item: (
            item.get("good_matches", 0),
            item.get("inliers", 0),
            item.get("inlier_ratio", 0.0),
        ),
        reverse=True,
    )
    best_failed = candidates[0] if candidates else {}
    return {
        "method": "not_found",
        "score": 0.0,
        "good_matches": int(best_failed.get("good_matches", 0)),
        "inliers": int(best_failed.get("inliers", 0)),
        "inlier_ratio": float(best_failed.get("inlier_ratio", 0.0)),
        "mean_distance": best_failed.get("mean_distance"),
        "scale": best_failed.get("scale"),
        "reference_id": best_failed.get("reference_id"),
        "sample_id": best_failed.get("sample_id"),
        "box": [],
        "failure_reason": best_failed.get("failure_reason", "no_valid_candidate"),
        "candidate_count": len(candidates),
    }


def empty_detection(reason: str) -> dict[str, Any]:
    return {
        "method": "not_found",
        "score": 0.0,
        "good_matches": 0,
        "inliers": 0,
        "inlier_ratio": 0.0,
        "mean_distance": None,
        "scale": None,
        "reference_id": None,
        "sample_id": None,
        "box": [],
        "failure_reason": reason,
        "candidate_count": 0,
    }


def match_sample(
    *,
    sample: dict[str, Any],
    arrays: dict[str, np.ndarray],
    target_points: np.ndarray,
    target_descriptors: np.ndarray,
    target_shape: tuple[int, int, int],
    lowe_ratio: float,
) -> dict[str, Any]:
    sample_id = str(sample["sample_id"])
    reference_id = str(sample["reference_id"])
    descriptors = arrays.get(str(sample["descriptors_key"]))
    source_points = arrays.get(str(sample["points_key"]))
    if descriptors is None or source_points is None:
        return rejected_candidate(sample, "sample_arrays_missing")
    if descriptors.shape[0] < MIN_GOOD_MATCHES:
        return rejected_candidate(sample, "not_enough_sample_descriptors")
    if target_descriptors.shape[0] < 2:
        return rejected_candidate(sample, "not_enough_target_descriptors")

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw_matches = matcher.knnMatch(descriptors, target_descriptors, k=2)
    good_matches = []
    for pair in raw_matches:
        if len(pair) != 2:
            continue
        first, second = pair
        if first.distance < lowe_ratio * second.distance:
            good_matches.append(first)

    good_count = len(good_matches)
    base = {
        "accepted": False,
        "sample_id": sample_id,
        "reference_id": reference_id,
        "good_matches": good_count,
        "inliers": 0,
        "inlier_ratio": 0.0,
        "mean_distance": None,
        "scale": None,
        "box": [],
        "score": 0.0,
    }
    if good_count < MIN_GOOD_MATCHES:
        base["failure_reason"] = "not_enough_good_matches"
        return base

    src = np.float32([source_points[match.queryIdx] for match in good_matches])
    dst = np.float32([target_points[match.trainIdx] for match in good_matches])
    affine, mask = cv2.estimateAffinePartial2D(
        src,
        dst,
        method=cv2.RANSAC,
        ransacReprojThreshold=5.0,
        maxIters=2000,
        confidence=0.99,
        refineIters=10,
    )
    if affine is None or mask is None:
        base["failure_reason"] = "affine_failed"
        return base

    inlier_mask = mask.ravel().astype(bool)
    inliers = int(inlier_mask.sum())
    inlier_ratio = inliers / max(1, good_count)
    distances = [match.distance for index, match in enumerate(good_matches) if inlier_mask[index]]
    mean_distance = float(np.mean(distances)) if distances else float("inf")
    scale = affine_scale(affine)

    base.update(
        {
            "inliers": inliers,
            "inlier_ratio": round(float(inlier_ratio), 3),
            "mean_distance": round(float(mean_distance), 3) if np.isfinite(mean_distance) else None,
            "scale": round(float(scale), 3),
        }
    )
    if inliers < MIN_INLIERS:
        base["failure_reason"] = "not_enough_inliers"
        return base
    if inlier_ratio < MIN_INLIER_RATIO:
        base["failure_reason"] = "weak_inlier_ratio"
        return base
    if not (MIN_SCALE <= scale <= MAX_SCALE):
        base["failure_reason"] = "scale_out_of_range"
        return base

    box = project_bbox_affine(sample.get("bbox", []), affine, target_shape)
    if not box:
        base["failure_reason"] = "projected_box_invalid"
        return base

    score = inliers * inlier_ratio / (1.0 + mean_distance / 60.0)
    base.update(
        {
            "accepted": True,
            "box": box,
            "score": round(float(score), 3),
            "failure_reason": None,
        }
    )
    return base


def rejected_candidate(sample: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "accepted": False,
        "sample_id": sample.get("sample_id"),
        "reference_id": sample.get("reference_id"),
        "good_matches": 0,
        "inliers": 0,
        "inlier_ratio": 0.0,
        "mean_distance": None,
        "scale": None,
        "box": [],
        "score": 0.0,
        "failure_reason": reason,
    }


def affine_scale(affine: np.ndarray) -> float:
    sx = float(np.linalg.norm(affine[0, :2]))
    sy = float(np.linalg.norm(affine[1, :2]))
    return (sx + sy) / 2.0


def project_bbox_affine(
    bbox: Any,
    affine: np.ndarray,
    target_shape: tuple[int, int, int],
) -> list[int]:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return []
    ymin, xmin, ymax, xmax = [float(value) for value in bbox]
    corners = np.float32(
        [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]]
    ).reshape(-1, 1, 2)
    projected = cv2.transform(corners, affine).reshape(-1, 2)
    xs = projected[:, 0]
    ys = projected[:, 1]
    height, width = target_shape[:2]
    out = [
        clamp(math.floor(float(ys.min())), 0, height - 1),
        clamp(math.floor(float(xs.min())), 0, width - 1),
        clamp(math.ceil(float(ys.max())), 1, height),
        clamp(math.ceil(float(xs.max())), 1, width),
    ]
    if out[2] <= out[0] or out[3] <= out[1]:
        return []
    return out


def draw_detections(image: np.ndarray, result: dict[str, Any]) -> np.ndarray:
    output = image.copy()
    boxes = result.get("boxes", {})
    detections = result.get("detections", {})
    line_width = max(3, round(min(output.shape[:2]) / 350))
    font_scale = max(0.75, min(output.shape[:2]) / 1600)
    for label in COMPONENT_LABELS:
        box = boxes.get(label, [])
        if not box:
            continue
        ymin, xmin, ymax, xmax = box
        color = COLOR_BGR[label]
        cv2.rectangle(output, (xmin, ymin), (xmax, ymax), color, line_width)

        detection = detections.get(label, {})
        score = detection.get("score")
        score_text = "" if score is None else f" {float(score):.2f}"
        text = f"{label}{score_text}"
        (text_width, text_height), baseline = cv2.getTextSize(
            text,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            max(1, line_width - 1),
        )
        label_top = max(0, ymin - text_height - baseline - 8)
        cv2.rectangle(
            output,
            (xmin, label_top),
            (xmin + text_width + 10, label_top + text_height + baseline + 8),
            color,
            -1,
        )
        cv2.putText(
            output,
            text,
            (xmin + 5, label_top + text_height + 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            max(1, line_width - 1),
            cv2.LINE_AA,
        )
    return output


def ensure_sift_descriptor_db(max_side: int = DEFAULT_MAX_SIDE) -> dict[str, Any]:
    if needs_sift_rebuild():
        return rebuild_sift_descriptor_db(max_side=max_side)
    metadata = read_json(SIFT_DB_JSON_PATH, {})
    if not metadata.get("samples"):
        return rebuild_sift_descriptor_db(max_side=max_side)
    return {"metadata": metadata, "stats": metadata.get("stats", {})}


def needs_sift_rebuild() -> bool:
    if not SIFT_DB_JSON_PATH.exists() or not SIFT_DB_NPZ_PATH.exists():
        return True
    if ANNOTATIONS_PATH.exists():
        db_mtime = min(SIFT_DB_JSON_PATH.stat().st_mtime, SIFT_DB_NPZ_PATH.stat().st_mtime)
        if ANNOTATIONS_PATH.stat().st_mtime > db_mtime:
            return True
    try:
        metadata = read_json(SIFT_DB_JSON_PATH, {})
    except json.JSONDecodeError:
        return True
    return not bool(metadata.get("samples"))


def rebuild_sift_descriptor_db(max_side: int = DEFAULT_MAX_SIDE) -> dict[str, Any]:
    if not hasattr(cv2, "SIFT_create"):
        raise CV2Error("Current OpenCV build does not provide SIFT_create.")

    ensure_data_dirs()
    annotations = load_annotations()
    samples: list[dict[str, Any]] = []
    unavailable_samples: list[dict[str, Any]] = []
    arrays: dict[str, np.ndarray] = {
        "__empty__": np.zeros((0,), dtype=np.float32),
    }
    build_started_at = time.perf_counter()

    for reference in annotations.get("references", []):
        reference_id = str(reference["id"])
        image_path = IMAGES_DIR / str(reference["filename"])
        if not image_path.exists():
            unavailable_samples.append(
                {"reference_id": reference_id, "label": "", "reason": "image_missing"}
            )
            continue
        image = read_image(image_path)
        width = int(reference["image_size"]["width"])
        height = int(reference["image_size"]["height"])
        for label in COMPONENT_LABELS:
            label_data = reference.get("labels", {}).get(label, {})
            polygons = label_data.get("polygons", [])
            bbox = label_data.get("bbox", [])
            mask_area = int(label_data.get("mask_area", 0))
            if not polygons or not bbox or mask_area <= 0:
                unavailable_samples.append(
                    {
                        "reference_id": reference_id,
                        "label": label,
                        "reason": "empty_mask",
                        "keypoint_count": 0,
                    }
                )
                continue

            mask = polygons_to_mask(polygons, width, height)
            points, descriptors = extract_sift_features(image, mask=mask, max_side=max_side)
            keypoint_count = int(points.shape[0])
            if descriptors is None or keypoint_count < MIN_DB_KEYPOINTS:
                unavailable_samples.append(
                    {
                        "reference_id": reference_id,
                        "label": label,
                        "reason": "not_enough_keypoints",
                        "keypoint_count": keypoint_count,
                        "bbox": bbox,
                        "mask_area": mask_area,
                    }
                )
                continue

            sample_id = f"{reference_id}_{label}_sift"
            points_key = f"{sample_id}_points"
            descriptors_key = f"{sample_id}_descriptors"
            arrays[points_key] = points.astype(np.float32)
            arrays[descriptors_key] = descriptors.astype(np.float32)
            samples.append(
                {
                    "sample_id": sample_id,
                    "reference_id": reference_id,
                    "label": label,
                    "bbox": bbox,
                    "mask_area": mask_area,
                    "keypoint_count": keypoint_count,
                    "points_key": points_key,
                    "descriptors_key": descriptors_key,
                    "max_side": max_side,
                    "original_filename": reference.get("original_filename", ""),
                }
            )

    stats = compute_db_stats(samples, unavailable_samples)
    metadata = {
        "version": 1,
        "feature": "SIFT",
        "max_side": max_side,
        "created_at": time.time(),
        "build_ms": elapsed_ms(build_started_at),
        "samples": samples,
        "unavailable_samples": unavailable_samples,
        "stats": stats,
    }
    atomic_write_npz(SIFT_DB_NPZ_PATH, arrays)
    atomic_write_json(SIFT_DB_JSON_PATH, metadata)
    return {"metadata": metadata, "stats": stats}


def extract_sift_features(
    image: np.ndarray,
    *,
    mask: np.ndarray | None = None,
    max_side: int = DEFAULT_MAX_SIDE,
) -> tuple[np.ndarray, np.ndarray | None]:
    if not hasattr(cv2, "SIFT_create"):
        raise CV2Error("Current OpenCV build does not provide SIFT_create.")

    working, scale = resize_for_work(image, max_side)
    working_mask = None
    if mask is not None:
        if mask.shape[:2] != image.shape[:2]:
            raise CV2Error("Mask size does not match image size.")
        if scale != 1.0:
            working_mask = cv2.resize(
                mask,
                (working.shape[1], working.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
        else:
            working_mask = mask.copy()

    gray = prepare_gray(working)
    sift = cv2.SIFT_create()
    keypoints, descriptors = sift.detectAndCompute(gray, working_mask)
    if not keypoints:
        return np.zeros((0, 2), dtype=np.float32), None
    points = np.array([kp.pt for kp in keypoints], dtype=np.float32)
    points[:, 0] /= scale
    points[:, 1] /= scale
    return points, descriptors


def load_sift_descriptor_db() -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    ensure_sift_descriptor_db()
    metadata = read_json(SIFT_DB_JSON_PATH, {})
    arrays: dict[str, np.ndarray] = {}
    with np.load(SIFT_DB_NPZ_PATH, allow_pickle=False) as npz:
        for key in npz.files:
            arrays[key] = npz[key].copy()
    return metadata, arrays


def detect_image_sift(
    target: np.ndarray,
    *,
    max_side: int = DEFAULT_MAX_SIDE,
    lowe_ratio: float = LOWE_RATIO,
) -> dict[str, Any]:
    total_started_at = time.perf_counter()
    ensure_sift_descriptor_db(max_side=max_side)
    metadata, arrays = load_sift_descriptor_db()
    samples = metadata.get("samples", [])
    if not samples:
        raise CV2Error("SIFT descriptor database has no usable samples.")

    extract_started_at = time.perf_counter()
    target_points, target_descriptors = extract_sift_features(
        target,
        mask=None,
        max_side=max_side,
    )
    feature_extract_ms = elapsed_ms(extract_started_at)

    boxes: dict[str, list[int]] = {label: [] for label in COMPONENT_LABELS}
    detections: dict[str, dict[str, Any]] = {}
    component_ms: dict[str, float] = {}

    for label in COMPONENT_LABELS:
        component_started_at = time.perf_counter()
        label_samples = [sample for sample in samples if sample.get("label") == label]
        detection = match_label_generic(
            label=label,
            samples=label_samples,
            arrays=arrays,
            target_points=target_points,
            target_descriptors=target_descriptors,
            target_shape=target.shape,
            lowe_ratio=lowe_ratio,
            norm_type=cv2.NORM_L2,
            method_name="sift_affine",
            distance_normalizer=160.0,
        )
        boxes[label] = detection.get("box", [])
        component_ms[label] = elapsed_ms(component_started_at)
        detection["component_ms"] = component_ms[label]
        detections[label] = detection

    return {
        "boxes": boxes,
        "detections": detections,
        "timings": {
            "feature_extract_ms": feature_extract_ms,
            "component_ms": component_ms,
            "match_total_ms": round(sum(component_ms.values()), 1),
            "total_ms": elapsed_ms(total_started_at),
        },
        "target_keypoints": int(target_points.shape[0]),
        "database_stats": metadata.get("stats", {}),
        "parameters": {
            "feature": "SIFT",
            "max_side": max_side,
            "lowe_ratio": lowe_ratio,
            "min_good_matches": MIN_GOOD_MATCHES,
            "min_inliers": MIN_INLIERS,
            "min_inlier_ratio": MIN_INLIER_RATIO,
            "scale_range": [MIN_SCALE, MAX_SCALE],
        },
    }


def match_label_generic(
    *,
    label: str,
    samples: list[dict[str, Any]],
    arrays: dict[str, np.ndarray],
    target_points: np.ndarray,
    target_descriptors: np.ndarray | None,
    target_shape: tuple[int, int, int],
    lowe_ratio: float,
    norm_type: int,
    method_name: str,
    distance_normalizer: float,
) -> dict[str, Any]:
    if not samples:
        return empty_detection("no_reference_samples")
    if target_descriptors is None or target_points.shape[0] < 2:
        return empty_detection("not_enough_target_keypoints")

    candidates = []
    for sample in samples:
        candidates.append(
            match_sample_generic(
                sample=sample,
                arrays=arrays,
                target_points=target_points,
                target_descriptors=target_descriptors,
                target_shape=target_shape,
                lowe_ratio=lowe_ratio,
                norm_type=norm_type,
                distance_normalizer=distance_normalizer,
            )
        )

    accepted = [candidate for candidate in candidates if candidate["accepted"]]
    if accepted:
        accepted.sort(
            key=lambda item: (
                item["inliers"],
                item["inlier_ratio"],
                -item["mean_distance"],
            ),
            reverse=True,
        )
        best = accepted[0]
        return {
            "method": method_name,
            "score": best["score"],
            "good_matches": best["good_matches"],
            "inliers": best["inliers"],
            "inlier_ratio": best["inlier_ratio"],
            "mean_distance": best["mean_distance"],
            "scale": best["scale"],
            "reference_id": best["reference_id"],
            "sample_id": best["sample_id"],
            "box": best["box"],
            "failure_reason": None,
            "candidate_count": len(candidates),
        }

    candidates.sort(
        key=lambda item: (
            item.get("good_matches", 0),
            item.get("inliers", 0),
            item.get("inlier_ratio", 0.0),
        ),
        reverse=True,
    )
    best_failed = candidates[0] if candidates else {}
    return {
        "method": "not_found",
        "score": 0.0,
        "good_matches": int(best_failed.get("good_matches", 0)),
        "inliers": int(best_failed.get("inliers", 0)),
        "inlier_ratio": float(best_failed.get("inlier_ratio", 0.0)),
        "mean_distance": best_failed.get("mean_distance"),
        "scale": best_failed.get("scale"),
        "reference_id": best_failed.get("reference_id"),
        "sample_id": best_failed.get("sample_id"),
        "box": [],
        "failure_reason": best_failed.get("failure_reason", "no_valid_candidate"),
        "candidate_count": len(candidates),
    }


def match_sample_generic(
    *,
    sample: dict[str, Any],
    arrays: dict[str, np.ndarray],
    target_points: np.ndarray,
    target_descriptors: np.ndarray,
    target_shape: tuple[int, int, int],
    lowe_ratio: float,
    norm_type: int,
    distance_normalizer: float,
) -> dict[str, Any]:
    sample_id = str(sample["sample_id"])
    reference_id = str(sample["reference_id"])
    descriptors = arrays.get(str(sample["descriptors_key"]))
    source_points = arrays.get(str(sample["points_key"]))
    if descriptors is None or source_points is None:
        return rejected_candidate(sample, "sample_arrays_missing")
    if descriptors.shape[0] < MIN_GOOD_MATCHES:
        return rejected_candidate(sample, "not_enough_sample_descriptors")
    if target_descriptors.shape[0] < 2:
        return rejected_candidate(sample, "not_enough_target_descriptors")

    matcher = cv2.BFMatcher(norm_type)
    raw_matches = matcher.knnMatch(descriptors, target_descriptors, k=2)
    good_matches = []
    for pair in raw_matches:
        if len(pair) != 2:
            continue
        first, second = pair
        if first.distance < lowe_ratio * second.distance:
            good_matches.append(first)

    good_count = len(good_matches)
    base = {
        "accepted": False,
        "sample_id": sample_id,
        "reference_id": reference_id,
        "good_matches": good_count,
        "inliers": 0,
        "inlier_ratio": 0.0,
        "mean_distance": None,
        "scale": None,
        "box": [],
        "score": 0.0,
    }
    if good_count < MIN_GOOD_MATCHES:
        base["failure_reason"] = "not_enough_good_matches"
        return base

    src = np.float32([source_points[match.queryIdx] for match in good_matches])
    dst = np.float32([target_points[match.trainIdx] for match in good_matches])
    affine, mask = cv2.estimateAffinePartial2D(
        src,
        dst,
        method=cv2.RANSAC,
        ransacReprojThreshold=5.0,
        maxIters=2000,
        confidence=0.99,
        refineIters=10,
    )
    if affine is None or mask is None:
        base["failure_reason"] = "affine_failed"
        return base

    inlier_mask = mask.ravel().astype(bool)
    inliers = int(inlier_mask.sum())
    inlier_ratio = inliers / max(1, good_count)
    distances = [match.distance for index, match in enumerate(good_matches) if inlier_mask[index]]
    mean_distance = float(np.mean(distances)) if distances else float("inf")
    scale = affine_scale(affine)

    base.update(
        {
            "inliers": inliers,
            "inlier_ratio": round(float(inlier_ratio), 3),
            "mean_distance": round(float(mean_distance), 3) if np.isfinite(mean_distance) else None,
            "scale": round(float(scale), 3),
        }
    )
    if inliers < MIN_INLIERS:
        base["failure_reason"] = "not_enough_inliers"
        return base
    if inlier_ratio < MIN_INLIER_RATIO:
        base["failure_reason"] = "weak_inlier_ratio"
        return base
    if not (MIN_SCALE <= scale <= MAX_SCALE):
        base["failure_reason"] = "scale_out_of_range"
        return base

    box = project_bbox_affine(sample.get("bbox", []), affine, target_shape)
    if not box:
        base["failure_reason"] = "projected_box_invalid"
        return base

    score = inliers * inlier_ratio / (1.0 + mean_distance / distance_normalizer)
    base.update(
        {
            "accepted": True,
            "box": box,
            "score": round(float(score), 3),
            "failure_reason": None,
        }
    )
    return base


def detect_image_relaxed(
    target: np.ndarray,
    *,
    max_side: int = DEFAULT_MAX_SIDE,
    lowe_ratio: float = LOWE_RATIO,
    min_good_matches: int = RELAXED_MIN_GOOD_MATCHES,
    min_inliers: int = RELAXED_MIN_INLIERS,
    min_inlier_ratio: float = RELAXED_MIN_INLIER_RATIO,
    top_k_per_label: int = RELAXED_TOP_K_PER_LABEL,
    overlap_weight: float = RELAXED_OVERLAP_WEIGHT,
    component_labels: list[str] | None = None,
) -> dict[str, Any]:
    total_started_at = time.perf_counter()
    metadata, arrays = load_descriptor_db()
    samples = metadata.get("samples", [])
    if not samples:
        raise CV2Error("Descriptor database has no usable samples.")

    extract_started_at = time.perf_counter()
    target_points, target_descriptors = extract_akaze_features(
        target,
        mask=None,
        max_side=max_side,
    )
    feature_extract_ms = elapsed_ms(extract_started_at)

    result = detect_relaxed_generic(
        samples=samples,
        arrays=arrays,
        target_points=target_points,
        target_descriptors=target_descriptors,
        target_shape=target.shape,
        lowe_ratio=lowe_ratio,
        norm_type=cv2.NORM_HAMMING,
        method_name="akaze_affine_relaxed",
        feature_name="AKAZE",
        database_stats=metadata.get("stats", {}),
        distance_normalizer=60.0,
        min_good_matches=min_good_matches,
        min_inliers=min_inliers,
        min_inlier_ratio=min_inlier_ratio,
        top_k_per_label=top_k_per_label,
        overlap_weight=overlap_weight,
        component_labels=component_labels,
    )
    result["timings"]["feature_extract_ms"] = feature_extract_ms
    result["timings"]["total_ms"] = elapsed_ms(total_started_at)
    result["target_keypoints"] = int(target_points.shape[0])
    result["parameters"].update(
        {
            "max_side": max_side,
            "lowe_ratio": lowe_ratio,
        }
    )
    return result


def detect_image_sift_relaxed(
    target: np.ndarray,
    *,
    max_side: int = DEFAULT_MAX_SIDE,
    lowe_ratio: float = LOWE_RATIO,
    min_good_matches: int = RELAXED_MIN_GOOD_MATCHES,
    min_inliers: int = RELAXED_MIN_INLIERS,
    min_inlier_ratio: float = RELAXED_MIN_INLIER_RATIO,
    top_k_per_label: int = RELAXED_TOP_K_PER_LABEL,
    overlap_weight: float = RELAXED_OVERLAP_WEIGHT,
    component_labels: list[str] | None = None,
) -> dict[str, Any]:
    total_started_at = time.perf_counter()
    ensure_sift_descriptor_db(max_side=max_side)
    metadata, arrays = load_sift_descriptor_db()
    samples = metadata.get("samples", [])
    if not samples:
        raise CV2Error("SIFT descriptor database has no usable samples.")

    extract_started_at = time.perf_counter()
    target_points, target_descriptors = extract_sift_features(
        target,
        mask=None,
        max_side=max_side,
    )
    feature_extract_ms = elapsed_ms(extract_started_at)

    result = detect_relaxed_generic(
        samples=samples,
        arrays=arrays,
        target_points=target_points,
        target_descriptors=target_descriptors,
        target_shape=target.shape,
        lowe_ratio=lowe_ratio,
        norm_type=cv2.NORM_L2,
        method_name="sift_affine_relaxed",
        feature_name="SIFT",
        database_stats=metadata.get("stats", {}),
        distance_normalizer=160.0,
        min_good_matches=min_good_matches,
        min_inliers=min_inliers,
        min_inlier_ratio=min_inlier_ratio,
        top_k_per_label=top_k_per_label,
        overlap_weight=overlap_weight,
        component_labels=component_labels,
    )
    result["timings"]["feature_extract_ms"] = feature_extract_ms
    result["timings"]["total_ms"] = elapsed_ms(total_started_at)
    result["target_keypoints"] = int(target_points.shape[0])
    result["parameters"].update(
        {
            "max_side": max_side,
            "lowe_ratio": lowe_ratio,
        }
    )
    return result


def detect_relaxed_generic(
    *,
    samples: list[dict[str, Any]],
    arrays: dict[str, np.ndarray],
    target_points: np.ndarray,
    target_descriptors: np.ndarray | None,
    target_shape: tuple[int, int, int],
    lowe_ratio: float,
    norm_type: int,
    method_name: str,
    feature_name: str,
    database_stats: dict[str, Any],
    distance_normalizer: float,
    min_good_matches: int,
    min_inliers: int,
    min_inlier_ratio: float,
    top_k_per_label: int,
    overlap_weight: float,
    component_labels: list[str] | None = None,
) -> dict[str, Any]:
    labels = component_labels or COMPONENT_LABELS
    boxes: dict[str, list[int]] = {label: [] for label in labels}
    detections: dict[str, dict[str, Any]] = {}
    component_ms: dict[str, float] = {}
    candidates_by_label: dict[str, list[dict[str, Any]]] = {}
    all_candidates_by_label: dict[str, list[dict[str, Any]]] = {}

    for label in labels:
        component_started_at = time.perf_counter()
        label_samples = [sample for sample in samples if sample.get("label") == label]
        candidates = collect_relaxed_candidates(
            samples=label_samples,
            arrays=arrays,
            target_points=target_points,
            target_descriptors=target_descriptors,
            target_shape=target_shape,
            lowe_ratio=lowe_ratio,
            norm_type=norm_type,
            distance_normalizer=distance_normalizer,
            min_good_matches=min_good_matches,
            min_inliers=min_inliers,
            min_inlier_ratio=min_inlier_ratio,
        )
        all_candidates_by_label[label] = candidates
        accepted = [candidate for candidate in candidates if candidate.get("accepted")]
        accepted.sort(key=lambda item: item.get("raw_score", 0.0), reverse=True)
        candidates_by_label[label] = accepted[:top_k_per_label]
        component_ms[label] = elapsed_ms(component_started_at)

    selection_started_at = time.perf_counter()
    selected, penalties = select_non_overlapping_candidates(
        candidates_by_label,
        overlap_weight=overlap_weight,
        component_labels=labels,
    )
    selection_ms = elapsed_ms(selection_started_at)

    for label in labels:
        candidate = selected.get(label)
        all_candidates = all_candidates_by_label.get(label, [])
        boxes[label] = candidate.get("box", []) if candidate else []
        if candidate:
            overlap_penalty = penalties.get(label, 0.0)
            final_score = max(0.0, float(candidate["raw_score"]) - overlap_penalty)
            detections[label] = {
                "method": method_name,
                "score": round(final_score, 3),
                "raw_score": round(float(candidate["raw_score"]), 3),
                "overlap_penalty": round(float(overlap_penalty), 3),
                "good_matches": candidate["good_matches"],
                "inliers": candidate["inliers"],
                "inlier_ratio": candidate["inlier_ratio"],
                "mean_distance": candidate["mean_distance"],
                "scale": candidate["scale"],
                "reference_id": candidate["reference_id"],
                "sample_id": candidate["sample_id"],
                "box": candidate["box"],
                "failure_reason": None,
                "candidate_count": len(all_candidates),
                "accepted_candidate_count": len(candidates_by_label.get(label, [])),
                "selection": "relaxed_global_overlap",
                "component_ms": component_ms[label],
            }
        else:
            detections[label] = relaxed_empty_detection(
                all_candidates,
                component_ms=component_ms[label],
            )

    return {
        "boxes": boxes,
        "detections": detections,
        "timings": {
            "feature_extract_ms": 0.0,
            "component_ms": component_ms,
            "selection_ms": selection_ms,
            "match_total_ms": round(sum(component_ms.values()), 1),
            "total_ms": 0.0,
        },
        "target_keypoints": int(target_points.shape[0]),
        "database_stats": database_stats,
        "parameters": {
            "feature": feature_name,
            "relaxed_candidates": True,
            "min_good_matches": min_good_matches,
            "min_inliers": min_inliers,
            "min_inlier_ratio": min_inlier_ratio,
            "top_k_per_label": top_k_per_label,
            "overlap_weight": overlap_weight,
            "scale_range": [MIN_SCALE, MAX_SCALE],
        },
    }


def collect_relaxed_candidates(
    *,
    samples: list[dict[str, Any]],
    arrays: dict[str, np.ndarray],
    target_points: np.ndarray,
    target_descriptors: np.ndarray | None,
    target_shape: tuple[int, int, int],
    lowe_ratio: float,
    norm_type: int,
    distance_normalizer: float,
    min_good_matches: int,
    min_inliers: int,
    min_inlier_ratio: float,
) -> list[dict[str, Any]]:
    if not samples:
        return []
    if target_descriptors is None or target_points.shape[0] < 2:
        return [
            rejected_candidate(sample, "not_enough_target_keypoints")
            for sample in samples
        ]

    return [
        match_sample_relaxed_generic(
            sample=sample,
            arrays=arrays,
            target_points=target_points,
            target_descriptors=target_descriptors,
            target_shape=target_shape,
            lowe_ratio=lowe_ratio,
            norm_type=norm_type,
            distance_normalizer=distance_normalizer,
            min_good_matches=min_good_matches,
            min_inliers=min_inliers,
            min_inlier_ratio=min_inlier_ratio,
        )
        for sample in samples
    ]


def match_sample_relaxed_generic(
    *,
    sample: dict[str, Any],
    arrays: dict[str, np.ndarray],
    target_points: np.ndarray,
    target_descriptors: np.ndarray,
    target_shape: tuple[int, int, int],
    lowe_ratio: float,
    norm_type: int,
    distance_normalizer: float,
    min_good_matches: int,
    min_inliers: int,
    min_inlier_ratio: float,
) -> dict[str, Any]:
    descriptors = arrays.get(str(sample.get("descriptors_key")))
    source_points = arrays.get(str(sample.get("points_key")))
    if descriptors is None or source_points is None:
        return rejected_candidate(sample, "sample_arrays_missing")
    if descriptors.shape[0] < min_good_matches:
        return rejected_candidate(sample, "not_enough_sample_descriptors")
    if target_descriptors.shape[0] < 2:
        return rejected_candidate(sample, "not_enough_target_descriptors")

    matcher = cv2.BFMatcher(norm_type)
    raw_matches = matcher.knnMatch(descriptors, target_descriptors, k=2)
    good_matches = []
    for pair in raw_matches:
        if len(pair) != 2:
            continue
        first, second = pair
        if first.distance < lowe_ratio * second.distance:
            good_matches.append(first)

    good_count = len(good_matches)
    base = {
        "accepted": False,
        "sample_id": sample.get("sample_id"),
        "reference_id": sample.get("reference_id"),
        "good_matches": good_count,
        "inliers": 0,
        "inlier_ratio": 0.0,
        "mean_distance": None,
        "scale": None,
        "box": [],
        "score": 0.0,
        "raw_score": 0.0,
    }
    if good_count < min_good_matches:
        base["failure_reason"] = "not_enough_good_matches"
        return base

    src = np.float32([source_points[match.queryIdx] for match in good_matches])
    dst = np.float32([target_points[match.trainIdx] for match in good_matches])
    affine, mask = cv2.estimateAffinePartial2D(
        src,
        dst,
        method=cv2.RANSAC,
        ransacReprojThreshold=5.0,
        maxIters=2000,
        confidence=0.99,
        refineIters=10,
    )
    if affine is None or mask is None:
        base["failure_reason"] = "affine_failed"
        return base

    inlier_mask = mask.ravel().astype(bool)
    inliers = int(inlier_mask.sum())
    inlier_ratio = inliers / max(1, good_count)
    distances = [match.distance for index, match in enumerate(good_matches) if inlier_mask[index]]
    mean_distance = float(np.mean(distances)) if distances else float("inf")
    scale = affine_scale(affine)
    base.update(
        {
            "inliers": inliers,
            "inlier_ratio": round(float(inlier_ratio), 3),
            "mean_distance": round(float(mean_distance), 3) if np.isfinite(mean_distance) else None,
            "scale": round(float(scale), 3),
        }
    )

    if inliers < min_inliers:
        base["failure_reason"] = "not_enough_inliers"
        return base
    if inlier_ratio < min_inlier_ratio:
        base["failure_reason"] = "weak_inlier_ratio"
        return base
    if not (MIN_SCALE <= scale <= MAX_SCALE):
        base["failure_reason"] = "scale_out_of_range"
        return base

    box = project_bbox_affine(sample.get("bbox", []), affine, target_shape)
    if not box:
        base["failure_reason"] = "projected_box_invalid"
        return base

    distance_quality = 1.0 / (1.0 + mean_distance / distance_normalizer)
    raw_score = (2.0 * inliers + 0.2 * good_count) * max(inlier_ratio, 0.05) * distance_quality
    base.update(
        {
            "accepted": True,
            "box": box,
            "score": round(float(raw_score), 3),
            "raw_score": round(float(raw_score), 3),
            "failure_reason": None,
        }
    )
    return base


def relaxed_empty_detection(
    candidates: list[dict[str, Any]],
    *,
    component_ms: float,
) -> dict[str, Any]:
    accepted_count = len([candidate for candidate in candidates if candidate.get("accepted")])
    candidates.sort(
        key=lambda item: (
            item.get("accepted", False),
            item.get("raw_score", item.get("score", 0.0)),
            item.get("inliers", 0),
            item.get("inlier_ratio", 0.0),
            item.get("good_matches", 0),
        ),
        reverse=True,
    )
    best_failed = candidates[0] if candidates else {}
    reason = best_failed.get("failure_reason", "no_relaxed_candidate")
    if accepted_count:
        reason = "dropped_by_overlap_selection"
    return {
        "method": "not_found",
        "score": 0.0,
        "raw_score": float(best_failed.get("raw_score", best_failed.get("score", 0.0))),
        "overlap_penalty": None,
        "good_matches": int(best_failed.get("good_matches", 0)),
        "inliers": int(best_failed.get("inliers", 0)),
        "inlier_ratio": float(best_failed.get("inlier_ratio", 0.0)),
        "mean_distance": best_failed.get("mean_distance"),
        "scale": best_failed.get("scale"),
        "reference_id": best_failed.get("reference_id"),
        "sample_id": best_failed.get("sample_id"),
        "box": [],
        "best_candidate_box": best_failed.get("box", []),
        "failure_reason": reason,
        "candidate_count": len(candidates),
        "accepted_candidate_count": accepted_count,
        "selection": "relaxed_global_overlap",
        "component_ms": component_ms,
    }


def select_non_overlapping_candidates(
    candidates_by_label: dict[str, list[dict[str, Any]]],
    *,
    overlap_weight: float,
    component_labels: list[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    labels = component_labels or COMPONENT_LABELS
    options_by_label: list[tuple[str, list[dict[str, Any] | None]]] = []
    for label in labels:
        options = list(candidates_by_label.get(label, []))
        options_by_label.append((label, [None, *options]))

    best_score = 0.0
    best_combo: dict[str, dict[str, Any]] = {}

    def combo_score(combo: dict[str, dict[str, Any]]) -> float:
        candidates = list(combo.values())
        base = sum(float(candidate.get("raw_score", 0.0)) for candidate in candidates)
        return base - combo_overlap_penalty(candidates, overlap_weight)

    def backtrack(index: int, combo: dict[str, dict[str, Any]]) -> None:
        nonlocal best_score, best_combo
        if index >= len(options_by_label):
            score = combo_score(combo)
            if score > best_score:
                best_score = score
                best_combo = dict(combo)
            return

        label, options = options_by_label[index]
        for candidate in options:
            if candidate is None:
                combo.pop(label, None)
            else:
                combo[label] = candidate
            backtrack(index + 1, combo)
        combo.pop(label, None)

    backtrack(0, {})
    return best_combo, per_label_overlap_penalties(best_combo, overlap_weight)


def combo_overlap_penalty(candidates: list[dict[str, Any]], overlap_weight: float) -> float:
    penalty = 0.0
    for index, first in enumerate(candidates):
        for second in candidates[index + 1 :]:
            iou = box_iou(first.get("box", []), second.get("box", []))
            if iou <= 0:
                continue
            first_score = max(0.0, float(first.get("raw_score", 0.0)))
            second_score = max(0.0, float(second.get("raw_score", 0.0)))
            penalty += overlap_weight * iou * math.sqrt(first_score * second_score)
    return penalty


def per_label_overlap_penalties(
    selected: dict[str, dict[str, Any]],
    overlap_weight: float,
) -> dict[str, float]:
    penalties = {label: 0.0 for label in selected}
    items = list(selected.items())
    for index, (first_label, first) in enumerate(items):
        for second_label, second in items[index + 1 :]:
            iou = box_iou(first.get("box", []), second.get("box", []))
            if iou <= 0:
                continue
            first_score = max(0.0, float(first.get("raw_score", 0.0)))
            second_score = max(0.0, float(second.get("raw_score", 0.0)))
            pair_penalty = overlap_weight * iou * math.sqrt(first_score * second_score)
            penalties[first_label] += pair_penalty / 2.0
            penalties[second_label] += pair_penalty / 2.0
    return penalties


def box_iou(first: Any, second: Any) -> float:
    if not isinstance(first, list) or not isinstance(second, list):
        return 0.0
    if len(first) != 4 or len(second) != 4:
        return 0.0
    ay1, ax1, ay2, ax2 = [float(value) for value in first]
    by1, bx1, by2, bx2 = [float(value) for value in second]
    inter_y1 = max(ay1, by1)
    inter_x1 = max(ax1, bx1)
    inter_y2 = min(ay2, by2)
    inter_x2 = min(ax2, bx2)
    inter_area = max(0.0, inter_y2 - inter_y1) * max(0.0, inter_x2 - inter_x1)
    if inter_area <= 0:
        return 0.0
    first_area = max(0.0, ay2 - ay1) * max(0.0, ax2 - ax1)
    second_area = max(0.0, by2 - by1) * max(0.0, bx2 - bx1)
    union = first_area + second_area - inter_area
    if union <= 0:
        return 0.0
    return inter_area / union


def detect_image_pixel_ncc(
    target: np.ndarray,
    *,
    max_side: int = DEFAULT_MAX_SIDE,
    scales: list[float] | None = None,
) -> dict[str, Any]:
    total_started_at = time.perf_counter()
    scales = scales or PIXEL_SCALES
    annotations = load_annotations()
    references = annotations.get("references", [])
    if not references:
        raise CV2Error("No reference annotations found.")

    prep_started_at = time.perf_counter()
    target_work, target_scale = resize_for_work(target, max_side)
    target_gray = prepare_gray(target_work)
    prep_ms = elapsed_ms(prep_started_at)

    boxes: dict[str, list[int]] = {label: [] for label in COMPONENT_LABELS}
    detections: dict[str, dict[str, Any]] = {}
    component_ms: dict[str, float] = {}

    for label in COMPONENT_LABELS:
        component_started_at = time.perf_counter()
        best: dict[str, Any] | None = None
        candidate_count = 0
        for reference in references:
            label_data = reference.get("labels", {}).get(label, {})
            polygons = label_data.get("polygons", [])
            bbox = label_data.get("bbox", [])
            if not polygons or not bbox:
                continue
            image_path = IMAGES_DIR / str(reference.get("filename", ""))
            if not image_path.exists():
                continue
            reference_image = read_image(image_path)
            width = int(reference["image_size"]["width"])
            height = int(reference["image_size"]["height"])
            mask = polygons_to_mask(polygons, width, height)
            template = crop_bbox(reference_image, bbox)
            mask_crop = crop_bbox(mask, bbox)
            if template.size == 0 or mask_crop.size == 0:
                continue
            template_gray = prepare_gray(template)
            for scale in scales:
                candidate_count += 1
                candidate = match_masked_ncc_candidate(
                    label=label,
                    reference_id=str(reference.get("id", "")),
                    bbox=bbox,
                    template_gray=template_gray,
                    mask=mask_crop,
                    target_gray=target_gray,
                    target_shape=target.shape,
                    target_scale=target_scale,
                    scale=scale,
                )
                if candidate is None:
                    continue
                if best is None or candidate["pixel_score"] > best["pixel_score"]:
                    best = candidate

        component_ms[label] = elapsed_ms(component_started_at)
        if best is None:
            detections[label] = {
                "method": "not_found",
                "score": 0.0,
                "pixel_score": None,
                "reference_id": None,
                "scale": None,
                "box": [],
                "candidate_count": candidate_count,
                "failure_reason": "no_pixel_candidate",
                "component_ms": component_ms[label],
            }
        else:
            boxes[label] = best["box"]
            best.update(
                {
                    "method": "pixel_masked_ncc",
                    "score": best["pixel_score"],
                    "candidate_count": candidate_count,
                    "failure_reason": None,
                    "component_ms": component_ms[label],
                }
            )
            detections[label] = best

    return {
        "boxes": boxes,
        "detections": detections,
        "timings": {
            "prep_ms": prep_ms,
            "component_ms": component_ms,
            "match_total_ms": round(sum(component_ms.values()), 1),
            "total_ms": elapsed_ms(total_started_at),
        },
        "target_keypoints": None,
        "database_stats": pixel_database_stats(annotations),
        "parameters": {
            "feature": "Pixel/Masked NCC",
            "max_side": max_side,
            "scales": scales,
            "always_show_best": True,
        },
    }


def crop_bbox(image: np.ndarray, bbox: list[int]) -> np.ndarray:
    if not bbox:
        return image[0:0, 0:0]
    ymin, xmin, ymax, xmax = [int(value) for value in bbox]
    return image[ymin:ymax, xmin:xmax]


def match_masked_ncc_candidate(
    *,
    label: str,
    reference_id: str,
    bbox: list[int],
    template_gray: np.ndarray,
    mask: np.ndarray,
    target_gray: np.ndarray,
    target_shape: tuple[int, int, int],
    target_scale: float,
    scale: float,
) -> dict[str, Any] | None:
    template_scale = target_scale * scale
    width = max(1, round(template_gray.shape[1] * template_scale))
    height = max(1, round(template_gray.shape[0] * template_scale))
    if width < 12 or height < 12:
        return None
    if width >= target_gray.shape[1] or height >= target_gray.shape[0]:
        return None

    resized_template = cv2.resize(
        template_gray,
        (width, height),
        interpolation=cv2.INTER_AREA if template_scale < 1.0 else cv2.INTER_LINEAR,
    )
    resized_mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
    if np.count_nonzero(resized_mask) < 24:
        return None

    try:
        result = cv2.matchTemplate(
            target_gray,
            resized_template,
            cv2.TM_CCORR_NORMED,
            mask=resized_mask,
        )
    except cv2.error:
        result = cv2.matchTemplate(target_gray, resized_template, cv2.TM_CCORR_NORMED)
    result = np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0)
    _, max_value, _, max_location = cv2.minMaxLoc(result)
    xmin_work, ymin_work = max_location
    xmax_work = xmin_work + width
    ymax_work = ymin_work + height
    box = unscale_target_box(
        [ymin_work, xmin_work, ymax_work, xmax_work],
        target_scale,
        target_shape,
    )
    return {
        "label": label,
        "reference_id": reference_id,
        "box": box,
        "pixel_score": round(float(max_value), 4),
        "scale": round(float(scale), 3),
        "template_size": [int(template_gray.shape[1]), int(template_gray.shape[0])],
        "work_template_size": [int(width), int(height)],
    }


def unscale_target_box(
    box: list[int],
    scale: float,
    target_shape: tuple[int, int, int],
) -> list[int]:
    height, width = target_shape[:2]
    ymin, xmin, ymax, xmax = [int(round(value / scale)) for value in box]
    ymin = clamp(ymin, 0, height - 1)
    ymax = clamp(ymax, ymin + 1, height)
    xmin = clamp(xmin, 0, width - 1)
    xmax = clamp(xmax, xmin + 1, width)
    return [ymin, xmin, ymax, xmax]


def pixel_database_stats(annotations: dict[str, Any]) -> dict[str, Any]:
    per_label = {
        label: {"sample_count": 0, "mask_area": 0}
        for label in COMPONENT_LABELS
    }
    sample_count = 0
    for reference in annotations.get("references", []):
        for label in COMPONENT_LABELS:
            label_data = reference.get("labels", {}).get(label, {})
            if label_data.get("polygons") and label_data.get("bbox"):
                sample_count += 1
                per_label[label]["sample_count"] += 1
                per_label[label]["mask_area"] += int(label_data.get("mask_area", 0))
    return {"sample_count": sample_count, "per_label": per_label}


def clamp(value: int | float, low: int, high: int) -> int:
    return max(low, min(high, int(value)))
