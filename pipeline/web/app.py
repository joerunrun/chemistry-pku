from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, jsonify, render_template, request

from pipeline.common.cv_wrapper import (
    CVDetectionParams,
    build_reference_result,
    decode_image,
    list_available_references,
    load_reference_image,
)
from pipeline.common.timing import StepTimer, TimingRecord
from pipeline.common.vlm_client import PROVIDER_MODELS


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/references")
def references():
    try:
        refs = list_available_references()
        return jsonify({"references": refs})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/models")
def models():
    return jsonify({"provider_models": PROVIDER_MODELS})


@app.post("/api/run")
def run_pipeline():
    request_started_at = time.perf_counter()
    timing = TimingRecord()

    try:
        uploaded = request.files.get("wrong_image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "缺少待纠错图片。"}), 400

        with StepTimer(timing, "1_图片解码"):
            wrong_image = decode_image(uploaded.read())

        experiment_requirements = request.form.get("experiment_requirements", "").strip()
        if not experiment_requirements:
            return jsonify({"error": "请填写实验要求。"}), 400

        provider = request.form.get("provider", "openrouter").strip()
        model = request.form.get("model", "").strip() or None
        approach = request.form.get("approach", "both").strip()
        correct_source = request.form.get("correct_source", "database").strip()

        cv_params = CVDetectionParams(
            max_side=int(request.form.get("max_side", "1600")),
            lowe_ratio=float(request.form.get("lowe_ratio", "0.75")),
            min_good_matches=int(request.form.get("min_good_matches", "3")),
            min_inliers=int(request.form.get("min_inliers", "2")),
            min_inlier_ratio=float(request.form.get("min_inlier_ratio", "0.08")),
            top_k_per_label=int(request.form.get("top_k_per_label", "6")),
            overlap_weight=float(request.form.get("overlap_weight", "0.75")),
            feature=request.form.get("feature", "akaze").strip(),
        )

        from pipeline.common.cv_wrapper import detect_wrong_image

        with StepTimer(timing, "2_CV检测"):
            wrong_result = detect_wrong_image(wrong_image, cv_params)

        # --- 正确参考图：两种来源 ---
        if correct_source == "upload":
            uploaded_correct = request.files.get("correct_image")
            if uploaded_correct is None or not uploaded_correct.filename:
                return jsonify({"error": "选择了上传正确图，但未上传文件。"}), 400

            with StepTimer(timing, "3_解码正确图"):
                correct_annotated = decode_image(uploaded_correct.read())

            # 上传的已标注图：不做画框，直接用
            # 构造一个空的 correct_result 让 label_summary 知道参考图"全部存在"
            correct_result = _make_full_reference_result()
            label_summary = _build_label_summary_from_results(correct_result, wrong_result)

        else:
            reference_id = request.form.get("reference_id", "").strip()
            refs = list_available_references()
            if not reference_id and refs:
                reference_id = refs[0]["id"]
            if not reference_id:
                return jsonify({"error": "没有可用的参考图。"}), 400

            with StepTimer(timing, "3_加载参考图"):
                correct_image = load_reference_image(reference_id)
                correct_result = build_reference_result(reference_id)

            # 需要画框
            from pipeline.common.image_builder import build_annotated_pair
            from pipeline.common.cv_wrapper import annotate_image

            correct_annotated = annotate_image(correct_image, correct_result)
            label_summary = _build_label_summary_from_results(correct_result, wrong_result)

        # --- 构建标注图 ---
        with StepTimer(timing, "4_构建标注图"):
            from pipeline.common.image_builder import build_cv_structured_summary
            from pipeline.common.cv_wrapper import annotate_image, encode_annotated_image

            wrong_annotated = annotate_image(wrong_image, wrong_result)
            correct_data_url = encode_annotated_image(correct_annotated)
            wrong_data_url = encode_annotated_image(wrong_annotated)

            cv_summary = build_cv_structured_summary(label_summary, correct_is_uploaded=(correct_source == "upload"))

            import cv2
            _, c_jpg = cv2.imencode(".jpg", correct_annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            _, w_jpg = cv2.imencode(".jpg", wrong_annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            jpeg_pair = (c_jpg.tobytes(), w_jpg.tobytes())

        result: dict = {
            "label_summary": label_summary,
            "correct_data_url": correct_data_url,
            "wrong_data_url": wrong_data_url,
        }

        need_a = approach in ("a", "both")
        need_b = approach in ("b", "both")

        if need_a:
            from pipeline.approach_a.pipeline_a import run_pipeline_a

            with StepTimer(timing, "5A_VLM调用"):
                a_result = run_pipeline_a(
                    experiment_requirements=experiment_requirements,
                    vlm_provider=provider,
                    vlm_model=model,
                    label_summary=label_summary,
                    cv_summary=cv_summary,
                    prebuilt_jpeg=jpeg_pair,
                )
            result["approach_a"] = {
                "analysis_text": a_result.analysis_text,
            }

        if need_b:
            with StepTimer(timing, "5B1_VLM找不同"):
                diff_text = _run_b_step1(
                    vlm_provider=provider,
                    vlm_model=model,
                    cv_summary=cv_summary,
                    prebuilt_jpeg=jpeg_pair,
                )

            with StepTimer(timing, "5B2_VLM分析"):
                analysis_text = _run_b_step2(
                    diff_text=diff_text,
                    cv_summary=cv_summary,
                    experiment_requirements=experiment_requirements,
                    vlm_provider=provider,
                    vlm_model=model,
                )

            result["approach_b"] = {
                "diff_text": diff_text,
                "analysis_text": analysis_text,
            }

        timing.record("总计", (time.perf_counter() - request_started_at) * 1000)
        result["timings"] = timing.to_dict()
        return jsonify(result)

    except Exception as exc:
        return jsonify({"error": f"Pipeline 失败: {exc}"}), 500


def _make_full_reference_result() -> dict:
    """上传已标注图时，假设所有 6 个装置在参考图中都存在（由人工标注保证）。"""
    from pipeline.common.cv_wrapper import get_component_info
    boxes = {}
    detections = {}
    for comp in get_component_info():
        # 标记为"存在"但无具体 box 坐标（因为上传图已经画好框了）
        boxes[comp["label"]] = [0, 0, 1, 1]  # sentinel: non-empty means "present"
        detections[comp["label"]] = {"score": None}
    return {"boxes": boxes, "detections": detections}


def _build_label_summary_from_results(
    correct_result: dict,
    wrong_result: dict,
) -> dict:
    from pipeline.common.cv_wrapper import get_component_info

    wrong_boxes = wrong_result.get("boxes", {})
    wrong_detections = wrong_result.get("detections", {})
    correct_boxes = correct_result.get("boxes", {})

    label_summary: dict = {}
    for comp in get_component_info():
        label = comp["label"]
        c_box = correct_boxes.get(label, [])
        w_box = wrong_boxes.get(label, [])
        label_summary[label] = {
            "name": comp["name"],
            "color": comp["color"],
            "in_correct": bool(c_box),
            "in_wrong": bool(w_box),
            "correct_box": c_box if c_box != [0, 0, 1, 1] else [],
            "wrong_box": w_box,
            "wrong_score": wrong_detections.get(label, {}).get("score"),
            "wrong_raw_score": wrong_detections.get(label, {}).get("raw_score"),
            "wrong_failure_reason": wrong_detections.get(label, {}).get("failure_reason"),
        }
    return label_summary


def _run_b_step1(
    *,
    vlm_provider: str,
    vlm_model: str | None,
    cv_summary: str,
    prebuilt_jpeg: tuple[bytes, bytes],
) -> str:
    from pipeline.approach_b.pipeline_b import build_diff_messages
    from pipeline.common.vlm_client import call_vlm, create_vlm

    client = create_vlm(vlm_provider, vlm_model)
    correct_jpeg_bytes, wrong_jpeg_bytes = prebuilt_jpeg
    messages = build_diff_messages(correct_jpeg_bytes, wrong_jpeg_bytes, cv_summary, client)
    result, _ = call_vlm(client, messages)
    return result


def _run_b_step2(
    *,
    diff_text: str,
    cv_summary: str,
    experiment_requirements: str,
    vlm_provider: str,
    vlm_model: str | None,
) -> str:
    from pipeline.approach_b.pipeline_b import build_analysis_messages
    from pipeline.common.vlm_client import call_vlm, create_vlm

    client = create_vlm(vlm_provider, vlm_model)
    messages = build_analysis_messages(diff_text, cv_summary, experiment_requirements)
    result, _ = call_vlm(client, messages)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chemistry Pipeline Web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5080)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
