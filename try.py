from __future__ import annotations

import argparse
from pathlib import Path

from chemistry_ai import ChemistryExperimentAI
from openrouter_client import (
    OpenRouterError,
    build_image_part,
    build_text_part,
    load_openrouter_config,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="最小化验证 OpenRouter + Gemini 图片理解链路。"
    )
    parser.add_argument("--image", help="单张图片路径，用于描述图片内容。")
    parser.add_argument("--correct", help="正确示范图片路径。")
    parser.add_argument("--wrong", help="错误示范图片路径。")
    parser.add_argument(
        "--prompt",
        help="自定义提示词。未提供时，单图走描述，多图走对比。",
    )
    return parser.parse_args()


def read_bytes(image_path: str) -> bytes:
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到图片: {path}")
    return path.read_bytes()


def main() -> int:
    args = parse_args()
    config = load_openrouter_config()
    ai = ChemistryExperimentAI()

    try:
        if args.correct and args.wrong:
            correct_bytes = read_bytes(args.correct)
            wrong_bytes = read_bytes(args.wrong)
            if args.prompt:
                result = ai.client.chat(
                    [
                        {
                            "role": "system",
                            "content": "你是化学实验图像分析助手，只输出中文。",
                        },
                        {
                            "role": "user",
                            "content": [
                                build_text_part(args.prompt),
                                build_text_part("第一张图片：正确示范"),
                                build_image_part(correct_bytes, config=ai.client.config),
                                build_text_part("第二张图片：错误示范"),
                                build_image_part(wrong_bytes, config=ai.client.config),
                            ],
                        },
                    ]
                )
            else:
                result = ai.compare_images(correct_bytes, wrong_bytes)
        elif args.image:
            image_bytes = read_bytes(args.image)
            result = ai.describe_image(image_bytes, args.prompt)
        else:
            raise ValueError("请提供 `--image`，或同时提供 `--correct` 和 `--wrong`。")

        print(f"当前模型: {config.model}")
        print("=" * 80)
        print(result)
        return 0
    except (FileNotFoundError, ValueError, OpenRouterError) as exc:
        print(f"错误: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
