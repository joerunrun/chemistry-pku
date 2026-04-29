from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from openrouter_client import OpenRouterError

load_dotenv()


class GeminiError(OpenRouterError):
    """Raised when Gemini API configuration or requests fail."""


@dataclass(slots=True)
class GeminiConfig:
    api_key: str
    model: str
    timeout_seconds: int
    max_image_side: int
    jpeg_quality: int
    jpeg_optimize: bool
    disable_thinking: bool


def load_gemini_config(require_api_key: bool = True) -> GeminiConfig:
    api_key = (
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )
    if require_api_key and not api_key:
        raise GeminiError("未读取到 GEMINI_API_KEY 或 GOOGLE_API_KEY。请先配置环境变量或 .env 文件。")

    return GeminiConfig(
        api_key=api_key,
        model=os.getenv("GEMINI_MODEL", "gemini-3-flash-preview").strip(),
        timeout_seconds=int(os.getenv("GEMINI_TIMEOUT_SECONDS", "180")),
        max_image_side=int(os.getenv("GEMINI_MAX_IMAGE_SIDE", "1280")),
        jpeg_quality=int(os.getenv("GEMINI_JPEG_QUALITY", "75")),
        jpeg_optimize=os.getenv("GEMINI_JPEG_OPTIMIZE", "false").strip().lower()
        in {"1", "true", "yes", "on"},
        disable_thinking=os.getenv("GEMINI_DISABLE_THINKING", "false").strip().lower()
        in {"1", "true", "yes", "on"},
    )


class GeminiClient:
    def __init__(self, config: GeminiConfig | None = None) -> None:
        self.config = config or load_gemini_config()
        try:
            from google import genai
        except ImportError as exc:
            raise GeminiError("缺少 Gemini SDK。请先执行: pip install -r requirements.txt") from exc

        self._client = genai.Client(api_key=self.config.api_key)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        reasoning_enabled: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        try:
            from google.genai import types
        except ImportError as exc:
            raise GeminiError("缺少 Gemini SDK。请先执行: pip install -r requirements.txt") from exc

        system_instruction, contents = self._convert_messages(messages, types)
        config = self._build_generate_config(
            types,
            system_instruction=system_instruction,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_enabled=reasoning_enabled,
        )

        try:
            response = self._client.models.generate_content(
                model=self.config.model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            raise GeminiError(f"Gemini 请求失败: {exc}") from exc

        try:
            text = getattr(response, "text", None)
        except Exception:
            text = None
        if isinstance(text, str) and text.strip():
            return text.strip()

        parts = getattr(response, "parts", None)
        if parts:
            texts: list[str] = []
            for part in parts:
                part_text = getattr(part, "text", None)
                if part_text:
                    texts.append(str(part_text))
            if texts:
                return "\n".join(texts).strip()

        raise GeminiError(f"Gemini 返回格式异常: {response}")

    def _build_generate_config(
        self,
        types: Any,
        *,
        system_instruction: str | None,
        temperature: float,
        max_tokens: int | None,
        reasoning_enabled: bool,
    ) -> Any:
        config_kwargs: dict[str, Any] = {
            "temperature": temperature,
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if max_tokens is not None:
            config_kwargs["max_output_tokens"] = max_tokens
        if self.config.disable_thinking and not reasoning_enabled and hasattr(types, "ThinkingConfig"):
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        return types.GenerateContentConfig(**config_kwargs)

    def _convert_messages(self, messages: list[dict[str, Any]], types: Any) -> tuple[str | None, list[Any]]:
        system_parts: list[str] = []
        contents: list[Any] = []

        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if role == "system":
                system_parts.extend(self._content_to_system_text(content))
                continue
            contents.extend(self._content_to_gemini_parts(content, types))

        system_instruction = "\n\n".join(part for part in system_parts if part).strip() or None
        return system_instruction, contents

    def _content_to_system_text(self, content: Any) -> list[str]:
        if isinstance(content, str):
            return [content]
        if isinstance(content, list):
            texts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    texts.append(item)
                elif isinstance(item, dict) and item.get("type") == "text":
                    texts.append(str(item.get("text", "")))
            return texts
        return [str(content)]

    def _content_to_gemini_parts(self, content: Any, types: Any) -> list[Any]:
        if isinstance(content, str):
            return [content]
        if not isinstance(content, list):
            return [str(content)]

        parts: list[Any] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                parts.append(str(item))
                continue

            item_type = item.get("type")
            if item_type == "text":
                parts.append(str(item.get("text", "")))
            elif item_type == "image_url":
                parts.append(self._image_url_to_part(item, types))
        return parts

    def _image_url_to_part(self, item: dict[str, Any], types: Any) -> Any:
        image_url = item.get("image_url", {})
        if isinstance(image_url, dict):
            url = str(image_url.get("url", ""))
        else:
            url = str(image_url)

        prefix = "data:"
        if not url.startswith(prefix) or "," not in url:
            raise GeminiError("GeminiClient 目前只支持 data URL 格式的图片输入。")

        header, encoded = url.split(",", 1)
        mime_type = "image/jpeg"
        if header.startswith("data:") and ";base64" in header:
            mime_type = header.removeprefix("data:").split(";", 1)[0] or mime_type

        try:
            image_bytes = base64.b64decode(encoded)
        except ValueError as exc:
            raise GeminiError("图片 data URL 的 base64 内容无效。") from exc

        return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
