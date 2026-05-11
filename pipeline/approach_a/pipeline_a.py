"""方案A：一步到位 — 两张标注图 + 全部上下文 → 单次 VLM 调用。接受实验配置参数。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pipeline.common.vlm_client import (
    SYSTEM_PROMPT,
    build_image_part_from_bytes,
    build_text_part,
    call_vlm,
    create_vlm,
)


@dataclass
class ApproachAResult:
    analysis_text: str
    correct_data_url: str
    wrong_data_url: str
    label_summary: dict[str, Any]


def build_one_step_messages(
    correct_jpeg_bytes: bytes,
    wrong_jpeg_bytes: bytes,
    cv_summary: str,
    experiment_requirements: str,
    client: Any,
    *,
    prompt_template: str,
    color_legend: str,
) -> list[dict[str, Any]]:
    prompt = prompt_template.format(
        color_legend=color_legend,
        cv_summary=cv_summary,
        requirements=experiment_requirements,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                build_text_part(prompt),
                build_text_part("图A（参考示范图）:"),
                build_image_part_from_bytes(correct_jpeg_bytes, client),
                build_text_part("图B（待纠错图片）:"),
                build_image_part_from_bytes(wrong_jpeg_bytes, client),
            ],
        },
    ]


def run_pipeline_a(
    *,
    experiment_requirements: str,
    vlm_provider: str = "openrouter",
    vlm_model: str | None = None,
    label_summary: dict[str, Any] | None = None,
    cv_summary: str | None = None,
    prebuilt_jpeg: tuple[bytes, bytes] | None = None,
    experiment_config: Any = None,
) -> ApproachAResult:
    correct_jpeg_bytes, wrong_jpeg_bytes = prebuilt_jpeg  # type: ignore[misc]

    import pipeline.experiments.distillation as default_cfg
    cfg = experiment_config or default_cfg
    client = create_vlm(vlm_provider, vlm_model)
    messages = build_one_step_messages(
        correct_jpeg_bytes,
        wrong_jpeg_bytes,
        cv_summary or "",
        experiment_requirements,
        client,
        prompt_template=cfg.APPROACH_A_PROMPT,
        color_legend=cfg.COLOR_LEGEND,
    )
    analysis_text, _ = call_vlm(client, messages)

    return ApproachAResult(
        analysis_text=analysis_text,
        correct_data_url="",
        wrong_data_url="",
        label_summary=label_summary or {},
    )