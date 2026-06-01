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

    "overlay_test",
    "experiment_check",
    "safety_check",
    "select_experiment",
    "demo_guide",
    "select_demo_video",
    "stop_demo_video",
    "qa",

    "unclear",
    "unknown",
}
SUPPORTED_EXPERIMENTS = {"distillation", "photometer", "electrolysis"}

# 沉睡模式：精简 prompt，只检测唤醒词
SLEEP_MODE_PROMPT = """你是唤醒词检测器。判断用户是否说了“小北助手”。
只输出 JSON: {"wake": true, "transcript": "小北助手"} 或 {"wake": false, "transcript": "其他内容"}
规则: 只要明确听到“小北助手”就返回 true；其它内容、环境噪声、听不清都返回 false。
"""

# 苏醒模式：完整指令识别（不含 wake）
AWAKE_MODE_PROMPT = """你是智能眼镜的语音指令解析器。
你的任务是把用户语音转写并映射到固定 intent。
只能输出 JSON，不要输出 Markdown，不要解释。

支持的 intent:
- start_recording: 用户要求开始记录、开始录像、开始录制实验过程
- stop_recording: 用户要求结束记录、结束录像、停止录制并保存
- overlay_test: 用户要求投影测试、框测试、透明框叠加测试、检测框投到实物上
- experiment_check: 用户要求实验纠错、检查实验装置、装置检测，但还没有明确实验类型
- safety_check: 用户要求安全检查、检查安全问题、检查安全隐患
- select_experiment: 用户选择实验类型
- demo_guide: 用户要求演示指引、播放演示、看操作视频，但还没有明确选择哪个视频
- select_demo_video: 用户选择演示视频，例如第一个、第二个、操作流程、原理小结
- stop_demo_video: 用户要求停止播放、退出播放、暂停演示
- qa: 用户要提问、问答，或直接问了一个化学实验问题
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
8. 如果当前状态是 recording_stop_only，只识别停止记录；其它内容返回 unknown。
9. 不要猜测低置信度命令；不确定就 unclear。
"""


class OmniIntentCallback(OmniRealtimeCallback):
    def __init__(self, *, complete_on_transcript: bool = False) -> None:
        self.complete_on_transcript = complete_on_transcript
        self.done = False
        self.error: str | None = None
        self.transcript: str = ""
        self.transcript_completed_at: float | None = None
        self.answer_text: str = ""
        self._answer_chunks: list[str] = []

    def on_event(self, response: dict[str, Any]) -> None:
        event_type = str(response.get("type", ""))
        if event_type == "conversation.item.input_audio_transcription.delta":
            # Realtime ASR may stream partial content in "stash" before finalizing.
            partial = str(response.get("stash") or response.get("text") or "").strip()
            if partial:
                self.transcript = partial
        elif event_type == "conversation.item.input_audio_transcription.completed":
            self.transcript = str(response.get("transcript", "")).strip()
            self.transcript_completed_at = time.time()
            if self.complete_on_transcript:
                self.done = True
        elif event_type == "response.text.delta":
            delta = str(response.get("delta", ""))
            if delta:
                self._answer_chunks.append(delta)
        elif event_type == "response.text.done":
            self.answer_text = str(response.get("text", "")).strip() or "".join(self._answer_chunks).strip()
            if self.answer_text:
                self.done = True
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
    ctx = (context or "").strip()
    is_sleep_mode = ctx == "sleep"
    mode_name = "SLEEP" if is_sleep_mode else "AWAKE"

    print(f"\n[voice_intent] === 开始处理 === mode={mode_name}, audio_size={len(audio_bytes)} bytes")

    if not audio_bytes:
        print(f"[voice_intent] 音频为空，返回默认结果")
        if is_sleep_mode:
            return {"wake": False, "transcript": "", "timings": {}}
        return _safe_intent("", "unclear", "我没有听清，请再说一遍。", 0.0)

    config = load_omni_config()
    if model:
        config = replace(config, model=model)
    if timeout_seconds is not None:
        config = replace(config, timeout_seconds=timeout_seconds)
    elif is_sleep_mode:
        config = replace(config, timeout_seconds=8)
    else:
        config = replace(config, timeout_seconds=8)

    # 根据模式选择 prompt
    system_prompt = SLEEP_MODE_PROMPT if is_sleep_mode else AWAKE_MODE_PROMPT
    print(f"[voice_intent] 使用 prompt: {mode_name}_MODE_PROMPT")

    dashscope.api_key = config.api_key
    callback = OmniIntentCallback(complete_on_transcript=is_sleep_mode)
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
            instructions=system_prompt,
        )

        for chunk_start in range(0, len(audio_bytes), 3200):
            chunk = audio_bytes[chunk_start:chunk_start + 3200]
            conv.append_audio(base64.b64encode(chunk).decode("ascii"))
        conv.commit()

        model_started_at = time.perf_counter()
        conv.create_response(
            instructions=_build_response_instruction(ctx, is_sleep_mode=is_sleep_mode),
            output_modalities=[MultiModality.TEXT],
        )

        deadline = time.time() + config.timeout_seconds
        fallback_after_transcript_seconds = 2.0
        while not callback.done and time.time() < deadline:
            if (
                not is_sleep_mode
                and callback.transcript_completed_at is not None
                and time.time() - callback.transcript_completed_at >= fallback_after_transcript_seconds
            ):
                print(f"[voice_intent] 模型 JSON 未及时返回，使用转写兜底: {callback.transcript!r}")
                break
            time.sleep(0.05)

        if not callback.done:
            if callback.transcript:
                print(f"[voice_intent] 未收到模型回复，使用转写兜底: {callback.transcript!r}")
            else:
                print(f"[voice_intent] ERROR: 超时")
                raise TimeoutError(f"等待 Qwen Omni 指令解析超时，超过 {config.timeout_seconds} 秒。")
        if callback.error:
            print(f"[voice_intent] ERROR: {callback.error}")
            raise AliyunError(callback.error)

        timings = {
            "omni": round((time.perf_counter() - model_started_at) * 1000, 1),
            "total": round((time.perf_counter() - started_at) * 1000, 1),
        }

        # Debug 输出
        print(f"[voice_intent] Omni 转写: {callback.transcript!r}")
        print(f"[voice_intent] Omni 回复: {callback.answer_text!r}")

        if is_sleep_mode:
            result = _parse_wake_json(callback.answer_text, callback.transcript)
            print(f"[voice_intent] 解析结果: wake={result.get('wake')}, transcript={result.get('transcript')!r}")
        else:
            result = _parse_intent_json(callback.answer_text, callback.transcript)
            if ctx == "recording_stop_only" and result.get("intent") != "stop_recording":
                result = _safe_intent(
                    str(result.get("transcript") or callback.transcript or ""),
                    "unknown",
                    "",
                    0.5,
                )
            print(f"[voice_intent] 解析结果: intent={result.get('intent')}, transcript={result.get('transcript')!r}")
        
        result["timings"] = timings
        print(f"[voice_intent] === 完成 === 耗时 {timings['total']}ms\n")
        return result
    except Exception as e:
        print(f"[voice_intent] === 异常 === {type(e).__name__}: {e}\n")
        raise
    finally:
        conv.close()


def _build_response_instruction(context: str | None, *, is_sleep_mode: bool = False) -> str:
    if is_sleep_mode:
        return "只判断是否出现“小北助手”，输出严格 JSON。"
    context_text = (context or "VOICE_IDLE").strip() or "VOICE_IDLE"
    return (
        f"当前眼镜状态: {context_text}\n"
        "请根据用户语音输出严格 JSON。confidence 小于 0.65 时使用 unclear。"
    )


def _parse_wake_json(text: str, fallback_transcript: str) -> dict[str, Any]:
    """解析沉睡模式返回：只提取 wake 和 transcript。"""
    payload = _extract_json(text)
    transcript = fallback_transcript or ""
    if payload is not None:
        transcript = str(payload.get("transcript") or fallback_transcript or "").strip()
        wake = payload.get("wake")
        if isinstance(wake, bool):
            return {"wake": wake, "transcript": transcript}
    # JSON 解析失败或格式不对，用关键词兜底
    normalized = transcript.strip()
    wake = "小北助手" in normalized
    return {"wake": wake, "transcript": transcript}


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
    """苏醒模式的关键词兜底匹配。"""
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
    if any(word in normalized for word in ("投影测试", "框测试", "叠加测试", "检测框测试", "实物叠加", "透明框")):
        return _safe_intent(normalized, "overlay_test", "开始投影测试，请保持目标在视野中央。", 0.7)
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
    if any(
        word in normalized
        for word in ("提问", "我要问", "问一个", "请问", "问答", "解释一下", "为什么", "怎么")
    ) or "?" in lower:
        return _safe_intent(normalized, "qa", "请说你的问题。", 0.7)
    if any(word in normalized for word in ("取消", "退出")):
        return _safe_intent(normalized, "cancel", "已取消。", 0.7)
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
    replies = {
        "start_recording": "开始记录。",
        "stop_recording": "结束记录。",
        "capture_photo": "开始拍照。",
        "overlay_test": "开始投影测试，请保持目标在视野中央。",
        "experiment_check": "开始实验纠错，请说实验名称。",
        "safety_check": "开始安全检查，请保持现场在视野中央。",
        "demo_guide": "进入演示指引。",
        "select_demo_video": "开始播放演示视频。",
        "stop_demo_video": "已停止播放。",
        "qa": "请说你的问题。",
        "cancel": "已取消。",
        "repeat": "请重复上一条提示。",
        "unknown": "这个指令暂不支持。你可以说：演示指引、开始记录、安全检查、实验纠错或我要提问。",
    }
    if intent == "select_experiment":
        names = {"distillation": "减压蒸馏", "photometer": "光度计", "electrolysis": "电解"}
        name = names.get(slots.get("experiment_type"), "该实验")
        return f"已选择{name}，请保持装置在视野中央。"
    return replies.get(intent, "我没有听清，请再说一遍。")
