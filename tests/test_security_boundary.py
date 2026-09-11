"""No real network, credentials, media queues or model calls."""
import pytest
from flask import Flask, session
from pipeline.web.security import install_security


@pytest.fixture
def secured(tmp_path, monkeypatch):
    for key in ("CHEM_REQUIRE_HTTPS", "CHEM_REQUIRE_DEVICE_AUTH", "CHEM_TRUST_PROXY",
                "CHEM_ENABLE_DEBUG_ROUTES"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CHEM_SECURITY_MODE", "production")
    app = Flask(__name__)
    app.secret_key = "unit-test-only-not-a-real-secret-12345"
    install_security(app, lambda: session.get("user"), lambda: session.get("admin", False), tmp_path / "devices.json")
    endpoints = {
        "/login": "login_page", "/auth/admin/login": "admin_login",
        "/admin/api/feedback": "feedback", "/admin/api/demo/videos": "demo",
        "/api/glasses/health": "health", "/api/glasses/records/upload": "upload",
        "/pipeline": "pipeline", "/teacher-admin/api/derived/daily-course": "derived",
        "/api/workspace/admin/test-identities": "identities", "/new-unclassified": "new",
    }
    for path, endpoint in endpoints.items():
        app.add_url_rule(path, endpoint, lambda: {"ok": True}, methods=["GET", "POST", "DELETE"])
    return app


@pytest.mark.parametrize("path,status", [
    ("/login", 200), ("/admin/api/feedback", 401), ("/admin/api/demo/videos", 401),
    ("/pipeline", 404), ("/teacher-admin/api/derived/daily-course", 410),
    ("/api/workspace/admin/test-identities", 401), ("/new-unclassified", 401),
    ("/api/glasses/health", 200),
])
def test_anonymous_boundary(secured, path, status):
    assert secured.test_client().get(path).status_code == status


def test_browser_csrf_and_role(secured):
    client = secured.test_client()
    with client.session_transaction() as state:
        state["user"] = {"role": "student"}
    assert client.get("/admin/api/feedback").status_code == 403
    with client.session_transaction() as state:
        state["user"] = {"role": "teacher"}
    assert client.get("/admin/api/feedback").status_code == 200
    assert client.post("/admin/api/feedback", json={}).status_code == 403
    response = client.get("/api/auth/csrf")
    token = response.json["csrf_token"]
    assert "HttpOnly" in response.headers["Set-Cookie"]
    assert "SameSite=Lax" in response.headers["Set-Cookie"]
    assert client.post("/admin/api/feedback", headers={"X-CSRF-Token": token}).status_code == 200
    assert client.delete("/admin/api/feedback", headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_login_throttle(secured):
    client = secured.test_client()
    token = client.get("/api/auth/csrf").json["csrf_token"]
    for _ in range(5):
        assert client.post("/auth/admin/login", headers={"X-CSRF-Token": token}).status_code == 200
    assert client.post("/auth/admin/login", headers={"X-CSRF-Token": token, "X-Real-IP": "1.2.3.4"}).status_code == 429


def test_legacy_device_compatibility_and_future_fail_closed(secured):
    client = secured.test_client()
    assert client.post("/api/glasses/records/upload").status_code == 200
    secured.config["CHEM_REQUIRE_DEVICE_AUTH"] = True
    assert client.post("/api/glasses/records/upload").status_code == 401
    secured.config["CHEM_REQUIRE_HTTPS"] = True
    assert client.get("/login").status_code == 426
    assert client.get("/login", base_url="https://localhost").status_code == 200


def test_security_headers(secured):
    response = secured.test_client().get("/login")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" not in response.headers
