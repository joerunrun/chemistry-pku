from __future__ import annotations

import base64
import io
import os
import time
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps

load_dotenv()


class OpenRouterError(RuntimeError):
    """Raised when configuration or API calls fail."""


@dataclass(slots=True)
class ImagePrepMetrics:
    original_width: int
    original_height: int
    optimized_width: int
    optimized_height: int
    original_bytes: int
    optimized_bytes: int
    preprocess_ms: float

    def as_dict(self) -> dict[str, float | int]:
        compression_ratio = (
            self.optimized_bytes / self.original_bytes if self.original_bytes else 0.0
        )
        return {
            "original_width": self.original_width,
            "original_height": self.original_height,
            "optimized_width": self.optimized_width,
            "optimized_height": self.optimized_height,
            "original_bytes": self.original_bytes,
            "optimized_bytes": self.optimized_bytes,
            "preprocess_ms": round(self.preprocess_ms, 1),
            "compression_ratio": round(compression_ratio, 3),
        }


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
    jpeg_optimize: bool


def load_openrouter_config(require_api_key: bool = True) -> OpenRouterConfig:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    #print(f"Loaded OpenRouter API Key:",api_key)
    if require_api_key and not api_key:
        raise OpenRouterError(
            "未读取到 OPENROUTER_API_KEY。请先在环境变量或 .env 文件中配置。"
        )

    return OpenRouterConfig(
        api_key=api_key,
        model=os.getenv("OPENROUTER_MODEL", "qwen/qwen3-vl-8b-instruct").strip(),
        base_url=os.getenv(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
        ).strip(),
        site_url=os.getenv("OPENROUTER_SITE_URL", "").strip() or None,
        app_name=os.getenv("OPENROUTER_APP_NAME", "Chemistry Experiment Checker").strip(),
        timeout_seconds=int(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "180")),
        max_image_side=int(os.getenv("OPENROUTER_MAX_IMAGE_SIDE", "1280")),
        jpeg_quality=int(os.getenv("OPENROUTER_JPEG_QUALITY", "75")),
        jpeg_optimize=os.getenv("OPENROUTER_JPEG_OPTIMIZE", "false").strip().lower()
        in {"1", "true", "yes", "on"},
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


def build_image_part(image_bytes: bytes, *, config: Any | None = None) -> dict[str, Any]:
    part, _ = prepare_image_part(image_bytes, config=config)
    return part


def prepare_image_part(
    image_bytes: bytes, *, config: Any | None = None
) -> tuple[dict[str, Any], ImagePrepMetrics]:
    cfg = config or load_openrouter_config(require_api_key=False)
    started_at = time.perf_counter()
    (
        optimized_bytes,
        original_width,
        original_height,
        optimized_width,
        optimized_height,
    ) = optimize_image(
        image_bytes,
        cfg.max_image_side,
        cfg.jpeg_quality,
        getattr(cfg, "jpeg_optimize", False),
    )
    preprocess_ms = (time.perf_counter() - started_at) * 1000
    encoded = base64.b64encode(optimized_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
    }, ImagePrepMetrics(
        original_width=original_width,
        original_height=original_height,
        optimized_width=optimized_width,
        optimized_height=optimized_height,
        original_bytes=len(image_bytes),
        optimized_bytes=len(optimized_bytes),
        preprocess_ms=preprocess_ms,
    )


def optimize_image(
    image_bytes: bytes,
    max_image_side: int,
    jpeg_quality: int,
    jpeg_optimize: bool,
) -> tuple[bytes, int, int, int, int]:
    with Image.open(io.BytesIO(image_bytes)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")

        original_width, original_height = image.size
        image.thumbnail(
            (max_image_side, max_image_side),
            Image.Resampling.BILINEAR,
            reducing_gap=3.0,
        )
        optimized_width, optimized_height = image.size

        output = io.BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=jpeg_quality,
            optimize=jpeg_optimize,
            progressive=False,
        )
        return (
            output.getvalue(),
            original_width,
            original_height,
            optimized_width,
            optimized_height,
        )
