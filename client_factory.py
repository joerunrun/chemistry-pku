from __future__ import annotations

from ali_client import AliyunQwenClient, load_aliyun_config
from gemini_client import GeminiClient, load_gemini_config
from omni_client import OmniChemistryClient, load_omni_config
from openrouter_client import OpenRouterGeminiClient, load_openrouter_config


def create_chat_client(provider: str):
    normalized = provider.strip().lower()
    if normalized == "openrouter":
        return OpenRouterGeminiClient()
    if normalized == "ali":
        return AliyunQwenClient()
    if normalized == "gemini":
        return GeminiClient()
    if normalized == "omni":
        return OmniChemistryClient()
    raise ValueError(f"不支持的 provider: {provider}")


def get_provider_model_name(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized == "openrouter":
        return load_openrouter_config(require_api_key=False).model
    if normalized == "ali":
        return load_aliyun_config(require_api_key=False).model
    if normalized == "gemini":
        return load_gemini_config(require_api_key=False).model
    if normalized == "omni":
        return load_omni_config(require_api_key=False).model
    raise ValueError(f"不支持的 provider: {provider}")
