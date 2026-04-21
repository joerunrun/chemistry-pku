from __future__ import annotations

import argparse
import json
from pathlib import Path

from qwen36_cache_mode import Qwen36GuideCacheClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 qwen3.6-flash + guide cache 做单图快速纠错。")
    parser.add_argument("--cache-id", required=True, help="build_qwen36_cache.py 输出的 cache_id")
    parser.add_argument("--image", required=True, help="错误图片路径")
    parser.add_argument(
        "--refresh-if-expired",
        action="store_true",
        help="如果远端显式 cache 超过约 5 分钟，先自动重建。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_bytes = Path(args.image).read_bytes()
    client = Qwen36GuideCacheClient()
    result, usage = client.correct_image_with_cache(
        args.cache_id,
        image_bytes,
        refresh_if_expired=args.refresh_if_expired,
    )
    print(result)
    print("\n" + "=" * 80)
    print(json.dumps(usage, ensure_ascii=False, indent=2))
    print("\n" + "=" * 80)
    print("Cache 命中信息")
    print(f"cache_id: {usage.get('cache_id')}")
    print(f"cache_expired: {usage.get('cache_expired')}")
    print(f"cache_hit: {usage.get('cache_hit')}")
    print(f"命中 cached tokens: {usage.get('cached_tokens')}")
    print(f"首次创建 cache tokens: {usage.get('cache_creation_input_tokens')}")
    print(f"总 tokens: {usage.get('total_tokens')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
