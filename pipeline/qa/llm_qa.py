from __future__ import annotations

from typing import Any

from pipeline.common.vlm_client import DEFAULT_VLM_MODEL, DEFAULT_VLM_PROVIDER, call_vlm, create_vlm

SYSTEM_PROMPT = (
    "你是化学实验辅助问答助手。只根据模型已有知识回答，不调用外部知识库。"
    "回答要简洁、准确、适合学生在实验现场快速理解。"
)


def answer_question(
    question: str,
    *,
    provider: str = DEFAULT_VLM_PROVIDER,
    model: str | None = DEFAULT_VLM_MODEL,
) -> tuple[str, float]:
    normalized = question.strip()
    if not normalized:
        raise ValueError("question is empty")

    client = create_vlm(provider, model)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "请用中文简短回答下面的问题。若问题和化学实验无关，也可以直接回答，"
                "但不要编造不确定事实。\n\n"
                f"问题：{normalized}"
            ),
        },
    ]
    return call_vlm(client, messages, temperature=0.2)
