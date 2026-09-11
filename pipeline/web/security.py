"""Transport, browser-session and device boundaries; business ownership stays in app.py."""
from __future__ import annotations

from collections import OrderedDict, deque
from datetime import timedelta
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
from threading import Lock
import time

from flask import g, jsonify, redirect, request, session
from werkzeug.middleware.proxy_fix import ProxyFix


def _loopback(address: str | None) -> bool:
    try:
        return ipaddress.ip_address(address or "").is_loopback
    except ValueError:
        return False


def device_identity(path: Path, authorization: str) -> dict | None:
    """Read on each request so revocation takes effect without worker restarts."""
    if not authorization.startswith("Bearer ") or len(authorization) > 512:
        return None
    token = authorization[7:]
    token_id, separator, secret = token.partition(".")
    if not separator or len(secret) < 40:
        return None
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
        entry = registry["devices"].get(token_id)
        if not isinstance(entry, dict) or entry.get("enabled") is not True:
            return None
        expected = str(entry.get("sha256", ""))
        if not hmac.compare_digest(expected, hashlib.sha256(token.encode()).hexdigest()):
            return None
        if entry.get("role") not in {"student", "teacher"}:
            return None
        return {"token_id": token_id, "role": entry["role"], "label": entry.get("label", "")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None  # Missing/malformed registry must fail closed.


class LoginWindow:
    def __init__(self):
        self.lock = Lock()
        self.entries = OrderedDict()

    def allow(self, address: str, limit: int, window: int = 300) -> bool:
        now = time.monotonic()
        with self.lock:
            attempts = self.entries.setdefault(address, deque())
            while attempts and attempts[0] <= now - window:
                attempts.popleft()
            self.entries.move_to_end(address)
            if len(self.entries) > 4096:
                self.entries.popitem(last=False)
            if len(attempts) >= limit:
                return False
            attempts.append(now)
            return True


def install_security(app, current_user, admin_unlocked, registry_path: Path) -> None:
    mode = os.getenv("CHEM_SECURITY_MODE", "production").lower()
    if mode not in {"production", "development"}:
        raise RuntimeError("CHEM_SECURITY_MODE must be production or development")
    require_https = os.getenv("CHEM_REQUIRE_HTTPS") == "1"
    require_device_auth = os.getenv("CHEM_REQUIRE_DEVICE_AUTH") == "1"
    if require_device_auth and not require_https:
        raise RuntimeError("Device credentials require HTTPS; do not send credentials over plain HTTP")
    if require_https and len(os.getenv("CHEM_SESSION_SECRET", "")) < 32:
        raise RuntimeError("Production requires a unique CHEM_SESSION_SECRET of at least 32 characters")
    app.config.update(
        CHEM_SECURITY_MODE=mode,
        CHEM_REQUIRE_HTTPS=require_https,
        CHEM_REQUIRE_DEVICE_AUTH=require_device_auth,
        CHEM_DEVICE_REGISTRY=str(registry_path),
        CHEM_ENABLE_DEBUG_ROUTES=os.getenv("CHEM_ENABLE_DEBUG_ROUTES") == "1",
        CHEM_ADMIN_LOGIN_LIMIT=5,
        SESSION_COOKIE_SECURE=require_https,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    # Only enable behind our private, non-published Gunicorn container. Nginx
    # overwrites (does not append) forwarded headers. Never trust arbitrary clients.
    if os.getenv("CHEM_TRUST_PROXY") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    login_window = LoginWindow()
    app.extensions["chem_login_window"] = login_window

    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return session["csrf_token"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.get("/api/auth/csrf")
    def security_csrf():
        return jsonify({"csrf_token": csrf_token()})

    def deny(message, status):
        return jsonify({"error": message}), status

    @app.before_request
    def security_boundary():
        # Registered before background-job hooks: rejected traffic never enqueues work.
        production = app.config["CHEM_SECURITY_MODE"] == "production"
        if app.config["CHEM_REQUIRE_HTTPS"] and not request.is_secure:
            return deny("HTTPS is required", 426)
        if not production and not _loopback(request.remote_addr):
            return deny("Development mode accepts loopback clients only", 403)

        path = request.path
        if path.startswith(("/static-videos/", "/teacher-admin/api/derived/")):
            return deny("This endpoint is retired", 410)
        debug_route = path in {
            "/pipeline", "/experiment-lab", "/api/detect", "/api/analyze",
            "/api/safety/check", "/api/electrolysis/check", "/api/models",
            "/api/references", "/api/experiment-configs", "/api/config-reference-image",
        } or path.startswith("/api/experiment-lab/")
        test_route = path.startswith(("/admin/test-identities", "/api/workspace/admin/test-identities"))
        if debug_route and not app.config["CHEM_ENABLE_DEBUG_ROUTES"]:
            return deny("Not found", 404)
        if request.url_rule is None:
            return None  # Preserve genuine 404/405 rather than a catch-all success page.

        user = current_user()
        admin = admin_unlocked()
        logged_in = admin or user is not None
        teacher = admin or bool(user and user.get("role") == "teacher")
        is_read = request.method in {"GET", "HEAD", "OPTIONS"}

        if path.startswith("/api/glasses/"):
            # Transitional compatibility only: existing APKs have no credentials.
            # This is NOT authentication; enable only after HTTPS + APK provisioning.
            if not app.config["CHEM_REQUIRE_DEVICE_AUTH"]:
                return None
            # Browser health/config and media playback use their existing session.
            browser_read = is_read and (path == "/api/glasses/health" or path.startswith("/api/glasses/demo/videos"))
            if not (browser_read and logged_in):
                identity = device_identity(Path(app.config["CHEM_DEVICE_REGISTRY"]), request.headers.get("Authorization", ""))
                if identity is None:
                    return deny("Device credential required or revoked", 401)
                if path.startswith("/api/glasses/teacher/") and identity["role"] != "teacher":
                    return deny("Teacher device credential required", 403)
                if path in {"/api/glasses/records/upload", "/api/glasses/photos/upload"} and identity["role"] != "student":
                    return deny("Student device credential required", 403)
                g.chem_device = identity
                return None  # Device bearer authentication does not use browser cookies.
        elif request.endpoint in {"index", "login_page", "pku_callback_route", "admin_login", "auth_me", "security_csrf", "static"}:
            pass
        elif debug_route or test_route or path.startswith(("/api/workspace/admin/", "/admin/api/auth/")):
            if not admin:
                return deny("Administrator login required", 403 if logged_in else 401)
        elif path.startswith(("/admin/api/demo/", "/admin/api/feedback", "/admin/api/records", "/admin/api/photos", "/teacher-admin/api/")):
            if not teacher:
                return deny("Teacher or administrator login required", 403 if logged_in else 401)
        elif path.startswith(("/api/claims", "/api/workspace/", "/api/auth/", "/auth/")) or path in {"/workspace", "/onboarding", "/admin", "/admin/users", "/teacher-admin"}:
            if not logged_in:
                if is_read and not path.startswith("/api/"):
                    return redirect("/login")
                return deny("Login required", 401)
        elif not admin:
            # Newly introduced unclassified endpoints are never public by accident.
            return deny("Administrator login required", 403 if logged_in else 401)

        if not is_read:
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not expected or not hmac.compare_digest(str(expected), str(supplied)):
                return deny("Session verification failed; refresh the page and retry", 403)
        if path == "/auth/admin/login" and not is_read:
            if not login_window.allow(request.remote_addr or "unknown", app.config["CHEM_ADMIN_LOGIN_LIMIT"]):
                response = jsonify({"error": "Too many login attempts; retry in 5 minutes"})
                response.status_code = 429
                response.headers["Retry-After"] = "300"
                return response
        return None

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self'; object-src 'none'"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.path.startswith(("/api/", "/admin/", "/teacher-admin/", "/auth/")) or request.path in {"/workspace", "/login"}:
            response.headers["Cache-Control"] = "no-store"
        return response
