from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import tempfile


_RUNTIME_DIR = Path(tempfile.mkdtemp(prefix="chem_auth_test_"))
os.environ["CHEM_RUNTIME_ASSETS_DIR"] = str(_RUNTIME_DIR)
os.environ["CHEM_ADMIN_PASSWORD"] = "test-admin-password"
os.environ["CHEM_SESSION_SECRET"] = "test-session-secret"

import pipeline.web.app as app_module


def _unlock_admin(client) -> None:
    response = client.post(
        "/auth/admin/login",
        json={"password": "test-admin-password"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin")


def test_admin_and_user_routes_are_independent() -> None:
    client = app_module.app.test_client()
    _unlock_admin(client)

    test_page = client.get("/admin/test-identities")
    assert test_page.status_code == 200
    assert "demo-jia" in test_page.get_data(as_text=True)

    login = client.post("/admin/test-identities/jia/login")
    assert login.status_code == 302
    assert login.headers["Location"].endswith("/onboarding")

    role = client.post("/auth/role", data={"role": "student"})
    assert role.status_code == 302
    assert role.headers["Location"].endswith("/workspace")

    workspace = client.get("/workspace")
    admin = client.get("/admin")
    assert "学生工作台" in workspace.get_data(as_text=True)
    assert "眼镜编号校准" in admin.get_data(as_text=True)
    assert client.get("/admin/users").status_code == 200
    assert client.get("/api/workspace/data").status_code == 200

    me = client.get("/api/auth/me").get_json()
    assert me["authenticated"] is True
    assert me["admin_unlocked"] is True
    assert me["user"]["identity_id"] == "demo-jia"


def test_first_and_existing_test_identities() -> None:
    client = app_module.app.test_client()
    _unlock_admin(client)

    first = client.post("/admin/test-identities/bing/login")
    assert first.headers["Location"].endswith("/onboarding")

    client.post("/auth/logout")
    existing = client.post("/admin/test-identities/ding/login")
    assert existing.headers["Location"].endswith("/workspace")
    assert "教师工作台" in client.get("/workspace").get_data(as_text=True)


def test_pku_callback_stores_complete_user_info() -> None:
    original_validate = app_module.validate_token
    app_module.validate_token = lambda token, remote_addr: {
        "ok": True,
        "identity_id": "pku-test-user",
        "identity_display": "pk***er",
        "user_info": {
            "identityId": "pku-test-user",
            "name": "门户测试用户",
            "department": "测试院系",
        },
    }
    try:
        client = app_module.app.test_client()
        callback = client.post("/auth/pku/callback", json={"token": "short-lived-token"})
        assert callback.status_code == 200
        assert callback.get_json()["next_url"].endswith("/onboarding")

        client.post("/auth/role", json={"role": "student"})
        profile = client.get("/api/auth/profile").get_json()
        assert profile["profile"]["name"] == "门户测试用户"
        assert profile["profile"]["department"] == "测试院系"
    finally:
        app_module.validate_token = original_validate


def test_test_identity_reset_replays_onboarding() -> None:
    client = app_module.app.test_client()
    _unlock_admin(client)
    client.post("/admin/test-identities/yi/login")
    assert client.get("/workspace").status_code == 200

    reset = client.post("/admin/test-identities/yi/reset")
    assert reset.status_code == 302
    replay = client.post("/admin/test-identities/yi/login")
    assert replay.headers["Location"].endswith("/onboarding")


def test_discovered_device_assignment_updates_both_mappings() -> None:
    client = app_module.app.test_client()
    _unlock_admin(client)
    device_id = "teacher_android_test_glass_05"

    upload = client.post(
        "/api/glasses/teacher/records/upload",
        data={
            "device_id": device_id,
            "experiment_type": "distillation",
            "started_at": "2026-06-05T09:00:00+08:00",
            "ended_at": "2026-06-05T09:20:00+08:00",
            "video": (BytesIO(b"test-video"), "record.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201

    assignment = client.put(
        f"/teacher-admin/api/devices/{device_id}",
        json={"label": "05"},
    )
    assert assignment.status_code == 200
    assert assignment.get_json()["device"]["label"] == "05"

    glasses = client.get("/admin/api/auth/glasses").get_json()["glasses"]
    assert next(item for item in glasses if item["glass_number"] == "05")["device_id"] == device_id
    mappings = json.loads((_RUNTIME_DIR / "device_mappings.json").read_text(encoding="utf-8"))
    assert mappings[device_id] == "05"


def test_pending_claim_overview_disappears_after_claim() -> None:
    client = app_module.app.test_client()
    _unlock_admin(client)
    device_id = "student_android_pending_glass_06"
    calibration = client.put(
        "/admin/api/auth/glasses/06",
        json={"device_id": device_id},
    )
    assert calibration.status_code == 200

    client.post("/admin/test-identities/jia/reset")
    client.post("/admin/test-identities/jia/login")
    client.post("/auth/role", data={"role": "student"})
    upload = client.post(
        "/api/glasses/records/upload",
        data={
            "device_id": device_id,
            "experiment_type": "distillation",
            "started_at": "2026-06-05T09:10:00+08:00",
            "ended_at": "2026-06-05T10:00:00+08:00",
            "video": (BytesIO(b"test-video"), "record.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201

    pending = client.get("/api/claims/pending")
    assert pending.status_code == 200
    group = next(
        item for item in pending.get_json()["groups"]
        if item["glass_number"] == "06" and item["date"] == "2026-06-05"
    )
    assert group["period"] == "上午"
    assert group["record_count"] == 1

    matched = client.post(
        "/api/claims/match",
        json={
            "glass_number": "06",
            "started_at": group["started_at"],
            "ended_at": group["ended_at"],
        },
    ).get_json()["items"]
    claim = client.post(
        "/api/claims",
        json={
            "glass_number": "06",
            "started_at": group["started_at"],
            "ended_at": group["ended_at"],
            "items": matched,
        },
    )
    assert claim.status_code == 201
    remaining = client.get("/api/claims/pending").get_json()["groups"]
    assert not any(item["glass_number"] == "06" and item["date"] == "2026-06-05" for item in remaining)
