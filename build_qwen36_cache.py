from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from qwen36_cache_mode import Qwen36GuideCacheClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 qwen3.6-flash 离线处理 PDF 并创建可复用 guide cache。")
    parser.add_argument("--pdf", required=True, help="实验指导 PDF 路径")
    parser.add_argument("--cache-name", help="可选，逻辑 cache_id")
    parser.add_argument(
        "--no-warm-remote",
        action="store_true",
        help="只生成本地 guide artifact，不预热远端显式 cache。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = Qwen36GuideCacheClient()
    artifact = client.build_cache_from_pdf(
        args.pdf,
        cache_name=args.cache_name,
        warm_remote_cache=not args.no_warm_remote,
    )
    print(json.dumps(asdict(artifact), ensure_ascii=False, indent=2))
    print("\n" + "=" * 80)
    print("Cache 预热信息")
    if artifact.remote_cache_usage:
        print(f"模型: {artifact.model}")
        print(f"cache_id: {artifact.cache_id}")
        print(
            "显式缓存创建 tokens: "
            f"{artifact.remote_cache_usage.get('cache_creation_input_tokens')}"
        )
        print(
            "本次命中 cached tokens: "
            f"{artifact.remote_cache_usage.get('cached_tokens')}"
        )
        print(f"总 tokens: {artifact.remote_cache_usage.get('total_tokens')}")
    else:
        print("未执行远端 cache 预热。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
