from __future__ import annotations

import base64
import json
import os
from dataclasses import replace
import io
import time
from typing import Any
import wave

import dashscope
from dashscope.audio.qwen_omni import (
    MultiModality,
    OmniRealtimeCallback,
    OmniRealtimeConversation,
)

from ali_client import AliyunError
from omni_client import load_omni_config


SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2
DEFAULT_GAIN = 3.0
DEFAULT_TTS_MODEL = "qwen3.5-omni-plus-realtime"


class OmniTTSCallback(OmniRealtimeCallback):
    def __init__(self) -> None:
        self.done = False
        self.error: str | None = None
        self.audio_chunks: list[bytes] = []
        self.transcript: str = ""

    def on_event(self, response: dict[str, Any]) -> None:
        event_type = str(response.get("type", ""))
        if event_type == "response.audio.delta":
            delta = response.get("delta", "")
            if delta:
                self.audio_chunks.append(base64.b64decode(delta))
        elif event_type == "response.audio_transcript.done":
            self.transcript = str(response.get("transcript", "")).strip()
        elif event_type == "error":
            self.error = str(response)
            self.done = True
        elif event_type == "response.done":
            self.done = True


def _tts_gain() -> float:
    raw = os.getenv("OMNI_TTS_GAIN", str(DEFAULT_GAIN)).strip()
    try:
        value = float(raw)
    except ValueError:
        value = DEFAULT_GAIN
    return max(0.2, min(value, 8.0))


def amplify_pcm16(pcm_bytes: bytes, gain: float | None = None) -> bytes:
    if not pcm_bytes:
        return pcm_bytes
    factor = _tts_gain() if gain is None else gain
    if abs(factor - 1.0) < 0.01:
        return pcm_bytes

    out = bytearray(len(pcm_bytes))
    limit = len(pcm_bytes) - (len(pcm_bytes) % 2)
    for index in range(0, limit, 2):
        sample = int.from_bytes(pcm_bytes[index:index + 2], "little", signed=True)
        amplified = int(sample * factor)
        if amplified > 32767:
            amplified = 32767
        elif amplified < -32768:
            amplified = -32768
        out[index:index + 2] = amplified.to_bytes(2, "little", signed=True)
    if limit < len(pcm_bytes):
        out[limit:] = pcm_bytes[limit:]
    return bytes(out)


def pcm16_to_wav_bytes(pcm_bytes: bytes, *, gain: float | None = None) -> bytes:
    amplified_pcm = amplify_pcm16(pcm_bytes, gain=gain)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(amplified_pcm)
    return buffer.getvalue()


def wav_data_url(wav_bytes: bytes) -> str:
    encoded = base64.b64encode(wav_bytes).decode("ascii")
    return f"data:audio/wav;base64,{encoded}"


def synthesize_text_to_audio(
    text: str,
    *,
    model: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("text is empty")

    config = load_omni_config()
    tts_model = model or os.getenv("OMNI_TTS_MODEL", DEFAULT_TTS_MODEL).strip() or DEFAULT_TTS_MODEL
    config = replace(config, model=tts_model)
    if timeout_seconds is not None:
        config = replace(config, timeout_seconds=timeout_seconds)

    dashscope.api_key = config.api_key
    callback = OmniTTSCallback()
    conv = OmniRealtimeConversation(
        model=config.model,
        callback=callback,
        url=config.url,
    )

    started_at = time.perf_counter()
    try:
        conv.connect()
        conv.update_session(
            output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
            voice=config.voice,
            enable_input_audio_transcription=False,
            enable_turn_detection=False,
            instructions=(
                "你是化学实验眼镜助手的中文语音播报器。"
                "请只自然朗读用户给出的内容，不要添加解释、前后缀或口头禅。"
            ),
        )
        conv.send_raw(json.dumps({
            "event_id": f"event_tts_text_{int(time.time() * 1000)}",
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": f"请只朗读下面这段中文，不要回答，不要翻译，不要添加任何内容：\n{cleaned}",
                }],
            },
        }, ensure_ascii=False))
        conv.create_response(
            instructions="只朗读用户消息中给出的中文内容，不要解释，不要添加其它文字。",
            output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
        )

        deadline = time.time() + config.timeout_seconds
        while not callback.done and time.time() < deadline:
            time.sleep(0.05)

        if not callback.done:
            raise TimeoutError(f"等待 Qwen Omni 语音合成超时，超过 {config.timeout_seconds} 秒。")
        if callback.error:
            raise AliyunError(callback.error)

        pcm = b"".join(callback.audio_chunks)
        if not pcm:
            raise AliyunError("Qwen Omni 未返回语音音频。")
        wav_bytes = pcm16_to_wav_bytes(pcm)
        return {
            "audio_data_url": wav_data_url(wav_bytes),
            "audio_bytes": wav_bytes,
            "transcript": callback.transcript,
            "timings": {
                "tts": round((time.perf_counter() - started_at) * 1000, 1),
            },
        }
    finally:
        conv.close()
