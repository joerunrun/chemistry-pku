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
from pipeline.experiments import get_experiment_config, list_specs, resolve_experiment_spec
from pipeline.experiments.spec_loader import SPECS_DIR
from pipeline.media_analysis import MediaAnalysisQueue
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
_DEMO_VIDEO_EXPERIMENTS = {
    spec["id"]: spec["name"]
    for spec in list_specs()
    if spec.get("supports_demo", False)
}
_ALLOWED_DEMO_VIDEO_SUFFIXES = {".mp4"}
_ALLOWED_RECORD_SUFFIXES = {".mp4", ".wav", ".m4a", ".aac", ".mp3"}
_ALLOWED_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png"}
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
_media_analysis_queue = MediaAnalysisQueue()


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
    return render_template(
        "admin.html",
        portal_title="化学实验资源管理",
        record_api_prefix="/admin/api",
    )


@app.get("/teacher-admin")
def teacher_admin_index():
    return render_template(
        "admin.html",
        portal_title="化学实验教师资源管理",
        record_api_prefix="/teacher-admin/api",
    )


@app.before_request
def start_media_analysis_queue():
    _media_analysis_queue.start()


@app.get("/experiment-lab")
def experiment_lab():
    return render_template("experiment_lab.html")


@app.get("/api/experiment-lab/specs")
def experiment_lab_specs():
    specs = [
        spec
        for spec in list_specs()
        if spec.get("expose_in_experiment_lab", True)
    ]
    return jsonify({"experiments": [
        {
            "id": s["id"],
            "name": s.get("name", s["id"]),
            "strategy": s.get("strategy", "vlm_only"),
            "has_components": bool(s.get("components")),
        }
        for s in specs
    ]})


@app.post("/api/experiment-lab/run")
def experiment_lab_run():
    try:
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "请上传图片。"}), 400
        experiment_id = request.form.get("experiment_id", "").strip()
        if resolve_experiment_spec(experiment_id) is None:
            return jsonify({"error": f"未知实验: {experiment_id}"}), 400
        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip() or _GLASSES_DEFAULT_PROVIDER
        model = request.form.get("model", "").strip() or None
        image_bytes = uploaded.read()

        from pipeline.experiments.runner import run_experiment_check
        result = run_experiment_check(experiment_id, image_bytes, provider=provider, model=model)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": f"实验纠错失败: {exc}"}), 500


@app.get("/api/references")
def references():
    try:
        experiment_type = request.args.get("experiment_type", "distillation").strip() or "distillation"
        cfg = get_experiment_config(experiment_type)
        if cfg is None:
            return jsonify({"error": f"未知实验类型: {experiment_type}"}), 400
        if not cfg.get("reference_db", False):
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
            {"value": spec["id"], "label": spec["name"]}
            for spec in list_specs()
            if spec["id"] != "safety" and spec.get("expose_in_glasses", True)
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
        experiment_type = _normalize_demo_experiment_type(
            request.args.get("experiment_type", "distillation")
        )
        videos = _list_demo_videos(experiment_type)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "videos": [
            {
                "id": item["id"],
                "label": item["label"],
                "filename": item["filename"],
                "experiment_type": item["experiment_type"],
                "stream_url": f"/api/glasses/demo/videos/{item['id']}/stream",
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
        experiment_type = request.args.get("experiment_type", "").strip()
        videos = _list_demo_videos(_normalize_demo_experiment_type(experiment_type) if experiment_type else None)
        return jsonify({
            "experiments": [
                {"value": key, "label": label}
                for key, label in _DEMO_VIDEO_EXPERIMENTS.items()
            ],
            "videos": videos,
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/admin/api/demo/videos")
def admin_upload_demo_video():
    uploaded = request.files.get("video")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "missing video file"}), 400
    try:
        experiment_type = _normalize_demo_experiment_type(request.form.get("experiment_type", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    original_name = _safe_upload_filename(uploaded.filename)
    if Path(original_name).suffix.lower() not in _ALLOWED_DEMO_VIDEO_SUFFIXES:
        return jsonify({"error": "only .mp4 demo videos are supported"}), 400

    target_dir = _demo_video_dir(experiment_type)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = _unique_path(target_dir / original_name)
    uploaded.save(target_path)

    videos = _list_demo_videos(experiment_type)
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
    return _store_uploaded_record(_record_root(), record_role="student")


@app.post("/api/glasses/teacher/records/upload")
def glasses_upload_teacher_record():
    return _store_uploaded_record(_teacher_record_root(), record_role="teacher")


def _store_uploaded_record(record_root: Path, *, record_role: str):
    video_file = request.files.get("video")
    audio_file = request.files.get("audio")
    if (video_file is None or not video_file.filename) and (audio_file is None or not audio_file.filename):
        return jsonify({"error": "missing record file"}), 400

    now = datetime.now(timezone.utc)
    device_id = _safe_text_field(request.form.get("device_id"), "unknown_device")
    experiment_type = _safe_text_field(request.form.get("experiment_type"), "unknown_experiment")
    record_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    record_dir = record_root / now.strftime("%Y%m%d") / record_id
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
        "record_role": record_role,
        "device_id": device_id,
        "experiment_type": experiment_type,
        "started_at": request.form.get("started_at", ""),
        "ended_at": request.form.get("ended_at", ""),
        "created_at": now.isoformat(),
        "files": files,
        "metadata": client_metadata,
    }
    (record_dir / "record.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    for file_info in files:
        file_path = record_dir / file_info["filename"]
        if file_path.suffix.lower() in {".wav", ".m4a", ".aac", ".mp3"}:
            _media_analysis_queue.enqueue(record_dir, file_path)

    return jsonify({"status": "ok", "record": _record_summary(record_dir)}), 201


@app.get("/admin/api/records")
def admin_records():
    return jsonify({"records": _list_records(_record_root(), "admin_download_record_file")})


@app.get("/admin/api/records/<record_id>/files/<filename>")
def admin_download_record_file(record_id: str, filename: str):
    record_dir = _resolve_record_dir(_record_root(), record_id)
    if record_dir is None:
        return jsonify({"error": "record not found"}), 404
    safe_name = Path(filename).name
    file_path = record_dir / safe_name
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "file not found"}), 404
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return send_file(
        file_path,
        mimetype=mime,
        as_attachment=request.args.get("download", "1") != "0",
        download_name=file_path.name,
    )


@app.delete("/admin/api/records/<record_id>")
def admin_delete_record(record_id: str):
    record_dir = _resolve_record_dir(_record_root(), record_id)
    if record_dir is None:
        return jsonify({"error": "record not found"}), 404
    try:
        shutil.rmtree(record_dir)
    except PermissionError:
        return jsonify({"error": "record is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.get("/teacher-admin/api/records")
def teacher_admin_records():
    return jsonify({"records": _list_records(_teacher_record_root(), "teacher_admin_download_record_file")})


@app.get("/teacher-admin/api/records/<record_id>/files/<filename>")
def teacher_admin_download_record_file(record_id: str, filename: str):
    record_dir = _resolve_record_dir(_teacher_record_root(), record_id)
    if record_dir is None:
        return jsonify({"error": "record not found"}), 404
    safe_name = Path(filename).name
    file_path = record_dir / safe_name
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "file not found"}), 404
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return send_file(
        file_path,
        mimetype=mime,
        as_attachment=request.args.get("download", "1") != "0",
        download_name=file_path.name,
    )


@app.delete("/teacher-admin/api/records/<record_id>")
def teacher_admin_delete_record(record_id: str):
    record_dir = _resolve_record_dir(_teacher_record_root(), record_id)
    if record_dir is None:
        return jsonify({"error": "record not found"}), 404
    try:
        shutil.rmtree(record_dir)
    except PermissionError:
        return jsonify({"error": "record is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.post("/api/glasses/photos/upload")
def glasses_upload_photo():
    return _store_uploaded_photo(_photo_root(), record_role="student")


@app.post("/api/glasses/teacher/photos/upload")
def glasses_upload_teacher_photo():
    return _store_uploaded_photo(_teacher_photo_root(), record_role="teacher")


def _store_uploaded_photo(photo_root: Path, *, record_role: str):
    photo_file = request.files.get("photo")
    if photo_file is None or not photo_file.filename:
        return jsonify({"error": "missing photo file"}), 400

    original_name = _safe_upload_filename(photo_file.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in _ALLOWED_PHOTO_SUFFIXES:
        return jsonify({"error": f"unsupported photo file type: {suffix}"}), 400

    now = datetime.now(timezone.utc)
    photo_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    photo_dir = photo_root / now.strftime("%Y%m%d") / photo_id
    photo_dir.mkdir(parents=True, exist_ok=True)
    photo_path = photo_dir / f"photo{suffix}"
    try:
        photo_file.save(photo_path)
    except Exception:
        shutil.rmtree(photo_dir, ignore_errors=True)
        raise

    metadata = {
        "photo_id": photo_id,
        "record_role": record_role,
        "device_id": _safe_text_field(request.form.get("device_id"), "unknown_device"),
        "experiment_type": _safe_text_field(
            request.form.get("experiment_type"),
            "unknown_experiment",
        ),
        "captured_at": request.form.get("captured_at", ""),
        "created_at": now.isoformat(),
        "filename": photo_path.name,
        "original_filename": original_name,
        "size": photo_path.stat().st_size,
    }
    (photo_dir / "photo.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _media_analysis_queue.enqueue(photo_dir, photo_path)
    return jsonify({"status": "ok", "photo": _photo_summary(photo_dir)}), 201


@app.get("/admin/api/photos")
def admin_photos():
    return jsonify({"photos": _list_photos(_photo_root(), "admin_photo_file")})


@app.get("/admin/api/photos/<photo_id>/file")
def admin_photo_file(photo_id: str):
    photo_dir = _resolve_photo_dir(_photo_root(), photo_id)
    if photo_dir is None:
        return jsonify({"error": "photo not found"}), 404
    photo_path = next(
        (
            path
            for path in photo_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _ALLOWED_PHOTO_SUFFIXES
        ),
        None,
    )
    if photo_path is None:
        return jsonify({"error": "photo file not found"}), 404
    return send_file(
        photo_path,
        mimetype=mimetypes.guess_type(photo_path.name)[0] or "image/jpeg",
        as_attachment=request.args.get("download") == "1",
        download_name=photo_path.name,
    )


@app.delete("/admin/api/photos/<photo_id>")
def admin_delete_photo(photo_id: str):
    photo_dir = _resolve_photo_dir(_photo_root(), photo_id)
    if photo_dir is None:
        return jsonify({"error": "photo not found"}), 404
    try:
        shutil.rmtree(photo_dir)
    except PermissionError:
        return jsonify({"error": "photo is currently in use"}), 409
    return jsonify({"status": "ok"})


@app.get("/teacher-admin/api/photos")
def teacher_admin_photos():
    return jsonify({"photos": _list_photos(_teacher_photo_root(), "teacher_admin_photo_file")})


@app.get("/teacher-admin/api/photos/<photo_id>/file")
def teacher_admin_photo_file(photo_id: str):
    return _send_photo_file(_teacher_photo_root(), photo_id)


@app.delete("/teacher-admin/api/photos/<photo_id>")
def teacher_admin_delete_photo(photo_id: str):
    photo_dir = _resolve_photo_dir(_teacher_photo_root(), photo_id)
    if photo_dir is None:
        return jsonify({"error": "photo not found"}), 404
    try:
        shutil.rmtree(photo_dir)
    except PermissionError:
        return jsonify({"error": "photo is currently in use"}), 409
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

        component_labels = [
            item["label"]
            for item in cfg.get("components", [])
        ]
        components = list(cfg.get("components", []))
        if not component_labels or not components:
            return jsonify({"error": f"{experiment_type} is missing component config."}), 400

        image = decode_image(image_bytes)
        from pipeline.common.cv_wrapper import detect_wrong_image, annotate_image, encode_annotated_image

        base_params = {
            "max_side": 1600,
            "lowe_ratio": 0.75,
            "min_good_matches": 3,
            "min_inliers": 2,
            "min_inlier_ratio": 0.05,
            "top_k_per_label": 6,
            "overlap_weight": 0.75,
            "component_labels": component_labels,
        }

        result = None
        detection_feature = "sift"
        last_error = None
        for feature in ("sift", "akaze"):
            try:
                candidate = detect_wrong_image(image, CVDetectionParams(**base_params, feature=feature))
            except Exception as exc:
                last_error = exc
                continue
            if result is None:
                result = candidate
                detection_feature = feature
            if any(candidate.get("boxes", {}).get(label) for label in component_labels):
                result = candidate
                detection_feature = feature
                break
        if result is None:
            raise last_error or RuntimeError("CV detection failed.")

        image_size = {"width": int(image.shape[1]), "height": int(image.shape[0])}
        overlay_boxes = _build_overlay_boxes(result, components, image_size)
        preview_data_url = encode_annotated_image(annotate_image(image, result))

        return jsonify({
            "experiment_type": experiment_type,
            "detection_feature": detection_feature,
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
        if mode == "experiment" and resolve_experiment_spec(experiment_type) is None:
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
        component_labels = [
            item["label"]
            for item in cfg.get("components", [])
        ]
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
    """JSON-configured safety retrieval and analysis."""
    try:
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "请上传待检查图片。"}), 400

        image_bytes = uploaded.read()
        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip() or _GLASSES_DEFAULT_PROVIDER
        model = request.form.get("model", "").strip() or None
        from pipeline.experiments.runner import run_experiment_check
        return jsonify(
            run_experiment_check(
                "safety",
                image_bytes,
                provider=provider,
                model=model,
            )
        )
    except Exception as exc:
        return jsonify({"error": f"安全检查失败: {exc}"}), 500


@app.post("/api/electrolysis/check")
def electrolysis_check():
    """JSON-configured electrolysis retrieval and analysis."""
    try:
        uploaded = request.files.get("image") or request.files.get("wrong_image")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "请上传待纠错图片。"}), 400

        image_bytes = uploaded.read()
        provider = request.form.get("provider", _GLASSES_DEFAULT_PROVIDER).strip() or _GLASSES_DEFAULT_PROVIDER
        model = request.form.get("model", "").strip() or _GLASSES_DEFAULT_MODEL
        from pipeline.experiments.runner import run_experiment_check
        return jsonify(
            run_experiment_check(
                "electrolysis",
                image_bytes,
                provider=provider,
                model=model,
            )
        )
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

        component_labels = [
            item["label"]
            for item in cfg.get("components", [])
        ]
        components = list(cfg.get("components", []))
        if not component_labels or not components:
            return jsonify({"error": f"{experiment_type} 实验缺少组件配置。"}), 400

        ui_config = _get_experiment_ui_config(experiment_type)
        reference_id = str(ui_config.get("reference_id", "")).strip()
        experiment_requirements = str(ui_config.get("experiment_requirements", "")).strip()
        correct_source = str(ui_config.get("correct_source", "database")).strip() or "database"

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
        from pipeline.experiments.runner import run_experiment_check
        exp_id = experiment_type if mode == "experiment" else "safety"
        _update_glasses_job(job_id, stage="processing", message="Running check.")
        result = run_experiment_check(exp_id, image_bytes, provider=provider, model=model)
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


def _demo_video_label(
    index: int,
    path: Path,
    experiment_type: str,
    *,
    include_experiment: bool = False,
) -> str:
    if include_experiment:
        return f"{index}. {_DEMO_VIDEO_EXPERIMENTS.get(experiment_type, experiment_type)} / {path.name}"
    return f"{index}. {path.name}"


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
    configs: dict[str, dict] = {}
    for spec in list_specs():
        if spec.get("strategy") != "cv_reference":
            continue
        reference = spec.get("reference", {})
        configs[spec["id"]] = {
            "correct_source": reference.get("source", "database"),
            "reference_id": reference.get("reference_id", ""),
            "annotated_image_path": reference.get("annotated_image_path", ""),
            "experiment_requirements": spec.get("experiment_requirements", ""),
        }
    return configs



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
        path = (SPECS_DIR / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"配置参考图不存在: {path}")
    return path



def _list_experiment_references(cfg):
    component_labels = [
        component["label"]
        for component in cfg.get("components", [])
    ]
    refs = list_available_references(component_labels or None)
    if cfg.get("require_reference_labels", False):
        refs = [ref for ref in refs if ref.get("annotated_labels")]
    return refs



def _demo_video_root() -> Path:
    return runtime_asset_path("demo_videos")



def _demo_video_dir(experiment_type: str) -> Path:
    return _demo_video_root() / experiment_type



def _record_root() -> Path:
    return runtime_asset_path("user_records")


def _teacher_record_root() -> Path:
    return runtime_asset_path("teacher_records")


def _photo_root() -> Path:
    return runtime_asset_path("user_photos")


def _teacher_photo_root() -> Path:
    return runtime_asset_path("teacher_photos")



def _feedback_root() -> Path:
    return runtime_asset_path("feedback")



def _normalize_demo_experiment_type(value: str | None) -> str:
    experiment_type = (value or "").strip()
    if experiment_type not in _DEMO_VIDEO_EXPERIMENTS:
        raise ValueError(f"未知演示视频实验类型: {experiment_type or '(empty)'}")
    return experiment_type



def _path_demo_experiment_type(path: Path, root: Path) -> str:
    rel_parts = path.relative_to(root).parts
    if rel_parts and rel_parts[0] in _DEMO_VIDEO_EXPERIMENTS:
        return rel_parts[0]
    # 兼容旧资源：之前直接放在 demo_videos/ 根目录的视频默认按减压蒸馏处理。
    return "distillation"



def _list_demo_videos(experiment_type: str | None = None) -> list[dict[str, str]]:
    video_dir = _demo_video_root()
    video_dir.mkdir(parents=True, exist_ok=True)

    paths = [
        path for path in video_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in _ALLOWED_DEMO_VIDEO_SUFFIXES
    ]
    if experiment_type is not None:
        paths = [
            path for path in paths
            if _path_demo_experiment_type(path, video_dir) == experiment_type
        ]
    paths.sort(key=lambda item: item.relative_to(video_dir).as_posix().lower())

    videos = []
    for index, path in enumerate(paths, start=1):
        item_experiment_type = _path_demo_experiment_type(path, video_dir)
        videos.append({
            "id": _demo_video_id(path),
            "label": _demo_video_label(index, path, item_experiment_type, include_experiment=experiment_type is None),
            "filename": path.name,
            "experiment_type": item_experiment_type,
            "experiment_label": _DEMO_VIDEO_EXPERIMENTS.get(item_experiment_type, item_experiment_type),
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


def _demo_video_id(path: Path) -> str:
    relative_path = path.relative_to(_demo_video_root()).as_posix()
    return _encode_urlsafe_id(relative_path)



def _teacher_embedding_model_path() -> Path:
    raw = os.getenv("SPEAKER_VERIFY_EMBEDDING_MODEL", "").strip()
    path = Path(raw) if raw else _TEACHER_DEFAULT_EMBEDDING_MODEL
    if not path.exists():
        raise FileNotFoundError(
            "声纹模型不存在。请设置 SPEAKER_VERIFY_EMBEDDING_MODEL，"
            f"当前路径: {path}"
        )
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


def _list_records(root: Path, download_endpoint: str) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    records = []
    for metadata_path in root.rglob("record.json"):
        record_dir = metadata_path.parent
        try:
            records.append(_record_summary(record_dir, download_endpoint))
        except Exception:
            continue
    records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return records


def _record_summary(record_dir: Path, download_endpoint: str = "admin_download_record_file") -> dict[str, Any]:
    metadata_path = record_dir / "record.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    files = []
    for file_path in sorted(record_dir.iterdir(), key=lambda item: item.name.lower()):
        if not file_path.is_file() or file_path.name == "record.json":
            continue
        file_url = url_for(
            download_endpoint,
            record_id=record_dir.name,
            filename=file_path.name,
        )
        files.append({
            "filename": file_path.name,
            "size": file_path.stat().st_size,
            "file_url": f"{file_url}?download=0",
            "download_url": f"{file_url}?download=1",
        })
    return {
        "record_id": record_dir.name,
        "device_id": metadata.get("device_id", ""),
        "experiment_type": metadata.get("experiment_type", ""),
        "created_at": metadata.get("created_at", ""),
        "started_at": metadata.get("started_at", ""),
        "ended_at": metadata.get("ended_at", ""),
        "files": files,
        "analysis": _load_analysis(record_dir),
        "path": str(record_dir),
    }


def _resolve_record_dir(root: Path, record_id: str) -> Path | None:
    safe_id = _safe_text_field(record_id, "")
    if not safe_id or safe_id != record_id:
        return None
    root = root.resolve()
    for metadata_path in root.rglob("record.json"):
        record_dir = metadata_path.parent.resolve()
        if record_dir.name == record_id and _is_relative_to(record_dir, root):
            return record_dir
    return None


def _list_photos(root: Path, file_endpoint: str) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    photos = []
    for metadata_path in root.rglob("photo.json"):
        try:
            photos.append(_photo_summary(metadata_path.parent, file_endpoint))
        except Exception:
            continue
    photos.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return photos


def _photo_summary(photo_dir: Path, file_endpoint: str = "admin_photo_file") -> dict[str, Any]:
    metadata_path = photo_dir / "photo.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    photo_path = next(
        (
            path
            for path in photo_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _ALLOWED_PHOTO_SUFFIXES
        ),
        None,
    )
    if photo_path is None:
        raise FileNotFoundError("photo file not found")
    photo_id = str(metadata.get("photo_id") or photo_dir.name)
    file_url = url_for(file_endpoint, photo_id=photo_id)
    return {
        "photo_id": photo_id,
        "device_id": metadata.get("device_id", ""),
        "experiment_type": metadata.get("experiment_type", ""),
        "captured_at": metadata.get("captured_at", ""),
        "created_at": metadata.get("created_at", ""),
        "filename": photo_path.name,
        "size": photo_path.stat().st_size,
        "file_url": file_url,
        "download_url": f"{file_url}?download=1",
        "analysis": _load_analysis(photo_dir),
    }


def _resolve_photo_dir(root: Path, photo_id: str) -> Path | None:
    safe_id = _safe_text_field(photo_id, "")
    if not safe_id or safe_id != photo_id:
        return None
    root = root.resolve()
    for metadata_path in root.rglob("photo.json"):
        photo_dir = metadata_path.parent.resolve()
        if photo_dir.name == photo_id and _is_relative_to(photo_dir, root):
            return photo_dir
    return None


def _send_photo_file(photo_root: Path, photo_id: str):
    photo_dir = _resolve_photo_dir(photo_root, photo_id)
    if photo_dir is None:
        return jsonify({"error": "photo not found"}), 404
    photo_path = next(
        (
            path
            for path in photo_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _ALLOWED_PHOTO_SUFFIXES
        ),
        None,
    )
    if photo_path is None:
        return jsonify({"error": "photo file not found"}), 404
    return send_file(
        photo_path,
        mimetype=mimetypes.guess_type(photo_path.name)[0] or "image/jpeg",
        as_attachment=request.args.get("download") == "1",
        download_name=photo_path.name,
    )


def _load_analysis(media_dir: Path) -> dict[str, Any]:
    path = media_dir / "analysis.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"items": {}}
    except (OSError, json.JSONDecodeError):
        return {"items": {}}


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
