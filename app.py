from __future__ import annotations

import time
import uuid
from functools import lru_cache

from flask import Flask, jsonify, render_template, request

from chemistry_ai import ChemistryExperimentAI
from openrouter_client import OpenRouterError, load_openrouter_config

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

ANALYSIS_TTL_SECONDS = 60 * 60
analysis_store: dict[str, dict[str, str | float]] = {}


@lru_cache(maxsize=1)
def get_ai() -> ChemistryExperimentAI:
    return ChemistryExperimentAI()


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
    return load_openrouter_config(require_api_key=False).model


@app.get("/")
def index():
    return render_template("index.html", model_name=current_model_name())


@app.post("/api/compare-images")
def compare_images():
    cleanup_store()
    try:
        correct_image = read_uploaded_image("correct_image")
        wrong_image = read_uploaded_image("wrong_image")

        difference_summary = get_ai().compare_images(correct_image, wrong_image)
        session_id = uuid.uuid4().hex
        analysis_store[session_id] = {
            "difference_summary": difference_summary,
            "created_at": time.time(),
        }
        return jsonify(
            {
                "session_id": session_id,
                "difference_summary": difference_summary,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OpenRouterError as exc:
        return jsonify({"error": str(exc)}), 502


@app.post("/api/analyze-error")
def analyze_error():
    cleanup_store()
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
        final_result = get_ai().analyze_main_error(
            experiment_requirements,
            str(stored["difference_summary"]),
        )
        return jsonify({"final_result": final_result})
    except OpenRouterError as exc:
        return jsonify({"error": str(exc)}), 502


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
