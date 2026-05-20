from __future__ import annotations

import base64
from dataclasses import replace
import time
from typing import Any

import dashscope
from dashscope.audio.qwen_omni import (
    MultiModality,
    OmniRealtimeCallback,
    OmniRealtimeConversation,
)

from ali_client import AliyunError
from omni_client import load_omni_config


SYSTEM_INSTRUCTIONS = (
    "你是化学实验现场问答助手。请根据用户语音问题直接用中文回答。"
    "回答要简洁、准确，适合学生在实验现场快速理解。"
    "如果问题不清楚，先说明你听到的内容可能不完整，再给出最可能有用的建议。"
)


class OmniAudioQACallback(OmniRealtimeCallback):
    def __init__(self) -> None:
        self.done = False
        self.error: str | None = None
        self.transcript: str = ""
        self.answer_text: str = ""
        self._answer_chunks: list[str] = []

    def on_event(self, response: dict[str, Any]) -> None:
        event_type = str(response.get("type", ""))
        if event_type == "conversation.item.input_audio_transcription.completed":
            self.transcript = str(response.get("transcript", "")).strip()
        elif event_type == "response.text.delta":
            delta = str(response.get("delta", ""))
            if delta:
                self._answer_chunks.append(delta)
        elif event_type == "response.text.done":
            self.answer_text = str(response.get("text", "")).strip() or "".join(self._answer_chunks).strip()
        elif event_type == "response.audio_transcript.done":
            if not self.answer_text:
                self.answer_text = str(response.get("transcript", "")).strip()
        elif event_type == "error":
            self.error = str(response)
            self.done = True
        elif event_type == "response.done":
            if not self.answer_text:
                self.answer_text = "".join(self._answer_chunks).strip()
            self.done = True


def answer_audio_question(
    audio_bytes: bytes,
    *,
    model: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    if not audio_bytes:
        raise ValueError("audio is empty")

    config = load_omni_config()
    if model:
        config = replace(config, model=model)
    if timeout_seconds is not None:
        config = replace(config, timeout_seconds=timeout_seconds)

    dashscope.api_key = config.api_key
    callback = OmniAudioQACallback()
    conv = OmniRealtimeConversation(
        model=config.model,
        callback=callback,
        url=config.url,
    )

    started_at = time.perf_counter()
    try:
        conv.connect()
        conv.update_session(
            output_modalities=[MultiModality.TEXT],
            voice=config.voice,
            enable_input_audio_transcription=True,
            enable_turn_detection=False,
            instructions=SYSTEM_INSTRUCTIONS,
        )

        for chunk_start in range(0, len(audio_bytes), 3200):
            chunk = audio_bytes[chunk_start:chunk_start + 3200]
            conv.append_audio(base64.b64encode(chunk).decode("ascii"))
        conv.commit()

        model_started_at = time.perf_counter()
        conv.create_response(
            instructions=(
                "请先理解用户语音问题，再给出简短中文回答。"
                "回答控制在三句话以内；如涉及实验安全，优先给安全建议。"
            ),
            output_modalities=[MultiModality.TEXT],
        )

        deadline = time.time() + config.timeout_seconds
        while not callback.done and time.time() < deadline:
            time.sleep(0.05)

        if not callback.done:
            raise TimeoutError(f"等待 Qwen Omni 响应超时，超过 {config.timeout_seconds} 秒。")
        if callback.error:
            raise AliyunError(callback.error)

        answer_text = callback.answer_text.strip()
        if not answer_text:
            answer_text = "我没有生成有效回答，请重新提问。"

        return {
            "transcript": callback.transcript,
            "answer_text": answer_text,
            "answer_audio_data_url": None,
            "timings": {
                "omni": round((time.perf_counter() - model_started_at) * 1000, 1),
                "total": round((time.perf_counter() - started_at) * 1000, 1),
            },
        }
    finally:
        conv.close()
