from __future__ import annotations

from typing import Any

import numpy as np

from pipeline.common.cv_wrapper import (
    annotate_image,
    encode_annotated_image,
    get_component_info,
)


def build_annotated_pair(
    correct_image: np.ndarray,
    correct_result: dict[str, Any],
    wrong_image: np.ndarray,
    wrong_result: dict[str, Any],
    jpeg_quality: int = 80,
) -> dict[str, Any]:
    correct_annotated = annotate_image(correct_image, correct_result)
    wrong_annotated = annotate_image(wrong_image, wrong_result)

    correct_data_url = encode_annotated_image(correct_annotated, quality=jpeg_quality)
    wrong_data_url = encode_annotated_image(wrong_annotated, quality=jpeg_quality)

    return {
        "correct_annotated": correct_annotated,
        "wrong_annotated": wrong_annotated,
        "correct_data_url": correct_data_url,
        "wrong_data_url": wrong_data_url,
    }


def build_cv_structured_summary(
    label_summary: dict[str, Any],
    *,
    correct_is_uploaded: bool = False,
) -> str:
    lines: list[str] = []

    correct_labels = [info["name"] for info in label_summary.values() if info["in_correct"]]
    wrong_labels = [
        f"{info['name']}"
        + (f"(score={info['wrong_score']:.3f})" if info.get("wrong_score") else "")
        for info in label_summary.values()
        if info["in_wrong"]
    ]
    only_correct = [
        info["name"] for info in label_summary.values()
        if info["in_correct"] and not info["in_wrong"]
    ]
    only_wrong = [
        info["name"] for info in label_summary.values()
        if info["in_wrong"] and not info["in_correct"]
    ]
    neither = [
        info["name"] for info in label_summary.values()
        if not info["in_correct"] and not info["in_wrong"]
    ]

    if correct_is_uploaded:
        lines.append(f"图A（参考图，人工标注）包含的装置：{', '.join(correct_labels) or '未知（由人工标注）'}")
    else:
        lines.append(f"图A（参考图）检测到的装置：{', '.join(correct_labels) or '无'}")
    lines.append(f"图B（待纠错图）检测到的装置：{', '.join(wrong_labels) or '无'}")

    if not correct_is_uploaded:
        if only_correct:
            lines.append(f"仅在图A中检测到（图B可能缺失）：{', '.join(only_correct)}")
        if only_wrong:
            lines.append(f"仅在图B中检测到（图A可能缺失）：{', '.join(only_wrong)}")
    else:
        if only_correct:
            lines.append(f"图A有人工标注但图B未检测到：{', '.join(only_correct)}")
        if only_wrong:
            lines.append(f"仅在图B中检测到（图A未标注）：{', '.join(only_wrong)}")

    if neither:
        lines.append(f"两张图都未检测到：{', '.join(neither)}")

    for label, info in label_summary.items():
        if info["in_wrong"] and info.get("wrong_failure_reason") and info["wrong_failure_reason"] not in (None, "dropped_by_overlap_selection"):
            lines.append(f"  {info['name']}检测备注：{info['wrong_failure_reason']}")

    return "\n".join(lines)
