from __future__ import annotations

import argparse
import base64
import threading
import time
from pathlib import Path

import dashscope
from dashscope.audio.qwen_omni import MultiModality, OmniRealtimeCallback, OmniRealtimeConversation

from ali_client import load_aliyun_config
from openrouter_client import optimize_image


DEFAULT_MODEL = "qwen3.5-omni-flash-realtime"
DEFAULT_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_VOICE = "Ethan"


class OneShotCallback(OmniRealtimeCallback):
    def __init__(self) -> None:
        self.done = threading.Event()
        self.error: str | None = None
        self.text_chunks: list[str] = []
        self.final_text: str = ""
        self.events: list[str] = []

    def on_event(self, response: dict) -> None:
        event_type = response.get("type", "")
        self.events.append(event_type)

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Qwen Omni Realtime 单次图片纠错：一次输入两张图和实验规范，直接返回结果。"
    )
    parser.add_argument("--correct", required=True, help="参考图片路径")
    parser.add_argument("--wrong", required=True, help="待纠错图片路径")
    parser.add_argument("--requirements", required=True, help="实验规范文本，或文本文件路径")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Omni Realtime 模型名")
    parser.add_argument("--url", default=DEFAULT_URL, help="Realtime WebSocket 地址")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="Omni Realtime 音色")
    parser.add_argument("--timeout", type=int, default=90, help="等待模型返回的超时秒数")
    return parser.parse_args()


def read_bytes(path_str: str) -> bytes:
    path = Path(path_str)
    if not path.is_file():
        raise FileNotFoundError(f"找不到文件: {path}")
    return path.read_bytes()


def read_requirements(raw: str) -> str:
    candidate = Path(raw)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8").strip()
    return raw.strip()


def prepare_image_b64(image_bytes: bytes) -> tuple[str, dict[str, int | float]]:
    cfg = load_aliyun_config(require_api_key=False)
    started_at = time.perf_counter()
    optimized_bytes, ow, oh, nw, nh = optimize_image(
        image_bytes,
        cfg.max_image_side,
        cfg.jpeg_quality,
        cfg.jpeg_optimize,
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
    }


def build_prompt(experiment_requirements: str) -> str:
    return f"""你是化学实验纠错助手。现在一次性给你两张图片和实验规范，请直接完成最终判断。

输入约定：
1. 第一张图片是参考图A。
2. 第二张图片是待纠错图B。
3. 图像可能存在拍摄角度、远近、裁剪、光照差异，你必须忽略这些伪差异。
4. 只关注真实装置差异、连接差异、部件位置差异、液面/电极/接口位置差异、阀门或开关状态差异、光路或样品位置差异。

实验规范：
{experiment_requirements}

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


def build_silence_audio_b64(duration_ms: int = 120, sample_rate: int = 16000) -> str:
    sample_count = int(sample_rate * duration_ms / 1000)
    silence_pcm16 = b"\x00\x00" * sample_count
    return base64.b64encode(silence_pcm16).decode("ascii")


def main() -> int:
    args = parse_args()
    dashscope.api_key = load_aliyun_config().api_key

    correct_bytes = read_bytes(args.correct)
    wrong_bytes = read_bytes(args.wrong)
    experiment_requirements = read_requirements(args.requirements)

    correct_b64, correct_metrics = prepare_image_b64(correct_bytes)
    wrong_b64, wrong_metrics = prepare_image_b64(wrong_bytes)

    callback = OneShotCallback()
    conv = OmniRealtimeConversation(model=args.model, callback=callback, url=args.url)

    total_started_at = time.perf_counter()
    try:
        conv.connect()
        conv.update_session(
            output_modalities=[MultiModality.TEXT],
            voice=args.voice,
            enable_input_audio_transcription=False,
            enable_turn_detection=False,
            instructions="你是化学实验纠错助手，只输出中文。",
        )

        # 官方文档要求：在发送图片前，至少先 append 一次音频。
        conv.append_audio(build_silence_audio_b64())
        conv.append_video(correct_b64)
        conv.append_video(wrong_b64)
        conv.commit()
        model_started_at = time.perf_counter()
        conv.create_response(
            instructions=build_prompt(experiment_requirements),
            output_modalities=[MultiModality.TEXT],
        )

        if not callback.done.wait(timeout=args.timeout):
            raise TimeoutError(f"等待模型响应超时，超过 {args.timeout} 秒。")

        if callback.error:
            raise RuntimeError(callback.error)

        model_ms = (time.perf_counter() - model_started_at) * 1000
        total_ms = (time.perf_counter() - total_started_at) * 1000

        print(f"当前模型: {args.model}")
        print("=" * 80)
        print(callback.final_text.strip())
        print("\n" + "=" * 80)
        print("性能信息")
        print(
            f"图A: {correct_metrics['original_width']}x{correct_metrics['original_height']} "
            f"-> {correct_metrics['optimized_width']}x{correct_metrics['optimized_height']}"
        )
        print(
            f"图A大小: {correct_metrics['original_bytes']} -> {correct_metrics['optimized_bytes']} bytes, "
            f"预处理 {correct_metrics['preprocess_ms']} ms"
        )
        print(
            f"图B: {wrong_metrics['original_width']}x{wrong_metrics['original_height']} "
            f"-> {wrong_metrics['optimized_width']}x{wrong_metrics['optimized_height']}"
        )
        print(
            f"图B大小: {wrong_metrics['original_bytes']} -> {wrong_metrics['optimized_bytes']} bytes, "
            f"预处理 {wrong_metrics['preprocess_ms']} ms"
        )
        print(f"模型请求耗时: {round(model_ms, 1)} ms")
        print(f"总耗时: {round(total_ms, 1)} ms")
        return 0
    finally:
        conv.close()


if __name__ == "__main__":
    raise SystemExit(main())
