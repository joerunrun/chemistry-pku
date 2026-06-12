"""并发压测脚本：模拟 N 台眼镜同时使用服务器。

用法：
    python pipeline/tools/load_test_glasses.py --url http://localhost:5080 --clients 10
    python pipeline/tools/load_test_glasses.py --url http://192.168.1.100 --clients 5 --scenario safety

场景：
    health    - 健康检查（最轻量）
    safety    - 安全检查（上传图片 + VLM）
    experiment - 实验纠错（上传图片 + CV + VLM）
    mixed     - 混合（50% safety + 50% experiment）
    video     - 视频流拉取
"""
from __future__ import annotations

import argparse
import io
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

DEFAULT_URL = "http://localhost:5080"
DEFAULT_CLIENTS = 10
DEFAULT_ROUNDS = 3


def make_test_image() -> bytes:
    try:
        import numpy as np
        img = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        import cv2
        _, buf = cv2.imencode(".jpg", img)
        return buf.tobytes()
    except ImportError:
        from PIL import Image
        img = Image.new("RGB", (640, 480), color=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()


def run_health(base_url: str) -> dict:
    start = time.perf_counter()
    r = requests.get(f"{base_url}/api/glasses/health", timeout=30)
    elapsed = (time.perf_counter() - start) * 1000
    return {"scenario": "health", "status": r.status_code, "elapsed_ms": elapsed, "ok": r.status_code == 200}


def run_safety(base_url: str, image_bytes: bytes) -> dict:
    start = time.perf_counter()
    r = requests.post(
        f"{base_url}/api/glasses/jobs",
        files={"image": ("test.jpg", image_bytes, "image/jpeg")},
        data={"mode": "safety", "experiment_type": "other"},
        timeout=30,
    )
    if r.status_code != 202:
        elapsed = (time.perf_counter() - start) * 1000
        return {"scenario": "safety", "status": r.status_code, "elapsed_ms": elapsed, "ok": False, "error": r.text[:200]}

    job_id = r.json().get("job_id")
    while True:
        time.sleep(1)
        poll = requests.get(f"{base_url}/api/glasses/jobs/{job_id}", timeout=30)
        data = poll.json()
        status = data.get("status", "")
        if status in ("succeeded", "failed", "expired"):
            elapsed = (time.perf_counter() - start) * 1000
            return {
                "scenario": "safety",
                "status": poll.status_code,
                "job_status": status,
                "elapsed_ms": elapsed,
                "ok": status == "succeeded",
                "error": data.get("error"),
            }
        if (time.perf_counter() - start) > 120:
            elapsed = (time.perf_counter() - start) * 1000
            return {"scenario": "safety", "status": 0, "elapsed_ms": elapsed, "ok": False, "error": "timeout 120s"}


def run_experiment(base_url: str, image_bytes: bytes) -> dict:
    start = time.perf_counter()
    r = requests.post(
        f"{base_url}/api/glasses/jobs",
        files={"image": ("test.jpg", image_bytes, "image/jpeg")},
        data={"mode": "experiment", "experiment_type": "distillation"},
        timeout=30,
    )
    if r.status_code != 202:
        elapsed = (time.perf_counter() - start) * 1000
        return {"scenario": "experiment", "status": r.status_code, "elapsed_ms": elapsed, "ok": False, "error": r.text[:200]}

    job_id = r.json().get("job_id")
    while True:
        time.sleep(1)
        poll = requests.get(f"{base_url}/api/glasses/jobs/{job_id}", timeout=30)
        data = poll.json()
        status = data.get("status", "")
        if status in ("succeeded", "failed", "expired"):
            elapsed = (time.perf_counter() - start) * 1000
            return {
                "scenario": "experiment",
                "status": poll.status_code,
                "job_status": status,
                "elapsed_ms": elapsed,
                "ok": status == "succeeded",
                "error": data.get("error"),
            }
        if (time.perf_counter() - start) > 180:
            elapsed = (time.perf_counter() - start) * 1000
            return {"scenario": "experiment", "status": 0, "elapsed_ms": elapsed, "ok": False, "error": "timeout 180s"}


def run_video(base_url: str) -> dict:
    start = time.perf_counter()
    r = requests.get(f"{base_url}/api/glasses/demo/videos", timeout=30)
    if r.status_code != 200 or not r.json().get("videos"):
        elapsed = (time.perf_counter() - start) * 1000
        return {"scenario": "video", "status": r.status_code, "elapsed_ms": elapsed, "ok": False, "error": "no videos available"}

    video_url = r.json()["videos"][0]["stream_url"]
    if video_url.startswith("/"):
        video_url = base_url + video_url
    chunk_r = requests.get(video_url, stream=True, timeout=30)
    downloaded = 0
    for chunk in chunk_r.iter_content(chunk_size=64 * 1024):
        downloaded += len(chunk)
        if downloaded >= 2 * 1024 * 1024:
            break
    chunk_r.close()
    elapsed = (time.perf_counter() - start) * 1000
    return {"scenario": "video", "status": chunk_r.status_code, "elapsed_ms": elapsed, "ok": chunk_r.status_code == 200, "bytes": downloaded}


def run_one(base_url: str, scenario: str, image_bytes: bytes) -> dict:
    if scenario == "health":
        return run_health(base_url)
    elif scenario == "safety":
        return run_safety(base_url, image_bytes)
    elif scenario == "experiment":
        return run_experiment(base_url, image_bytes)
    elif scenario == "video":
        return run_video(base_url)
    elif scenario == "mixed":
        chosen = random.choice(["safety", "experiment"])
        return run_one(base_url, chosen, image_bytes)
    else:
        return {"scenario": scenario, "ok": False, "error": "unknown scenario"}


def main():
    parser = argparse.ArgumentParser(description="Chemistry pipeline load test")
    parser.add_argument("--url", default=DEFAULT_URL, help="Server base URL")
    parser.add_argument("--clients", type=int, default=DEFAULT_CLIENTS, help="Number of concurrent clients")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="Number of rounds per client")
    parser.add_argument("--scenario", default="safety", choices=["health", "safety", "experiment", "mixed", "video"])
    args = parser.parse_args()

    print(f"Target: {args.url}")
    print(f"Clients: {args.clients}, Rounds: {args.rounds}, Scenario: {args.scenario}")
    print(f"Generating test image...")
    image_bytes = make_test_image()
    print(f"Test image: {len(image_bytes)} bytes")
    print("-" * 60)

    all_results: list[dict] = []

    for round_num in range(1, args.rounds + 1):
        print(f"\n--- Round {round_num}/{args.rounds} ---")
        with ThreadPoolExecutor(max_workers=args.clients) as executor:
            futures = [
                executor.submit(run_one, args.url, args.scenario, image_bytes)
                for _ in range(args.clients)
            ]
            results = [f.result() for f in as_completed(futures)]

        all_results.extend(results)

        ok_count = sum(1 for r in results if r.get("ok"))
        fail_count = len(results) - ok_count
        times = [r["elapsed_ms"] for r in results if "elapsed_ms" in r]

        print(f"  Success: {ok_count}/{len(results)}, Failed: {fail_count}")
        if times:
            print(f"  Avg: {statistics.mean(times):.0f}ms, "
                  f"Min: {min(times):.0f}ms, "
                  f"Max: {max(times):.0f}ms, "
                  f"P95: {sorted(times)[int(len(times) * 0.95)]:.0f}ms")

        for r in results:
            if not r.get("ok"):
                print(f"  FAIL: {r.get('error', r.get('job_status', 'unknown'))}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total = len(all_results)
    ok_total = sum(1 for r in all_results if r.get("ok"))
    all_times = [r["elapsed_ms"] for r in all_results if "elapsed_ms" in r]
    print(f"Total requests: {total}")
    print(f"Success rate: {ok_total}/{total} ({ok_total/total*100:.1f}%)")
    if all_times:
        print(f"Response time:")
        print(f"  Avg:  {statistics.mean(all_times):.0f}ms")
        print(f"  Min:  {min(all_times):.0f}ms")
        print(f"  Max:  {max(all_times):.0f}ms")
        print(f"  P50:  {sorted(all_times)[int(len(all_times) * 0.5)]:.0f}ms")
        print(f"  P95:  {sorted(all_times)[int(len(all_times) * 0.95)]:.0f}ms")
        print(f"  P99:  {sorted(all_times)[min(int(len(all_times) * 0.99), len(all_times)-1)]:.0f}ms")


if __name__ == "__main__":
    main()
