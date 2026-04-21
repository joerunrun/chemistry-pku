from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_cache_mode import PdfCacheModeClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于本地 PDF cache artifact 做单图快速纠错。")
    parser.add_argument("--cache-id", required=True, help="build_pdf_cache.py 输出的 cache_id")
    parser.add_argument("--image", required=True, help="错误图片路径")
    parser.add_argument(
        "--refresh-if-expired",
        action="store_true",
        help="如果远端 explicit cache 超过约 5 分钟，先自动重新预热。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_bytes = Path(args.image).read_bytes()
    client = PdfCacheModeClient()
    result, usage = client.correct_image_with_cache(
        args.cache_id,
        image_bytes,
        refresh_if_expired=args.refresh_if_expired,
    )
    print(result)
    print("\n" + "=" * 80)
    print(json.dumps(usage, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
