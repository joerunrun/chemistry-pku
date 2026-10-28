"""New usage events only; independent of lesson media and ownership."""
from datetime import date, datetime, timedelta, timezone
import sqlite3
import uuid
from zoneinfo import ZoneInfo

from flask import g, jsonify, request

from pipeline.resource_paths import runtime_asset_path
from pipeline.web.identity_store import glass_for_device

ACTIONS = {
    "qa": "提问", "video_play": "视频播放", "experiment_check": "实验纠错",
    "safety_check": "安全检查", "photo": "拍照", "audio_record": "主动录音",
    "video_record": "录像", "feedback": "意见反馈",
}
BEIJING = ZoneInfo("Asia/Shanghai")


def _connect():
    path = runtime_asset_path("usage", "usage.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.execute("""CREATE TABLE IF NOT EXISTS usage_events (
        event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, day TEXT NOT NULL,
        action TEXT NOT NULL, role TEXT NOT NULL, device_id TEXT NOT NULL,
        glass_number TEXT NOT NULL, label TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS usage_day_role ON usage_events(day, role)")
    return db


def record_event(event):
    event_id = str(uuid.UUID(event["event_id"]))
    action, role = event["action"], event["role"]
    if action not in ACTIONS or role not in {"student", "teacher"}:
        raise ValueError("invalid action or role")
    device = str(event["device_id"]).strip()
    if not device or len(device) > 128:
        raise ValueError("invalid device_id")
    when = datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError("occurred_at requires timezone")
    glass = glass_for_device(device) or ""
    with _connect() as db:
        inserted = db.execute("INSERT OR IGNORE INTO usage_events VALUES (?,?,?,?,?,?,?,?)", (
            event_id, when.astimezone(timezone.utc).isoformat(), when.astimezone(BEIJING).date().isoformat(),
            action, role, device, glass, str(event.get("label", ""))[:200],
        )).rowcount
    db.close()
    return bool(inserted)


def usage_summary(start, end, role="all"):
    if start > end or role not in {"all", "student", "teacher"}:
        raise ValueError("日期范围或角色无效")
    where, values = "day BETWEEN ? AND ?", [start.isoformat(), end.isoformat()]
    if role != "all":
        where += " AND role=?"
        values.append(role)
    with _connect() as db:
        counts = dict(db.execute(f"SELECT action, COUNT(*) FROM usage_events WHERE {where} GROUP BY action", values))
        days = dict(db.execute(f"SELECT day, COUNT(*) FROM usage_events WHERE {where} GROUP BY day", values))
        devices = db.execute(f"SELECT COUNT(DISTINCT device_id) FROM usage_events WHERE {where}", values).fetchone()[0]
        first = db.execute("SELECT MIN(occurred_at) FROM usage_events").fetchone()[0]
    db.close()
    return {
        "start": start.isoformat(), "end": end.isoformat(), "role": role,
        "total": sum(counts.values()), "active_devices": devices, "first_event_at": first,
        "actions": [{"action": key, "label": label, "count": counts.get(key, 0)} for key, label in ACTIONS.items()],
        "trend": [{"date": (day := start + timedelta(days=i)).isoformat(), "count": days.get(day.isoformat(), 0)}
                  for i in range((end - start).days + 1)],
    }


def register_usage(app, current_user, admin_unlocked):
    @app.post("/api/glasses/usage")
    def glasses_usage():
        event = request.get_json(silent=True) or {}
        if not isinstance(event, dict):
            return jsonify({"error": "event must be an object"}), 400
        identity = getattr(g, "chem_device", None)
        if identity and identity["role"] != event.get("role"):
            return jsonify({"error": "device role mismatch"}), 403
        try:
            inserted = record_event(event)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            return jsonify({"error": str(error)}), 400
        return jsonify({"ok": True, "recorded": inserted})

    @app.get("/api/workspace/usage")
    def workspace_usage():
        user = current_user()
        if not admin_unlocked() and not (user and user.get("role") == "teacher"):
            return jsonify({"error": "Teacher or administrator login required"}), 403 if user else 401
        try:
            end = date.fromisoformat(request.args.get("end") or datetime.now(BEIJING).date().isoformat())
            start = date.fromisoformat(request.args.get("start") or (end - timedelta(days=6)).isoformat())
            return jsonify(usage_summary(start, end, request.args.get("role", "all")))
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
