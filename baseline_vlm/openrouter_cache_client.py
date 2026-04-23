from __future__ import annotations

import base64
import io
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Iterator

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps

load_dotenv()


class OpenRouterCacheError(RuntimeError):
    """Raised when the baseline OpenRouter cache flow fails."""


@dataclass(slots=True)
class OpenRouterCacheConfig:
    api_key: str
    base_url: str
    site_url: str | None
    app_name: str
    timeout_seconds: int
    max_image_side: int
    jpeg_quality: int


def load_openrouter_cache_config() -> OpenRouterCacheConfig:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise OpenRouterCacheError("缺少环境变量 OPENROUTER_API_KEY。")

    site_url = os.getenv("OPENROUTER_SITE_URL", "").strip() or None
    return OpenRouterCacheConfig(
        api_key=api_key,
        base_url=os.getenv(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
        ).strip(),
        site_url=site_url,
        app_name=os.getenv("OPENROUTER_APP_NAME", "Baseline VLM Cache Demo").strip(),
        timeout_seconds=int(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "180")),
        max_image_side=int(os.getenv("OPENROUTER_MAX_IMAGE_SIDE", "1280")),
        jpeg_quality=int(os.getenv("OPENROUTER_JPEG_QUALITY", "75")),
    )


def optimize_image(
    image_bytes: bytes,
    *,
    max_image_side: int,
    jpeg_quality: int,
) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.thumbnail(
            (max_image_side, max_image_side),
            Image.Resampling.BILINEAR,
            reducing_gap=3.0,
        )
        output = io.BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=jpeg_quality,
            optimize=False,
            progressive=False,
        )
        return output.getvalue()


def image_bytes_to_data_url(image_bytes: bytes, *, config: OpenRouterCacheConfig) -> str:
    optimized = optimize_image(
        image_bytes,
        max_image_side=config.max_image_side,
        jpeg_quality=config.jpeg_quality,
    )
    encoded = base64.b64encode(optimized).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def build_usage_summary(usage: dict[str, Any] | None) -> dict[str, Any]:
    usage = usage or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "cached_tokens": prompt_details.get("cached_tokens", 0),
        "cache_write_tokens": prompt_details.get("cache_write_tokens", 0),
    }


class OpenRouterCacheClient:
    def __init__(self, config: OpenRouterCacheConfig | None = None) -> None:
        self.config = config or load_openrouter_cache_config()

    def warm_text_cache(
        self,
        *,
        model: str,
        guide_text: str,
        system_prompt: str,
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "temperature": 0,
            "messages": self._build_prefix_messages(
                guide_text=guide_text,
                system_prompt=system_prompt,
            )
            + [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "请仅回复 READY。"}],
                }
            ],
        }

        started_at = time.perf_counter()
        try:
            response = requests.post(
                self.config.base_url,
                headers=self._build_headers(),
                json=payload,
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise OpenRouterCacheError(f"OpenRouter 预热请求失败: {exc}") from exc

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text.strip() or "未知错误"
            raise OpenRouterCacheError(f"OpenRouter 预热请求失败: {detail}") from exc

        data = response.json()
        content = self._extract_message_content(data)
        usage = build_usage_summary(data.get("usage"))
        return {
            "content": content,
            "usage": usage,
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
        data_url = image_bytes_to_data_url(image_bytes, config=self.config)
        payload = {
            "model": model,
            "temperature": 0.0,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": self._build_prefix_messages(
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
        }

        started_at = time.perf_counter()
        first_token_ms: float | None = None
        accumulated_text: list[str] = []
        usage: dict[str, Any] | None = None

        try:
            response = requests.post(
                self.config.base_url,
                headers=self._build_headers(),
                json=payload,
                timeout=self.config.timeout_seconds,
                stream=True,
            )
        except requests.RequestException as exc:
            raise OpenRouterCacheError(f"OpenRouter 流式请求失败: {exc}") from exc

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text.strip() or "未知错误"
            raise OpenRouterCacheError(f"OpenRouter 流式请求失败: {detail}") from exc

        response.encoding = "utf-8"
        yield {"event": "start", "model": model}

        try:
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue

                raw = line[6:].strip()
                if raw == "[DONE]":
                    break

                data = json.loads(raw)
                if data.get("error"):
                    raise OpenRouterCacheError(str(data["error"]))

                delta_text = self._extract_delta_text(data)
                if delta_text:
                    if first_token_ms is None:
                        first_token_ms = (time.perf_counter() - started_at) * 1000
                    accumulated_text.append(delta_text)
                    yield {"event": "delta", "text": delta_text}

                if data.get("usage"):
                    usage = build_usage_summary(data.get("usage"))
        finally:
            response.close()

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

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "X-Title": self.config.app_name,
        }
        if self.config.site_url:
            headers["HTTP-Referer"] = self.config.site_url
        return headers

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
                    {
                        "type": "text",
                        "text": system_prompt.strip()+guide_text.strip(),
                        "cache_control":{"type":"ephemeral"}
                    }
                ],
            },

        ]

    @staticmethod
    def _extract_message_content(data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterCacheError(f"OpenRouter 返回结构异常: {data}") from exc

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "".join(parts).strip()

        return str(content).strip()

    @staticmethod
    def _extract_delta_text(data: dict[str, Any]) -> str:
        try:
            delta = data["choices"][0]["delta"]
        except (KeyError, IndexError, TypeError):
            return ""

        content = delta.get("content")
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "".join(parts)

        return ""
