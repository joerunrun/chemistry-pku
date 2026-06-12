"""化学实验 Pipeline Web：检测/标注后立刻显示结果，VLM 分析异步进行。"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import mimetypes
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from threading import Lock, Semaphore
from typing import Any

import cv2
import numpy as np

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, jsonify, render_template, request, send_file, url_for

from pipeline.common.cv_wrapper import (
    CVDetectionParams,
    build_reference_result,
    decode_image,
    list_available_references,
    load_reference_image,
)
from pipeline.common.timing import StepTimer, TimingRecord
from pipeline.common.vlm_client import DEFAULT_VLM_MODEL, DEFAULT_VLM_PROVIDER, PROVIDER_MODELS
from pipeline.experiments import get_experiment_config
from pipeline.resource_paths import runtime_asset_path

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024

# 内存存储：session_id → detection 结果
_detect_store: dict[str, dict] = {}
_TTL_SECONDS = 3600
_GLASSES_JOB_TTL_SECONDS = 30 * 60
_GLASSES_DEFAULT_PROVIDER = DEFAULT_VLM_PROVIDER
_GLASSES_DEFAULT_MODEL = DEFAULT_VLM_MODEL
_GLASSES_JOB_WORKERS = int(os.environ.get("CHEM_GLASSES_JOB_WORKERS", "4"))
_GLASSES_MAX_PENDING = int(os.environ.get("CHEM_GLASSES_MAX_PENDING_JOBS", "50"))
_GLASSES_EXPERIMENTS = {
    "distillation": "减压蒸馏",
    "photometer": "光度计",
    "electrolysis": "电解实验",
    "other": "其它测试",
}
_DEMO_VIDEO_EXPERIMENTS = ("distillation",)
_ALLOWED_DEMO_VIDEO_SUFFIXES = {".mp4"}
_ALLOWED_RECORD_SUFFIXES = {".mp4", ".wav", ".m4a", ".aac", ".mp3"}
_TEACHER_SPEAKER_THRESHOLD = 0.5
_TEACHER_SPEAKER_MIN_SPEECH_MS = 200
_TEACHER_DEFAULT_EMBEDDING_MODEL = runtime_asset_path(
    "speaker_models",
    "3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx",
)
_teacher_speaker_lock = Lock()
_teacher_reference_audio: bytes | None = None
_teacher_reference_created_at: float | None = None
_glasses_executor = ThreadPoolExecutor(
    max_workers=_GLASSES_JOB_WORKERS,
    thread_name_prefix="glasses-job",
)
_glasses_jobs: dict[str, dict[str, Any]] = {}
_glasses_lock = Lock()
_vlm_semaphore = Semaphore(int(os.environ.get("CHEM_VLM_CONCURRENCY", "4")))
_omni_semaphore = Semaphore(int(os.environ.get("CHEM_OMNI_CONCURRENCY", "4")))
EXPERIMENT_UI_CONFIG_PATH = Path(__file__).resolve().parent.parent / "experiment_ui_config.json"


def _cleanup_store():
    now = time.time()
    expired = [k for k, v in _detect_store.items()
               if now - v.get("_created", 0) > _TTL_SECONDS]
    for k in expired:
        _detect_store.pop(k, None)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/admin")
def admin_index():
    return render_template("admin.html")


@app.get("/api/references")
def references():
    try:
        experiment_type = request.args.get("experiment_type", "distillation").strip() or "distillation"
        cfg = get_experiment_config(experiment_type)
        if cfg is None:
            return jsonify({"error": f"未知实验类型: {experiment_type}"}), 400
        if not getattr(cfg, "HAS_REFERENCE_DB", True):
            return jsonify({"references": []})
        return jsonify({"references": _list_experiment_references(cfg)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/models")
def models():
    return jsonify({"provider_models": PROVIDER_MODELS})


@app.get("/api/glasses/health")
def glasses_health():
    return jsonify({
        "status": "ok",
        "default_provider": _GLASSES_DEFAULT_PROVIDER,
        "default_model": _GLASSES_DEFAULT_MODEL,
        "modes": ["experiment", "safety"],
        "features": [
            "experiment",
            "safety",
            "qa",
            "qa_audio",
            "voice_intent",
            "tts_audio",
            "demo_video",
            "teacher_speaker_verify",
        ],
        "experiments": [
            {"value": key, "label": label}
            for key, label in _GLASSES_EXPERIMENTS.items()
        ],
    })


@app.post("/api/glasses/teacher/enroll")
def glasses_teacher_enroll():
    try:
        uploaded = request.files.get("audio")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "audio is required."}), 400

        audio_bytes = uploaded.read()
        if not audio_bytes:
            return jsonify({"error": "audio is empty."}), 400

        model_path = _teacher_embedding_model_path()
        from pipeline.speaker_verify.compare import compare_speakers

        # Self-compare once to validate that the reference clip contains speech
        # and that the speaker embedding model can load before entering the loop.
        result = compare_speakers(
            audio_bytes,
            audio_bytes,
            embedding_model=model_path,
            min_speech_ms=_TEACHER_SPEAKER_MIN_SPEECH_MS,
            same_threshold=_TEACHER_SPEAKER_THRESHOLD,
            uncertain_threshold=_TEACHER_SPEAKER_THRESHOLD,
        )
        if result.decision in {"no_speech", "too_short", "bad_reference"}:
            return jsonify({
                "ok": False,
                "error": result.message or result.decision,
                "decision": result.to_dict(),
            }), 400

        global _teacher_reference_audio, _teacher_reference_created_at
        with _teacher_speaker_lock:
            _teacher_reference_audio = audio_bytes
            _teacher_reference_created_at = time.time()

        return jsonify({
            "ok": True,
            "message": "teacher reference enrolled",
            "threshold": _TEACHER_SPEAKER_THRESHOLD,
            "decision": result.to_dict(),
        })
    except Exception as exc:
        return jsonify({"error": f"Teacher enroll failed: {exc}"}), 500


@app.post("/api/glasses/teacher/verify")
def glasses_teacher_verify():
    try:
        uploaded = request.files.get("audio")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "audio is required."}), 400

        query_bytes = uploaded.read()
        if not query_bytes:
            return jsonify({"error": "audio is empty."}), 400

        with _teacher_speaker_lock:
            reference_bytes = _teacher_reference_audio
            enrolled_at = _teacher_reference_created_at
        if reference_bytes is None:
            return jsonify({"error": "teacher reference is not enrolled."}), 409

        threshold = float(request.form.get("threshold", str(_TEACHER_SPEAKER_THRESHOLD)))
        model_path = _teacher_embedding_model_path()
        from pipeline.speaker_verify.compare import compare_speakers

        result = compare_speakers(
            reference_bytes,
            query_bytes,
            embedding_model=model_path,
            min_speech_ms=_TEACHER_SPEAKER_MIN_SPEECH_MS,
            same_threshold=threshold,
            uncertain_threshold=max(0.0, threshold - 0.08),
        )
        return jsonify({
            "match": result.decision == "same_speaker",
            "threshold": threshold,
            "enrolled_at": enrolled_at,
            **result.to_dict(),
        })
    except Exception as exc:
        return jsonify({"error": f"Teacher verify failed: {exc}"}), 500


@app.get("/api/glasses/demo/videos")
def glasses_demo_videos():
    try:
        videos = _list_demo_videos()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "videos": [
            {
                "id": item["id"],
                "label": item["label"],
                "filename": item["filename"],
                "stream_url": f"/static-videos/{item['relative_path']}",
            }
            for item in videos
        ],
    })


@app.get("/api/glasses/demo/videos/<video_id>/stream")
def glasses_demo_video_stream(video_id: str):
    video_path = _resolve_demo_video(video_id)
    if video_path is None:
        return jsonify({"error": "video not found"}), 404
    return send_file(video_path, mimetype="video/mp4", conditional=True)


@app.get("/admin/api/demo/videos")
def admin_demo_videos():
    try:
        return jsonify({"videos": _list_demo_videos()})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/admin/api/demo/videos")
def admin_upload_demo_video():
    uploaded = request.files.get("video")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "missing video file"}), 400

    original_name = _safe_upload_filename(uploaded.filename)
    if Path(original_name).suffix.lower() not in _ALLOWED_DEMO_VIDEO_SUFFIXES:
        return jsonify({"error": "only .mp4 demo videos are supported"}), 400

    target_dir = _demo_video_root()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = _unique_path(target_dir / original_name)
    uploaded.save(target_path)

    videos = _list_demo_videos()
    created = next((item for item in videos if Path(item["path"]) == target_path), None)
    return jsonify({"status": "ok", "video": created}), 201


@app.get("/admin/api/demo/videos/<video_id>/download")
def admin_download_demo_video(video_id: str):
    video_path = _resolve_demo_video(video_id)
    if video_path is None:
        return jsonify({"error": "video not found"}), 404
    return send_file(video_path, mimetype="video/mp4", as_attachment=True, download_name=video_path.name)


@app.delete("/admin/api/demo/videos/<video_id>")
def admin_delete_demo_video(video_id: str):
    video_path = _resolve_demo_video(video_id)
    if video_path is None:
        return jsonify({"error": "video not found"}), 404
    try:
        video_path.unlink()
    except PermissionError:
        return jsonify({"error": "video is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.post("/api/glasses/records/upload")
def glasses_upload_record():
    video_file = request.files.get("video")
    audio_file = request.files.get("audio")
    if (video_file is None or not video_file.filename) and (audio_file is None or not audio_file.filename):
        return jsonify({"error": "missing record file"}), 400

    now = datetime.now(timezone.utc)
    device_id = _safe_text_field(request.form.get("device_id"), "unknown_device")
    experiment_type = _safe_text_field(request.form.get("experiment_type"), "unknown_experiment")
    record_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    record_dir = _record_root() / now.strftime("%Y%m%d") / record_id
    record_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, Any]] = []
    try:
        if video_file is not None and video_file.filename:
            files.append(_save_record_upload(video_file, record_dir, "record_video"))
        if audio_file is not None and audio_file.filename:
            files.append(_save_record_upload(audio_file, record_dir, "record_audio"))
    except ValueError as exc:
        shutil.rmtree(record_dir, ignore_errors=True)
        return jsonify({"error": str(exc)}), 400

    client_metadata = _parse_metadata_field(request.form.get("metadata"))
    metadata = {
        "record_id": record_id,
        "device_id": device_id,
        "experiment_type": experiment_type,
        "started_at": request.form.get("started_at", ""),
        "ended_at": request.form.get("ended_at", ""),
        "created_at": now.isoformat(),
        "files": files,
        "metadata": client_metadata,
    }
    (record_dir / "record.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    return jsonify({"status": "ok", "record": _record_summary(record_dir)}), 201


@app.get("/admin/api/records")
def admin_records():
    return jsonify({"records": _list_records()})


@app.get("/admin/api/records/<record_id>/files/<filename>")
def admin_download_record_file(record_id: str, filename: str):
    record_dir = _resolve_record_dir(record_id)
    if record_dir is None:
        return jsonify({"error": "record not found"}), 404
    safe_name = Path(filename).name
    file_path = record_dir / safe_name
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "file not found"}), 404
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return send_file(file_path, mimetype=mime, as_attachment=True, download_name=file_path.name)


@app.delete("/admin/api/records/<record_id>")
def admin_delete_record(record_id: str):
    record_dir = _resolve_record_dir(record_id)
    if record_dir is None:
        return jsonify({"error": "record not found"}), 404
    try:
        shutil.rmtree(record_dir)
    except PermissionError:
        return jsonify({"error": "record is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.get("/admin/api/feedback")
def admin_feedback():
    return jsonify({"feedback": _list_feedback()})


@app.post("/admin/api/feedback")
def admin_create_feedback():
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title", "")).strip()
    content = str(payload.get("content", "")).strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    if not content:
        return jsonify({"error": "content is required"}), 400
    if len(title) > 120:
        return jsonify({"error": "title is too long"}), 400
    if len(content) > 5000:
        return jsonify({"error": "content is too long"}), 400

    now = datetime.now(timezone.utc)
    feedback_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    item = {
        "feedback_id": feedback_id,
        "title": title,
        "content": content,
        "created_at": now.isoformat(),
    }
    root = _feedback_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{feedback_id}.json").write_text(
        json.dumps(item, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return jsonify({"status": "ok", "feedback": item}), 201


@app.delete("/admin/api/feedback/<feedback_id>")
def admin_delete_feedback(feedback_id: str):
    feedback_path = _resolve_feedback_path(feedback_id)
    if feedback_path is None:
        return jsonify({"error": "feedback not found"}), 404
    try:
        feedback_path.unlink()
    except PermissionError:
        return jsonify({"error": "feedback is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.post("/api/glasses/qa")
def glasses_qa():
    started = time.perf_counter()
    try:
        payload = request.get_json(silent=True) or {}
        question = str(payload.get("question", "")).strip()
        if not question:
            return jsonify({"error": "question is required."}), 400

        provider = str(payload.get("provider", _GLASSES_DEFAULT_PROVIDER)).strip() or _GLASSES_DEFAULT_PROVIDER
        model = str(payload.get("model", _GLASSES_DEFAULT_MODEL)).strip() or _GLASSES_DEFAULT_MODEL

        from pipeline.qa.llm_qa import answer_question

        if not _vlm_semaphore.acquire(timeout=30):
            return jsonify({"error": "Server busy, try again."}), 503
        try:
            answer, vlm_ms = answer_question(
                question,
                provider=provider,
                model=model,
            )
        finally:
            _vlm_semaphore.release()
        return jsonify({
            "question": question,
            "answer": answer,
            "timings": {
                "vlm": vlm_ms,
                "total": round((time.perf_counter() - started) * 1000, 1),
            },
        })
    except Exception as exc:
        return jsonify({"error": f"QA failed: {exc}"}), 500


@app.post("/api/glasses/qa/audio")
def glasses_qa_audio():
    started = time.perf_counter()
    try:
        uploaded = request.files.get("audio")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "audio is required."}), 400

        audio_bytes = uploaded.read()
        if not audio_bytes:
            return jsonify({"error": "audio is empty."}), 400

        model = request.form.get("model", "qwen3.5-omni-plus-realtime").strip()
        timeout_raw = request.form.get("timeout_seconds", "").strip()
        timeout_seconds = int(timeout_raw) if timeout_raw else None

        from pipeline.qa.omni_audio_qa import answer_audio_question

        if not _omni_semaphore.acquire(timeout=30):
            return jsonify({"error": "Server busy, try again."}), 503
        try:
            result = answer_audio_question(
                audio_bytes,
                model=model or None,
                timeout_seconds=timeout_seconds,
            )
        finally:
            _omni_semaphore.release()
        timings = dict(result.get("timings", {}))
        timings["total_http"] = round((time.perf_counter() - started) * 1000, 1)
        return jsonify({
            "transcript": result.get("transcript", ""),
            "answer_text": result.get("answer_text", ""),
            "answer_audio_data_url": result.get("answer_audio_data_url"),
            "timings": timings,
        })
    except Exception as exc:
        return jsonify({"error": f"Audio QA failed: {exc}"}), 500


@app.post("/api/glasses/tts")
def glasses_tts():
    started = time.perf_counter()
    try:
        payload = request.get_json(silent=True) or {}
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text is required."}), 400

        model = str(payload.get("model", "qwen3.5-omni-plus-realtime")).strip() or None
        timeout_raw = str(payload.get("timeout_seconds", "")).strip()
        timeout_seconds = int(timeout_raw) if timeout_raw else None

        from pipeline.qa.omni_tts import synthesize_text_to_audio

        if not _omni_semaphore.acquire(timeout=30):
            return jsonify({"error": "Server busy, try again."}), 503
        try:
            result = synthesize_text_to_audio(
                text,
                model=model,
                timeout_seconds=timeout_seconds,
            )
        finally:
            _omni_semaphore.release()
        timings = dict(result.get("timings", {}))
        timings["total_http"] = round((time.perf_counter() - started) * 1000, 1)
        return jsonify({
            "audio_data_url": result.get("audio_data_url"),
            "transcript": result.get("transcript", ""),
            "timings": timings,
        })
    except Exception as exc:
        return jsonify({"error": f"TTS failed: {exc}"}), 500


@app.post("/api/glasses/voice/intent")
def glasses_voice_intent():
    started = time.perf_counter()
    try:
        uploaded = request.files.get("audio")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "audio is required."}), 400

        audio_bytes = uploaded.read()
        if not audio_bytes:
            return jsonify({"error": "audio is empty."}), 400

        model = request.form.get("model", "qwen3.5-omni-flash-realtime").strip()
        context = request.form.get("context", "").strip() or None
        demo_options = request.form.get("demo_options", "").strip() or None
        timeout_raw = request.form.get("timeout_seconds", "").strip()
        timeout_seconds = int(timeout_raw) if timeout_raw else None

        from pipeline.voice_control.intent_omni import classify_audio_intent

        if not _omni_semaphore.acquire(timeout=30):
            return jsonify({"error": "Server busy, try again."}), 503
        try:
            result = classify_audio_intent(
                audio_bytes,
                context=context,
                demo_options=demo_options,
                model=model or None,
                timeout_seconds=timeout_seconds,
            )
        finally:
            _omni_semaphore.release()
        timings = dict(result.get("timings", {}))
        timings["total_http"] = round((time.perf_counter() - started) * 1000, 1)
        result["timings"] = timings
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": f"Voice intent failed: {exc}"}), 500


@app.post("/api/glasses/overlay-test")
def glasses_overlay_test():
    try:
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "Missing image file."}), 400

        image_bytes = uploaded.read()
        if not image_bytes:
            return jsonify({"error": "Uploaded image is empty."}), 400

        experiment_type = request.form.get("experiment_type", "other").strip() or "other"
        cfg = get_experiment_config(experiment_type)
        if cfg is None:
            return jsonify({"error": f"Unknown experiment_type: {experiment_type}"}), 400

        component_labels = list(getattr(cfg, "COMPONENT_LABELS", []))
        components = list(getattr(cfg, "COMPONENTS", []))
        if not component_labels or not components:
            return jsonify({"error": f"{experiment_type} is missing component config."}), 400

        image = decode_image(image_bytes)
        cv_params = CVDetectionParams(
            max_side=1600,
            lowe_ratio=0.75,
            min_good_matches=3,
            min_inliers=2,
            min_inlier_ratio=0.08,
            top_k_per_label=6,
            overlap_weight=0.75,
            feature="akaze",
            component_labels=component_labels,
        )
        from pipeline.common.cv_wrapper import detect_wrong_image, annotate_image, encode_annotated_image

        result = detect_wrong_image(image, cv_params)
        image_size = {"width": int(image.shape[1]), "height": int(image.shape[0])}
        overlay_boxes = _build_overlay_boxes(result, components, image_size)
        preview_data_url = encode_annotated_image(annotate_image(image, result))

        return jsonify({
            "experiment_type": experiment_type,
            "overlay_image_size": image_size,
            "overlay_boxes": overlay_boxes,
            "preview_data_url": preview_data_url,
            "message": "Overlay test detection finished.",
        })
    except Exception as exc:
        return jsonify({"error": f"Overlay test failed: {exc}"}), 500


@app.post("/api/glasses/jobs")
def create_glasses_job():
    _cleanup_glasses_jobs()
    try:
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "Missing image file."}), 400

        mode = request.form.get("mode", "experiment").strip() or "experiment"
        if mode not in {"experiment", "safety"}:
            return jsonify({"error": "mode must be experiment or safety."}), 400

        experiment_type = request.form.get("experiment_type", "distillation").strip() or "distillation"
        if mode == "experiment" and experiment_type != "electrolysis" and get_experiment_config(experiment_type) is None:
            return jsonify({"error": f"Unknown experiment_type: {experiment_type}"}), 400

        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip() or _GLASSES_DEFAULT_PROVIDER
        model = request.form.get("model", _GLASSES_DEFAULT_MODEL).strip() or _GLASSES_DEFAULT_MODEL
        image_bytes = uploaded.read()
        if not image_bytes:
            return jsonify({"error": "Uploaded image is empty."}), 400

        job_id = _submit_glasses_job(
            image_bytes=image_bytes,
            mode=mode,
            experiment_type=experiment_type,
            provider=provider,
            model=model,
        )
        return jsonify({
            "job_id": job_id,
            "status": "queued",
            "poll_url": f"/api/glasses/jobs/{job_id}",
        }), 202
    except Exception as exc:
        return jsonify({"error": f"Failed to create job: {exc}"}), 500


@app.get("/api/glasses/jobs/<job_id>")
def get_glasses_job(job_id: str):
    _cleanup_glasses_jobs()
    job = _snapshot_glasses_job(job_id)
    if job is None:
        return jsonify({
            "job_id": job_id,
            "status": "expired",
            "stage": "done",
            "message": "Job not found or expired.",
            "preview_data_url": None,
            "overlay_image_size": None,
            "overlay_boxes": [],
            "result": None,
            "error": "Job not found or expired.",
        }), 404
    return jsonify(job)


@app.get("/api/experiment-configs")
def experiment_configs():
    try:
        configs = _load_experiment_ui_config()
        enriched: dict[str, dict] = {}
        for experiment_type, item in configs.items():
            cfg = get_experiment_config(experiment_type)
            refs = _list_experiment_references(cfg) if cfg is not None else []
            ref_by_id = {str(ref.get("id", "")): ref for ref in refs}
            ref = ref_by_id.get(str(item.get("reference_id", "")))
            configured_file = str(item.get("annotated_image_path", "")).strip()
            enriched[experiment_type] = {
                **item,
                "reference_name": Path(configured_file).name if configured_file else (ref.get("original_filename", "") if ref else ""),
            }
        return jsonify({"experiment_configs": enriched})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/config-reference-image")
def config_reference_image():
    try:
        experiment_type = request.args.get("experiment_type", "distillation").strip() or "distillation"
        cfg = get_experiment_config(experiment_type)
        if cfg is None:
            return jsonify({"error": f"未知实验类型: {experiment_type}"}), 400
        component_labels = list(getattr(cfg, "COMPONENT_LABELS", []))
        ui_config = _get_experiment_ui_config(experiment_type)
        from pipeline.common.cv_wrapper import encode_annotated_image
        if str(ui_config.get("correct_source", "")).strip() == "file":
            image_path = _resolve_configured_image_path(ui_config)
            annotated = decode_image(image_path.read_bytes())
            return jsonify({"data_url": encode_annotated_image(annotated)})

        reference_id = str(ui_config.get("reference_id", "")).strip()
        if not reference_id:
            return jsonify({"error": "未配置参考图。"}), 400

        image = load_reference_image(reference_id)
        result = build_reference_result(reference_id, component_labels)
        annotated = cv_core.annotate_image(image, result)
        return jsonify({"data_url": encode_annotated_image(annotated)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/safety/check")
def safety_check():
    """实验室安全检查：上传图片 → 检索相似安全案例 → VLM 分析。"""
    request_started = time.perf_counter()
    timings: dict[str, float] = {}

    try:
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "请上传待检查图片。"}), 400

        image_bytes = uploaded.read()
        top_k = int(request.form.get("top_k", "1"))
        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip()
        model = request.form.get("model", "").strip() or None

        from pipeline.safety_check.retriever import retrieve
        from pipeline.safety_check.vlm_check import analyze as analyze_safety

        with StepTimer(timing := TimingRecord(), "检索"):
            results = retrieve(image_bytes, top_k=top_k, include_images=False)
        timings.update(timing.to_dict())

        if not results:
            timings["总计"] = round((time.perf_counter() - request_started) * 1000, 1)
            return jsonify({
                "analysis_text": "未发现明显安全问题。",
                "timings": timings,
            }), 200

        with StepTimer(timing := TimingRecord(), "VLM分析"):
            analysis_text, _ = analyze_safety(
                image_bytes,
                results,
                provider=provider,
                model=model,
            )
        timings.update(timing.to_dict())
        timings["总计"] = round((time.perf_counter() - request_started) * 1000, 1)

        return jsonify({
            "analysis_text": analysis_text,
            "timings": timings,
        })

    except Exception as exc:
        return jsonify({"error": f"安全检查失败: {exc}"}), 500


@app.post("/api/electrolysis/check")
def electrolysis_check():
    """电解实验纠错：上传图片 -> 正误样例各取 top1 -> VLM 简洁纠错。"""
    request_started = time.perf_counter()
    timings: dict[str, float] = {}

    try:
        uploaded = request.files.get("image") or request.files.get("wrong_image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "请上传待纠错图片。"}), 400

        image_bytes = uploaded.read()
        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip() or _GLASSES_DEFAULT_PROVIDER
        model = request.form.get("model", "").strip() or _GLASSES_DEFAULT_MODEL

        from pipeline.electrolysis_check.retriever import retrieve_pair
        from pipeline.electrolysis_check.vlm_check import analyze as analyze_electrolysis

        with StepTimer(timing := TimingRecord(), "检索"):
            retrieved = retrieve_pair(image_bytes, include_images=True)
        timings.update(timing.to_dict())

        with StepTimer(timing := TimingRecord(), "VLM分析"):
            analysis_text, _ = analyze_electrolysis(
                image_bytes,
                retrieved,
                provider=provider,
                model=model,
            )
        timings.update(timing.to_dict())
        timings["总计"] = round((time.perf_counter() - request_started) * 1000, 1)

        return jsonify({
            "analysis_text": analysis_text,
            "retrieved": _summarize_electrolysis_retrieval(retrieved),
            "timings": timings,
        })

    except Exception as exc:
        return jsonify({"error": f"电解实验纠错失败: {exc}"}), 500


@app.post("/api/detect")
def detect():
    """Step 1: CV 检测，立即返回标注图，同时缓存结果供后续 VLM 调用。"""
    _cleanup_store()
    timing = TimingRecord()

    try:
        uploaded = request.files.get("wrong_image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "缺少待纠错图片。"}), 400

        with StepTimer(timing, "图片解码"):
            wrong_image = decode_image(uploaded.read())

        experiment_type = request.form.get("experiment_type", "distillation").strip() or "distillation"
        cfg = get_experiment_config(experiment_type)
        if cfg is None:
            return jsonify({"error": f"未知实验类型: {experiment_type}"}), 400

        component_labels = list(getattr(cfg, "COMPONENT_LABELS", []))
        components = list(getattr(cfg, "COMPONENTS", []))
        if not component_labels or not components:
            return jsonify({"error": f"{experiment_type} 实验缺少组件配置。"}), 400

        ui_config = _get_experiment_ui_config(experiment_type)
        reference_id = request.form.get("reference_id", "").strip() or str(ui_config.get("reference_id", "")).strip()
        experiment_requirements = (
            request.form.get("experiment_requirements", "").strip()
            or str(ui_config.get("experiment_requirements", "")).strip()
        )
        correct_source = (
            request.form.get("correct_source", "").strip()
            or str(ui_config.get("correct_source", "database")).strip()
            or "database"
        )

        cv_params = CVDetectionParams(
            max_side=int(request.form.get("max_side", "1600")),
            lowe_ratio=float(request.form.get("lowe_ratio", "0.75")),
            min_good_matches=int(request.form.get("min_good_matches", "3")),
            min_inliers=int(request.form.get("min_inliers", "2")),
            min_inlier_ratio=float(request.form.get("min_inlier_ratio", "0.08")),
            top_k_per_label=int(request.form.get("top_k_per_label", "6")),
            overlap_weight=float(request.form.get("overlap_weight", "0.75")),
            feature=request.form.get("feature", "akaze").strip(),
            component_labels=component_labels,
        )

        from pipeline.common.cv_wrapper import detect_wrong_image

        with StepTimer(timing, "CV检测"):
            wrong_result = detect_wrong_image(wrong_image, cv_params)

        if correct_source == "file":
            with StepTimer(timing, "加载正确标注图"):
                image_path = _resolve_configured_image_path(ui_config)
                correct_annotated = decode_image(image_path.read_bytes())
            correct_result = _build_uploaded_result(components)
        elif correct_source == "upload":
            uploaded_correct = request.files.get("correct_image")
            if uploaded_correct is None or not uploaded_correct.filename:
                return jsonify({"error": "选择了上传模式但未上传正确图。"}), 400
            with StepTimer(timing, "解码正确图"):
                correct_annotated = decode_image(uploaded_correct.read())

            correct_result = _build_uploaded_result(components)
        else:
            refs = _list_experiment_references(cfg)
            if not reference_id and refs:
                reference_id = refs[0]["id"]
            if not reference_id:
                return jsonify({"error": "没有可用的参考图。"}), 400

            with StepTimer(timing, "加载参考图"):
                correct_image = load_reference_image(reference_id)
                correct_result = build_reference_result(reference_id, component_labels)
                correct_annotated = cv_core.annotate_image(correct_image, correct_result)

        with StepTimer(timing, "构建标注图"):
            from pipeline.common.cv_wrapper import annotate_image, encode_annotated_image
            from pipeline.common.image_builder import build_cv_structured_summary

            wrong_annotated = annotate_image(wrong_image, wrong_result)

            # label_summary
            label_summary = _build_label_summary_from_results(correct_result, wrong_result, components)
            cv_summary = build_cv_structured_summary(
                label_summary,
                correct_is_uploaded=(correct_source == "upload"),
            )

            correct_data_url = encode_annotated_image(correct_annotated)
            wrong_data_url = encode_annotated_image(wrong_annotated)

            # 给 VLM 用的 JPEG
            _, c_jpg = cv2.imencode(".jpg", correct_annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            _, w_jpg = cv2.imencode(".jpg", wrong_annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])

        session_id = uuid.uuid4().hex[:12]
        _detect_store[session_id] = {
            "experiment_type": experiment_type,
            "correct_source": correct_source,
            "label_summary": label_summary,
            "cv_summary": cv_summary,
            "jpeg_pair": (c_jpg.tobytes(), w_jpg.tobytes()),
            "experiment_requirements": experiment_requirements,
            "_created": time.time(),
        }

        timing.record(
            "总计",
            timing.to_dict().get("CV检测", 0)
            + timing.to_dict().get("加载正确标注图", 0)
            + timing.to_dict().get("构建标注图", 0),
        )
        return jsonify({
            "session_id": session_id,
            "correct_data_url": correct_data_url,
            "wrong_data_url": wrong_data_url,
            "label_summary": label_summary,
            "timings": timing.to_dict(),
        })

    except Exception as exc:
        return jsonify({"error": f"检测失败: {exc}"}), 500


@app.post("/api/analyze")
def analyze():
    """Step 2: VLM 分析。传入 session_id + approach + provider/model。"""
    _cleanup_store()
    timing = TimingRecord()

    try:
        session_id = request.form.get("session_id", "").strip()
        approach = request.form.get("approach", "a").strip()
        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip()
        model = request.form.get("model", "").strip() or None

        stored = _detect_store.get(session_id)
        if stored is None:
            return jsonify({"error": "会话已过期，请重新检测。"}), 400
        cfg = get_experiment_config(stored.get("experiment_type", "distillation"))
        if cfg is None:
            return jsonify({"error": "实验配置已失效，请重新检测。"}), 400

        jpeg_pair = stored["jpeg_pair"]
        cv_summary = stored["cv_summary"]
        label_summary = stored["label_summary"]
        experiment_requirements = stored["experiment_requirements"]

        result: dict = {}

        # 当前生产路径只触发 Approach A。Approach B 的实现保留在代码里，
        # 但不会被 Web 流程调用，避免两次 VLM 调用。
        need_a = True
        need_b = False

        if need_a:
            from pipeline.approach_a.pipeline_a import run_pipeline_a
            with StepTimer(timing, "方案A_VLM"):
                a_result = run_pipeline_a(
                    experiment_requirements=experiment_requirements,
                    vlm_provider=provider,
                    vlm_model=model,
                    label_summary=label_summary,
                    cv_summary=cv_summary,
                    prebuilt_jpeg=jpeg_pair,
                    experiment_config=cfg,
                )
            result["approach_a"] = {"analysis_text": a_result.analysis_text}

        if need_b:
            with StepTimer(timing, "方案B_找不同"):
                diff_text = _run_b_step1(
                    vlm_provider=provider, vlm_model=model,
                    cv_summary=cv_summary, prebuilt_jpeg=jpeg_pair,
                )
            with StepTimer(timing, "方案B_分析"):
                analysis_text = _run_b_step2(
                    diff_text=diff_text, cv_summary=cv_summary,
                    experiment_requirements=experiment_requirements,
                    vlm_provider=provider, vlm_model=model,
                )
            result["approach_b"] = {"diff_text": diff_text, "analysis_text": analysis_text}

        result["timings"] = timing.to_dict()
        return jsonify(result)

    except Exception as exc:
        return jsonify({"error": f"分析失败: {exc}"}), 500


def _cleanup_glasses_jobs():
    cutoff = time.time() - (_GLASSES_JOB_TTL_SECONDS * 2)
    with _glasses_lock:
        expired = [
            job_id for job_id, job in _glasses_jobs.items()
            if job.get("created_at", 0) < cutoff
        ]
        for job_id in expired:
            _glasses_jobs.pop(job_id, None)


def _submit_glasses_job(
    *,
    image_bytes: bytes,
    mode: str,
    experiment_type: str,
    provider: str,
    model: str,
) -> str:
    with _glasses_lock:
        pending = sum(1 for j in _glasses_jobs.values() if j.get("status") in ("queued", "running"))
        if pending >= _GLASSES_MAX_PENDING:
            raise RuntimeError(f"Too many pending jobs ({pending}), try again later.")
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _glasses_lock:
        _glasses_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "upload",
            "message": "Job queued.",
            "preview_data_url": None,
            "overlay_image_size": None,
            "overlay_boxes": [],
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
    _glasses_executor.submit(
        _run_glasses_job,
        job_id,
        image_bytes,
        mode,
        experiment_type,
        provider,
        model,
    )
    return job_id


def _update_glasses_job(job_id: str, **updates: Any) -> None:
    updates["updated_at"] = time.time()
    with _glasses_lock:
        job = _glasses_jobs.get(job_id)
        if job is not None:
            job.update(updates)


def _snapshot_glasses_job(job_id: str) -> dict[str, Any] | None:
    with _glasses_lock:
        job = _glasses_jobs.get(job_id)
        if job is None:
            return None
        snapshot = dict(job)

    if time.time() - snapshot.get("created_at", 0) > _GLASSES_JOB_TTL_SECONDS:
        snapshot.update({
            "status": "expired",
            "stage": "done",
            "message": "Job expired.",
            "preview_data_url": None,
            "overlay_image_size": None,
            "overlay_boxes": [],
            "result": None,
            "error": "Job expired.",
        })

    return {
        "job_id": snapshot.get("job_id", job_id),
        "status": snapshot.get("status", "expired"),
        "stage": snapshot.get("stage", "done"),
        "message": snapshot.get("message", ""),
        "preview_data_url": snapshot.get("preview_data_url"),
        "overlay_image_size": snapshot.get("overlay_image_size"),
        "overlay_boxes": snapshot.get("overlay_boxes", []),
        "result": snapshot.get("result"),
        "error": snapshot.get("error"),
    }


def _run_glasses_job(
    job_id: str,
    image_bytes: bytes,
    mode: str,
    experiment_type: str,
    provider: str,
    model: str,
) -> None:
    _update_glasses_job(job_id, status="running", stage="upload", message="Image accepted.")
    try:
        if mode == "safety":
            result = _run_glasses_safety_job(
                job_id=job_id,
                image_bytes=image_bytes,
                provider=provider,
                model=model,
            )
        elif experiment_type == "electrolysis":
            result = _run_glasses_electrolysis_job(
                job_id=job_id,
                image_bytes=image_bytes,
                provider=provider,
                model=model,
            )
        else:
            result = _run_glasses_experiment_job(
                job_id=job_id,
                image_bytes=image_bytes,
                experiment_type=experiment_type,
                provider=provider,
                model=model,
            )
        _attach_analysis_audio(job_id, result)
        _update_glasses_job(
            job_id,
            status="succeeded",
            stage="done",
            message="Done.",
            result=result,
            error=None,
        )
    except Exception as exc:
        _update_glasses_job(
            job_id,
            status="failed",
            stage="done",
            message="Failed.",
            result=None,
            error=str(exc),
        )


def _attach_analysis_audio(job_id: str, result: dict[str, Any]) -> None:
    analysis_text = str(result.get("analysis_text", "")).strip()
    if not analysis_text:
        result["analysis_audio_data_url"] = None
        return

    _update_glasses_job(job_id, stage="tts", message="Preparing voice summary.")
    timings = result.setdefault("timings", {})
    try:
        from pipeline.qa.omni_tts import synthesize_text_to_audio

        tts_text = analysis_text[:160]
        audio = synthesize_text_to_audio(tts_text, timeout_seconds=8)
        result["analysis_audio_data_url"] = audio.get("audio_data_url")
        if isinstance(timings, dict):
            timings.update(audio.get("timings", {}))
    except Exception as exc:
        result["analysis_audio_data_url"] = None
        if isinstance(timings, dict):
            timings["tts_error"] = str(exc)


def _run_glasses_safety_job(
    *,
    job_id: str,
    image_bytes: bytes,
    provider: str,
    model: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    from pipeline.safety_check.retriever import retrieve
    from pipeline.safety_check.vlm_check import analyze as analyze_safety

    _update_glasses_job(job_id, stage="retrieval", message="Retrieving safety cases.")
    with StepTimer(timing := TimingRecord(), "retrieval"):
        results = retrieve(image_bytes, top_k=1, include_images=False)
    timings.update(timing.to_dict())

    if not results:
        timings["total"] = round((time.perf_counter() - started) * 1000, 1)
        return {
            "analysis_text": "未发现明显安全问题。",
            "mode": "safety",
            "experiment_type": None,
            "timings": timings,
        }

    _update_glasses_job(job_id, stage="vlm", message="Analyzing safety image.")
    with StepTimer(timing := TimingRecord(), "vlm"):
        analysis_text, _ = analyze_safety(
            image_bytes,
            results,
            provider=provider,
            model=model,
        )
    timings.update(timing.to_dict())
    timings["total"] = round((time.perf_counter() - started) * 1000, 1)

    return {
        "analysis_text": analysis_text,
        "mode": "safety",
        "experiment_type": None,
        "timings": timings,
    }


def _run_glasses_electrolysis_job(
    *,
    job_id: str,
    image_bytes: bytes,
    provider: str,
    model: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    from pipeline.electrolysis_check.retriever import retrieve_pair
    from pipeline.electrolysis_check.vlm_check import analyze as analyze_electrolysis

    _update_glasses_job(job_id, stage="retrieval", message="Retrieving electrolysis references.")
    with StepTimer(timing := TimingRecord(), "retrieval"):
        retrieved = retrieve_pair(image_bytes, include_images=True)
    timings.update(timing.to_dict())

    _update_glasses_job(job_id, stage="vlm", message="Analyzing electrolysis image.")
    with StepTimer(timing := TimingRecord(), "vlm"):
        analysis_text, _ = analyze_electrolysis(
            image_bytes,
            retrieved,
            provider=provider,
            model=model,
        )
    timings.update(timing.to_dict())
    timings["total"] = round((time.perf_counter() - started) * 1000, 1)

    return {
        "analysis_text": analysis_text,
        "mode": "experiment",
        "experiment_type": "electrolysis",
        "retrieved": _summarize_electrolysis_retrieval(retrieved),
        "timings": timings,
    }


def _run_glasses_experiment_job(
    *,
    job_id: str,
    image_bytes: bytes,
    experiment_type: str,
    provider: str,
    model: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    timing = TimingRecord()

    cfg = get_experiment_config(experiment_type)
    if cfg is None:
        raise ValueError(f"Unknown experiment_type: {experiment_type}")

    component_labels = list(getattr(cfg, "COMPONENT_LABELS", []))
    components = list(getattr(cfg, "COMPONENTS", []))
    if not component_labels or not components:
        raise ValueError(f"{experiment_type} is missing component config.")

    ui_config = _get_experiment_ui_config(experiment_type)
    reference_id = str(ui_config.get("reference_id", "")).strip()
    experiment_requirements = str(ui_config.get("experiment_requirements", "")).strip()
    correct_source = str(ui_config.get("correct_source", "database")).strip() or "database"

    _update_glasses_job(job_id, stage="cv_detection", message="Running CV detection.")
    with StepTimer(timing, "decode"):
        wrong_image = decode_image(image_bytes)

    cv_params = CVDetectionParams(
        max_side=1600,
        lowe_ratio=0.75,
        min_good_matches=3,
        min_inliers=2,
        min_inlier_ratio=0.08,
        top_k_per_label=6,
        overlap_weight=0.75,
        feature="akaze",
        component_labels=component_labels,
    )

    from pipeline.common.cv_wrapper import detect_wrong_image

    with StepTimer(timing, "cv_detection"):
        wrong_result = detect_wrong_image(wrong_image, cv_params)

    if correct_source == "file":
        with StepTimer(timing, "load_reference"):
            image_path = _resolve_configured_image_path(ui_config)
            correct_annotated = decode_image(image_path.read_bytes())
        correct_result = _build_uploaded_result(components)
    elif correct_source == "upload":
        raise ValueError("Glasses jobs do not support correct_source=upload.")
    else:
        refs = _list_experiment_references(cfg)
        if not reference_id and refs:
            reference_id = refs[0]["id"]
        if not reference_id:
            raise ValueError("No configured reference image is available.")
        with StepTimer(timing, "load_reference"):
            correct_image = load_reference_image(reference_id)
            correct_result = build_reference_result(reference_id, component_labels)
            correct_annotated = cv_core.annotate_image(correct_image, correct_result)

    with StepTimer(timing, "build_vlm_images"):
        from pipeline.common.cv_wrapper import annotate_image, encode_annotated_image
        from pipeline.common.image_builder import build_cv_structured_summary

        wrong_annotated = annotate_image(wrong_image, wrong_result)
        preview_data_url = encode_annotated_image(wrong_annotated)
        overlay_image_size = {
            "width": int(wrong_image.shape[1]),
            "height": int(wrong_image.shape[0]),
        }
        overlay_boxes = _build_overlay_boxes(wrong_result, components, overlay_image_size)
        label_summary = _build_label_summary_from_results(correct_result, wrong_result, components)
        cv_summary = build_cv_structured_summary(
            label_summary,
            correct_is_uploaded=(correct_source == "upload"),
        )
        _, c_jpg = cv2.imencode(".jpg", correct_annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        _, w_jpg = cv2.imencode(".jpg", wrong_annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])

    _update_glasses_job(
        job_id,
        stage="vlm",
        message="Analyzing experiment image.",
        preview_data_url=preview_data_url,
        overlay_image_size=overlay_image_size,
        overlay_boxes=overlay_boxes,
    )
    from pipeline.approach_a.pipeline_a import run_pipeline_a
    with StepTimer(timing, "vlm"):
        a_result = run_pipeline_a(
            experiment_requirements=experiment_requirements,
            vlm_provider=provider,
            vlm_model=model,
            label_summary=label_summary,
            cv_summary=cv_summary,
            prebuilt_jpeg=(c_jpg.tobytes(), w_jpg.tobytes()),
            experiment_config=cfg,
        )

    timings = timing.to_dict()
    timings["total"] = round((time.perf_counter() - started) * 1000, 1)
    return {
        "analysis_text": a_result.analysis_text,
        "mode": "experiment",
        "experiment_type": experiment_type,
        "timings": timings,
    }


def _run_b_step1(*, vlm_provider, vlm_model, cv_summary, prebuilt_jpeg):
    from pipeline.approach_b.pipeline_b import build_diff_messages
    from pipeline.common.vlm_client import call_vlm, create_vlm
    client = create_vlm(vlm_provider, vlm_model)
    messages = build_diff_messages(prebuilt_jpeg[0], prebuilt_jpeg[1], cv_summary, client)
    result, _ = call_vlm(client, messages)
    return result


def _run_b_step2(*, diff_text, cv_summary, experiment_requirements, vlm_provider, vlm_model):
    from pipeline.approach_b.pipeline_b import build_analysis_messages
    from pipeline.common.vlm_client import call_vlm, create_vlm
    client = create_vlm(vlm_provider, vlm_model)
    messages = build_analysis_messages(diff_text, cv_summary, experiment_requirements)
    result, _ = call_vlm(client, messages)
    return result


def _build_uploaded_result(components):
    return {
        "boxes": {c["label"]: [0, 0, 1, 1] for c in components},
        "detections": {c["label"]: {"score": None} for c in components},
    }


def _summarize_electrolysis_retrieval(retrieved: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for label in ("correct", "wrong"):
        item = retrieved.get(label)
        if not item:
            summary[label] = None
            continue
        summary[label] = {
            "sample_id": item.get("sample_id"),
            "label": item.get("label"),
            "image_file": item.get("image_file"),
            "description": item.get("description", ""),
            "match_count": item.get("match_count"),
            "score": item.get("score"),
        }
    return summary


def _build_overlay_boxes(
    detection_result: dict[str, Any],
    components: list[dict[str, Any]],
    image_size: dict[str, int],
) -> list[dict[str, Any]]:
    width = max(1, int(image_size.get("width", 1)))
    height = max(1, int(image_size.get("height", 1)))
    component_by_label = {str(item.get("label", "")): item for item in components}
    boxes = detection_result.get("boxes", {}) or {}
    detections = detection_result.get("detections", {}) or {}
    overlay: list[dict[str, Any]] = []

    for label, raw_box in boxes.items():
        if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
            continue
        try:
            x1, y1, x2, y2 = [float(v) for v in raw_box]
        except (TypeError, ValueError):
            continue
        if x2 <= x1 or y2 <= y1:
            continue

        comp = component_by_label.get(str(label), {})
        detection = detections.get(label, {}) if isinstance(detections, dict) else {}
        overlay.append({
            "label": str(label),
            "name": str(comp.get("name", label)),
            "color": str(comp.get("color", "#FFD54F")),
            "box": [
                round(max(0.0, min(1.0, x1 / width)), 6),
                round(max(0.0, min(1.0, y1 / height)), 6),
                round(max(0.0, min(1.0, x2 / width)), 6),
                round(max(0.0, min(1.0, y2 / height)), 6),
            ],
            "score": detection.get("score") if isinstance(detection, dict) else None,
        })
    return overlay


def _load_experiment_ui_config() -> dict[str, dict]:
    if not EXPERIMENT_UI_CONFIG_PATH.exists():
        return {}
    data = json.loads(EXPERIMENT_UI_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _get_experiment_ui_config(experiment_type: str) -> dict:
    return _load_experiment_ui_config().get(experiment_type, {})


def _resolve_configured_image_path(ui_config: dict) -> Path:
    raw_path = str(ui_config.get("annotated_image_path", "")).strip()
    if not raw_path:
        raise FileNotFoundError("未配置 annotated_image_path。")
    if raw_path.startswith("assets://"):
        path = runtime_asset_path(*[part for part in raw_path[len("assets://"):].split("/") if part])
    else:
        path = Path(raw_path)
    if not path.is_absolute():
        path = (EXPERIMENT_UI_CONFIG_PATH.parent / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"配置参考图不存在: {path}")
    return path


def _list_experiment_references(cfg):
    refs = list_available_references(getattr(cfg, "COMPONENT_LABELS", None))
    if getattr(cfg, "REQUIRE_REFERENCE_LABELS", False):
        refs = [ref for ref in refs if ref.get("annotated_labels")]
    return refs


def _demo_video_root() -> Path:
    return runtime_asset_path("demo_videos")


def _record_root() -> Path:
    return runtime_asset_path("user_records")


def _feedback_root() -> Path:
    return runtime_asset_path("feedback")


def _list_demo_videos() -> list[dict[str, str]]:
    video_dir = _demo_video_root()
    video_dir.mkdir(parents=True, exist_ok=True)

    paths = [
        path for path in video_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in _ALLOWED_DEMO_VIDEO_SUFFIXES
    ]
    paths.sort(key=lambda item: item.relative_to(video_dir).as_posix().lower())

    videos = []
    for index, path in enumerate(paths, start=1):
        videos.append({
            "id": _demo_video_id(path),
            "label": _demo_video_label(index, path),
            "filename": path.name,
            "relative_path": path.relative_to(video_dir).as_posix(),
            "path": str(path),
            "size": path.stat().st_size,
        })
    return videos


def _resolve_demo_video(video_id: str) -> Path | None:
    try:
        rel = _decode_urlsafe_id(video_id)
    except ValueError:
        return None
    path = (_demo_video_root() / rel).resolve()
    root = _demo_video_root().resolve()
    if not _is_relative_to(path, root):
        return None
    if not path.exists() or not path.is_file() or path.suffix.lower() not in _ALLOWED_DEMO_VIDEO_SUFFIXES:
        return None
    return path


def _teacher_embedding_model_path() -> Path:
    raw = os.getenv("SPEAKER_VERIFY_EMBEDDING_MODEL", "").strip()
    path = Path(raw) if raw else _TEACHER_DEFAULT_EMBEDDING_MODEL
    if not path.exists():
        raise FileNotFoundError(
            "声纹模型不存在。请设置 SPEAKER_VERIFY_EMBEDDING_MODEL，"
            f"当前路径: {path}"
        )
    return path


def _demo_video_id(path: Path) -> str:
    rel = path.relative_to(_demo_video_root()).as_posix()
    return _encode_urlsafe_id(rel)


def _demo_video_label(index: int, path: Path) -> str:
    return f"{index}. {path.name}"


def _encode_urlsafe_id(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_urlsafe_id(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise ValueError("invalid id") from exc


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_upload_filename(filename: str) -> str:
    raw = Path(filename).name.strip()
    raw = re.sub(r"[\x00-\x1f\x7f]+", "", raw)
    raw = re.sub(r'[<>:"/\\|?*]+', "_", raw)
    raw = raw.strip(" .")
    return raw or f"upload_{uuid.uuid4().hex[:8]}"


def _safe_text_field(value: str | None, fallback: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"[\x00-\x1f\x7f]+", "", text)
    text = re.sub(r'[<>:"/\\|?*]+', "_", text)
    return text[:80] or fallback


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}_{timestamp}_{uuid.uuid4().hex[:6]}{path.suffix}")


def _parse_metadata_field(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _save_record_upload(uploaded, record_dir: Path, prefix: str) -> dict[str, Any]:
    original_name = _safe_upload_filename(uploaded.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in _ALLOWED_RECORD_SUFFIXES:
        raise ValueError(f"unsupported record file type: {suffix}")
    target = _unique_path(record_dir / f"{prefix}{suffix}")
    uploaded.save(target)
    return {
        "filename": target.name,
        "original_filename": original_name,
        "size": target.stat().st_size,
        "content_type": uploaded.mimetype or mimetypes.guess_type(target.name)[0] or "",
    }


def _list_records() -> list[dict[str, Any]]:
    root = _record_root()
    root.mkdir(parents=True, exist_ok=True)
    records = []
    for metadata_path in root.rglob("record.json"):
        record_dir = metadata_path.parent
        try:
            records.append(_record_summary(record_dir))
        except Exception:
            continue
    records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return records


def _record_summary(record_dir: Path) -> dict[str, Any]:
    metadata_path = record_dir / "record.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    files = []
    for file_path in sorted(record_dir.iterdir(), key=lambda item: item.name.lower()):
        if not file_path.is_file() or file_path.name == "record.json":
            continue
        files.append({
            "filename": file_path.name,
            "size": file_path.stat().st_size,
            "download_url": url_for(
                "admin_download_record_file",
                record_id=record_dir.name,
                filename=file_path.name,
            ),
        })
    return {
        "record_id": record_dir.name,
        "device_id": metadata.get("device_id", ""),
        "experiment_type": metadata.get("experiment_type", ""),
        "created_at": metadata.get("created_at", ""),
        "started_at": metadata.get("started_at", ""),
        "ended_at": metadata.get("ended_at", ""),
        "files": files,
        "path": str(record_dir),
    }


def _resolve_record_dir(record_id: str) -> Path | None:
    safe_id = _safe_text_field(record_id, "")
    if not safe_id or safe_id != record_id:
        return None
    root = _record_root().resolve()
    for metadata_path in root.rglob("record.json"):
        record_dir = metadata_path.parent.resolve()
        if record_dir.name == record_id and _is_relative_to(record_dir, root):
            return record_dir
    return None


def _list_feedback() -> list[dict[str, Any]]:
    root = _feedback_root()
    root.mkdir(parents=True, exist_ok=True)
    items = []
    for path in root.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        item.setdefault("feedback_id", path.stem)
        item.setdefault("title", "")
        item.setdefault("content", "")
        item.setdefault("created_at", "")
        items.append(item)
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return items


def _resolve_feedback_path(feedback_id: str) -> Path | None:
    safe_id = _safe_text_field(feedback_id, "")
    if not safe_id or safe_id != feedback_id:
        return None
    root = _feedback_root().resolve()
    path = (root / f"{feedback_id}.json").resolve()
    if not _is_relative_to(path, root):
        return None
    if not path.exists() or not path.is_file():
        return None
    return path


def _build_label_summary_from_results(correct_result, wrong_result, components):
    from pipeline.common.cv_wrapper import get_component_info
    wrong_boxes = wrong_result.get("boxes", {})
    wrong_detections = wrong_result.get("detections", {})
    correct_boxes = correct_result.get("boxes", {})
    result = {}
    for comp in get_component_info(components):
        label = comp["label"]
        c_box = correct_boxes.get(label, [])
        w_box = wrong_boxes.get(label, [])
        sentinel = [0, 0, 1, 1]
        result[label] = {
            "name": comp["name"], "color": comp["color"],
            "in_correct": bool(c_box),
            "in_wrong": bool(w_box),
            "correct_box": c_box if c_box != sentinel else [],
            "wrong_box": w_box,
            "wrong_score": wrong_detections.get(label, {}).get("score"),
            "wrong_raw_score": wrong_detections.get(label, {}).get("raw_score"),
            "wrong_failure_reason": wrong_detections.get(label, {}).get("failure_reason"),
        }
    return result


# 延迟导入，避免循环
import pipeline.common.cv_wrapper as cv_core

def parse_args():
    p = argparse.ArgumentParser(description="Distillation Pipeline Web")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5080)
    p.add_argument("--debug", action="store_true")
    return p.parse_args()

if __name__ == "__main__":
    try:
        from pipeline.safety_check.retriever import reload_db
        reload_db()
    except Exception as exc:
        print(f"[safety_check] warmup skipped: {exc}")
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
