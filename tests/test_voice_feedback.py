import io
import json
from unittest.mock import Mock

from flask import Flask
from pipeline.web import feedback


def test_feedback_audio_is_separate_retry_is_idempotent_and_schedule_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("CHEM_RUNTIME_ASSETS_DIR", str(tmp_path))
    monkeypatch.setattr(feedback, "glass_for_device", lambda device: "11")
    binding = Mock(return_value={"identity_id": "scheduled-user"})
    monkeypatch.setattr(feedback, "teacher_binding_for_glass_at", binding)
    queue = Mock()
    app = Flask(__name__)
    feedback.register_feedback(app, queue)
    client = app.test_client()
    for role in ("teacher", "student"):
        payload = {"client_record_id": "one-session", "device_id": "glasses", "role": role,
                   "started_at": "2026-10-19T10:00:00+08:00", "ended_at": "2026-10-19T10:01:00+08:00"}
        response = client.post("/api/glasses/feedback/upload", data={**payload, "audio": (io.BytesIO(b"original-audio"), "record_audio.wav")})
        assert response.status_code == 201
        item = response.json["feedback"]
        assert item["owner_identity_id"] == "scheduled-user" and item["glass_number"] == "11"
        folder = tmp_path / "feedback" / item["feedback_id"]
        assert (folder / "feedback_audio.wav").read_bytes() == b"original-audio"
        assert client.get(item["audio_url"]).data == b"original-audio"
        duplicate = client.post("/api/glasses/feedback/upload", data={**payload, "audio": (io.BytesIO(b"retry"), "record_audio.wav")})
        assert duplicate.json["feedback"]["feedback_id"] == item["feedback_id"]
        assert (folder / "feedback_audio.wav").read_bytes() == b"original-audio"
        (folder / "analysis.json").write_text(json.dumps({"items": {"feedback_audio.wav": {"status": "succeeded", "transcript": "建议提高提示音音量"}}}), encoding="utf-8")
    assert queue.enqueue.call_count == 2
    assert {call.kwargs["role"] for call in binding.call_args_list} == {"teacher", "student"}
    assert len(feedback.list_voice_feedback()) == 2
    assert all(item["content"] == "建议提高提示音音量" for item in feedback.list_voice_feedback())
    assert not (tmp_path / "user_records").exists() and not (tmp_path / "teacher_records").exists()
