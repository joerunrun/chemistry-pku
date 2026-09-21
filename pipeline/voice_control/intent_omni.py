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
    "start_audio_recording",
    "start_video_recording",
    "stop_recording",
    "capture_photo",
    "experiment_check",
    "safety_check",
    "select_experiment",
    "demo_guide",
    "select_demo_video",
    "stop_demo_video",
    "qa",
    "unclear",
}
MAIN_INTENTS = {
    "start_recording",
    "start_audio_recording",
    "start_video_recording",
    "capture_photo",
    "experiment_check",
    "safety_check",
    "demo_guide",
    "qa",
    "unclear",
}
# 教师版：唤醒后只支持拍照和录像两类需求，不含纠错/安全检查/演示/问答。
TEACHER_INTENTS = {
    "capture_photo",
    "start_recording",
    "unclear",
}
SUPPORTED_EXPERIMENTS = {
    "distillation",
    "atmospheric_distillation",
    "photometer",
    "electrolysis",
    "tlc",
    "thin_layer_chromatography",
    "column_chromatography",
    "recrystallization_reflux",
    "recrystallization_filtration",
}
# select_experiment 只用于启动阶段的 experiment_setup 上下文；苏醒模式不切换实验。

# 沉睡模式：检测用户是否正在呼叫或启动化小北助手。
SLEEP_MODE_PROMPT = """你是“化小北”眼镜助手的唤醒检测器。请判断用户是否有意呼叫或启动小北助手。
以下情况返回 wake=true：
1. 用户说“小北助手”，包括“小北助手，开始工作”等带后续要求的说法；
2. 音频或转写出现明显近音，例如“小贝助手”“晓北助手”“小北助守”；
3. 用户用“小北”“化小北”或“助手”称呼系统，并表达“开始工作”“启动”“醒醒”“在吗”“帮个忙”等唤醒意图。
不要要求逐字完全一致，应结合发音相似度和呼叫意图判断。
旁人闲聊、偶然提到小北、只说“开始工作”但没有呼叫助手、环境噪声或无法判断时，返回 wake=false。
只输出严格 JSON：{"wake": true/false, "transcript": "忠实转录的文字"}。
"""

TEACHER_SLEEP_MODE_PROMPT = """你是“化小北”教师版眼镜助手的唤醒和快捷动作检测器。
判断教师是否有意呼叫“小北助手/化小北”或明显近音。不要要求逐字完全一致。
如果同一句话还明确要求拍照或开始录像，同时返回对应 intent；仅唤醒时 intent=null。
旁人闲聊、偶然提到小北、没有呼叫助手的普通谈话、噪声或无法判断时返回 wake=false。
只输出严格 JSON：
{"wake": true/false, "intent": "capture_photo|start_recording|null", "transcript": "忠实转录的文字"}。
"""

_WAKE_NEAR_SOUND_PATTERN = re.compile(r"(?:小|晓)[北贝倍备背][助住柱][手守首]")
_WAKE_ADDRESSES = ("小北", "小贝", "晓北", "化小北", "小北助理", "助手")
_WAKE_ACTIONS = (
    "开始工作",
    "开始服务",
    "开始干活",
    "启动",
    "醒醒",
    "在吗",
    "听得到",
    "帮个忙",
    "帮帮我",
    "来帮我",
)

# 苏醒模式：自然语言理解 → 分类到固定 action（不含 wake）
AWAKE_MODE_PROMPT = """你是一个自然语言理解分类器。
眼镜用户正在做化学实验，随时会用**自然语言**（口语、随意、不固定句式）表达当下的需求。
你的任务是**理解用户语义意图**，把它归类到下面某个 action。用户不会背诵固定口令，请根据含义判断，而不是字面匹配。

可分类的 action（按语义场景）:
- start_audio_recording: 用户想只记录声音、口述实验现象或留存语音，比如“开始记录”“开始录音”“把我说的录下来”“我想留个语音档”。没有明确提到录像/视频时，默认选择这个动作。
- start_video_recording: 用户明确想打开摄像头录像，比如“开始录像”“开始视频录制”“把画面录下来”。只有明确提到录像或视频时才选择这个动作。
- capture_photo: 用户只想拍摄并保存一张照片，不要求分析，也不要求开始录像，比如“拍张照片”“帮我拍一下”“把现在这个画面拍下来”。
- experiment_check: 用户对当前实验装置/操作有疑问，想检查装置对不对、纠错、纠正操作，比如“这样搭对吗”“装置好像不太对”“帮我看看有没有装错”“这里是不是不对”。
- safety_check: 用户关心安全、担心隐患、想排查危险，比如“这样安全吗”“有没有什么问题”“会不会有问题”“检查一下安全”。
- demo_guide: 用户想看演示/操作流程/原理视频指引，但还没明确选哪一个，比如“给我看看演示”“有操作视频吗”“放一下流程”。
- qa: 用户在提问、求知、想知道原理/原因/做法，或者直接抛出一个化学问题，比如“为什么会这样”“这个原理是什么”“我想问一下”“帮我解释一下”。
- unclear: 无法可靠映射到上述动作，包括听不清、内容不完整或内容与支持动作无关。

判断原则:
1. 先把语音转写到 transcript。
2. 理解用户**当下想做的一件事**，归类到最贴近的 action。口语化、省略主语、近义词、比喻都应该正确归类，不要苛求字面。
3. 无法映射到支持动作时统一输出 unclear，不要输出 unknown。
4. 苏醒模式不负责停止记录、选择视频、停止视频，也不负责投影测试。
5. “记录”或“录音”默认是纯录音；“录像”“视频”才是录像。不要把泛化的“记录”误判为录像。
6. 如果用户只说实验名称而没有表达其它需求，intent=unclear。

输出严格 JSON，不要 Markdown，不要解释:
{
  "transcript": "用户语音转写",
  "intent": "上面的某个 action 名",
  "slots": {"experiment_type": null, "video_index": null, "video_query": null},
  "reply_text": "给眼镜用户看的简短中文确认或追问",
  "confidence": 0.8
}
"""

EXPERIMENT_SETUP_PROMPT = """识别用户选择的实验。
支持：减压蒸馏、常压蒸馏、光度计、电解实验、TLC、薄层色谱实验、柱色谱实验、重结晶回流实验、重结晶过滤实验。
只输出 JSON：
{"transcript":"转写文本","intent":"select_experiment或unclear","slots":{"experiment_type":"对应实验ID或null"}}
"""

# 教师版唤醒后：自然语言理解 → 只分类到拍照 / 开始录像
TEACHER_AWAKE_PROMPT = """你是教师版智能眼镜的自然语言理解分类器。
教师用**自然语言**（口语、随意、不固定句式）表达当下的需求，你只负责理解语义意图，归类到下面某个 action。根据含义判断，不要字面匹配。

可分类的 action（只有这两个）:
- capture_photo: 教师只想拍一张照片保存当前画面，比如"拍张照片""帮我拍一下""把现在这个画面拍下来""截个图"。
- start_recording: 教师想开始录像、留存一段实验过程，比如"开始记录""录一段""把这一段录下来""开始录像""帮我存一下过程"。
- unclear: 听不清、内容不完整、或不是拍照/录像需求（比如问问题、讨论、说与拍照录像无关的话）。

判断原则:
1. 先把语音转写到 transcript。
2. 理解教师当下想做的一件事，归类到最贴近的 action。
3. 无法映射到上述两个动作时统一输出 unclear。


输出严格 JSON，不要 Markdown，不要解释:
{
  "transcript": "用户语音转写",
  "intent": "capture_photo|start_recording|unclear",
  "slots": {"experiment_type": null, "video_index": null, "video_query": null},
  "reply_text": "给教师看的简短中文确认",
  "confidence": 0.8
}
"""

# 教师版录像中：窄域，只识别停止录像
TEACHER_RECORDING_STOP_PROMPT = """你是教师版眼镜的录像停止识别器。
教师正在录像，你只判断教师是否要结束/保存这段录像。

理解自然语言要宽松：任何表达完成/够了/停/保存的话都算 stop_recording，例如
结束录像 / 停止录像 / 保存录像 / 录好了 / 可以了 / 差不多了 / 先停一下 / 别录了 / 结束吧 / 存一下。

只有真正无关的内容（提问、聊天、环境噪声）才返回 unclear。
允许的 action 只有 stop_recording 或 unclear。

输出严格 JSON，slots 全部为 null:
{
  "transcript": "用户语音转写",
  "intent": "stop_recording|unclear",
  "slots": {"experiment_type": null, "video_index": null, "video_query": null},
  "reply_text": "简短中文确认",
  "confidence": 0.8
}
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
    demo_options: str | None = None,
    model: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    ctx = (context or "").strip()
    is_teacher_sleep = ctx == "teacher_sleep"
    is_sleep_mode = ctx in {"sleep", "teacher_sleep"}
    is_experiment_setup = ctx == "experiment_setup"
    mode_name = (
        "TEACHER_SLEEP" if is_teacher_sleep
        else "SLEEP" if is_sleep_mode
        else "EXPERIMENT_SETUP" if is_experiment_setup
        else "AWAKE"
    )

    print(f"\n[voice_intent] === 开始处理 === mode={mode_name}, audio_size={len(audio_bytes)} bytes")

    if not audio_bytes:
        print(f"[voice_intent] 音频为空，返回默认结果")
        if is_teacher_sleep:
            return {
                "wake": False,
                "intent": None,
                "transcript": "",
                "decision_source": "text",
                "timings": {},
            }
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

    # Keep passive states narrow at both session and response levels. Otherwise
    # the model still sees the full awake intent list and may emit unrelated
    # intents such as qa while a video is playing.
    if is_teacher_sleep:
        system_prompt = TEACHER_SLEEP_MODE_PROMPT
    elif is_sleep_mode:
        system_prompt = SLEEP_MODE_PROMPT
    elif ctx == "teacher_awake":
        system_prompt = TEACHER_AWAKE_PROMPT
    elif ctx == "teacher_recording":
        system_prompt = TEACHER_RECORDING_STOP_PROMPT
    elif ctx in {"demo_playing", "recording_stop_only", "demo_select", "experiment_setup"}:
        system_prompt = _build_response_instruction(ctx, demo_options=demo_options)
    else:
        system_prompt = AWAKE_MODE_PROMPT
    print(f"[voice_intent] 使用 prompt: {mode_name}_MODE_PROMPT, context={ctx}")

    dashscope.api_key = config.api_key
    # The legacy student sleep path keeps its fast transcript completion.
    # Teacher sleep waits for model JSON unless deterministic text already wakes it.
    callback = OmniIntentCallback(complete_on_transcript=(ctx == "sleep"))
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
            instructions=_build_response_instruction(
                ctx,
                is_sleep_mode=is_sleep_mode,
                demo_options=demo_options,
            ),
            output_modalities=[MultiModality.TEXT],
        )

        deadline = time.time() + config.timeout_seconds
        fallback_after_transcript_seconds = 2.0
        quick_teacher_result: dict[str, Any] | None = None
        while not callback.done and time.time() < deadline:
            if is_teacher_sleep and callback.transcript_completed_at is not None:
                quick = _teacher_wake_from_transcript(callback.transcript)
                if quick["wake"]:
                    quick_teacher_result = quick
                    break
            if (
                not is_sleep_mode
                and callback.transcript_completed_at is not None
                and time.time() - callback.transcript_completed_at >= fallback_after_transcript_seconds
            ):
                print(f"[voice_intent] 模型 JSON 未及时返回，使用转写兜底: {callback.transcript!r}")
                break
            time.sleep(0.05)

        if is_teacher_sleep and quick_teacher_result is None and callback.transcript:
            quick = _teacher_wake_from_transcript(callback.transcript)
            if quick["wake"]:
                quick_teacher_result = quick

        if not callback.done and quick_teacher_result is None:
            if callback.transcript:
                print(f"[voice_intent] 未收到模型回复，使用转写兜底: {callback.transcript!r}")
            else:
                print(f"[voice_intent] ERROR: 超时")
                raise TimeoutError(f"等待 Qwen Omni 指令解析超时，超过 {config.timeout_seconds} 秒。")
        if callback.error and quick_teacher_result is None:
            print(f"[voice_intent] ERROR: {callback.error}")
            raise AliyunError(callback.error)

        timings = {
            "omni": round((time.perf_counter() - model_started_at) * 1000, 1),
            "total": round((time.perf_counter() - started_at) * 1000, 1),
        }

        # Debug 输出
        print(f"[voice_intent] Omni 转写: {callback.transcript!r}")
        print(f"[voice_intent] Omni 回复: {callback.answer_text!r}")

        if is_teacher_sleep:
            result = quick_teacher_result or _parse_teacher_wake_json(
                callback.answer_text,
                callback.transcript,
            )
            print(
                f"[voice_intent] 解析结果: wake={result.get('wake')}, "
                f"intent={result.get('intent')}, transcript={result.get('transcript')!r}"
            )
        elif is_sleep_mode:
            result = _parse_wake_json(callback.answer_text, callback.transcript)
            print(f"[voice_intent] 解析结果: wake={result.get('wake')}, transcript={result.get('transcript')!r}")
        else:
            result = _parse_intent_json(callback.answer_text, callback.transcript, context=ctx)
            if ctx == "experiment_setup":
                transcript = str(result.get("transcript") or callback.transcript or "").strip()
                slots = result.get("slots") if isinstance(result.get("slots"), dict) else {}
                # The transcript contains the user-facing experiment name and is
                # less ambiguous than a model-generated enum value.
                experiment_type = _match_experiment_type(transcript)
                if experiment_type is None:
                    experiment_type = slots.get("experiment_type")
                if experiment_type in SUPPORTED_EXPERIMENTS:
                    result = _safe_intent(
                        transcript,
                        "select_experiment",
                        _default_reply("select_experiment", {"experiment_type": experiment_type}),
                        0.7,
                        experiment_type=experiment_type,
                    )
                else:
                    result = _safe_intent(
                        transcript,
                        "unclear",
                        "请选择今天的实验。",
                        0.5,
                    )
            elif result.get("intent") == "select_experiment":
                result = _safe_intent(
                    str(result.get("transcript") or callback.transcript or ""),
                    "unclear",
                    "请先在启动阶段选择实验。当前可以说：实验纠错、安全检查、演示指引、开始记录或我要提问。",
                    0.5,
                )
            if ctx == "demo_playing" and result.get("intent") != "stop_demo_video":
                result = _safe_intent(
                    str(result.get("transcript") or callback.transcript or ""),
                    "unclear",
                    "",
                    0.5,
                )
            if ctx == "recording_stop_only" and result.get("intent") != "stop_recording":
                result = _safe_intent(
                    str(result.get("transcript") or callback.transcript or ""),
                    "unclear",
                    "",
                    0.5,
                )
            if ctx == "demo_select" and result.get("intent") != "select_demo_video":
                result = _safe_intent(
                    str(result.get("transcript") or callback.transcript or ""),
                    "unclear",
                    "",
                    0.5,
                )
            # 教师版唤醒后：只允许拍照/开始录像，其它一律 unclear
            if ctx == "teacher_awake" and result.get("intent") not in {"capture_photo", "start_recording"}:
                result = _safe_intent(
                    str(result.get("transcript") or callback.transcript or ""),
                    "unclear",
                    "教师版只支持拍照和开始录像，请再说一遍。",
                    0.5,
                )
            # 教师版录像中：只允许停止录像
            if ctx == "teacher_recording" and result.get("intent") != "stop_recording":
                result = _safe_intent(
                    str(result.get("transcript") or callback.transcript or ""),
                    "unclear",
                    "",
                    0.5,
                )
            if ctx not in {
                "experiment_setup", "demo_playing", "recording_stop_only", "demo_select",
                "teacher_awake", "teacher_recording",
            }:
                if result.get("intent") not in MAIN_INTENTS:
                    result = _safe_intent(
                        str(result.get("transcript") or callback.transcript or ""),
                        "unclear",
                        "我没有听清，请再说一遍。",
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


def _build_response_instruction(
    context: str | None,
    *,
    is_sleep_mode: bool = False,
    demo_options: str | None = None,
) -> str:
    if (context or "").strip() == "teacher_sleep":
        return (
            "Teacher glasses wake detector for the Chinese assistant '化小北 / 小北助手'. "
            "Return wake=true for an intentional call, including clear near-homophones. If the same "
            "utterance clearly asks to take one photo, set intent=capture_photo; if it clearly asks "
            "to start video recording, set intent=start_recording; otherwise set intent=null. Return "
            "wake=false for unrelated speech or noise. Output strict JSON: "
            '{"wake": true/false, "intent": "capture_photo|start_recording|null", '
            '"transcript": "faithful recognized text"}.'
        )
    if is_sleep_mode:
        return (
            "Wake detector for the Chinese assistant named '化小北 / 小北助手' "
            "(xiao bei zhu shou). Return wake=true when the user intentionally calls or activates "
            "the assistant, including phrases such as '小北助手，开始工作', clear near-homophones "
            "such as '小贝助手' or '晓北助手', or an assistant address combined with an activation "
            "request such as start working, wake up, are you there, or help me. Judge pronunciation "
            "and intent instead of requiring an exact transcript. Return wake=false for unrelated "
            "conversation, incidental mentions, generic '开始工作' without addressing the assistant, "
            "noise, or uncertain speech. Output strict JSON: "
            "{\"wake\": true/false, \"transcript\": \"faithful recognized text\"}."
        )

    context_text = (context or "VOICE_IDLE").strip() or "VOICE_IDLE"
    options = (demo_options or "").strip()

    if context_text == "demo_playing":
        return (
            "The glasses are currently playing a demo video. Your only task is to decide whether "
            "the user asks to stop playback. Allowed intents: stop_demo_video or unclear. "
            "If the user says stop playback / exit playback / stop demo / pause demo / cancel, "
            "return stop_demo_video. For noise, questions, video selection, unclear speech, or any "
            "other content, return unclear. Output strict JSON with slots all null."
        )

    if context_text == "experiment_setup":
        return EXPERIMENT_SETUP_PROMPT

    if context_text == "recording_stop_only":
        return (
            "The glasses are currently recording audio or video. Your only task is to decide whether "
            "the user wants to end or save the current recording. Understand natural language generously: "
            "treat any expression of finishing/done/enough/stopping/saving as stop_recording, e.g. "
            "end recording / stop recording / stop audio / stop video / save recording / I'm done / that's enough / "
            "停止录音 / 结束录音 / 保存录音 / 停止录像 / 结束录像 / 保存录像 / 可以了 / 差不多行了 / "
            "先停一下 / 别录了 / 录好了 / 保存一下 / 结束吧. "
            "Only return unclear for genuinely unrelated content (asking questions, discussing "
            "something else, environmental noise, or unrelated chitchat). "
            "Allowed intents: stop_recording or unclear. Output strict JSON with slots all null."
        )

    if context_text == "demo_select":
        extra = ""
        if options:
            extra = (
                "\nAvailable demo videos:\n"
                f"{options}\n"
                "When the user says first/second/third/number 3, output video_index as a 0-based index. "
                "When the user says filename keywords, output video_query as the original phrase.\n"
            )
        return (
            "The glasses are on the demo-video selection page. Only decide which video the user selected. "
            "Allowed intents: select_demo_video or unclear. "
            "Do not output qa, demo_guide, cancel, or other intents. "
            f"{extra}"
            "Output strict JSON. slots.video_index must be 0-based."
        )

    if context_text == "teacher_awake":
        return TEACHER_AWAKE_PROMPT

    if context_text == "teacher_recording":
        return TEACHER_RECORDING_STOP_PROMPT

    return (
        f"Current glasses state: {context_text}\n"
        "Understand the user's natural, spoken Chinese and classify it to the single most fitting "
        "action. Spoken, casual, or paraphrased expressions should be mapped correctly by meaning, "
        "not by literal keywords. Use unclear whenever the speech cannot be mapped to a supported "
        "main action. Output strict JSON."
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
    # JSON 解析失败或模型尚未返回时，用转录文本快速兜底。沉睡模式通常
    # 在 ASR 完成后立即结束，以免云端生成延迟扩大眼镜的监听空窗。
    normalized = re.sub(r"[\s，。！？、,.!?：:；;‘’'\"“”_-]+", "", transcript).lower()
    near_sound = bool(_WAKE_NEAR_SOUND_PATTERN.search(normalized)) or "xiaobeizhushou" in normalized
    addressed_activation = (
        any(address in normalized for address in _WAKE_ADDRESSES)
        and any(action in normalized for action in _WAKE_ACTIONS)
    )
    wake = near_sound or addressed_activation
    return {"wake": wake, "transcript": transcript}


def _teacher_wake_from_transcript(transcript: str) -> dict[str, Any]:
    """Deterministic positive-only shortcut for teacher wake and one-shot actions."""
    normalized = re.sub(r"[\s，。！？、,.!?：:；;‘’'\"“”_-]+", "", transcript).lower()
    direct_call = (
        bool(_WAKE_NEAR_SOUND_PATTERN.search(normalized))
        or "xiaobeizhushou" in normalized
        or "化小北" in normalized
    )
    addressed_activation = (
        any(address in normalized for address in _WAKE_ADDRESSES)
        and any(action in normalized for action in _WAKE_ACTIONS)
    )
    wake = direct_call or addressed_activation
    intent: str | None = None
    if wake and any(word in normalized for word in (
        "拍张照片", "拍一张", "拍照", "帮我拍", "拍下来", "截个图", "保存照片",
    )):
        intent = "capture_photo"
    elif wake and any(word in normalized for word in (
        "开始录像", "开始录制", "录一段", "录一下", "录下来", "录像", "视频录制",
        "记录这一段", "把这一段记录下来", "帮我存一下过程",
    )):
        intent = "start_recording"
    return {
        "wake": wake,
        "intent": intent,
        "transcript": transcript.strip(),
        "decision_source": "text",
    }


def _parse_teacher_wake_json(text: str, fallback_transcript: str) -> dict[str, Any]:
    """Combine deterministic wake matching with the teacher Omni decision."""
    payload = _extract_json(text)
    transcript = fallback_transcript.strip()
    if payload is not None:
        transcript = str(payload.get("transcript") or fallback_transcript or "").strip()

    local = _teacher_wake_from_transcript(transcript)
    model_wake = payload.get("wake") if payload is not None else None
    model_intent = payload.get("intent") if payload is not None else None
    if model_intent not in {"capture_photo", "start_recording"}:
        model_intent = None

    wake = bool(local["wake"] or model_wake is True)
    intent = local["intent"] or (model_intent if wake else None)
    return {
        "wake": wake,
        "intent": intent,
        "transcript": transcript,
        "decision_source": "text" if local["wake"] else "omni",
    }


def _parse_intent_json(
    text: str,
    fallback_transcript: str,
    *,
    context: str | None = None,
) -> dict[str, Any]:
    payload = _extract_json(text)
    if payload is None:
        return _fallback_from_transcript(fallback_transcript, context=context)

    transcript = str(payload.get("transcript") or fallback_transcript or "").strip()
    intent = str(payload.get("intent") or "unclear").strip()
    if intent == "unknown":
        intent = "unclear"
    if intent not in SUPPORTED_INTENTS:
        intent = "unclear"

    raw_slots = payload.get("slots")
    slots = raw_slots if isinstance(raw_slots, dict) else {}
    experiment_type = slots.get("experiment_type")
    if experiment_type is not None:
        experiment_type = str(experiment_type).strip() or None
    if experiment_type not in SUPPORTED_EXPERIMENTS:
        experiment_type = None
    # Prefer an explicit ordinal in the transcript ("第二个", "3号").
    # Model responses are not always consistent about 0-based vs 1-based indices,
    # while transcript ordinals are unambiguous for the user-facing list.
    transcript_video_index = _match_demo_video_index(transcript)
    video_index = transcript_video_index
    if video_index is None:
        video_index = _coerce_video_index(slots.get("video_index"))
    video_query = str(slots.get("video_query") or "").strip() or None
    if (
        intent == "select_demo_video"
        and video_index is None
        and not video_query
        and _looks_like_demo_guide_request(transcript)
    ):
        intent = "demo_guide"
    slots = {
        "experiment_type": experiment_type,
        "video_index": video_index,
        "video_query": video_query,
    }

    raw_confidence = payload.get("confidence")
    confidence = _coerce_confidence(raw_confidence)
    if raw_confidence is None and intent != "unclear":
        # Qwen Omni sometimes returns a minimal but valid JSON such as
        # {"intent": "select_demo_video", "slots": {"video_index": 1}}.
        # Treat that as usable instead of downgrading it to unclear only
        # because the optional confidence field is missing.
        confidence = 0.7
    # 信任 omni 的分类结果，不再用置信度阈值降级 intent。
    # confidence 字段仍然透传，仅用于日志/调试。
    # omni 判定映射不到时自己会输出 unknown，作为兜底。

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


def _fallback_from_transcript(
    transcript: str,
    *,
    context: str | None = None,
) -> dict[str, Any]:
    """Context-aware keyword fallback when Omni does not return valid JSON."""
    normalized = transcript.strip()
    if not normalized:
        return _safe_intent("", "unclear", "我没有听清，请再说一遍。", 0.0)

    ctx = (context or "").strip()
    if ctx == "recording_stop_only":
        if any(word in normalized for word in (
            "结束记录", "停止记录", "结束录像", "停止录像", "保存录像",
            "结束录音", "停止录音", "保存录音", "录好了", "可以停了", "别录了", "先停一下", "结束吧",
        )):
            return _safe_intent(normalized, "stop_recording", "结束记录。", 0.7)
        return _safe_intent(normalized, "unclear", "", 0.5)

    if ctx == "demo_playing":
        if any(word in normalized for word in (
            "停止播放", "退出播放", "暂停演示", "停止演示", "别放了",
        )):
            return _safe_intent(normalized, "stop_demo_video", "已停止播放。", 0.7)
        return _safe_intent(normalized, "unclear", "", 0.5)

    if ctx == "demo_select":
        video_index = _match_demo_video_index(normalized)
        if video_index is not None or any(
            word in normalized for word in ("操作流程", "操作视频", "原理", "小结")
        ):
            return _safe_intent(
                normalized,
                "select_demo_video",
                "开始播放演示视频。",
                0.7,
                video_index=video_index,
                video_query=normalized,
            )
        return _safe_intent(normalized, "unclear", "", 0.5)

    if ctx == "experiment_setup":
        experiment_type = _match_experiment_type(normalized)
        if experiment_type:
            return _safe_intent(
                normalized,
                "select_experiment",
                _default_reply("select_experiment", {"experiment_type": experiment_type}),
                0.7,
                experiment_type=experiment_type,
            )
        return _safe_intent(normalized, "unclear", "请选择今天的实验。", 0.5)

    # 教师版录像中：只识别停止录像
    if ctx == "teacher_recording":
        if any(word in normalized for word in (
            "结束录像", "停止录像", "保存录像", "结束记录", "停止记录",
            "录好了", "可以了", "差不多了", "别录了", "先停一下", "结束吧", "存一下",
        )):
            return _safe_intent(normalized, "stop_recording", "结束记录。", 0.7)
        return _safe_intent(normalized, "unclear", "", 0.5)

    # 教师版唤醒后：只识别拍照 / 开始录像
    if ctx == "teacher_awake":
        if any(word in normalized for word in (
            "拍张照片", "拍一张", "拍照", "帮我拍", "拍下来", "截个图", "保存照片",
        )):
            return _safe_intent(normalized, "capture_photo", "开始拍照。", 0.7)
        if any(word in normalized for word in (
            "开始记录", "开始录像", "开始录制", "录一段", "录一下", "录下来", "帮我存",
        )):
            return _safe_intent(normalized, "start_recording", "开始记录。", 0.7)
        return _safe_intent(normalized, "unclear", "教师版只支持拍照和开始录像。", 0.5)

    lower = normalized.lower()
    if any(word in normalized for word in ("演示指引", "播放演示", "看演示", "看视频", "演示视频")):
        return _safe_intent(normalized, "demo_guide", "进入演示指引。", 0.7)
    if any(word in normalized for word in (
        "开始录像", "开始视频", "录视频", "录像", "视频录制", "视频记录", "把画面录下来", "录下画面",
    )):
        return _safe_intent(normalized, "start_video_recording", "开始录像。", 0.7)
    if any(word in normalized for word in (
        "开始记录", "开始录音", "开始录制", "录音", "语音记录", "记录一下",
        "录一下", "录一段", "录下来", "帮我记录", "留个记录",
    )):
        return _safe_intent(normalized, "start_audio_recording", "开始录音。", 0.7)
    if any(word in normalized for word in ("开始拍照", "拍一张", "拍照", "保存照片")):
        return _safe_intent(normalized, "capture_photo", "开始拍照。", 0.7)
    if "安全" in normalized:
        return _safe_intent(normalized, "safety_check", "开始安全检查，请保持现场在视野中央。", 0.7)
    if any(word in normalized for word in ("实验纠错", "装置检测", "检查实验装置")):
        return _safe_intent(normalized, "experiment_check", "开始实验纠错，请保持画面稳定。", 0.7)
    if any(
        word in normalized
        for word in ("提问", "我要问", "问一个", "请问", "问答", "解释一下", "为什么", "怎么")
    ) or "?" in lower:
        return _safe_intent(normalized, "qa", "请说你的问题。", 0.7)
    return _safe_intent(normalized, "unclear", "我没有听清，请再说一遍。", 0.5)


def _looks_like_demo_guide_request(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    has_demo_request = any(word in compact for word in ("演示指引", "播放演示", "放演示", "演示视频", "看视频", "看演示"))
    has_specific_video = (
        _match_demo_video_index(compact) is not None
        or any(word in compact for word in ("操作流程", "操作视频", "原理", "小结"))
    )
    return has_demo_request and not has_specific_video


def _match_experiment_type(text: str) -> str | None:
    if "常压" in text:
        return "atmospheric_distillation"
    if "减压" in text:
        return "distillation"
    if "光度" in text:
        return "photometer"
    if "电解" in text:
        return "electrolysis"
    if "TLC" in text.upper():
        return "tlc"
    if "薄层色谱" in text or "薄层层析" in text:
        return "thin_layer_chromatography"
    if "柱色谱" in text or "柱层析" in text:
        return "column_chromatography"
    if "重结晶" in text and ("回流" in text or "冷凝" in text):
        return "recrystallization_reflux"
    if "重结晶" in text and ("过滤" in text or "吸滤" in text):
        return "recrystallization_filtration"
    return None


def _match_demo_video_index(text: str) -> int | None:
    compact = re.sub(r"\s+", "", text)
    digit_match = re.search(r"(?:第)?([0-9]{1,2})(?:个|段|号|部|集|条)?", compact)
    if digit_match:
        value = int(digit_match.group(1))
        if 1 <= value <= 20:
            return value - 1

    numerals = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "十一": 11,
        "十二": 12,
        "十三": 13,
        "十四": 14,
        "十五": 15,
        "十六": 16,
        "十七": 17,
        "十八": 18,
        "十九": 19,
        "二十": 20,
    }
    for word, value in sorted(numerals.items(), key=lambda item: len(item[0]), reverse=True):
        if (
            compact == word
            or f"第{word}" in compact
            or f"{word}号" in compact
            or f"{word}个" in compact
            or f"{word}段" in compact
            or f"{word}部" in compact
        ):
            return value - 1
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
        "intent": intent if intent in SUPPORTED_INTENTS else "unclear",
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
        "start_audio_recording": "开始录音。",
        "start_video_recording": "开始录像。",
        "stop_recording": "结束记录。",
        "capture_photo": "开始拍照。",
        "experiment_check": "开始实验纠错，请保持画面稳定。",
        "safety_check": "开始安全检查，请保持现场在视野中央。",
        "demo_guide": "进入演示指引。",
        "select_demo_video": "开始播放演示视频。",
        "stop_demo_video": "已停止播放。",
        "qa": "请说你的问题。",
        "unclear": "我没有听清，请再说一遍。",
    }
    if intent == "select_experiment":
        names = {
            "distillation": "减压蒸馏",
            "atmospheric_distillation": "常压蒸馏",
            "photometer": "光度计",
            "electrolysis": "电解",
            "tlc": "TLC",
            "thin_layer_chromatography": "薄层色谱实验",
            "column_chromatography": "柱色谱实验",
            "recrystallization_reflux": "重结晶回流实验",
            "recrystallization_filtration": "重结晶过滤实验",
        }
        name = names.get(slots.get("experiment_type"), "该实验")
        return f"已选择{name}，请保持装置在视野中央。"
    return replies.get(intent, "我没有听清，请再说一遍。")
