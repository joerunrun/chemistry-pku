"""Retrieval strategies with JSON-owned prompts and response rules."""
from __future__ import annotations

import time
from typing import Any

from pipeline.common.vlm_client import (
    DEFAULT_VLM_PROVIDER,
    build_image_part_from_bytes,
    build_text_part,
    call_vlm,
    create_vlm,
)


def retrieve_and_analyze(
    spec: dict[str, Any],
    image_bytes: bytes,
    provider: str = DEFAULT_VLM_PROVIDER,
    model: str | None = None,
) -> dict[str, Any]:
    db_name = spec["retrieval_db"]
    if db_name == "electrolysis_check_data":
        return _run_electrolysis(spec, image_bytes, provider, model)
    if db_name == "safety_check_data":
        return _run_safety(spec, image_bytes, provider, model)
    raise ValueError(f"Unknown retrieval db: {db_name}")


def _run_electrolysis(
    spec: dict[str, Any],
    image_bytes: bytes,
    provider: str,
    model: str | None,
) -> dict[str, Any]:
    from pipeline.electrolysis_check.retriever import retrieve_pair

    started = time.perf_counter()
    retrieval_started = time.perf_counter()
    retrieved = retrieve_pair(image_bytes, include_images=True)
    retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 1)
    correct = retrieved.get("correct")
    wrong = retrieved.get("wrong")
    if not correct and not wrong:
        return {
            "analysis_text": spec["empty_result_text"],
            "mode": "experiment",
            "experiment_type": spec["id"],
            "retrieved": {"correct": None, "wrong": None},
            "timings": {
                "retrieval": retrieval_ms,
                "vlm": 0.0,
                "total": round((time.perf_counter() - started) * 1000, 1),
            },
        }

    correct_description = (
        (correct or {}).get("description")
        or spec.get("default_correct_description", "")
    )
    wrong_description = (
        (wrong or {}).get("description")
        or spec.get("default_wrong_description", "")
    )
    prompt = spec["prompt"].format(
        correct_description=correct_description,
        wrong_description=wrong_description,
    )
    client = create_vlm(provider, model)
    content = [
        build_text_part(prompt),
        build_text_part("[待检图片]"),
        build_image_part_from_bytes(image_bytes, client),
    ]
    if correct and correct.get("image_jpeg"):
        content.extend([
            build_text_part("[参考正确操作图片]"),
            build_image_part_from_bytes(correct["image_jpeg"], client),
        ])
    if wrong and wrong.get("image_jpeg"):
        content.extend([
            build_text_part("[参考常见错误图片]"),
            build_image_part_from_bytes(wrong["image_jpeg"], client),
        ])

    analysis_text, vlm_ms = call_vlm(
        client,
        [
            {"role": "system", "content": spec["system_prompt"]},
            {"role": "user", "content": content},
        ],
        temperature=float(spec.get("temperature", 0.2)),
    )
    return {
        "analysis_text": analysis_text,
        "mode": "experiment",
        "experiment_type": spec["id"],
        "retrieved": {
            "correct": _summarize_electrolysis_item(correct),
            "wrong": _summarize_electrolysis_item(wrong),
        },
        "timings": {
            "retrieval": retrieval_ms,
            "vlm": vlm_ms,
            "total": round((time.perf_counter() - started) * 1000, 1),
        },
    }


def _run_safety(
    spec: dict[str, Any],
    image_bytes: bytes,
    provider: str,
    model: str | None,
) -> dict[str, Any]:
    from pipeline.safety_check.retriever import retrieve

    started = time.perf_counter()
    retrieval_started = time.perf_counter()
    results = retrieve(
        image_bytes,
        top_k=int(spec.get("top_k", 1)),
        include_images=False,
    )
    retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 1)
    if not results:
        return {
            "analysis_text": spec["empty_result_text"],
            "mode": "safety",
            "experiment_type": None,
            "retrieved": [],
            "timings": {
                "retrieval": retrieval_ms,
                "vlm": 0.0,
                "total": round((time.perf_counter() - started) * 1000, 1),
            },
        }

    descriptions = "\n".join(
        f"- {item.get('rectification', '')}"
        for item in results
        if str(item.get("rectification", "")).strip()
    )
    prompt = spec["prompt"].format(top_k_descriptions=descriptions)
    client = create_vlm(provider, model)
    analysis_text, vlm_ms = call_vlm(
        client,
        [
            {"role": "system", "content": spec["system_prompt"]},
            {
                "role": "user",
                "content": [
                    build_text_part(prompt),
                    build_text_part("[待检图片]"),
                    build_image_part_from_bytes(image_bytes, client),
                ],
            },
        ],
        temperature=float(spec.get("temperature", 0.2)),
    )
    return {
        "analysis_text": analysis_text,
        "mode": "safety",
        "experiment_type": None,
        "retrieved": [
            {
                "title": item.get("title", ""),
                "rectification": item.get("rectification", ""),
                "match_count": item.get("match_count"),
                "score": item.get("score"),
            }
            for item in results
        ],
        "timings": {
            "retrieval": retrieval_ms,
            "vlm": vlm_ms,
            "total": round((time.perf_counter() - started) * 1000, 1),
        },
    }


def _summarize_electrolysis_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    return {
        "sample_id": item.get("sample_id"),
        "label": item.get("label"),
        "image_file": item.get("image_file"),
        "description": item.get("description", ""),
        "match_count": item.get("match_count"),
        "score": item.get("score"),
    }
