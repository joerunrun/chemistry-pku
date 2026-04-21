from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI
from pypdf import PdfReader

from ali_client import AliyunError, load_aliyun_config
from openrouter_client import build_image_part


CACHE_DIR = Path(".pdf_cache_mode")
CACHE_DIR.mkdir(exist_ok=True)

DEFAULT_CACHE_MODEL = "qwen3-vl-plus"
CACHE_VALID_SECONDS = 300
EXPLICIT_CACHE_MODELS = {"qwen-flash", "qwen3.5-flash", "qwen3-vl-plus"}


@dataclass(slots=True)
class PdfCacheArtifact:
    cache_id: str
    pdf_path: str
    artifact_path: str
    extracted_text_path: str
    model: str
    created_at: float
    guidance_text_chars: int
    cache_mode: str
    remote_cache_warmed_at: float | None
    notes: str


class PdfCacheModeClient:
    def __init__(self) -> None:
        cfg = load_aliyun_config()
        self.api_key = cfg.api_key
        self.base_url = cfg.base_url
        self.model = os.getenv("ALI_CACHE_MODEL", "qwen3-vl-flash").strip() or "qwen3-vl-flash"
        self.max_image_side = int(os.getenv("CACHE_MODE_IMAGE_SIDE", str(cfg.max_image_side)))
        self.jpeg_quality = int(os.getenv("CACHE_MODE_IMAGE_QUALITY", str(cfg.jpeg_quality)))
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=int(os.getenv("CACHE_MODE_TIMEOUT_SECONDS", "300")),
        )

    def build_cache_from_pdf(
        self,
        pdf_path: str,
        *,
        cache_name: str | None = None,
        warm_remote_cache: bool = True,
    ) -> PdfCacheArtifact:
        pdf_file = Path(pdf_path).resolve()
        if not pdf_file.is_file():
            raise FileNotFoundError(f"找不到 PDF: {pdf_file}")

        raw_text = self.extract_pdf_text(pdf_file)
        guidance_text = self.build_guidance_text(raw_text)
        cache_id = cache_name or self.make_cache_id(pdf_file, guidance_text)

        extracted_text_path = CACHE_DIR / f"{cache_id}.txt"
        artifact_path = CACHE_DIR / f"{cache_id}.json"
        extracted_text_path.write_text(guidance_text, encoding="utf-8")

        warmed_at: float | None = None
        cache_mode = self.get_cache_mode()
        if warm_remote_cache and cache_mode == "explicit":
            self.warm_remote_cache(guidance_text)
            warmed_at = time.time()

        artifact = PdfCacheArtifact(
            cache_id=cache_id,
            pdf_path=str(pdf_file),
            artifact_path=str(artifact_path.resolve()),
            extracted_text_path=str(extracted_text_path.resolve()),
            model=self.model,
            created_at=time.time(),
            guidance_text_chars=len(guidance_text),
            cache_mode=cache_mode,
            remote_cache_warmed_at=warmed_at,
            notes=(
                "本地 artifact 可长期复用；如果是 explicit cache，远端 cache 约 5 分钟有效，命中会续期。"
                if cache_mode == "explicit"
                else "当前模型走 implicit cache，不需要显式预热，但命中率不保证。"
            ),
        )
        artifact_path.write_text(
            json.dumps(asdict(artifact), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return artifact

    def correct_image_with_cache(
        self,
        cache_id: str,
        wrong_image_bytes: bytes,
        *,
        refresh_if_expired: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        artifact = self.load_artifact(cache_id)
        guidance_text = Path(artifact.extracted_text_path).read_text(encoding="utf-8")

        if artifact.cache_mode == "explicit" and refresh_if_expired and self.cache_is_expired(artifact):
            self.warm_remote_cache(guidance_text)
            artifact.remote_cache_warmed_at = time.time()
            Path(artifact.artifact_path).write_text(
                json.dumps(asdict(artifact), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        messages = self.build_correction_messages(
            guidance_text,
            wrong_image_bytes,
            use_explicit_cache=(artifact.cache_mode == "explicit"),
        )
        started_at = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
            )
        except Exception as exc:
            raise AliyunError(f"Cache 模式纠错请求失败: {exc}") from exc

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        content = response.choices[0].message.content
        result = self.normalize_content(content)
        usage = self.extract_usage(response)
        usage["elapsed_ms"] = round(elapsed_ms, 1)
        usage["cache_id"] = cache_id
        usage["cache_expired"] = self.cache_is_expired(artifact)
        return result, usage

    def warm_remote_cache(self, guidance_text: str) -> dict[str, Any]:
        if self.get_cache_mode() != "explicit":
            return {"skipped": True, "reason": "当前模型只支持 implicit cache，不需要显式预热。"}
        messages = self.build_cache_warm_messages(guidance_text)
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                max_tokens=16,
            )
        except Exception as exc:
            raise AliyunError(f"创建远端 explicit cache 失败: {exc}") from exc
        return self.extract_usage(response)

    def build_cache_warm_messages(self, guidance_text: str) -> list[dict[str, Any]]:
        return [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "你是化学实验纠错助手。后面给你的内容是从实验 PDF 中离线提取出的指导材料。"
                            "后续请求会复用这些指导材料，请严格按材料判断。"
                        ),
                    },
                    {
                        "type": "text",
                        "text": guidance_text,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
            },
            {
                "role": "user",
                "content": "请只回复：缓存建立完成。",
            },
        ]

    def build_correction_messages(
        self,
        guidance_text: str,
        wrong_image_bytes: bytes,
        *,
        use_explicit_cache: bool,
    ) -> list[dict[str, Any]]:
        image_part = build_image_part(
            wrong_image_bytes,
            config=self,
        )
        guidance_part: dict[str, Any] = {
            "type": "text",
            "text": guidance_text,
        }
        if use_explicit_cache:
            guidance_part["cache_control"] = {"type": "ephemeral"}
        return [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "你是化学实验纠错助手。后面给你的内容是从实验 PDF 中离线提取出的指导材料。"
                            "你必须以这些材料为准分析错误图片。"
                        ),
                    },
                    guidance_part,
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "下面是一张错误实验图片。请根据上面的指导材料快速判断这张图最主要的错误。"
                            "忽略拍摄角度、远近、构图偏移、光照变化。"
                            "请按以下格式输出：\n"
                            "主要错误：...\n\n原因：...\n\n纠正建议：..."
                        ),
                    },
                    image_part,
                ],
            },
        ]

    @staticmethod
    def extract_pdf_text(pdf_file: Path) -> str:
        reader = PdfReader(str(pdf_file))
        page_texts: list[str] = []
        for index, page in enumerate(reader.pages, start=1):
            extracted = page.extract_text() or ""
            cleaned = re.sub(r"\s+", " ", extracted).strip()
            if cleaned:
                page_texts.append(f"[第{index}页]\n{cleaned}")
        if not page_texts:
            raise ValueError("PDF 未提取到可用文本。")
        return "\n\n".join(page_texts)

    @staticmethod
    def build_guidance_text(raw_text: str) -> str:
        cleaned = raw_text.replace("\x00", " ")
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return (
            "以下内容来自实验指导 PDF 的离线提取结果。可能存在排版噪声，但请尽量抓取其中的实验目的、"
            "装置要求、操作步骤、判错依据和安全注意事项。\n\n"
            f"{cleaned.strip()}"
        )

    @staticmethod
    def normalize_content(content: Any) -> str:
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

    @staticmethod
    def make_cache_id(pdf_file: Path, guidance_text: str) -> str:
        digest = hashlib.sha1(
            f"{pdf_file.resolve()}::{len(guidance_text)}::{guidance_text[:2048]}".encode("utf-8")
        ).hexdigest()[:12]
        return f"{pdf_file.stem}-{digest}"

    def get_cache_mode(self) -> str:
        return "explicit" if self.model in EXPLICIT_CACHE_MODELS else "implicit"

    @staticmethod
    def extract_usage(response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {}

        prompt_details = getattr(usage, "prompt_tokens_details", None)
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "cached_tokens": getattr(prompt_details, "cached_tokens", None)
            if prompt_details is not None else None,
            "cache_creation_input_tokens": getattr(
                prompt_details, "cache_creation_input_tokens", None
            ) if prompt_details is not None else None,
        }

    @staticmethod
    def load_artifact(cache_id: str) -> PdfCacheArtifact:
        artifact_path = CACHE_DIR / f"{cache_id}.json"
        if not artifact_path.is_file():
            raise FileNotFoundError(f"找不到 cache artifact: {artifact_path}")
        return PdfCacheArtifact(**json.loads(artifact_path.read_text(encoding="utf-8")))

    @staticmethod
    def cache_is_expired(artifact: PdfCacheArtifact) -> bool:
        if artifact.remote_cache_warmed_at is None:
            return True
        return time.time() - artifact.remote_cache_warmed_at > CACHE_VALID_SECONDS
