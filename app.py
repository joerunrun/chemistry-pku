from __future__ import annotations

import argparse
import time
import uuid
from functools import lru_cache

from flask import Flask, jsonify, render_template, request

from chemistry_ai import ChemistryExperimentAI
from client_factory import create_chat_client, get_provider_model_name
from openrouter_client import OpenRouterError

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

ANALYSIS_TTL_SECONDS = 60 * 60
analysis_store: dict[str, dict[str, str | float]] = {}
ACTIVE_PROVIDER = "openrouter"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="化学实验纠错 Web")
    parser.add_argument(
        "--provider",
        choices=["openrouter", "ali"],
        default="openrouter",
        help="选择底层模型提供方。",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


@lru_cache(maxsize=2)
def get_ai(provider: str) -> ChemistryExperimentAI:
    return ChemistryExperimentAI(create_chat_client(provider))


def cleanup_store() -> None:
    now = time.time()
    expired_keys = [
        session_id
        for session_id, data in analysis_store.items()
        if now - float(data["created_at"]) > ANALYSIS_TTL_SECONDS
    ]
    for session_id in expired_keys:
        analysis_store.pop(session_id, None)


def read_uploaded_image(field_name: str) -> bytes:
    uploaded = request.files.get(field_name)
    if uploaded is None or not uploaded.filename:
        raise ValueError(f"缺少上传文件: {field_name}")
    if not (uploaded.mimetype or "").startswith("image/"):
        raise ValueError(f"{field_name} 不是图片文件。")

    image_bytes = uploaded.read()
    if not image_bytes:
        raise ValueError(f"{field_name} 内容为空。")
    return image_bytes


def current_model_name() -> str:
    return get_provider_model_name(ACTIVE_PROVIDER)


@app.get("/")
def index():
    return render_template(
        "index.html",
        model_name=current_model_name(),
        provider_name=ACTIVE_PROVIDER,
    )


@app.post("/api/compare-images")
def compare_images():
    cleanup_store()
    request_started_at = time.perf_counter()
    try:
        correct_image = read_uploaded_image("correct_image")
        wrong_image = read_uploaded_image("wrong_image")

        difference_summary, timings = get_ai(ACTIVE_PROVIDER).compare_images_with_metrics(
            correct_image, wrong_image
        )
        timings["total_ms"] = round((time.perf_counter() - request_started_at) * 1000, 1)
        session_id = uuid.uuid4().hex
        analysis_store[session_id] = {
            "difference_summary": difference_summary,
            "created_at": time.time(),
        }
        print(
            f"[compare-images] provider={ACTIVE_PROVIDER} "
            f"preprocess={timings['preprocess_total_ms']}ms "
            f"model={timings['model_request_ms']}ms "
            f"total={timings['total_ms']}ms"
        )
        return jsonify(
            {
                "session_id": session_id,
                "difference_summary": difference_summary,
                "timings": timings,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OpenRouterError as exc:
        return jsonify({"error": str(exc)}), 502


@app.post("/api/analyze-error")
def analyze_error():
    cleanup_store()
    request_started_at = time.perf_counter()
    payload = request.get_json(silent=True) or {}

    session_id = str(payload.get("session_id", "")).strip()
    experiment_requirements = str(payload.get("experiment_requirements", "")).strip()

    if not session_id:
        return jsonify({"error": "缺少 session_id，请先完成图片对比。"}), 400
    if not experiment_requirements:
        return jsonify({"error": "请填写实验要求文字。"}), 400

    stored = analysis_store.get(session_id)
    if stored is None:
        return jsonify({"error": "当前会话已失效，请重新上传图片。"}), 400

    try:
        final_result, timings = get_ai(ACTIVE_PROVIDER).analyze_main_error_with_metrics(
            experiment_requirements,
            str(stored["difference_summary"]),
        )
        timings["input_chars"] = len(experiment_requirements)
        timings["difference_chars"] = len(str(stored["difference_summary"]))
        timings["total_ms"] = round((time.perf_counter() - request_started_at) * 1000, 1)
        print(
            f"[analyze-error] provider={ACTIVE_PROVIDER} "
            f"model={timings['model_request_ms']}ms "
            f"total={timings['total_ms']}ms "
            f"input_chars={timings['input_chars']} "
            f"difference_chars={timings['difference_chars']}"
        )
        return jsonify({"final_result": final_result, "timings": timings})
    except OpenRouterError as exc:
        return jsonify({"error": str(exc)}), 502


if __name__ == "__main__":
    args = parse_args()
    ACTIVE_PROVIDER = args.provider
    app.run(host=args.host, port=args.port, debug=args.debug)
