from __future__ import annotations

import time
from typing import Any

from openrouter_client import (
    build_image_part,
    build_text_part,
    prepare_image_part,
)


SYSTEM_PROMPT = """你是化学实验图像分析与纠错助手。
你必须只输出中文，重点关注装置连接、部件位置、操作顺序、安全风险和会导致实验结果错误的差异。
对图片对比时，要优先识别真实装置差异，忽略仅由拍摄角度、远近、裁剪范围、光线和透视造成的表面差异。
避免泛泛而谈，不要描述无关背景。"""


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

    def compare_images(self, correct_image_bytes: bytes, wrong_image_bytes: bytes) -> str:
        return self.compare_images_with_metrics(correct_image_bytes, wrong_image_bytes)[0]

    def compare_images_with_metrics(
        self, correct_image_bytes: bytes, wrong_image_bytes: bytes
    ) -> tuple[str, dict[str, Any]]:
        prompt = """请比较两张化学实验图片，找出图A和图B之间真正存在的装置或操作差异。

注意：
1. 此步骤不要判断哪一张正确、哪一张错误。
2. 只描述“真实不同”，不要把拍摄角度变化、镜头远近、构图偏移、裁剪范围不同、遮挡轻微变化、光照差异当成不同点。
3. 如果两张图本质上是同一装置、只是视角不同，要明确说明“未发现可靠的关键差异”。
4. 优先关注实验装置连接、部件位置、导线或玻璃仪器连接方式、液面/电极/接口位置、开关阀门状态、样品或光路位置等关键结构差异。

请按下面格式输出：
真实不同点：
- ...
- ...


总结：
用一句话概括两张图最核心的真实差别；如果没有足够可靠的差别，就写“未发现可靠的关键差异”。"""

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
        model_started_at = time.perf_counter()
        result = self.client.chat(messages)
        model_request_ms = (time.perf_counter() - model_started_at) * 1000
        metrics = {
            "image_a": correct_metrics.as_dict(),
            "image_b": wrong_metrics.as_dict(),
            "preprocess_total_ms": round(
                correct_metrics.preprocess_ms + wrong_metrics.preprocess_ms, 1
            ),
            "model_request_ms": round(model_request_ms, 1),
        }
        return result, metrics

    def analyze_main_error(self, experiment_requirements: str, difference_summary: str) -> str:
        return self.analyze_main_error_with_metrics(
            experiment_requirements,
            difference_summary,
        )[0]

    def analyze_main_error_with_metrics(
        self, experiment_requirements: str, difference_summary: str
    ) -> tuple[str, dict[str, Any]]:
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

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        model_started_at = time.perf_counter()
        result = self.client.chat(messages)
        model_request_ms = (time.perf_counter() - model_started_at) * 1000
        return result, {"model_request_ms": round(model_request_ms, 1)}
