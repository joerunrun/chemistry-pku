"""Generate a coherent daily course narrative from second-layer transcripts."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from pipeline.common.vlm_client import DEFAULT_VLM_MODEL, DEFAULT_VLM_PROVIDER, create_vlm


MAX_INPUT_CHARS = 60_000
MAX_RECORD_CHARS = 6_000
MAX_OUTPUT_CHARS = 20_000


def _json_from_model(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValueError("模型没有返回合法 JSON") from exc
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as nested:
            raise ValueError("模型没有返回合法 JSON") from nested
    if not isinstance(value, dict):
        raise ValueError("模型返回结果不是 JSON 对象")
    return value


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def build_daily_course_prompt(
    date_text: str,
    transcripts: list[dict[str, str]],
    experiment_contexts: list[dict[str, str]] | None = None,
) -> str:
    """Build a prompt that asks for narration, not a verbatim concatenation."""
    blocks: list[str] = []
    total = 0
    truncated = False
    for item in transcripts:
        text = _clean_text(item.get("transcript"), MAX_RECORD_CHARS)
        if not text:
            continue
        if len(text) < len(str(item.get("transcript", ""))):
            truncated = True
        remaining = MAX_INPUT_CHARS - total
        if remaining <= 0:
            truncated = True
            break
        text = text[:remaining]
        total += len(text)
        blocks.append(
            "记录ID={record_id}; 时间={created_at}; 设备={device_id}; 实验={experiment_type}\n{transcript}".format(
                record_id=item.get("record_id", ""),
                created_at=item.get("created_at", ""),
                device_id=item.get("device_id", "") or "未提供",
                experiment_type=item.get("experiment_type", "") or "未提供",
                transcript=text,
            )
        )

    contexts = experiment_contexts or []
    context_text = json.dumps(contexts, ensure_ascii=False, indent=2)
    truncation_note = "部分转写因长度限制未全部送入模型。" if truncated else ""
    scope_label = f"{date_text}期间" if " 至 " in date_text else f"{date_text}当天"
    default_title = "时间段课程内容" if " 至 " in date_text else "当天课程内容"
    return f"""你是化学实验课程整理助手。请把{scope_label}多条、零散的教师/实验对话转写，整理成一份连贯、简洁、可阅读的课程内容转述。

实验信息仅用于理解名词和实验背景，不能据此断言对话中没有明确说出的操作或结论：
{context_text}

所选范围转写（按时间顺序）：
{chr(10).join(blocks)}

整理要求：
1. 不是逐条复制，也不是简单拼接；删除重复的唤醒词、拍照/录像等系统指令和无意义口头语。
2. 保留所选范围内明确讲到的实验原理、操作过程、现象、问题、教师解释和注意事项，并按课程推进顺序组织段落。
3. 不要编造转写中没有出现的事实；不清楚的地方用“转写不清”或“记录未明确”表达。
4. 不同实验或设备内容可以用简短小标题区分，但不要虚构课程内容。
5. 输出一段连贯的中文课程转述，语言简洁，不要写成对话逐句记录；最多保留 8 个短段落。
6. 只输出 JSON，不要 Markdown 代码围栏或额外解释。
{truncation_note}

JSON格式：
{{"title":"{default_title}","course_content":"连贯的课程转述","highlights":["课程重点1","课程重点2"]}}"""


def generate_daily_course(
    date_text: str,
    transcripts: list[dict[str, str]],
    *,
    experiment_contexts: list[dict[str, str]] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    provider = (provider or os.getenv("MEDIA_ANALYSIS_PROVIDER", DEFAULT_VLM_PROVIDER)).strip()
    model = (model or os.getenv("MEDIA_ANALYSIS_MODEL", DEFAULT_VLM_MODEL)).strip()
    client = create_vlm(provider, model)
    response = client.chat([
        {"role": "user", "content": build_daily_course_prompt(date_text, transcripts, experiment_contexts)},
    ], temperature=0.1)
    payload = _json_from_model(response)
    course_content = _clean_text(payload.get("course_content"), MAX_OUTPUT_CHARS)
    if not course_content:
        raise ValueError("模型没有返回课程内容")
    title = _clean_text(payload.get("title"), 200) or "当天课程内容"
    highlights = payload.get("highlights", [])
    if isinstance(highlights, str):
        highlights = [highlights]
    if not isinstance(highlights, list):
        highlights = []
    highlights = [_clean_text(item, 300) for item in highlights if _clean_text(item, 300)][:8]
    return {
        "title": title,
        "course_content": course_content,
        "highlights": highlights,
        "provider": provider,
        "model": model,
    }
