"""Analyze electrolysis experiment images with retrieved correct/wrong references."""
from __future__ import annotations

from typing import Any

from pipeline.common.vlm_client import (
    DEFAULT_VLM_PROVIDER,
    build_image_part_from_bytes,
    build_text_part,
    call_vlm,
    create_vlm,
)

SYSTEM_PROMPT = "你是普通化学电解实验纠错助手。请根据图片判断学生操作是否存在错误，并给出简洁修改意见。"

PROMPT = """请判断待检电解实验图片是否存在操作错误。

参考正确操作：
{correct_description}

参考常见错误：
{wrong_description}

图片顺序如下：
1. 待检图片
2. 参考正确操作图片
3. 参考常见错误图片

要求：
- 不要提“数据库”“检索”“相似图片”等来源
- 只输出中文
- 语言尽量简洁
- 若基本正确，输出“问题：未发现明显错误。”并给出保持操作的意见
- 若存在错误，指出最关键的问题和修改意见

输出格式：
问题：...
意见：..."""


def analyze(
    student_image_bytes: bytes,
    retrieved: dict[str, Any],
    *,
    provider: str = DEFAULT_VLM_PROVIDER,
    model: str | None = None,
) -> tuple[str, float]:
    correct = retrieved.get("correct")
    wrong = retrieved.get("wrong")
    if not correct and not wrong:
        return "问题：未发现明显错误。\n意见：请保持当前连接和电极位置。", 0.0

    correct_description = (correct or {}).get("description") or "电源正极应接铂电极，负极接铜片；注意电流表方向和导线位置。"
    wrong_description = (wrong or {}).get("description") or "常见错误包括电极接反、电流表接反、导线浸入溶液或铂电极位置错误。"

    client = create_vlm(provider, model)
    prompt = PROMPT.format(
        correct_description=correct_description,
        wrong_description=wrong_description,
    )

    content = [build_text_part(prompt)]
    content.append(build_text_part("[待检图片]"))
    content.append(build_image_part_from_bytes(student_image_bytes, client))

    if correct and correct.get("image_jpeg"):
        content.append(build_text_part("[参考正确操作图片]"))
        content.append(build_image_part_from_bytes(correct["image_jpeg"], client))

    if wrong and wrong.get("image_jpeg"):
        content.append(build_text_part("[参考常见错误图片]"))
        content.append(build_image_part_from_bytes(wrong["image_jpeg"], client))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
    return call_vlm(client, messages, temperature=0.2)
