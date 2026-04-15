from __future__ import annotations

from ali_client import AliyunQwenClient, load_aliyun_config
from openrouter_client import OpenRouterGeminiClient, load_openrouter_config


def create_chat_client(provider: str):
    normalized = provider.strip().lower()
    if normalized == "openrouter":
        return OpenRouterGeminiClient()
    if normalized == "ali":
        return AliyunQwenClient()
    raise ValueError(f"不支持的 provider: {provider}")


def get_provider_model_name(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized == "openrouter":
        return load_openrouter_config(require_api_key=False).model
    if normalized == "ali":
        return load_aliyun_config(require_api_key=False).model
    raise ValueError(f"不支持的 provider: {provider}")
