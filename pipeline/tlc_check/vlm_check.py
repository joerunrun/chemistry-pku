"""Minimal TLC image-analysis shell.

Replace ``PROMPT`` once the concrete TLC correction criteria are ready.
"""
from __future__ import annotations

from pipeline.common.vlm_client import (
    DEFAULT_VLM_PROVIDER,
    build_image_part_from_bytes,
    build_text_part,
    call_vlm,
    create_vlm,
)


SYSTEM_PROMPT = "你是化学实验图像分析助手。"

# TODO: Replace this placeholder with the concrete TLC correction rubric.
PROMPT = """请观察这张TLC实验图片，简要指出画面中最明显的操作问题并给出修改建议。

输出格式：
问题：...
意见：..."""


def analyze(
    image_bytes: bytes,
    *,
    provider: str = DEFAULT_VLM_PROVIDER,
    model: str | None = None,
) -> tuple[str, float]:
    client = create_vlm(provider, model)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                build_text_part(PROMPT),
                build_image_part_from_bytes(image_bytes, client),
            ],
        },
    ]
    return call_vlm(client, messages, temperature=0.2)
