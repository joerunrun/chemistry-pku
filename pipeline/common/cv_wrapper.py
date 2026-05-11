"""CV 检测包装，支持自定义实验组件配置。所有 component_labels 参数可选，默认走 core.py 全局值。"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

CV_DET_DIR = str(Path(__file__).resolve().parent.parent.parent / "CV_detection2")
if CV_DET_DIR not in sys.path:
    sys.path.insert(0, CV_DET_DIR)

import core as cv_core


@dataclass
class CVDetectionParams:
    max_side: int = 1600
    lowe_ratio: float = 0.75
    min_good_matches: int = 3
    min_inliers: int = 2
    min_inlier_ratio: float = 0.08
    top_k_per_label: int = 6
    overlap_weight: float = 0.75
    feature: str = "akaze"  # "akaze" or "sift"
    component_labels: list[str] | None = None  # 不传则用 core.py 默认


def detect_wrong_image(
    image: np.ndarray,
    params: CVDetectionParams | None = None,
) -> dict[str, Any]:
    p = params or CVDetectionParams()
    if p.feature == "sift":
        return cv_core.detect_image_sift_relaxed(
            image,
            max_side=p.max_side,
            lowe_ratio=p.lowe_ratio,
            min_good_matches=p.min_good_matches,
            min_inliers=p.min_inliers,
            min_inlier_ratio=p.min_inlier_ratio,
            top_k_per_label=p.top_k_per_label,
            overlap_weight=p.overlap_weight,
            component_labels=p.component_labels,
        )
    return cv_core.detect_image_relaxed(
        image,
        max_side=p.max_side,
        lowe_ratio=p.lowe_ratio,
        min_good_matches=p.min_good_matches,
        min_inliers=p.min_inliers,
        min_inlier_ratio=p.min_inlier_ratio,
        top_k_per_label=p.top_k_per_label,
        overlap_weight=p.overlap_weight,
        component_labels=p.component_labels,
    )


def build_reference_result(
    reference_id: str,
    component_labels: list[str] | None = None,
) -> dict[str, Any]:
    labels = component_labels or cv_core.COMPONENT_LABELS

    annotations = cv_core.load_annotations()
    target = None
    for ref in annotations.get("references", []):
        if str(ref.get("id", "")) == reference_id:
            target = ref
            break
    if target is None:
        raise cv_core.CV2Error(f"Reference not found: {reference_id}")

    boxes: dict[str, list[int]] = {}
    detections: dict[str, dict[str, Any]] = {}
    for label in labels:
        label_data = target.get("labels", {}).get(label, {})
        bbox = label_data.get("bbox", [])
        if bbox:
            boxes[label] = bbox
            detections[label] = {"score": None}
        else:
            boxes[label] = []
            detections[label] = {"score": None}

    return {"boxes": boxes, "detections": detections}


def annotate_image(image: np.ndarray, result: dict[str, Any]) -> np.ndarray:
    return cv_core.draw_detections(image, result)


def encode_annotated_image(image: np.ndarray, quality: int = 86) -> str:
    return cv_core.encode_image_data_url(image, quality)


def list_available_references(component_labels: list[str] | None = None) -> list[dict[str, Any]]:
    labels = component_labels or cv_core.COMPONENT_LABELS
    data = cv_core.list_references()
    result = []
    for ref in data.get("references", []):
        annotated = [
            label
            for label in labels
            if ref.get("keypoints", {}).get(label, 0) > 0
            or ref.get("labels", {}).get(label, {}).get("bbox")
        ]
        result.append({
            "id": ref.get("id", ""),
            "original_filename": ref.get("original_filename", ""),
            "filename": ref.get("filename", ""),
            "image_size": ref.get("image_size", {}),
            "annotated_labels": annotated,
        })
    return result


def load_reference_image(reference_id: str) -> np.ndarray:
    annotations = cv_core.load_annotations()
    for ref in annotations.get("references", []):
        if str(ref.get("id", "")) == reference_id:
            image_path = cv_core.IMAGES_DIR / str(ref.get("filename", ""))
            if not image_path.exists():
                raise cv_core.CV2Error(f"Reference image not found: {image_path}")
            return cv_core.read_image(image_path)
    raise cv_core.CV2Error(f"Reference not found: {reference_id}")


def decode_image(image_bytes: bytes) -> np.ndarray:
    return cv_core.decode_image(image_bytes, "uploaded_image")


def get_component_info(component_list: list[dict] | None = None) -> list[dict[str, str]]:
    """返回组件信息；可传入自定义组件列表。"""
    items = component_list or cv_core.COMPONENTS
    return [{"label": item["label"], "name": item["name"], "color": item["color"]} for item in items]