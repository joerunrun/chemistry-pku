from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps

load_dotenv()


class OpenRouterError(RuntimeError):
    """Raised when configuration or API calls fail."""


@dataclass(slots=True)
class OpenRouterConfig:
    api_key: str
    model: str
    base_url: str
    site_url: str | None
    app_name: str
    timeout_seconds: int
    max_image_side: int
    jpeg_quality: int


def load_openrouter_config(require_api_key: bool = True) -> OpenRouterConfig:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if require_api_key and not api_key:
        raise OpenRouterError(
            "未读取到 OPENROUTER_API_KEY。请先在环境变量或 .env 文件中配置。"
        )

    return OpenRouterConfig(
        api_key=api_key,
        model=os.getenv("OPENROUTER_MODEL", "qwen/qwen3.6-plus").strip(),
        base_url=os.getenv(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
        ).strip(),
        site_url=os.getenv("OPENROUTER_SITE_URL", "").strip() or None,
        app_name=os.getenv("OPENROUTER_APP_NAME", "Chemistry Experiment Checker").strip(),
        timeout_seconds=int(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "180")),
        max_image_side=int(os.getenv("OPENROUTER_MAX_IMAGE_SIDE", "1600")),
        jpeg_quality=int(os.getenv("OPENROUTER_JPEG_QUALITY", "85")),
    )


class OpenRouterGeminiClient:
    def __init__(self, config: OpenRouterConfig | None = None) -> None:
        self.config = config or load_openrouter_config()

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        reasoning_enabled: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
        }
        if reasoning_enabled:
            payload["reasoning"] = {"enabled": True}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            response = requests.post(
                self.config.base_url,
                headers=self._build_headers(),
                json=payload,
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise OpenRouterError(f"OpenRouter 连接失败: {exc}") from exc

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text.strip() or "无返回内容"
            raise OpenRouterError(f"OpenRouter 请求失败: {detail}") from exc

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError(f"OpenRouter 返回格式异常: {data}") from exc

        return self._normalize_content(content)

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
    def _normalize_content(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "\n".join(part for part in parts if part).strip()

        return str(content).strip()


def build_text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def build_image_part(image_bytes: bytes, *, config: OpenRouterConfig | None = None) -> dict[str, Any]:
    cfg = config or load_openrouter_config(require_api_key=False)
    optimized_bytes = optimize_image(image_bytes, cfg.max_image_side, cfg.jpeg_quality)
    encoded = base64.b64encode(optimized_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
    }


def optimize_image(image_bytes: bytes, max_image_side: int, jpeg_quality: int) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")

        image.thumbnail((max_image_side, max_image_side))

        output = io.BytesIO()
        image.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
        return output.getvalue()
