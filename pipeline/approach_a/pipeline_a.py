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


COLOR_LEGEND = """色框对应关系：
- 黄色框: 调压器 (voltage_regulator)
- 红色框: 加热装置 (heating_unit)
- 绿色框: 蒸馏核心 (distillation_core)
- 蓝色框: 冷凝管 (condenser_unit)
- 橙色框: 接收装置 (receiving_assembly)
- 紫色框: 真空表 (vacuum_gauge)"""

ONE_STEP_PROMPT = """请对比以下两张化学实验装置图片，找出差异并判断主要错误。

图A是参考示范图（正确），图B是待纠错的学生图片。两张图上都用不同颜色的矩形框标注了装置部件。

{color_legend}

CV检测结果说明：
{cv_summary}

注意事项：
1. 优先识别真实装置差异（增添、缺失、位置不同），忽略拍摄角度、光线、裁剪范围的表面差异。
2. 如果某种颜色框只在一张图中出现，说明该装置可能缺失或多余，必须报告。
3. 错误图B的颜色框为自动检测，可能出现漏检误检

请按以下格式输出：

一、逐色框差异
（对每种有差异的颜色/装置，分别描述差异）

二、主要错误
结合实验要求，判断图B相对图A最主要的错误。

实验要求：
{requirements}"""


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
) -> list[dict[str, Any]]:
    prompt = ONE_STEP_PROMPT.format(
        color_legend=COLOR_LEGEND,
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
) -> ApproachAResult:
    correct_jpeg_bytes, wrong_jpeg_bytes = prebuilt_jpeg  # type: ignore[misc]

    client = create_vlm(vlm_provider, vlm_model)
    messages = build_one_step_messages(
        correct_jpeg_bytes,
        wrong_jpeg_bytes,
        cv_summary or "",
        experiment_requirements,
        client,
    )
    analysis_text, _ = call_vlm(client, messages)

    return ApproachAResult(
        analysis_text=analysis_text,
        correct_data_url="",
        wrong_data_url="",
        label_summary=label_summary or {},
    )
