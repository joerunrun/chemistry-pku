from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Iterator

from dotenv import load_dotenv
from openai import OpenAI

from assets.baseline_vlm.openrouter_cache_client import (
    OpenRouterCacheConfig,
    OpenRouterCacheError,
    image_bytes_to_data_url,
)

load_dotenv()


class AliCacheError(OpenRouterCacheError):
    """Raised when the baseline Ali cache flow fails."""


@dataclass(slots=True)
class AliCacheConfig:
    api_key: str
    base_url: str
    timeout_seconds: int
    max_image_side: int
    jpeg_quality: int


def load_ali_cache_config() -> AliCacheConfig:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise AliCacheError("Missing environment variable DASHSCOPE_API_KEY.")

    return AliCacheConfig(
        api_key=api_key,
        base_url=os.getenv(
            "ALI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).strip(),
        timeout_seconds=int(os.getenv("ALI_TIMEOUT_SECONDS", "180")),
        max_image_side=int(os.getenv("ALI_MAX_IMAGE_SIDE", "1280")),
        jpeg_quality=int(os.getenv("ALI_JPEG_QUALITY", "75")),
    )


def build_usage_summary(usage: Any) -> dict[str, Any]:
    prompt_details = getattr(usage, "prompt_tokens_details", None) or {}

    if not isinstance(prompt_details, dict):
        prompt_details = {
            "cached_tokens": getattr(prompt_details, "cached_tokens", 0),
            "cache_creation_input_tokens": getattr(
                prompt_details, "cache_creation_input_tokens", 0
            ),
        }

    cache_creation_input_tokens = prompt_details.get("cache_creation_input_tokens", 0)
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        "cached_tokens": prompt_details.get("cached_tokens", 0) or 0,
        "cache_creation_input_tokens": cache_creation_input_tokens or 0,
        "cache_write_tokens": cache_creation_input_tokens or 0,
    }


class AliCacheClient:
    def __init__(self, config: AliCacheConfig | None = None) -> None:
        self.config = config or load_ali_cache_config()
        self._client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
        )

    def warm_text_cache(
        self,
        *,
        model: str,
        guide_text: str,
        system_prompt: str,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=model,
                temperature=0,
                messages=self._build_prefix_messages(
                    guide_text=guide_text,
                    system_prompt=system_prompt,
                )
                + [
                    {
                        "role": "user",
                        "content": "Please reply with READY only.",
                    }
                ],
            )
        except Exception as exc:
            raise AliCacheError(f"Ali warm-cache request failed: {exc}") from exc

        return {
            "content": self._normalize_content(response.choices[0].message.content),
            "usage": build_usage_summary(response.usage),
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
        }

    def stream_image_question(
        self,
        *,
        model: str,
        guide_text: str,
        system_prompt: str,
        prompt: str,
        image_bytes: bytes,
    ) -> Iterator[dict[str, Any]]:
        image_config = OpenRouterCacheConfig(
            api_key="unused",
            base_url="unused",
            site_url=None,
            app_name="unused",
            timeout_seconds=self.config.timeout_seconds,
            max_image_side=self.config.max_image_side,
            jpeg_quality=self.config.jpeg_quality,
        )
        data_url = image_bytes_to_data_url(image_bytes, config=image_config)

        started_at = time.perf_counter()
        first_token_ms: float | None = None
        accumulated_text: list[str] = []
        usage: dict[str, Any] | None = None

        try:
            stream = self._client.chat.completions.create(
                model=model,
                temperature=0.2,
                stream=True,
                stream_options={"include_usage": True},
                messages=self._build_prefix_messages(
                    guide_text=guide_text,
                    system_prompt=system_prompt,
                )
                + [
                    {
                        "role": "user",
                        "content": [
                        {
                            "type": "text",
                            "text": (
                                "请根据公共实验指导分析这张图片。"
                                "优先指出最主要的问题。\n\n"
                                f"本次具体问题：{prompt.strip()}"
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                    }
                ],
            )
        except Exception as exc:
            raise AliCacheError(f"Ali streaming request failed: {exc}") from exc

        yield {"event": "start", "model": model}

        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = build_usage_summary(chunk.usage)

            if not getattr(chunk, "choices", None):
                continue

            delta = chunk.choices[0].delta
            text = self._extract_delta_text(delta)
            if not text:
                continue

            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - started_at) * 1000
            accumulated_text.append(text)
            yield {"event": "delta", "text": text}

        total_ms = (time.perf_counter() - started_at) * 1000
        usage = usage or build_usage_summary(None)
        yield {
            "event": "done",
            "text": "".join(accumulated_text),
            "usage": usage,
            "first_token_ms": round(first_token_ms, 1) if first_token_ms else None,
            "total_ms": round(total_ms, 1),
            "cache_hit": bool(usage.get("cached_tokens", 0) > 0),
        }

    @staticmethod
    def _build_prefix_messages(
        *,
        guide_text: str,
        system_prompt: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": system_prompt.strip()},
                    {
                        "type": "text",
                        "text": guide_text.strip(),
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
            }
        ]

    @staticmethod
    def _normalize_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
                elif isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "".join(parts).strip()
        return str(content).strip()

    @classmethod
    def _extract_delta_text(cls, delta: Any) -> str:
        content = getattr(delta, "content", None)
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        return cls._normalize_content(content)
