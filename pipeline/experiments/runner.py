"""Unified JSON-driven experiment correction runner."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pipeline.common.timing import StepTimer, TimingRecord
from pipeline.common.vlm_client import (
    DEFAULT_VLM_PROVIDER,
    build_image_part_from_bytes,
    build_text_part,
    call_vlm,
    create_vlm,
)
from pipeline.experiments.spec_loader import SPECS_DIR, get_spec
from pipeline.resource_paths import runtime_asset_path


def run_experiment_check(
    experiment_id: str,
    image_bytes: bytes,
    *,
    provider: str = DEFAULT_VLM_PROVIDER,
    model: str | None = None,
) -> dict[str, Any]:
    """Run one configured experiment correction strategy."""
    spec = get_spec(experiment_id)
    if spec is None:
        raise ValueError(f"Unknown experiment: {experiment_id}")
    if not image_bytes:
        raise ValueError("Experiment image is empty")

    strategy = spec["strategy"]
    if strategy == "cv_reference":
        return _run_cv_reference(spec, image_bytes, provider, model)
    if strategy == "retrieval":
        return _run_retrieval(spec, image_bytes, provider, model)
    if strategy == "vlm_only":
        return _run_vlm_only(spec, image_bytes, provider, model)
    raise ValueError(f"Unsupported strategy: {strategy}")


def _run_cv_reference(
    spec: dict[str, Any],
    image_bytes: bytes,
    provider: str,
    model: str | None,
) -> dict[str, Any]:
    import cv2
    from pipeline.common.cv_wrapper import (
        CVDetectionParams,
        annotate_image,
        build_reference_result,
        decode_image,
        detect_wrong_image,
        encode_annotated_image,
        list_available_references,
        load_reference_image,
    )
    from pipeline.common.image_builder import build_cv_structured_summary

    experiment_id = spec["id"]
    components = list(spec["components"])
    component_labels = [component["label"] for component in components]
    cv_config = spec.get("cv_params", {})

    started = time.perf_counter()
    timing = TimingRecord()
    with StepTimer(timing, "decode"):
        wrong_image = decode_image(image_bytes)

    cv_params = CVDetectionParams(
        max_side=int(cv_config.get("max_side", 1600)),
        lowe_ratio=float(cv_config.get("lowe_ratio", 0.75)),
        min_good_matches=int(cv_config.get("min_good_matches", 3)),
        min_inliers=int(cv_config.get("min_inliers", 2)),
        min_inlier_ratio=float(cv_config.get("min_inlier_ratio", 0.08)),
        top_k_per_label=int(cv_config.get("top_k_per_label", 6)),
        overlap_weight=float(cv_config.get("overlap_weight", 0.75)),
        feature=str(cv_config.get("feature", "akaze")),
        component_labels=component_labels,
    )

    with StepTimer(timing, "cv_detection"):
        wrong_result = detect_wrong_image(wrong_image, cv_params)

    reference = spec["reference"]
    reference_source = reference["source"]
    reference_id: str | None = None
    with StepTimer(timing, "load_reference"):
        if reference_source == "file":
            reference_path = _resolve_spec_path(reference["annotated_image_path"])
            correct_annotated = decode_image(reference_path.read_bytes())
            correct_result = _build_component_presence_result(components)
            reference_name = reference_path.name
        else:
            required_labels = component_labels if spec.get("require_reference_labels") else None
            references = list_available_references(required_labels)
            configured_id = str(reference.get("reference_id", "")).strip()
            if configured_id:
                if not any(str(item.get("id", "")) == configured_id for item in references):
                    raise ValueError(
                        f"Configured reference '{configured_id}' is unavailable for {experiment_id}"
                    )
                reference_id = configured_id
            elif references:
                reference_id = str(references[0]["id"])
            else:
                raise ValueError(f"No reference images available for {experiment_id}")
            correct_image = load_reference_image(reference_id)
            correct_result = build_reference_result(reference_id, component_labels)
            correct_annotated = annotate_image(correct_image, correct_result)
            reference_name = reference_id

    with StepTimer(timing, "build_images"):
        wrong_annotated = annotate_image(wrong_image, wrong_result)
        correct_data_url = encode_annotated_image(correct_annotated)
        wrong_data_url = encode_annotated_image(wrong_annotated)
        ok_correct, correct_jpg = cv2.imencode(
            ".jpg",
            correct_annotated,
            [int(cv2.IMWRITE_JPEG_QUALITY), 80],
        )
        ok_wrong, wrong_jpg = cv2.imencode(
            ".jpg",
            wrong_annotated,
            [int(cv2.IMWRITE_JPEG_QUALITY), 80],
        )
        if not ok_correct or not ok_wrong:
            raise ValueError("Failed to encode annotated images")
        label_summary = _build_label_summary(correct_result, wrong_result, components)
        cv_summary = build_cv_structured_summary(
            label_summary,
            correct_is_uploaded=reference_source == "file",
        )

    prompt = spec["prompt_a"].format(
        color_legend=spec.get("color_legend", ""),
        cv_summary=cv_summary,
        requirements=spec.get("experiment_requirements", ""),
    )
    client = create_vlm(provider, model)
    messages = [
        {"role": "system", "content": spec["system_prompt"]},
        {
            "role": "user",
            "content": [
                build_text_part(prompt),
                build_text_part("图A（参考示范图）:"),
                build_image_part_from_bytes(correct_jpg.tobytes(), client),
                build_text_part("图B（待纠错图片）:"),
                build_image_part_from_bytes(wrong_jpg.tobytes(), client),
            ],
        },
    ]
    with StepTimer(timing, "vlm"):
        analysis_text, _ = call_vlm(
            client,
            messages,
            temperature=float(spec.get("temperature", 0.2)),
        )

    timings = timing.to_dict()
    timings["total"] = round((time.perf_counter() - started) * 1000, 1)
    return {
        "analysis_text": analysis_text,
        "mode": "experiment",
        "experiment_type": experiment_id,
        "reference": {
            "source": reference_source,
            "id": reference_id,
            "name": reference_name,
        },
        "correct_data_url": correct_data_url,
        "wrong_data_url": wrong_data_url,
        "label_summary": label_summary,
        "timings": timings,
    }


def _run_retrieval(
    spec: dict[str, Any],
    image_bytes: bytes,
    provider: str,
    model: str | None,
) -> dict[str, Any]:
    from pipeline.experiments.retrieval_runner import retrieve_and_analyze

    return retrieve_and_analyze(spec, image_bytes, provider, model)


def _run_vlm_only(
    spec: dict[str, Any],
    image_bytes: bytes,
    provider: str,
    model: str | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    client = create_vlm(provider, model)
    content = [
        build_text_part(spec["prompt"]),
        build_text_part("图 A（待检查图片）:"),
        build_image_part_from_bytes(image_bytes, client),
    ]
    reference_summary: list[dict[str, str]] = []
    for reference in spec.get("reference_images", []):
        reference_path = _resolve_spec_path(reference["path"])
        role = str(reference["role"])
        title = "正确参考图" if role == "correct" else "错误参考图"
        content.extend([
            build_text_part(f"图 {chr(ord('B') + len(reference_summary))}（{title}）:"),
            build_image_part_from_bytes(reference_path.read_bytes(), client),
        ])
        reference_summary.append({
            "role": role,
            "name": reference_path.name,
        })
    messages = [
        {"role": "system", "content": spec["system_prompt"]},
        {
            "role": "user",
            "content": content,
        },
    ]
    analysis_text, vlm_ms = call_vlm(
        client,
        messages,
        temperature=float(spec.get("temperature", 0.2)),
    )
    return {
        "analysis_text": analysis_text,
        "mode": "experiment",
        "experiment_type": spec["id"],
        "reference_images": reference_summary,
        "timings": {
            "vlm": vlm_ms,
            "total": round((time.perf_counter() - started) * 1000, 1),
        },
    }


def _resolve_spec_path(raw_path: str) -> Path:
    value = str(raw_path).strip()
    if value.startswith("assets://"):
        parts = [part for part in value[len("assets://"):].split("/") if part]
        path = runtime_asset_path(*parts)
    else:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (SPECS_DIR / path).resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Configured reference image not found: {path}")
    return path


def _build_component_presence_result(
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "boxes": {
            component["label"]: [0, 0, 1, 1]
            for component in components
        },
        "detections": {
            component["label"]: {"score": None}
            for component in components
        },
    }


def _build_label_summary(
    correct_result: dict[str, Any],
    wrong_result: dict[str, Any],
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    wrong_boxes = wrong_result.get("boxes", {})
    wrong_detections = wrong_result.get("detections", {})
    correct_boxes = correct_result.get("boxes", {})
    result: dict[str, Any] = {}
    for component in components:
        label = component["label"]
        correct_box = correct_boxes.get(label, [])
        wrong_box = wrong_boxes.get(label, [])
        sentinel = [0, 0, 1, 1]
        detection = wrong_detections.get(label, {})
        result[label] = {
            "name": component["name"],
            "color": component["color"],
            "in_correct": bool(correct_box),
            "in_wrong": bool(wrong_box),
            "correct_box": correct_box if correct_box != sentinel else [],
            "wrong_box": wrong_box,
            "wrong_score": detection.get("score"),
            "wrong_raw_score": detection.get("raw_score"),
            "wrong_failure_reason": detection.get("failure_reason"),
        }
    return result
