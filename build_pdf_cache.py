from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from pdf_cache_mode import PdfCacheModeClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="离线解析 PDF 并创建 cache artifact。")
    parser.add_argument("--pdf", required=True, help="PDF 路径")
    parser.add_argument("--cache-name", help="可选，自定义 cache 名称")
    parser.add_argument(
        "--no-warm-remote",
        action="store_true",
        help="只创建本地 artifact，不预热远端 explicit cache。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = PdfCacheModeClient()
    artifact = client.build_cache_from_pdf(
        args.pdf,
        cache_name=args.cache_name,
        warm_remote_cache=not args.no_warm_remote,
    )
    print(json.dumps(asdict(artifact), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
