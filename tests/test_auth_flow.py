from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import pytest
from flask.testing import FlaskClient


_RUNTIME_DIR = Path(tempfile.mkdtemp(prefix="chem_auth_test_"))
os.environ["CHEM_RUNTIME_ASSETS_DIR"] = str(_RUNTIME_DIR)
os.environ["CHEM_ADMIN_PASSWORD"] = "test-admin-password"
os.environ["CHEM_SESSION_SECRET"] = "test-session-secret"

import pipeline.derived_analysis as derived_analysis
import pipeline.web.app as app_module


class BrowserClient(FlaskClient):
    """Model the CSRF header supplied by the real browser adapter."""
    def open(self, *args, **kwargs):
        if kwargs.get("method", "GET").upper() not in {"GET", "HEAD", "OPTIONS"}:
            token = super().open("/api/auth/csrf").get_json()["csrf_token"]
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("X-CSRF-Token", token)
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


@pytest.fixture(autouse=True)
def browser_security_adapter(monkeypatch):
    monkeypatch.setattr(app_module.app, "test_client_class", BrowserClient)
    monkeypatch.setitem(app_module.app.config, "CHEM_ADMIN_LOGIN_LIMIT", 10000)


def _unlock_admin(client) -> None:
    response = client.post("/auth/admin/login", json={"password": "test-admin-password"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/workspace#admin")


def _login_test_identity(client, key: str) -> None:
    _unlock_admin(client)
    response = client.post(f"/admin/test-identities/{key}/login")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/workspace")


def _portal_login(client, identity_id: str, identity_type: str, detail_type: str = ""):
    original_validate = app_module.validate_token
    app_module.validate_token = lambda token, remote_addr: {
        "ok": True,
        "identity_id": identity_id,
        "identity_display": "po***al",
        "user_info": {
            "identityId": identity_id,
            "name": "门户测试用户",
            "identityType": identity_type,
            "detailType": detail_type,
            "dept": "测试院系",
        },
    }
    try:
        return client.post("/auth/pku/callback", json={"token": "short-lived-token"})
    finally:
        app_module.validate_token = original_validate


def test_login_page_has_fluid_and_inline_admin_dialog() -> None:
    client = app_module.app.test_client()
    root = client.get("/")
    assert root.status_code == 302
    assert root.headers["Location"].endswith("/login")
    legacy_pipeline = client.get("/pipeline")
    assert legacy_pipeline.status_code == 404
    page = client.get("/login")
    text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "<canvas" in text
    assert "进入北大门户登录" in text
    assert 'id="adminModal"' in text


def test_portal_roles_are_automatic_and_onboarding_is_disabled() -> None:
    samples = (
        ("portal-student", "学生", "本科生", "学生工作台"),
        ("portal-staff", "职工", "在职", "教师工作台"),
        ("portal-unknown", "其他", "", "教师工作台"),
    )
    for identity_id, identity_type, detail_type, heading in samples:
        client = app_module.app.test_client()
        callback = _portal_login(client, identity_id, identity_type, detail_type)
        assert callback.status_code == 200
        assert callback.get_json()["next_url"].endswith("/workspace")
        assert callback.get_json()["needs_role"] is False
        assert heading in client.get("/workspace").get_data(as_text=True)
        assert client.get("/onboarding").headers["Location"].endswith("/workspace")
        assert client.post("/auth/role", json={"role": "student"}).status_code == 410


def test_admin_override_survives_portal_login_and_can_be_restored() -> None:
    client = app_module.app.test_client()
    identity_id = "portal-role-override"
    _portal_login(client, identity_id, "学生", "本科生")
    _unlock_admin(client)
    changed = client.put(f"/api/workspace/admin/users/{identity_id}/role", json={"role": "teacher"})
    assert changed.get_json()["user"]["role_source"] == "admin"
    client.post("/auth/admin/logout")
    _portal_login(client, identity_id, "学生", "本科生")
    me = client.get("/api/auth/me").get_json()
    assert me["user"]["role"] == "teacher"
    assert me["user"]["portal_role"] == "student"
    assert me["user"]["role_source"] == "admin"
    _unlock_admin(client)
    restored = client.put(f"/api/workspace/admin/users/{identity_id}/role", json={"role": "portal"})
    assert restored.get_json()["user"]["role"] == "student"
    assert restored.get_json()["user"]["role_source"] == "portal"


def test_test_identities_are_fixed_and_role_pages_do_not_mix() -> None:
    client = app_module.app.test_client()
    _login_test_identity(client, "jia")
    page = client.get("/workspace").get_data(as_text=True)
    assert "学生工作台" in page
    assert "课程整理" not in page
    assert "眼镜编号校准" not in page
    assert client.get("/api/auth/me").get_json()["admin_unlocked"] is False
    client.post("/auth/logout")
    _login_test_identity(client, "bing")
    page = client.get("/workspace").get_data(as_text=True)
    assert "教师工作台" in page
    assert "课程整理" in page
    assert "眼镜编号校准" not in page


def test_legacy_pages_redirect_to_workspace_tabs() -> None:
    client = app_module.app.test_client()
    _login_test_identity(client, "ding")
    assert client.get("/teacher-admin").headers["Location"].endswith("/workspace#teacher-tools")
    client.post("/auth/logout")
    _unlock_admin(client)
    assert client.get("/admin").headers["Location"].endswith("/workspace#admin")
    assert client.get("/admin/users").headers["Location"].endswith("/workspace#admin-identities")


def test_discovered_device_assignment_updates_both_mappings() -> None:
    client = app_module.app.test_client()
    _unlock_admin(client)
    device_id = "teacher_android_test_glass_05"
    upload = client.post(
        "/api/glasses/teacher/records/upload",
        data={"device_id": device_id, "experiment_type": "distillation", "started_at": "2026-06-05T09:00:00+08:00", "ended_at": "2026-06-05T09:20:00+08:00", "video": (BytesIO(b"test-video"), "record.mp4")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201
    assignment = client.put(f"/teacher-admin/api/devices/{device_id}", json={"label": "05"})
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
    assert client.put("/admin/api/auth/glasses/06", json={"device_id": device_id}).status_code == 200
    _login_test_identity(client, "jia")
    upload = client.post(
        "/api/glasses/records/upload",
        data={"device_id": device_id, "experiment_type": "distillation", "started_at": "2026-06-05T09:10:00+08:00", "ended_at": "2026-06-05T10:00:00+08:00", "video": (BytesIO(b"test-video"), "record.mp4")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201
    pending = client.get("/api/claims/pending").get_json()
    group = next(item for item in pending["groups"] if item["glass_number"] == "06" and item["date"] == "2026-06-05")
    assert group["period"] == "上午"
    matched = client.post("/api/claims/match", json={"glass_number": "06", "started_at": group["started_at"], "ended_at": group["ended_at"]}).get_json()["items"]
    claim = client.post("/api/claims", json={"glass_number": "06", "started_at": group["started_at"], "ended_at": group["ended_at"], "items": matched})
    assert claim.status_code == 201
    remaining = client.get("/api/claims/pending").get_json()["groups"]
    assert not any(item["glass_number"] == "06" and item["date"] == "2026-06-05" for item in remaining)


def _prepare_teacher_audio_records(client, count: int = 2, *, month: str = "07", glass: str = "07"):
    _unlock_admin(client)
    device_id = f"teacher_report_device_{glass}"
    assert client.put(f"/admin/api/auth/glasses/{glass}", json={"device_id": device_id}).status_code == 200
    _login_test_identity(client, "ding")
    original_enqueue = app_module._media_analysis_queue.enqueue
    app_module._media_analysis_queue.enqueue = lambda *args, **kwargs: None
    records = []
    try:
        for index in range(count):
            upload = client.post(
                "/api/glasses/teacher/records/upload",
                data={"device_id": device_id, "experiment_type": "distillation", "started_at": f"2026-{month}-0{index + 1}T10:00:00+08:00", "ended_at": f"2026-{month}-0{index + 1}T10:40:00+08:00", "video": (BytesIO(b"RIFF-test-audio"), f"lesson-{index}.wav")},
                content_type="multipart/form-data",
            )
            assert upload.status_code == 201
            record = upload.get_json()["record"]
            Path(record["path"], "analysis.json").write_text(json.dumps({"items": {"audio": {"status": "succeeded", "transcript": f"第{index + 1}节课的有效转写"}}}, ensure_ascii=False), encoding="utf-8")
            records.append(record)
    finally:
        app_module._media_analysis_queue.enqueue = original_enqueue
    matched = client.post("/api/claims/match", json={"glass_number": glass, "started_at": f"2026-{month}-01T00:00", "ended_at": f"2026-{month}-03T00:00"}).get_json()["items"]
    claim = client.post("/api/claims", json={"glass_number": glass, "started_at": f"2026-{month}-01T00:00", "ended_at": f"2026-{month}-03T00:00", "items": matched})
    assert claim.status_code == 201
    return records


def test_teacher_report_sources_are_scoped_and_validate_range() -> None:
    client = app_module.app.test_client()
    records = _prepare_teacher_audio_records(client)
    sources = client.get("/api/workspace/teacher/report-sources?start_date=2026-07-01&end_date=2026-07-02&experiment_type=distillation&glass_number=07")
    assert sources.status_code == 200
    assert {item["record_id"] for item in sources.get_json()["sources"]} == {item["record_id"] for item in records}
    assert all(item["eligible"] for item in sources.get_json()["sources"])
    assert client.get("/api/workspace/teacher/report-sources?start_date=2026-07-01&end_date=2026-08-01").status_code == 400
    assert client.get("/api/workspace/teacher/report-sources?start_date=2026-07-03&end_date=2026-07-01").status_code == 400
    client.post("/auth/logout")
    _login_test_identity(client, "bing")
    other = client.get("/api/workspace/teacher/report-sources?start_date=2026-07-01&end_date=2026-07-02")
    assert other.get_json()["sources"] == []
    assert client.get("/teacher-admin/api/records").get_json()["records"] == []


def test_course_report_job_success_download_and_common_issue_minimum() -> None:
    client = app_module.app.test_client()
    records = _prepare_teacher_audio_records(client, month="08", glass="08")
    record_ids = [item["record_id"] for item in records]
    original_schedule = app_module._schedule_workspace_report_job
    original_generate = derived_analysis.generate_daily_course
    app_module._schedule_workspace_report_job = lambda path: None
    derived_analysis.generate_daily_course = lambda *args, **kwargs: {"title": "测试课程整理", "course_content": "整理后的课程内容", "highlights": ["重点"], "provider": "test", "model": "test-model"}
    try:
        created = client.post("/api/workspace/teacher/reports", json={"kind": "course", "start_date": "2026-08-01", "end_date": "2026-08-02", "experiment_type": "distillation", "glass_number": "08", "record_ids": record_ids})
        assert created.status_code == 202
        report_id = created.get_json()["report"]["report_id"]
        app_module._run_workspace_report_job(app_module._workspace_report_job_root() / f"{report_id}.json")
        detail = client.get(f"/api/workspace/teacher/reports/{report_id}").get_json()["report"]
        assert detail["status"] == "succeeded"
        assert detail["owner_identity_id"] == "demo-ding"
        assert detail["input_record_ids"] == record_ids
        assert detail["model"] == "test-model"
        download = client.get(f"/api/workspace/teacher/reports/{report_id}/download")
        assert download.status_code == 200
        assert "整理后的课程内容" in download.get_data(as_text=True)

        def fail_generation(*args, **kwargs):
            raise RuntimeError("模拟模型失败")

        derived_analysis.generate_daily_course = fail_generation
        failed = client.post("/api/workspace/teacher/reports", json={"kind": "course", "start_date": "2026-08-01", "end_date": "2026-08-02", "record_ids": record_ids})
        failed_id = failed.get_json()["report"]["report_id"]
        app_module._run_workspace_report_job(app_module._workspace_report_job_root() / f"{failed_id}.json")
        failed_report = client.get(f"/api/workspace/teacher/reports/{failed_id}").get_json()["report"]
        assert failed_report["status"] == "failed"
        assert "模拟模型失败" in failed_report["warnings"]

        insufficient = client.post("/api/workspace/teacher/reports", json={"kind": "common_issues", "start_date": "2026-08-01", "end_date": "2026-08-02", "experiment_type": "distillation", "record_ids": [record_ids[0]]})
        assert insufficient.status_code == 200
        assert insufficient.get_json()["report"]["status"] == "insufficient_data"
    finally:
        app_module._schedule_workspace_report_job = original_schedule
        derived_analysis.generate_daily_course = original_generate


def test_running_report_job_is_requeued_after_restart() -> None:
    job_path = app_module._workspace_report_job_root() / "restart-test.json"
    app_module._write_common_issue_json(job_path, {"job_id": "restart-test", "status": "running"})
    scheduled = []
    original_schedule = app_module._schedule_workspace_report_job
    app_module._schedule_workspace_report_job = lambda path: scheduled.append(path)
    try:
        app_module._recover_workspace_report_jobs()
    finally:
        app_module._schedule_workspace_report_job = original_schedule
    assert scheduled == [job_path]
    assert app_module._read_common_issue_json(job_path)["status"] == "queued"
