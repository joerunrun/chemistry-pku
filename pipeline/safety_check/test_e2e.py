"""端到端测试：选一张参考图当查询，检索 top-3，发给 VLM 分析。"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from pipeline.safety_check.retriever import retrieve, reload_db
from pipeline.safety_check.vlm_check import analyze

if __name__ == "__main__":
    # 用库中一张图测试
    img_path = Path(__file__).resolve().parent / "data/images/image50.jpeg"
    with open(img_path, "rb") as f:
        query_bytes = f.read()

    print("=== Step 1: 检索 ===")
    reload_db()
    t0 = time.perf_counter()
    results = retrieve(query_bytes, top_k=3)
    print(f"  Done in {(time.perf_counter() - t0) * 1000:.0f} ms")
    for i, r in enumerate(results):
        print(f"  #{i+1}: {r['image_file']}  score={r['score']:.1f}")
        print(f"       {r['rectification'][:100]}")
    print()

    print("=== Step 2: VLM 分析 ===")
    # 需要 API key，如果没配就跳过
    import os
    if not os.getenv("OPENROUTER_API_KEY"):
        print("  SKIP: OPENROUTER_API_KEY not set")
        sys.exit(0)

    t0 = time.perf_counter()
    text, vlm_ms = analyze(query_bytes, results, provider="openrouter")
    total = (time.perf_counter() - t0) * 1000

    print(f"  VLM: {vlm_ms:.0f} ms")
    print(f"  Total: {total:.0f} ms")
    print()
    print("=== VLM 输出 ===")
    print(text)