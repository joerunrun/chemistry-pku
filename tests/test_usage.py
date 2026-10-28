import uuid

from flask import Flask
import pytest

from pipeline.web import usage


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHEM_RUNTIME_ASSETS_DIR", str(tmp_path))
    monkeypatch.setattr(usage, "glass_for_device", lambda device: "03")
    app = Flask(__name__)
    app.config["role"] = None
    usage.register_usage(app, lambda: {"role": app.config["role"]} if app.config["role"] else None,
                         lambda: app.config["role"] == "admin")
    return app.test_client()


def event(action="qa", role="student", when="2026-10-28T16:05:00Z"):
    return {"event_id": str(uuid.uuid4()), "action": action, "role": role,
            "device_id": "glasses-03", "occurred_at": when}


def test_usage_retries_do_not_inflate_counts_and_dates_use_beijing(client):
    item = event()
    assert client.post("/api/glasses/usage", json=item).json["recorded"]
    assert not client.post("/api/glasses/usage", json=item).json["recorded"]
    client.post("/api/glasses/usage", json=event("video_play", "teacher"))
    client.post("/api/glasses/usage", json=event("photo", when="2026-10-28T08:00:00Z"))
    client.application.config["role"] = "teacher"
    data = client.get("/api/workspace/usage?start=2026-10-29&end=2026-10-29").json
    assert data["total"] == 2 and data["active_devices"] == 1
    assert data["trend"] == [{"date": "2026-10-29", "count": 2}]
    assert {v["action"]: v["count"] for v in data["actions"]}["qa"] == 1
    filtered = client.get("/api/workspace/usage?start=2026-10-29&end=2026-10-29&role=student").json
    assert filtered["total"] == 1


def test_usage_dashboard_permissions_and_empty_start(client):
    assert client.get("/api/workspace/usage").status_code == 401
    client.application.config["role"] = "student"
    assert client.get("/api/workspace/usage").status_code == 403
    client.application.config["role"] = "admin"
    data = client.get("/api/workspace/usage").json
    assert data["total"] == 0 and data["first_event_at"] is None
    assert all(v["count"] == 0 for v in data["actions"])
    assert client.post("/api/glasses/usage", json=event("not_a_function")).status_code == 400
    assert client.get("/api/workspace/usage?start=2026-10-30&end=2026-10-28").status_code == 400
