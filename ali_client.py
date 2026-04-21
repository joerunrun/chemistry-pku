from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Generator
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from openrouter_client import OpenRouterError

load_dotenv()


class AliyunError(OpenRouterError):
    """Raised when Aliyun DashScope requests fail."""


@dataclass(slots=True)
class AliyunConfig:
    api_key: str
    model: str
    base_url: str
    timeout_seconds: int
    max_image_side: int
    jpeg_quality: int
    jpeg_optimize: bool
    enable_thinking: bool


def load_aliyun_config(require_api_key: bool = True) -> AliyunConfig:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if require_api_key and not api_key:
        raise AliyunError("未读取到 DASHSCOPE_API_KEY。请先在环境变量或 .env 文件中配置。")

    return AliyunConfig(
        api_key=api_key,
        model=os.getenv("ALI_MODEL", "qwen3.6-flash").strip(),
        base_url=os.getenv(
            "ALI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).strip(),
        timeout_seconds=int(os.getenv("ALI_TIMEOUT_SECONDS", "180")),
        max_image_side=int(os.getenv("ALI_MAX_IMAGE_SIDE", "1280")),
        jpeg_quality=int(os.getenv("ALI_JPEG_QUALITY", "75")),
        jpeg_optimize=os.getenv("ALI_JPEG_OPTIMIZE", "false").strip().lower()
        in {"1", "true", "yes", "on"},
        enable_thinking=os.getenv("ALI_ENABLE_THINKING", "true").strip().lower()
        in {"1", "true", "yes", "on"},
    )


class AliyunQwenClient:
    def __init__(self, config: AliyunConfig | None = None) -> None:
        self.config = config or load_aliyun_config()
        self._client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        reasoning_enabled: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        request_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            request_kwargs["max_tokens"] = max_tokens
        if reasoning_enabled or self.config.enable_thinking:
            request_kwargs["extra_body"] = {"enable_thinking": True}

        try:
            response = self._client.chat.completions.create(**request_kwargs)
        except Exception as exc:
            raise AliyunError(f"阿里云请求失败: {exc}") from exc

        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise AliyunError(f"阿里云返回格式异常: {response}") from exc

        return self._normalize_content(content)

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        reasoning_enabled: bool = False,
        max_tokens: int | None = None,
    ) -> Generator[str, None, None]:
        request_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            request_kwargs["max_tokens"] = max_tokens
        if reasoning_enabled or self.config.enable_thinking:
            request_kwargs["extra_body"] = {"enable_thinking": True}

        try:
            stream = self._client.chat.completions.create(**request_kwargs)
            for chunk in stream:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                text = self._extract_delta_text(delta)
                if text:
                    yield text
        except Exception as exc:
            raise AliyunError(f"阿里云流式请求失败: {exc}") from exc

    @staticmethod
    def _normalize_content(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if hasattr(item, "text") and item.text:
                    parts.append(str(item.text))
                elif isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "\n".join(part for part in parts if part).strip()

        return str(content).strip()

    @classmethod
    def _extract_delta_text(cls, delta: Any) -> str:
        content = getattr(delta, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return cls._normalize_content(content)
        if content:
            return str(content)
        return ""
