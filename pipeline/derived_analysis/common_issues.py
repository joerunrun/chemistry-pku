from __future__ import annotations

import json
import os
import re
from typing import Any

from pipeline.common.vlm_client import DEFAULT_VLM_MODEL, DEFAULT_VLM_PROVIDER, create_vlm


MAX_TRANSCRIPT_CHARS = 30_000
MAX_TRANSCRIPT_PER_RECORD = 3_000


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


def _clean_text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _normalize_issues(payload: dict[str, Any], valid_ids: set[str]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    raw_items = payload.get("common_issues", [])
    if not isinstance(raw_items, list):
        raw_items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        problem = _clean_text(item.get("problem"), 300)
        guidance = _clean_text(item.get("guidance"), 500)
        raw_record_ids = item.get("record_ids", [])
        if isinstance(raw_record_ids, str):
            raw_record_ids = [raw_record_ids]
        if not isinstance(raw_record_ids, list):
            raw_record_ids = []
        record_ids = list(dict.fromkeys(
            str(record_id).strip()
            for record_id in raw_record_ids
            if str(record_id).strip() in valid_ids
        ))
        if not problem or not guidance or len(record_ids) < 2:
            continue
        key = (problem, tuple(record_ids))
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "problem": problem,
            "count": len(record_ids),
            "guidance": guidance,
            "record_ids": record_ids,
        })
        if len(normalized) >= 5:
            break
    return normalized


def build_common_issues_prompt(
    experiment: dict[str, Any],
    transcripts: list[dict[str, str]],
) -> str:
    knowledge = {
        "id": experiment.get("id", ""),
        "name": experiment.get("name", ""),
        "experiment_requirements": experiment.get("experiment_requirements", ""),
        "components": [
            {"label": item.get("label", ""), "name": item.get("name", "")}
            for item in experiment.get("components", [])
            if isinstance(item, dict)
        ],
    }
    lines = []
    total = 0
    for item in transcripts:
        transcript = _clean_text(item.get("transcript"), MAX_TRANSCRIPT_PER_RECORD)
        if not transcript:
            continue
        remaining = MAX_TRANSCRIPT_CHARS - total
        if remaining <= 0:
            break
        transcript = transcript[:remaining]
        total += len(transcript)
        lines.append(
            f"记录ID={item.get('record_id', '')}; 时间={item.get('created_at', '')}; 转写={transcript}"
        )

    return f"""你是化学实验教学分析助手。请分析多条实验对话转写，提炼重复出现的共性问题和简短指导建议。

实验基本信息（只能作为判断标准，不代表学生一定做过这些操作）：
{json.dumps(knowledge, ensure_ascii=False, indent=2)}

对话转写：
{chr(10).join(lines)}

规则：
1. 只把至少出现在两条不同记录中的问题列为 common_issues。
2. 合并同义问题，最多输出5条；每条问题和指导建议都要简洁。
3. 只能依据转写和实验基本信息，不补写对话中不存在的事实。
4. record_ids 必须使用输入中的记录 ID，count 由记录 ID 数量决定。
5. 没有足够证据时输出空数组，不要猜测。
6. 只输出 JSON，不要 Markdown，不要额外解释。

JSON格式：
{{"common_issues":[{{"problem":"简练问题","guidance":"指导建议","record_ids":["记录ID1","记录ID2"]}}]}}"""


def generate_common_issues(
    experiment: dict[str, Any],
    transcripts: list[dict[str, str]],
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    provider = (provider or os.getenv("MEDIA_ANALYSIS_PROVIDER", DEFAULT_VLM_PROVIDER)).strip()
    model = (model or os.getenv("MEDIA_ANALYSIS_MODEL", DEFAULT_VLM_MODEL)).strip()
    client = create_vlm(provider, model)
    response = client.chat([
        {"role": "user", "content": build_common_issues_prompt(experiment, transcripts)},
    ], temperature=0.1)
    payload = _json_from_model(response)
    valid_ids = {str(item.get("record_id", "")).strip() for item in transcripts}
    return {
        "common_issues": _normalize_issues(payload, valid_ids),
        "provider": provider,
        "model": model,
    }
