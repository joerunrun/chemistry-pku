"""Voice feedback lives apart from lesson recordings and reports."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Lock

from flask import jsonify, request, send_file

from pipeline.resource_paths import runtime_asset_path
from pipeline.web.identity_store import glass_for_device
from pipeline.web.teacher_binding_store import teacher_binding_for_glass_at

_upload_lock = Lock()


def feedback_item(path: Path) -> dict:
    item = json.loads(path.read_text(encoding="utf-8"))
    analysis_path = path.parent / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8")) if analysis_path.exists() else {}
    audio = analysis.get("items", {}).get("feedback_audio.wav", {})
    item["content"] = audio.get("transcript") or ""
    item["transcription_status"] = audio.get("status", "queued")
    item["audio_url"] = f"/admin/api/feedback/{item['feedback_id']}/audio"
    return item


def list_voice_feedback() -> list[dict]:
    items = []
    for path in runtime_asset_path("feedback").glob("*/feedback.json"):
        try:
            items.append(feedback_item(path))
        except (OSError, ValueError):
            continue
    return items


def register_feedback(app, analysis_queue) -> None:
    @app.post("/api/glasses/feedback/upload")
    def glasses_upload_feedback():
        audio = request.files.get("audio")
        client_id = request.form.get("client_record_id", "").strip()
        device_id = request.form.get("device_id", "").strip()
        role = request.form.get("role", "").strip()
        if audio is None or not client_id or not device_id or role not in {"teacher", "student"}:
            return jsonify({"error": "audio, client_record_id, device_id and role are required"}), 400
        if Path(audio.filename or "").suffix.lower() != ".wav":
            return jsonify({"error": "feedback audio must be WAV"}), 400
        feedback_id = hashlib.sha256(f"{role}:{device_id}:{client_id}".encode()).hexdigest()[:32]
        folder = runtime_asset_path("feedback", feedback_id)
        metadata_path = folder / "feedback.json"
        with _upload_lock:
            if metadata_path.exists():
                return jsonify({"status": "ok", "feedback": feedback_item(metadata_path)}), 201
            now = datetime.now(timezone.utc).isoformat()
            started_at = request.form.get("started_at", "") or now
            try:
                when = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            except ValueError:
                return jsonify({"error": "invalid started_at"}), 400
            glass_number = glass_for_device(device_id)
            binding = teacher_binding_for_glass_at(glass_number, when.isoformat(), role=role) if glass_number else None
            folder.mkdir(parents=True, exist_ok=True)
            audio_path = folder / "feedback_audio.wav"
            audio.save(audio_path)
            item = {
                "feedback_id": feedback_id, "client_record_id": client_id,
                "title": "教师语音反馈" if role == "teacher" else "学生语音反馈",
                "source": "voice", "role": role, "device_id": device_id,
                "glass_number": glass_number, "owner_identity_id": binding["identity_id"] if binding else None,
                "started_at": started_at, "ended_at": request.form.get("ended_at", ""), "created_at": now,
            }
            temporary = folder / "feedback.tmp"
            temporary.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
            temporary.replace(metadata_path)
            analysis_queue.enqueue(folder, audio_path)
            return jsonify({"status": "ok", "feedback": feedback_item(metadata_path)}), 201

    @app.get("/admin/api/feedback/<feedback_id>/audio")
    def feedback_audio(feedback_id):
        if len(feedback_id) != 32 or any(c not in "0123456789abcdef" for c in feedback_id):
            return jsonify({"error": "feedback not found"}), 404
        path = runtime_asset_path("feedback", feedback_id, "feedback_audio.wav")
        if not path.is_file():
            return jsonify({"error": "feedback not found"}), 404
        return send_file(path, mimetype="audio/wav", conditional=True)
