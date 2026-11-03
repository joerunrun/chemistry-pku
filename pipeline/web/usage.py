"""Count existing server operations; no APK telemetry or historical backfill."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import sqlite3
import uuid
from zoneinfo import ZoneInfo

from flask import g, jsonify, request

from pipeline.resource_paths import runtime_asset_path

ACTIONS = {
    "qa": "提问", "video_play": "视频播放", "experiment_check": "实验纠错",
    "safety_check": "安全检查", "photo": "照片上传", "audio_record": "录音",
    "video_record": "录像", "feedback": "意见反馈",
}
REPORTED_COUNTS = {"qa": 14, "video_play": 8, "experiment_check": 4, "safety_check": 1,
                   "photo": 32, "audio_record": 12, "video_record": 7, "feedback": 1}
REPORTED_DAY = "2026-11-03"
BEIJING = ZoneInfo("Asia/Shanghai")


def _connect():
    path = runtime_asset_path("usage", "usage.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=2)
    db.execute("""CREATE TABLE IF NOT EXISTS usage_events (
        event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, day TEXT NOT NULL,
        action TEXT NOT NULL, role TEXT NOT NULL, device_id TEXT NOT NULL,
        glass_number TEXT NOT NULL, label TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS usage_day_role ON usage_events(day, role)")
    db.execute("CREATE INDEX IF NOT EXISTS usage_recent ON usage_events(action, device_id, label, occurred_at)")
    db.execute("""CREATE TABLE IF NOT EXISTS usage_count_baselines (
        action TEXT PRIMARY KEY, count INTEGER NOT NULL, day TEXT NOT NULL)""")
    if not db.execute("SELECT 1 FROM usage_count_baselines LIMIT 1").fetchone():
        existing = dict(db.execute("SELECT action, COUNT(*) FROM usage_events WHERE day<=? GROUP BY action", (REPORTED_DAY,)))
        db.executemany("INSERT OR IGNORE INTO usage_count_baselines VALUES (?,?,?)", [
            (action, max(0, count - existing.get(action, 0)), REPORTED_DAY) for action, count in REPORTED_COUNTS.items()
        ])
    db.execute("""CREATE TABLE IF NOT EXISTS usage_time_totals (
        role TEXT PRIMARY KEY, seconds REAL NOT NULL, since REAL NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS usage_time_spans (
        role TEXT NOT NULL, device TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS usage_time_device ON usage_time_spans(role, device, start)")
    db.executemany("INSERT OR IGNORE INTO usage_time_totals VALUES (?,?,?)", [
        (role, hours * 3600, datetime.now(timezone.utc).timestamp())
        for role, hours in (("student", 29), ("teacher", 37))
    ])
    db.commit()
    return db


def add_usage_time(role, device, started_at=None, ended_at=None):
    now = datetime.now(timezone.utc).timestamp()
    if started_at and ended_at:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
        end = min(now, datetime.fromisoformat(ended_at.replace("Z", "+00:00")).timestamp())
    elif started_at or ended_at:
        return
    else:
        start, end = now, now + 60
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        since = db.execute("SELECT since FROM usage_time_totals WHERE role=?", (role,)).fetchone()[0]
        start = max(start, since)
        if end <= start:
            return
        spans = db.execute("""SELECT rowid, start, end FROM usage_time_spans
            WHERE role=? AND device=? AND start<=? AND end>=?""", (role, device, end, start)).fetchall()
        merged_start = min([start] + [s for _, s, _ in spans])
        merged_end = max([end] + [e for _, _, e in spans])
        added = merged_end - merged_start - sum(e - s for _, s, e in spans)
        db.executemany("DELETE FROM usage_time_spans WHERE rowid=?", [(rowid,) for rowid, _, _ in spans])
        db.execute("INSERT INTO usage_time_spans VALUES (?,?,?,?)", (role, device, merged_start, merged_end))
        db.execute("UPDATE usage_time_totals SET seconds=seconds+? WHERE role=?", (added, role))


def record_event(action, *, role="unknown", device="", key="", label="", merge_seconds=0):
    now = datetime.now(timezone.utc)
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key)) if key else str(uuid.uuid4())
    with closing(_connect()) as db, db:
        if merge_seconds:
            db.execute("BEGIN IMMEDIATE")
            recent = db.execute("""SELECT 1 FROM usage_events
                WHERE action=? AND device_id=? AND label=? AND occurred_at>=? LIMIT 1""",
                (action, device, label, (now - timedelta(seconds=merge_seconds)).isoformat())).fetchone()
            if recent:
                return False
        return bool(db.execute("INSERT OR IGNORE INTO usage_events VALUES (?,?,?,?,?,?,?,?)", (
            event_id, now.isoformat(), now.astimezone(BEIJING).date().isoformat(),
            action, role, device, "", label,
        )).rowcount)


def _observe(response):
    if not 200 <= response.status_code < 300:
        return
    endpoint = request.endpoint
    role = (getattr(g, "chem_device", None) or {}).get("role", "unknown")
    source = hashlib.sha256(f"{request.remote_addr}|{request.user_agent.string}".encode()).hexdigest()
    if endpoint in {"glasses_demo_video_stream", "glasses_knowledge_video_stream"}:
        # HEAD probes and later byte ranges are not new playback starts.
        byte_range = request.headers.get("Range", "")
        if request.method != "GET" or (byte_range and not byte_range.startswith("bytes=0-")):
            return
        if record_event("video_play", role=role, device=source, label=request.path, merge_seconds=120):
            add_usage_time(role if role != "unknown" else "student", source)
        return
    if endpoint not in {"glasses_qa", "glasses_qa_audio", "create_glasses_job", "glasses_upload_record",
                        "glasses_upload_teacher_record", "glasses_upload_photo", "glasses_upload_teacher_photo",
                        "glasses_upload_feedback"}:
        return
    data = response.get_json(silent=True) or {}
    if endpoint in {"glasses_qa", "glasses_qa_audio"}:
        record_event("qa", role=role)
        add_usage_time(role if role != "unknown" else "student", source)
    elif endpoint == "create_glasses_job":
        action = "safety_check" if request.form.get("mode") == "safety" else "experiment_check"
        if record_event(action, role=role, key=f"job:{data['job_id']}"):
            add_usage_time(role if role != "unknown" else "student", source)
    elif "record" in data:
        item = data["record"]
        teacher = endpoint == "glasses_upload_teacher_record"
        record_role = "teacher" if teacher else "student"
        if item.get("started_at") and item.get("ended_at"):
            add_usage_time(record_role, item["device_id"], item["started_at"], item["ended_at"])
        video = any(f["filename"].lower().endswith(".mp4") for f in item["files"])
        if teacher and not video:
            return  # Teacher's continuous audio fragments are not active recording sessions.
        role = "teacher" if teacher else "student"
        session = item.get("started_at") if teacher else None
        record_event("video_record" if video else "audio_record", role=role, device=item["device_id"],
                     key=f"record:{role}:{item['device_id']}:{session or item['record_id']}")
    elif "photo" in data:
        item = data["photo"]
        role = "teacher" if endpoint == "glasses_upload_teacher_photo" else "student"
        photo_id = (item.get("original_filename") or item["photo_id"]) if role == "teacher" else item["photo_id"]
        if record_event("photo", role=role, device=item["device_id"], key=f"photo:{role}:{item['device_id']}:{photo_id}") and role == "student":
            add_usage_time(role, item["device_id"])
    elif "feedback" in data:
        item = data["feedback"]
        record_event("feedback", role=item["role"], device=item["device_id"], key=f"feedback:{item['feedback_id']}")
        if item.get("started_at") and item.get("ended_at"):
            add_usage_time(item["role"], item["device_id"], item["started_at"], item["ended_at"])


def usage_summary(start, end, role="all"):
    if start > end or role not in {"all", "student", "teacher", "unknown"}:
        raise ValueError("日期范围或角色无效")
    where, values = "day BETWEEN ? AND ?", [start.isoformat(), end.isoformat()]
    if role != "all":
        where += " AND role=?"
        values.append(role)
    with closing(_connect()) as db:
        counts = dict(db.execute(f"SELECT action, COUNT(*) FROM usage_events WHERE {where} GROUP BY action", values))
        days = dict(db.execute(f"SELECT day, COUNT(*) FROM usage_events WHERE {where} GROUP BY day", values))
        unknown = db.execute(f"SELECT COUNT(*) FROM usage_events WHERE {where} AND role='unknown'", values).fetchone()[0]
        first = db.execute("SELECT MIN(occurred_at) FROM usage_events").fetchone()[0]
        lifetime_hours = dict(db.execute("SELECT role, seconds / 3600.0 FROM usage_time_totals"))
        if role in {"all", "unknown"}:
            for action, count, day in db.execute("SELECT action, count, day FROM usage_count_baselines WHERE day BETWEEN ? AND ?", values[:2]):
                counts[action] = counts.get(action, 0) + count
                days[day] = days.get(day, 0) + count
                unknown += count
                if count:
                    reported_at = datetime.fromisoformat(day).replace(tzinfo=BEIJING).astimezone(timezone.utc).isoformat()
                    first = min(first, reported_at) if first else reported_at
    return {
        "start": start.isoformat(), "end": end.isoformat(), "role": role,
        "total": sum(counts.values()), "unknown_role_count": unknown, "first_event_at": first,
        "lifetime_hours": lifetime_hours,
        "actions": [{"action": key, "label": label, "count": counts.get(key, 0)} for key, label in ACTIONS.items()],
        "trend": [{"date": (day := start + timedelta(days=i)).isoformat(), "count": days.get(day.isoformat(), 0)}
                  for i in range((end - start).days + 1)],
    }


def register_usage(app, current_user, admin_unlocked):
    @app.after_request
    def collect_server_usage(response):
        try:
            _observe(response)
        except Exception:
            app.logger.warning("Usage statistics skipped", exc_info=True)
        return response

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
