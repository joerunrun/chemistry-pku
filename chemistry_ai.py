from __future__ import annotations

import time
from typing import Any

from openrouter_client import (
    build_image_part,
    build_text_part,
    prepare_image_part,
)


SYSTEM_PROMPT = """你是化学实验图像分析与纠错助手。
"""


class ChemistryExperimentAI:
    def __init__(self, client: Any) -> None:
        self.client = client

    def describe_image(self, image_bytes: bytes, prompt: str | None = None) -> str:
        user_prompt = prompt or (
            "请用中文描述这张化学实验图片中可见的实验装置、连接关系和操作要点。"
            "如果存在明显错误，也请直接指出。"
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    build_text_part(user_prompt),
                    build_image_part(image_bytes, config=self.client.config),
                ],
            },
        ]
        return self.client.chat(messages)

    def build_compare_messages(
        self,
        correct_image_bytes: bytes,
        wrong_image_bytes: bytes,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        prompt = """请比较两张化学实验图片的差异。

       尽可能快速，输出中文，重点关注装置增添或者减少，管子连接、部件位置差异。
        对图片对比时，要优先识别真实装置差异，忽略仅由拍摄角度、远近、裁剪范围、光线和透视造成的表面差异。
        避免泛泛而谈，不要描述无关背景。图A为正确图片，图B为待纠错图片。
        我使用了不同颜色的框来标记不同的装置部分，请分别比较每个部分的差异，如果出现多出来或者缺失某种颜色的框，必须反馈。
        """


        correct_part, correct_metrics = prepare_image_part(
            correct_image_bytes, config=self.client.config
        )
        wrong_part, wrong_metrics = prepare_image_part(
            wrong_image_bytes, config=self.client.config
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    build_text_part(prompt),
                    build_text_part("图A"),
                    correct_part,
                    build_text_part("图B"),
                    wrong_part,
                ],
            },
        ]
        metrics = {
            "image_a": correct_metrics.as_dict(),
            "image_b": wrong_metrics.as_dict(),
            "preprocess_total_ms": round(
                correct_metrics.preprocess_ms + wrong_metrics.preprocess_ms, 1
            ),
        }
        return messages, metrics

    def build_analyze_messages(
        self,
        experiment_requirements: str,
        difference_summary: str,
    ) -> list[dict[str, Any]]:
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



要求：
1. 只输出中文。
2. 重点回答“最主要的错误是什么”，不要把很多次要问题混在一起。
3. 若实验要求与图像差异存在冲突，以实验要求为准重新判断。
"""

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

    def compare_images(self, correct_image_bytes: bytes, wrong_image_bytes: bytes) -> str:
        return self.compare_images_with_metrics(correct_image_bytes, wrong_image_bytes)[0]

    def compare_images_with_metrics(
        self, correct_image_bytes: bytes, wrong_image_bytes: bytes
    ) -> tuple[str, dict[str, Any]]:
        messages, metrics = self.build_compare_messages(correct_image_bytes, wrong_image_bytes)
        model_started_at = time.perf_counter()
        result = self.client.chat(messages)
        model_request_ms = (time.perf_counter() - model_started_at) * 1000
        metrics["model_request_ms"] = round(model_request_ms, 1)
        return result, metrics

    def analyze_main_error(self, experiment_requirements: str, difference_summary: str) -> str:
        return self.analyze_main_error_with_metrics(
            experiment_requirements,
            difference_summary,
        )[0]

    def analyze_main_error_with_metrics(
        self, experiment_requirements: str, difference_summary: str
    ) -> tuple[str, dict[str, Any]]:
        messages = self.build_analyze_messages(experiment_requirements, difference_summary)
        model_started_at = time.perf_counter()
        result = self.client.chat(messages)
        model_request_ms = (time.perf_counter() - model_started_at) * 1000
        return result, {"model_request_ms": round(model_request_ms, 1)}
