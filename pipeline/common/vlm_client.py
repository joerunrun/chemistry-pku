from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Generator

PARENT_DIR = str(Path(__file__).resolve().parent.parent.parent)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from client_factory import create_chat_client
from openrouter_client import (
    OpenRouterError,
    build_image_part,
    build_text_part,
    prepare_image_part,
)


SYSTEM_PROMPT = "你是化学实验图像分析与纠错助手。你擅长对比两张标注了彩色检测框的实验装置图片，找出装置差异并判断错误。"

# 每个 provider 可用的模型列表
PROVIDER_MODELS: dict[str, list[dict[str, str]]] = {
    "openrouter": [
        {"value": "qwen/qwen3-vl-8b-instruct", "label": "Qwen3-VL 8B"},
        {"value": "qwen/qwen2.5-vl-72b-instruct", "label": "Qwen2.5-VL 72B"},
        {"value": "google/gemini-2.5-flash-preview", "label": "Gemini 2.5 Flash"},
        {"value": "google/gemini-2.5-pro-preview", "label": "Gemini 2.5 Pro"},
        {"value": "deepseek/deepseek-v3-0324", "label": "DeepSeek V3"},
    ],
    "ali": [
        {"value": "qwen3.6-flash", "label": "Qwen3.6 Flash"},
        {"value": "qwen3-235b-a22b", "label": "Qwen3 235B-A22B"},
        {"value": "qwen2.5-vl-72b-instruct", "label": "Qwen2.5-VL 72B"},
        {"value": "qwen2.5-vl-32b-instruct", "label": "Qwen2.5-VL 32B"},
    ],
    "gemini": [
        {"value": "gemini-3-flash-preview", "label": "Gemini 3 Flash"},
        {"value": "gemini-2.5-flash-preview-05-20", "label": "Gemini 2.5 Flash"},
        {"value": "gemini-2.5-pro-preview-05-06", "label": "Gemini 2.5 Pro"},
    ],
}


def create_vlm(provider: str, model: str | None = None) -> Any:
    client = create_chat_client(provider)
    if model:
        client.config.model = model
    return client


def build_image_part_from_bytes(image_bytes: bytes, client: Any) -> dict[str, Any]:
    part, _metrics = prepare_image_part(image_bytes, config=client.config)
    return part


def call_vlm(
    client: Any,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
) -> tuple[str, float]:
    started_at = time.perf_counter()
    result = client.chat(messages, temperature=temperature)
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    return result, round(elapsed_ms, 1)


def stream_vlm(
    client: Any,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
) -> Generator[str, None, None]:
    yield from client.stream_chat(messages, temperature=temperature)
