"""把内部安全检查要点和待检图一起发给 VLM 分析。"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

_PARENT = str(Path(__file__).resolve().parent.parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from client_factory import create_chat_client
from openrouter_client import build_text_part, prepare_image_part
from pipeline.common.vlm_client import DEFAULT_VLM_MODEL, DEFAULT_VLM_PROVIDER

SYSTEM_PROMPT = "你是实验室安全检查助手。请直接判断图片中的安全问题，并给出整改意见。"

PROMPT = """请检查待检图片中的实验室安全问题。

可参考的安全检查要点：
{top_k_descriptions}

待检图片如下。

要求：
- 不要提“参考案例”“数据库”“相似图片”“检索”等来源
- 只输出中文
- 用最简洁的语言输出
- 只报告确实存在的安全问题；没有发现问题就输出“未发现明显安全问题”
- 不要描述无关背景

输出格式：
问题：...
意见：..."""


def analyze(
    student_image_bytes: bytes,
    retrieved: list[dict],
    *,
    provider: str = DEFAULT_VLM_PROVIDER,
    model: str | None = None,
) -> tuple[str, float]:
    if not retrieved:
        return "未发现明显安全问题。", 0.0

    client = create_chat_client(provider)
    if model:
        client.config.model = model
    elif provider == "ali":
        client.config.model = DEFAULT_VLM_MODEL
    elif provider == "gemini":
        client.config.model = "gemini-3-flash-preview"

    # 构建安全检查要点。对模型隐藏检索/数据库来源，只作为内部上下文。
    desc_parts: list[str] = []
    for idx, item in enumerate(retrieved, 1):
        desc_parts.append(f"- {item['rectification']}")

    prompt = PROMPT.format(top_k_descriptions="\n\n".join(desc_parts))

    # 构建消息：要点 + 待检图。不要把参考图发给模型，避免输出数据库对比痕迹。
    image_inputs: list[dict] = [build_text_part(prompt)]
    image_inputs.append(build_text_part("[待检图片]"))
    part, _ = prepare_image_part(student_image_bytes, config=client.config)
    image_inputs.append(part)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": image_inputs},
    ]

    started = time.perf_counter()
    result = client.chat(messages, temperature=0.2)
    elapsed = (time.perf_counter() - started) * 1000
    return result, round(elapsed, 1)
