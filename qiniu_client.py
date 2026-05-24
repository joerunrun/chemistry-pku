from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Generator

import requests
from dotenv import load_dotenv

from openrouter_client import OpenRouterError

load_dotenv()


class QiniuError(OpenRouterError):
    """Raised when Qiniu/Sufy-compatible requests fail."""


@dataclass(slots=True)
class QiniuConfig:
    api_key: str
    model: str
    base_url: str
    timeout_seconds: int
    max_retry: int
    max_image_side: int
    jpeg_quality: int
    jpeg_optimize: bool


def _is_qiniu_like_base(url: str | None) -> bool:
    if not url:
        return False
    normalized = url.lower()
    return "qnaigc.com" in normalized or "sufy.com" in normalized


def _normalize_qiniu_url(base: str) -> str:
    normalized = base.strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def _load_qiniu_api_key(require_api_key: bool) -> str:
    api_key = os.getenv("QINIU_API_KEY", "").strip()
    if api_key:
        return api_key

    anthropic_base = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    anthropic_token = os.getenv("ANTHROPIC_AUTH_TOKEN", "").strip()
    if anthropic_token and _is_qiniu_like_base(anthropic_base):
        return anthropic_token

    if require_api_key:
        raise QiniuError(
            "QINIU_API_KEY is not set. Set QINIU_API_KEY, or set "
            "ANTHROPIC_AUTH_TOKEN only when ANTHROPIC_BASE_URL points to Qiniu/Sufy."
        )
    return ""


def load_qiniu_config(require_api_key: bool = True) -> QiniuConfig:
    explicit_url = os.getenv("QINIU_API_URL", "").strip()
    base = explicit_url or os.getenv("QINIU_API_BASE", "https://api.qnaigc.com").strip()
    if not explicit_url and os.getenv("QINIU_API_BASE") and not _is_qiniu_like_base(base):
        raise QiniuError(f"QINIU_API_BASE points to a non-Qiniu endpoint: {base}")

    return QiniuConfig(
        api_key=_load_qiniu_api_key(require_api_key),
        model=os.getenv("QINIU_MODEL", "google/gemini-3.5-flash").strip()
        or "google/gemini-3.5-flash",
        base_url=_normalize_qiniu_url(base),
        timeout_seconds=int(os.getenv("QINIU_TIMEOUT_SECONDS", "180")),
        max_retry=int(os.getenv("QINIU_MAX_RETRY", "2")),
        max_image_side=int(os.getenv("QINIU_MAX_IMAGE_SIDE", "1280")),
        jpeg_quality=int(os.getenv("QINIU_JPEG_QUALITY", "75")),
        jpeg_optimize=os.getenv("QINIU_JPEG_OPTIMIZE", "false").strip().lower()
        in {"1", "true", "yes", "on"},
    )


class QiniuChatClient:
    def __init__(self, config: QiniuConfig | None = None) -> None:
        self.config = config or load_qiniu_config()

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
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        data = self._post(payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise QiniuError(f"Qiniu response format is invalid: {data}") from exc
        return self._normalize_content(content)

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        reasoning_enabled: bool = False,
        max_tokens: int | None = None,
    ) -> Generator[str, None, None]:
        yield self.chat(messages, temperature=temperature, max_tokens=max_tokens)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, self.config.max_retry + 1):
            try:
                response = requests.post(
                    self.config.base_url,
                    headers=self._build_headers(),
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )
                if response.status_code == 200:
                    return response.json()
                if (
                    response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
                    and attempt < self.config.max_retry
                ):
                    time.sleep(attempt * 2)
                    continue
                detail = response.text.strip() or "empty response"
                raise QiniuError(f"Qiniu HTTP {response.status_code}: {detail[:500]}")
            except requests.RequestException as exc:
                if attempt < self.config.max_retry:
                    time.sleep(attempt * 2)
                    continue
                raise QiniuError(f"Qiniu request failed: {exc}") from exc
        raise QiniuError("Qiniu request failed after retries.")

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Authentication": f"Bearer {self.config.api_key}",
            "X-API-Key": self.config.api_key,
            "x-api-key": self.config.api_key,
            "Content-Type": "application/json",
        }

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
