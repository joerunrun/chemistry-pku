from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz
from openai import OpenAI
from pypdf import PdfReader

from ali_client import AliyunError, load_aliyun_config
from openrouter_client import build_image_part


QWEN36_CACHE_DIR = Path(".qwen36_cache")
QWEN36_CACHE_DIR.mkdir(exist_ok=True)

DEFAULT_MODEL = "qwen3.6-flash"
CACHE_VALID_SECONDS = 300
PAGE_BATCH_SIZE = 2


@dataclass(slots=True)
class Qwen36GuideArtifact:
    cache_id: str
    pdf_path: str
    model: str
    created_at: float
    page_count: int
    page_summary_path: str
    guide_summary_path: str
    remote_cache_warmed_at: float | None
    remote_cache_usage: dict[str, Any] | None
    notes: str


class Qwen36GuideCacheClient:
    def __init__(self) -> None:
        cfg = load_aliyun_config()
        self.api_key = cfg.api_key
        self.base_url = cfg.base_url
        self.model = os.getenv("QWEN36_CACHE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.max_image_side = int(os.getenv("QWEN36_CACHE_IMAGE_SIDE", str(cfg.max_image_side)))
        self.jpeg_quality = int(os.getenv("QWEN36_CACHE_IMAGE_QUALITY", str(cfg.jpeg_quality)))
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=int(os.getenv("QWEN36_CACHE_TIMEOUT_SECONDS", "300")),
        )

    def build_cache_from_pdf(
        self,
        pdf_path: str,
        *,
        cache_name: str | None = None,
        warm_remote_cache: bool = True,
    ) -> Qwen36GuideArtifact:
        pdf_file = Path(pdf_path).resolve()
        if not pdf_file.is_file():
            raise FileNotFoundError(f"找不到 PDF: {pdf_file}")

        pdf_text = self.extract_pdf_text(pdf_file)
        page_images = self.render_pdf_pages(pdf_file)
        cache_id = cache_name or self.make_cache_id(pdf_file)

        page_summaries = self.summarize_pdf_pages(pdf_text, page_images)
        guide_summary = self.summarize_guide(page_summaries)

        page_summary_path = QWEN36_CACHE_DIR / f"{cache_id}.page_summaries.json"
        guide_summary_path = QWEN36_CACHE_DIR / f"{cache_id}.guide.txt"
        page_summary_path.write_text(
            json.dumps(page_summaries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        guide_summary_path.write_text(guide_summary, encoding="utf-8")

        warmed_at: float | None = None
        warm_usage: dict[str, Any] | None = None
        if warm_remote_cache:
            warm_usage = self.warm_remote_cache(guide_summary)
            warmed_at = time.time()

        artifact = Qwen36GuideArtifact(
            cache_id=cache_id,
            pdf_path=str(pdf_file),
            model=self.model,
            created_at=time.time(),
            page_count=len(page_images),
            page_summary_path=str(page_summary_path.resolve()),
            guide_summary_path=str(guide_summary_path.resolve()),
            remote_cache_warmed_at=warmed_at,
            remote_cache_usage=warm_usage,
            notes=(
                "全程使用 qwen3.6-flash。离线阶段先把 PDF 的文字和页图整理成指导摘要；"
                "在线阶段复用同一段显式 cache 前缀。远端 cache 为短时缓存，约 5 分钟有效，命中会续期。"
            ),
        )
        artifact_path = self.artifact_path(cache_id)
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
        guide_summary = Path(artifact.guide_summary_path).read_text(encoding="utf-8")

        if refresh_if_expired and self.cache_is_expired(artifact):
            self.warm_remote_cache(guide_summary)
            artifact.remote_cache_warmed_at = time.time()
            self.artifact_path(cache_id).write_text(
                json.dumps(asdict(artifact), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        messages = self.build_correction_messages(guide_summary, wrong_image_bytes)
        started_at = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.2,
            )
        except Exception as exc:
            raise AliyunError(f"qwen3.6-flash cache 模式纠错失败: {exc}") from exc

        usage = self.extract_usage(response)
        usage["elapsed_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        usage["cache_id"] = cache_id
        usage["cache_expired"] = self.cache_is_expired(artifact)
        usage["cache_hit"] = bool((usage.get("cached_tokens") or 0) > 0)
        result = self.normalize_content(response.choices[0].message.content)
        return result, usage

    def warm_remote_cache(self, guide_summary: str) -> dict[str, Any]:
        messages = self.build_cache_warm_messages(guide_summary)
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                max_tokens=16,
            )
        except Exception as exc:
            raise AliyunError(f"qwen3.6-flash 显式 cache 预热失败: {exc}") from exc
        return self.extract_usage(response)

    def summarize_pdf_pages(
        self,
        pdf_text: list[dict[str, str]],
        page_images: list[bytes],
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for start in range(0, len(page_images), PAGE_BATCH_SIZE):
            batch_text = pdf_text[start : start + PAGE_BATCH_SIZE]
            batch_images = page_images[start : start + PAGE_BATCH_SIZE]
            messages = self.build_page_summary_messages(batch_text, batch_images)
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.1,
                )
            except Exception as exc:
                raise AliyunError(f"qwen3.6-flash 离线解析 PDF 页失败: {exc}") from exc
            summaries.append(
                {
                    "page_range": [
                        batch_text[0]["page_number"],
                        batch_text[-1]["page_number"],
                    ],
                    "summary": self.normalize_content(response.choices[0].message.content),
                    "usage": self.extract_usage(response),
                }
            )
        return summaries

    def summarize_guide(self, page_summaries: list[dict[str, Any]]) -> str:
        summary_text = "\n\n".join(
            f"[页码 {item['page_range'][0]}-{item['page_range'][1]}]\n{item['summary']}"
            for item in page_summaries
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是化学实验指导提炼助手。请把给定的分页摘要整理成一个后续可复用的指导摘要。"
                    "要覆盖实验目的、装置要求、操作步骤、关键判错依据、安全注意事项。"
                    "输出必须结构清晰、稳定、适合作为后续纠错的固定前缀。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请把下面这些分页摘要整理成统一指导摘要。\n\n"
                    f"{summary_text}"
                ),
            },
        ]
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
            )
        except Exception as exc:
            raise AliyunError(f"qwen3.6-flash 生成指导摘要失败: {exc}") from exc
        return self.normalize_content(response.choices[0].message.content)

    def build_page_summary_messages(
        self,
        batch_text: list[dict[str, str]],
        batch_images: list[bytes],
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "下面是实验指导 PDF 的若干页，请结合每页图像和抽取文字，提炼对后续实验纠错真正有用的信息。"
                    "重点提取装置组成、连接关系、关键步骤、常见错误、安全要求。"
                ),
            }
        ]
        for item, image_bytes in zip(batch_text, batch_images):
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"第 {item['page_number']} 页抽取文字：\n{item['text']}"
                    ),
                }
            )
            content.append(build_image_part(image_bytes, config=self))

        return [
            {
                "role": "system",
                "content": "你是化学实验指导信息提炼助手，只输出中文。",
            },
            {"role": "user", "content": content},
        ]

    def build_cache_warm_messages(self, guide_summary: str) -> list[dict[str, Any]]:
        return [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "你是化学实验纠错助手。下面是一段固定指导摘要，后续请求会复用它。"
                        ),
                    },
                    {
                        "type": "text",
                        "text": guide_summary,
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
        guide_summary: str,
        wrong_image_bytes: bytes,
    ) -> list[dict[str, Any]]:
        return [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "你是化学实验纠错助手。下面是一段固定指导摘要，请据此判断错误图片。",
                    },
                    {
                        "type": "text",
                        "text": guide_summary,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "下面是一张错误实验图片。请根据上面的指导摘要，快速指出最主要的错误。"
                            "忽略拍摄角度、远近、构图偏移、光照变化。"
                            "请按以下格式输出：\n"
                            "主要错误：...\n\n原因：...\n\n纠正建议：..."
                        ),
                    },
                    build_image_part(wrong_image_bytes, config=self),
                ],
            },
        ]

    @staticmethod
    def extract_pdf_text(pdf_file: Path) -> list[dict[str, str]]:
        reader = PdfReader(str(pdf_file))
        page_items: list[dict[str, str]] = []
        for index, page in enumerate(reader.pages, start=1):
            extracted = page.extract_text() or ""
            cleaned = re.sub(r"\s+", " ", extracted).strip()
            page_items.append({"page_number": index, "text": cleaned or "（本页未提取到清晰文字）"})
        return page_items

    @staticmethod
    def render_pdf_pages(pdf_file: Path) -> list[bytes]:
        doc = fitz.open(str(pdf_file))
        pages: list[bytes] = []
        try:
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
                pages.append(pix.tobytes("jpg"))
        finally:
            doc.close()
        return pages

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
    def make_cache_id(pdf_file: Path) -> str:
        digest = hashlib.sha1(str(pdf_file.resolve()).encode("utf-8")).hexdigest()[:12]
        return f"{pdf_file.stem}-q36-{digest}"

    @staticmethod
    def artifact_path(cache_id: str) -> Path:
        return QWEN36_CACHE_DIR / f"{cache_id}.json"

    @staticmethod
    def load_artifact(cache_id: str) -> Qwen36GuideArtifact:
        path = QWEN36_CACHE_DIR / f"{cache_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"找不到 cache artifact: {path}")
        return Qwen36GuideArtifact(**json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def cache_is_expired(artifact: Qwen36GuideArtifact) -> bool:
        if artifact.remote_cache_warmed_at is None:
            return True
        return time.time() - artifact.remote_cache_warmed_at > CACHE_VALID_SECONDS
