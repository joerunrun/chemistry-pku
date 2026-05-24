from __future__ import annotations

import base64
from dataclasses import replace
import json
import re
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


SUPPORTED_INTENTS = {
    "start_recording",
    "stop_recording",
    "capture_photo",
    "experiment_check",
    "safety_check",
    "select_experiment",
    "demo_guide",
    "select_demo_video",
    "stop_demo_video",
    "qa",
    "cancel",
    "repeat",
    "unclear",
    "unknown",
}
SUPPORTED_EXPERIMENTS = {"distillation", "photometer", "electrolysis"}

SYSTEM_INSTRUCTIONS = """你是智能眼镜的语音指令解析器。
你的任务是把用户语音转写并映射到固定 intent。
只能输出 JSON，不要输出 Markdown，不要解释。

支持的 intent:
- start_recording: 用户要求开始记录、开始录像、开始录制实验过程
- stop_recording: 用户要求结束记录、结束录像、停止录制并保存
- capture_photo: 用户要求拍照、拍一张照片、保存照片
- experiment_check: 用户要求实验纠错、检查实验装置、装置检测，但还没有明确实验类型
- safety_check: 用户要求安全检查、检查安全问题、检查安全隐患
- select_experiment: 用户选择实验类型
- demo_guide: 用户要求演示指引、播放演示、看操作视频，但还没有明确选择哪个视频
- select_demo_video: 用户选择演示视频，例如第一个、第二个、操作流程、原理小结
- stop_demo_video: 用户要求停止播放、退出播放、暂停演示
- qa: 用户要提问、问答，或直接问了一个化学实验问题
- cancel: 用户要求取消、退出当前流程
- repeat: 用户要求重复上一条提示
- unclear: 语音里有人说话，但转写不完整或听不清，不能可靠判断命令
- unknown: 听清了，但不是支持命令

支持的实验类型 slots.experiment_type:
- distillation: 减压蒸馏、蒸馏
- photometer: 光度计、光度实验
- electrolysis: 电解、电解实验

输出 JSON 格式:
{
  "transcript": "用户语音转写",
  "intent": "固定 intent",
  "slots": {"experiment_type": null, "video_index": null, "video_query": null},
  "reply_text": "给眼镜用户看的简短中文确认或追问",
  "confidence": 0.0
}

规则:
1. intent 必须来自支持列表。
2. 如果没听清，intent=unclear，reply_text=我没有听清，请再说一遍。
3. 如果听清但不是支持命令，intent=unknown。
4. 如果用户说“实验纠错”但没说实验名称，intent=experiment_check，experiment_type=null。
5. 如果用户说“实验纠错，减压蒸馏”，intent=select_experiment，experiment_type=distillation。
6. 如果用户只说“减压蒸馏/光度计/电解”，也返回 select_experiment。
7. 如果当前状态是 demo_select 或 demo_playing，优先判断演示视频选择或停止播放。
8. 不要猜测低置信度命令；不确定就 unclear。
"""


class OmniIntentCallback(OmniRealtimeCallback):
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


def classify_audio_intent(
    audio_bytes: bytes,
    *,
    context: str | None = None,
    model: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    if not audio_bytes:
        return _safe_intent("", "unclear", "我没有听清，请再说一遍。", 0.0)

    config = load_omni_config()
    if model:
        config = replace(config, model=model)
    if timeout_seconds is not None:
        config = replace(config, timeout_seconds=timeout_seconds)

    dashscope.api_key = config.api_key
    callback = OmniIntentCallback()
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
            instructions=_build_response_instruction(context),
            output_modalities=[MultiModality.TEXT],
        )

        deadline = time.time() + config.timeout_seconds
        while not callback.done and time.time() < deadline:
            time.sleep(0.05)

        if not callback.done:
            raise TimeoutError(f"等待 Qwen Omni 指令解析超时，超过 {config.timeout_seconds} 秒。")
        if callback.error:
            raise AliyunError(callback.error)

        result = _parse_intent_json(callback.answer_text, callback.transcript)
        result["timings"] = {
            "omni": round((time.perf_counter() - model_started_at) * 1000, 1),
            "total": round((time.perf_counter() - started_at) * 1000, 1),
        }
        return result
    finally:
        conv.close()


def _build_response_instruction(context: str | None) -> str:
    context_text = (context or "VOICE_IDLE").strip() or "VOICE_IDLE"
    return (
        f"当前眼镜状态: {context_text}\n"
        "请根据用户语音输出严格 JSON。confidence 小于 0.65 时使用 unclear。"
    )


def _parse_intent_json(text: str, fallback_transcript: str) -> dict[str, Any]:
    payload = _extract_json(text)
    if payload is None:
        return _fallback_from_transcript(fallback_transcript)

    transcript = str(payload.get("transcript") or fallback_transcript or "").strip()
    intent = str(payload.get("intent") or "unclear").strip()
    if intent not in SUPPORTED_INTENTS:
        intent = "unknown"

    raw_slots = payload.get("slots")
    slots = raw_slots if isinstance(raw_slots, dict) else {}
    experiment_type = slots.get("experiment_type")
    if experiment_type is not None:
        experiment_type = str(experiment_type).strip() or None
    if experiment_type not in SUPPORTED_EXPERIMENTS:
        experiment_type = None
    video_index = _coerce_video_index(slots.get("video_index"))
    video_query = str(slots.get("video_query") or "").strip() or None
    slots = {
        "experiment_type": experiment_type,
        "video_index": video_index,
        "video_query": video_query,
    }

    confidence = _coerce_confidence(payload.get("confidence"))
    if confidence < 0.65 and intent not in {"unknown", "unclear"}:
        intent = "unclear"
        slots = {"experiment_type": None, "video_index": None, "video_query": None}

    reply_text = str(payload.get("reply_text") or "").strip()
    if not reply_text:
        reply_text = _default_reply(intent, slots)

    return {
        "transcript": transcript,
        "intent": intent,
        "slots": slots,
        "reply_text": reply_text,
        "confidence": confidence,
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _fallback_from_transcript(transcript: str) -> dict[str, Any]:
    normalized = transcript.strip()
    if not normalized:
        return _safe_intent("", "unclear", "我没有听清，请再说一遍。", 0.0)

    lower = normalized.lower()
    if any(word in normalized for word in ("停止播放", "退出播放", "暂停演示", "停止演示")):
        return _safe_intent(normalized, "stop_demo_video", "已停止播放。", 0.7)
    video_index = _match_demo_video_index(normalized)
    if video_index is not None or any(word in normalized for word in ("操作流程", "操作视频", "原理", "小结")):
        return _safe_intent(
            normalized,
            "select_demo_video",
            "开始播放演示视频。",
            0.7,
            video_index=video_index,
            video_query=normalized,
        )
    if any(word in normalized for word in ("演示指引", "播放演示", "看演示", "看视频", "操作视频")):
        return _safe_intent(normalized, "demo_guide", "进入演示指引。", 0.7)
    if any(word in normalized for word in ("开始记录", "开始录像", "开始录制")):
        return _safe_intent(normalized, "start_recording", "开始记录。", 0.7)
    if any(word in normalized for word in ("结束记录", "停止记录", "结束录像", "停止录像", "保存录像")):
        return _safe_intent(normalized, "stop_recording", "结束记录。", 0.7)
    if any(word in normalized for word in ("开始拍照", "拍一张", "拍照", "保存照片")):
        return _safe_intent(normalized, "capture_photo", "开始拍照。", 0.7)
    if "安全" in normalized:
        return _safe_intent(normalized, "safety_check", "开始安全检查，请保持现场在视野中央。", 0.7)
    if any(word in normalized for word in ("实验纠错", "装置检测", "检查实验装置")):
        return _safe_intent(normalized, "experiment_check", "开始实验纠错，请说实验名称。", 0.7)
    experiment_type = _match_experiment_type(normalized)
    if experiment_type:
        return _safe_intent(
            normalized,
            "select_experiment",
            _default_reply("select_experiment", {"experiment_type": experiment_type}),
            0.7,
            experiment_type=experiment_type,
        )
    if any(word in normalized for word in ("我要提问", "问答", "解释一下", "为什么", "怎么")) or "?" in lower:
        return _safe_intent(normalized, "qa", "请说你的问题。", 0.7)
    return _safe_intent(normalized, "unknown", "这个指令暂不支持。你可以说：演示指引、开始记录、安全检查、实验纠错或我要提问。", 0.5)


def _match_experiment_type(text: str) -> str | None:
    if "减压" in text or "蒸馏" in text:
        return "distillation"
    if "光度" in text:
        return "photometer"
    if "电解" in text:
        return "electrolysis"
    return None


def _match_demo_video_index(text: str) -> int | None:
    compact = re.sub(r"\s+", "", text)
    if any(word in text for word in ("第一个", "第一段", "一号", "1号")) or compact in {"一", "1"}:
        return 0
    if any(word in text for word in ("第二个", "第二段", "二号", "2号")) or compact in {"二", "2"}:
        return 1
    if any(word in text for word in ("第三个", "第三段", "三号", "3号")) or compact in {"三", "3"}:
        return 2
    return None


def _safe_intent(
    transcript: str,
    intent: str,
    reply_text: str,
    confidence: float,
    *,
    experiment_type: str | None = None,
    video_index: int | None = None,
    video_query: str | None = None,
) -> dict[str, Any]:
    return {
        "transcript": transcript,
        "intent": intent if intent in SUPPORTED_INTENTS else "unknown",
        "slots": {
            "experiment_type": experiment_type if experiment_type in SUPPORTED_EXPERIMENTS else None,
            "video_index": video_index,
            "video_query": video_query,
        },
        "reply_text": reply_text,
        "confidence": confidence,
    }


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _coerce_video_index(value: Any) -> int | None:
    if value is None:
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    if index <= 0:
        return index if index >= 0 else None
    return index - 1


def _default_reply(intent: str, slots: dict[str, Any]) -> str:
    if intent == "start_recording":
        return "开始记录。"
    if intent == "stop_recording":
        return "结束记录。"
    if intent == "capture_photo":
        return "开始拍照。"
    if intent == "experiment_check":
        return "开始实验纠错，请说实验名称。"
    if intent == "safety_check":
        return "开始安全检查，请保持现场在视野中央。"
    if intent == "select_experiment":
        names = {
            "distillation": "减压蒸馏",
            "photometer": "光度计",
            "electrolysis": "电解",
        }
        name = names.get(slots.get("experiment_type"), "该实验")
        return f"已选择{name}，请保持装置在视野中央。"
    if intent == "demo_guide":
        return "进入演示指引。"
    if intent == "select_demo_video":
        return "开始播放演示视频。"
    if intent == "stop_demo_video":
        return "已停止播放。"
    if intent == "qa":
        return "请说你的问题。"
    if intent == "cancel":
        return "已取消。"
    if intent == "repeat":
        return "请重复上一条提示。"
    if intent == "unknown":
        return "这个指令暂不支持。你可以说：演示指引、开始记录、安全检查、实验纠错或我要提问。"
    return "我没有听清，请再说一遍。"
