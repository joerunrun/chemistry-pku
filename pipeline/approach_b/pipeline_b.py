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

DIFF_PROMPT = """请对比以下两张化学实验装置图片，逐色框找出差异。

图A是参考示范图（正确），图B是待纠错的学生图片。

{color_legend}

CV检测概况：
{cv_summary}

请按每种颜色/装置分别描述：
1. 如果该颜色框在两张图中都存在，描述位置、大小、装置形态的差异。
2. 如果该颜色框只在一张图中存在，明确指出缺失或多余。
3. 错误图B的颜色框为自动检测，可能出现漏检误检

请严格按照以下格式输出，每种装置一段：
[voltage_regulator] ...
[heating_unit] ...
[distillation_core] ...
[condenser_unit] ...
[receiving_assembly] ...
[vacuum_gauge] ..."""

ANALYSIS_PROMPT = """现在你已经拿到了图片差异分析和CV检测的结构化数据，请结合实验要求给出最终判断。

已知：
1. 图A是参考示范图。
2. 图B是待纠错图片。

图片差异分析（按装置逐个）：
{diff_text}

CV检测结构化数据：
{cv_structured_summary}

实验要求：
{requirements}

请按下面格式输出：
主要错误：...

要求：
1. 只输出中文。
2. 重点回答"最主要的错误是什么"，不要把很多次要问题混在一起。
3. 若实验要求与图像差异存在冲突，以实验要求为准重新判断。
4. 如果某个装置CV未检测到但差异分析也未提及，说明该装置可能无问题，不要凭空猜测。"""


@dataclass
class ApproachBResult:
    diff_text: str
    analysis_text: str
    correct_data_url: str
    wrong_data_url: str
    label_summary: dict[str, Any]


def build_diff_messages(
    correct_jpeg_bytes: bytes,
    wrong_jpeg_bytes: bytes,
    cv_summary: str,
    client: Any,
) -> list[dict[str, Any]]:
    prompt = DIFF_PROMPT.format(
        color_legend=COLOR_LEGEND,
        cv_summary=cv_summary,
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


def build_analysis_messages(
    diff_text: str,
    cv_structured_summary: str,
    experiment_requirements: str,
) -> list[dict[str, Any]]:
    prompt = ANALYSIS_PROMPT.format(
        diff_text=diff_text,
        cv_structured_summary=cv_structured_summary,
        requirements=experiment_requirements,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def run_pipeline_b(
    *,
    experiment_requirements: str,
    vlm_provider: str = "openrouter",
    vlm_model: str | None = None,
    label_summary: dict[str, Any] | None = None,
    cv_summary: str | None = None,
    prebuilt_jpeg: tuple[bytes, bytes] | None = None,
) -> ApproachBResult:
    correct_jpeg_bytes, wrong_jpeg_bytes = prebuilt_jpeg  # type: ignore[misc]
    client = create_vlm(vlm_provider, vlm_model)

    diff_messages = build_diff_messages(correct_jpeg_bytes, wrong_jpeg_bytes, cv_summary or "", client)
    diff_text, _ = call_vlm(client, diff_messages)

    analysis_messages = build_analysis_messages(diff_text, cv_summary or "", experiment_requirements)
    analysis_text, _ = call_vlm(client, analysis_messages)

    return ApproachBResult(
        diff_text=diff_text,
        analysis_text=analysis_text,
        correct_data_url="",
        wrong_data_url="",
        label_summary=label_summary or {},
    )
