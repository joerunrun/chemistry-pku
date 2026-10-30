from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify, request
import pytest

from pipeline.web import usage


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHEM_RUNTIME_ASSETS_DIR", str(tmp_path))
    class Clock(datetime):
        current = datetime(2026, 10, 30, 16, 5, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)
    monkeypatch.setattr(usage, "datetime", Clock)
    app = Flask(__name__)
    app.config.update(role=None, reply={}, status=200)
    def reply():
        return jsonify(app.config["reply"]), app.config["status"]
    for endpoint in ["glasses_qa", "create_glasses_job", "glasses_upload_record", "glasses_upload_teacher_record",
                     "glasses_upload_photo", "glasses_upload_teacher_photo", "glasses_upload_feedback"]:
        app.add_url_rule("/" + endpoint, endpoint, reply, methods=["POST"])
    app.add_url_rule("/video/<name>", "glasses_demo_video_stream", lambda name: ("video", 206))
    usage.register_usage(app, lambda: {"role": app.config["role"]} if app.config["role"] else None,
                         lambda: app.config["role"] == "admin")
    return app.test_client()


def summary(client, role="all"):
    return client.get(f"/api/workspace/usage?start=2026-10-31&end=2026-10-31&role={role}").json


def test_server_actions_dates_roles_and_permissions(client):
    assert client.get("/api/workspace/usage").status_code == 401
    client.application.config["role"] = "student"
    assert client.get("/api/workspace/usage").status_code == 403
    client.application.config["role"] = "teacher"
    assert summary(client)["total"] == 0
    client.post("/glasses_qa")
    client.application.config["reply"] = {"job_id": "job-1"}
    client.post("/create_glasses_job", data={"mode": "safety"})
    data = summary(client)
    assert data["total"] == 2 and data["unknown_role_count"] == 2
    assert data["trend"] == [{"date": "2026-10-31", "count": 2}]
    assert {v["action"]: v["count"] for v in data["actions"]}["qa"] == 1
    assert summary(client, "student")["total"] == 0
    assert summary(client, "unknown")["total"] == 2
    client.application.config["status"] = 500
    client.post("/glasses_qa")
    assert summary(client)["total"] == 2
    assert client.get("/api/workspace/usage?start=2026-10-31&end=2026-10-30").status_code == 400


def test_upload_retries_teacher_audio_and_video_session_merge(client):
    client.application.config["role"] = "admin"
    record = {"record_id": "r1", "device_id": "eye03", "started_at": "2026-10-31T00:00:00+08:00",
              "files": [{"filename": "record_audio.wav"}]}
    client.application.config["reply"] = {"record": record}
    client.post("/glasses_upload_teacher_record")
    assert summary(client)["total"] == 0
    for _ in range(2):
        client.post("/glasses_upload_record")
    record["files"] = [{"filename": "record_video.mp4"}]
    for name in ("segment1", "segment2"):
        record["record_id"] = name
        client.post("/glasses_upload_teacher_record")
    client.application.config["reply"] = {"photo": {"photo_id": "p1", "device_id": "eye03"}}
    for _ in range(2):
        client.post("/glasses_upload_photo")
    client.application.config["reply"] = {"feedback": {"feedback_id": "f1", "role": "teacher", "device_id": "eye03"}}
    for _ in range(2):
        client.post("/glasses_upload_feedback")
    assert summary(client)["total"] == 4
    assert summary(client, "student")["total"] == 2
    assert summary(client, "teacher")["total"] == 2


def test_video_heads_ranges_and_dual_eyes_are_merged(client):
    client.application.config["role"] = "admin"
    client.head("/video/demo")
    client.get("/video/demo", headers={"Range": "bytes=900-"})
    assert summary(client)["total"] == 0
    client.get("/video/demo", headers={"Range": "bytes=0-"})
    client.get("/video/demo")
    assert summary(client)["total"] == 1
    usage.datetime.current += timedelta(seconds=121)
    client.get("/video/demo")
    assert summary(client)["total"] == 2
