"""安全检查 Web：上传学生图 → 检索 top-K 参考案例 → VLM 分析。默认 Gemini。"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, jsonify, render_template, request
from pipeline.common.vlm_client import DEFAULT_VLM_MODEL, DEFAULT_VLM_PROVIDER
from pipeline.safety_check.retriever import retrieve, reload_db
from pipeline.safety_check.vlm_check import analyze

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/check")
def check():
    request_started = time.perf_counter()
    timings: dict[str, float] = {}

    try:
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "请上传图片。"}), 400

        image_bytes = uploaded.read()
        top_k = int(request.form.get("top_k", "1"))
        provider = request.form.get("provider", DEFAULT_VLM_PROVIDER).strip()
        model = request.form.get("model", "").strip() or DEFAULT_VLM_MODEL

        # Step 1: 检索
        t0 = time.perf_counter()
        results = retrieve(image_bytes, top_k=top_k)
        timings["检索"] = round((time.perf_counter() - t0) * 1000, 1)

        if not results:
            return jsonify({
                "error": "未匹配到相似参考案例。",
                "timings": timings,
            })

        # 序列化结果（去掉 JPEG bytes，编码为 data URL）
        import base64
        ref_cases = []
        for r in results:
            jpeg_b64 = base64.b64encode(r["image_jpeg"]).decode("ascii") if r.get("image_jpeg") else ""
            ref_cases.append({
                "entry_index": r["entry_index"],
                "image_file": r["image_file"],
                "image_data_url": f"data:image/jpeg;base64,{jpeg_b64}" if jpeg_b64 else "",
                "title": r["title"],
                "rectification": r["rectification"],
                "score": r["score"],
            })

        # Step 2: VLM 分析
        t0 = time.perf_counter()
        analysis_text, vlm_ms = analyze(
            image_bytes, results,
            provider=provider, model=model,
        )
        timings["VLM分析"] = round((time.perf_counter() - t0) * 1000, 1)

        timings["总计"] = round((time.perf_counter() - request_started) * 1000, 1)

        return jsonify({
            "ref_cases": ref_cases,
            "analysis_text": analysis_text,
            "timings": timings,
        })

    except Exception as exc:
        return jsonify({"error": f"检查失败: {exc}"}), 500


def parse_args():
    p = argparse.ArgumentParser(description="Safety Check Web")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5090)
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    reload_db()
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
