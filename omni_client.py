from __future__ import annotations

import base64
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import dashscope
from dashscope.audio.qwen_omni import MultiModality, OmniRealtimeCallback, OmniRealtimeConversation
from dotenv import load_dotenv

from ali_client import AliyunError, load_aliyun_config
from openrouter_client import optimize_image

load_dotenv()


@dataclass(slots=True)
class OmniConfig:
    api_key: str
    model: str
    url: str
    voice: str
    timeout_seconds: int
    max_image_side: int
    jpeg_quality: int
    jpeg_optimize: bool


def load_omni_config(require_api_key: bool = True) -> OmniConfig:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if require_api_key and not api_key:
        raise AliyunError("未读取到 DASHSCOPE_API_KEY。请先在环境变量或 .env 文件中配置。")

    ali_cfg = load_aliyun_config(require_api_key=False)
    return OmniConfig(
        api_key=api_key,
        model=os.getenv("OMNI_MODEL", "qwen3.5-omni-plus-realtime").strip(),
        url=os.getenv(
            "OMNI_REALTIME_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        ).strip(),
        voice=os.getenv("OMNI_VOICE", "Ethan").strip() or "Ethan",
        timeout_seconds=int(os.getenv("OMNI_TIMEOUT_SECONDS", "90")),
        max_image_side=int(os.getenv("OMNI_MAX_IMAGE_SIDE", str(ali_cfg.max_image_side))),
        jpeg_quality=int(os.getenv("OMNI_JPEG_QUALITY", str(ali_cfg.jpeg_quality))),
        jpeg_optimize=os.getenv("OMNI_JPEG_OPTIMIZE", str(ali_cfg.jpeg_optimize)).strip().lower()
        in {"1", "true", "yes", "on"},
    )


class OmniOnceCallback(OmniRealtimeCallback):
    def __init__(self) -> None:
        self.done = threading.Event()
        self.error: str | None = None
        self.text_chunks: list[str] = []
        self.final_text: str = ""

    def on_event(self, response: dict) -> None:
        event_type = response.get("type", "")
        if event_type == "response.text.delta":
            delta = response.get("delta", "")
            if delta:
                self.text_chunks.append(delta)
        elif event_type == "response.text.done":
            self.final_text = response.get("text", "") or "".join(self.text_chunks)
        elif event_type == "error":
            self.error = str(response)
            self.done.set()
        elif event_type == "response.done":
            if not self.final_text:
                self.final_text = "".join(self.text_chunks).strip()
            self.done.set()


class OmniChemistryClient:
    def __init__(self, config: OmniConfig | None = None) -> None:
        self.config = config or load_omni_config()
        dashscope.api_key = self.config.api_key

    def compare_images_with_metrics(
        self,
        correct_image_bytes: bytes,
        wrong_image_bytes: bytes,
    ) -> tuple[str, dict[str, Any]]:
        prompt = """两张图片的内容有什么不同"""
        return self._run_realtime_request(
            instructions=prompt,
            correct_image_bytes=correct_image_bytes,
            wrong_image_bytes=wrong_image_bytes,
        )

    def analyze_main_error_with_metrics(
        self,
        experiment_requirements: str,
        difference_summary: str,
    ) -> tuple[str, dict[str, Any]]:
        prompt = f"""现在你已经拿到了图片差异分析，请结合实验要求给出最终判断。

已知：
1. 图A是参考示范图。
2. 图B是待纠错图片。
3. 你要根据实验要求，判断图B相对图A最主要的问题。

实验要求：
{experiment_requirements.strip()}

图片差异分析：
{difference_summary.strip()}

请按下面格式输出：
主要错误：...

原因：...

纠正建议：...

要求：
1. 只输出中文。
2. 重点回答“最主要的错误是什么”，不要把很多次要问题混在一起。
3. 若实验要求与图像差异存在冲突，以实验要求为准重新判断。
4. 如果第一步只发现视角差异、没有发现可靠的真实差异，就明确说明“仅凭当前图片无法确认主要错误”。"""
        result, metrics = self._run_realtime_request(instructions=prompt)
        metrics["input_chars"] = len(experiment_requirements)
        metrics["difference_chars"] = len(difference_summary)
        return result, metrics

    def analyze_images_once_with_metrics(
        self,
        correct_image_bytes: bytes,
        wrong_image_bytes: bytes,
        experiment_requirements: str,
    ) -> tuple[str, dict[str, Any]]:
        return self._run_realtime_request(
            instructions=self._build_once_prompt(experiment_requirements),
            correct_image_bytes=correct_image_bytes,
            wrong_image_bytes=wrong_image_bytes,
        )

    def _run_realtime_request(
        self,
        instructions: str,
        correct_image_bytes: bytes | None = None,
        wrong_image_bytes: bytes | None = None,
    ) -> tuple[str, dict[str, Any]]:
        correct_metrics: dict[str, int | float] | None = None
        wrong_metrics: dict[str, int | float] | None = None
        correct_b64: str | None = None
        wrong_b64: str | None = None

        if correct_image_bytes is not None and wrong_image_bytes is not None:
            correct_b64, correct_metrics = self._prepare_image_b64(correct_image_bytes)
            wrong_b64, wrong_metrics = self._prepare_image_b64(wrong_image_bytes)

        callback = OmniOnceCallback()
        conv = OmniRealtimeConversation(
            model=self.config.model,
            callback=callback,
            url=self.config.url,
        )

        total_started_at = time.perf_counter()
        try:
            conv.connect()
            conv.update_session(
                output_modalities=[MultiModality.TEXT],
                voice=self.config.voice,
                enable_input_audio_transcription=False,
                enable_turn_detection=False,
                instructions="你是化学实验纠错助手，只输出中文。",
            )

            conv.append_audio(self._build_silence_audio_b64())
            if correct_b64 is not None and wrong_b64 is not None:
                conv.append_video(correct_b64)
                conv.append_video(wrong_b64)
            conv.commit()

            model_started_at = time.perf_counter()
            conv.create_response(
                instructions=instructions,
                output_modalities=[MultiModality.TEXT],
            )

            if not callback.done.wait(timeout=self.config.timeout_seconds):
                raise AliyunError(f"等待 Omni Realtime 响应超时，超过 {self.config.timeout_seconds} 秒。")
            if callback.error:
                raise AliyunError(callback.error)

            model_request_ms = (time.perf_counter() - model_started_at) * 1000
            total_ms = (time.perf_counter() - total_started_at) * 1000
            metrics: dict[str, Any] = {
                "model_request_ms": round(model_request_ms, 1),
                "total_ms": round(total_ms, 1),
            }
            if correct_metrics is not None and wrong_metrics is not None:
                metrics["image_a"] = correct_metrics
                metrics["image_b"] = wrong_metrics
                metrics["preprocess_total_ms"] = round(
                    correct_metrics["preprocess_ms"] + wrong_metrics["preprocess_ms"], 1
                )
            return callback.final_text.strip(), metrics
        finally:
            conv.close()

    def _prepare_image_b64(self, image_bytes: bytes) -> tuple[str, dict[str, int | float]]:
        started_at = time.perf_counter()
        optimized_bytes, ow, oh, nw, nh = optimize_image(
            image_bytes,
            self.config.max_image_side,
            self.config.jpeg_quality,
            self.config.jpeg_optimize,
        )
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        return base64.b64encode(optimized_bytes).decode("ascii"), {
            "original_width": ow,
            "original_height": oh,
            "optimized_width": nw,
            "optimized_height": nh,
            "original_bytes": len(image_bytes),
            "optimized_bytes": len(optimized_bytes),
            "preprocess_ms": round(elapsed_ms, 1),
            "compression_ratio": round(
                len(optimized_bytes) / len(image_bytes), 3
            ) if image_bytes else 0.0,
        }

    @staticmethod
    def _build_silence_audio_b64(duration_ms: int = 120, sample_rate: int = 16000) -> str:
        sample_count = int(sample_rate * duration_ms / 1000)
        silence_pcm16 = b"\x00\x00" * sample_count
        return base64.b64encode(silence_pcm16).decode("ascii")

    @staticmethod
    def _build_once_prompt(experiment_requirements: str) -> str:
        return f"""你是化学实验纠错助手。现在一次性给你两张图片和实验规范，请直接完成最终判断。

输入约定：
1. 第一张图片是参考图A。
2. 第二张图片是待纠错图B。
3. 图像可能存在拍摄角度、远近、裁剪、光照差异，你必须忽略这些伪差异。
4. 只关注真实装置差异、连接差异、部件位置差异、液面/电极/接口位置差异、阀门或开关状态差异、光路或样品位置差异。
5. 优先关注核心的、根本的差异，不要被次要差异干扰判断。

实验规范：
{experiment_requirements.strip()}

请直接输出以下内容：
真实关键差异：
- ...
- ...

主要错误：
...

原因：
...

纠正建议：
...

要求：
1. 只输出中文。
2. 不要先复述任务，不要解释拍摄视角差异。
3. 如果两张图没有足够可靠的真实差异，就明确写“仅凭当前图片无法确认主要错误”。"""
