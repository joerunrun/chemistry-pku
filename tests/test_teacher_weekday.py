import sqlite3
import pytest
from pipeline.web import teacher_binding_store as store


def test_old_bindings_survive_and_weekday_ownership_uses_capture_time(tmp_path, monkeypatch):
    path = tmp_path / "identity.sqlite3"
    monkeypatch.setattr(store, "_db_path", lambda: path)
    monkeypatch.setattr(store, "_now", lambda: "2026-10-16T00:00:00+00:00")
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE teacher_glass_bindings (binding_id TEXT PRIMARY KEY, identity_id TEXT,
                glass_number TEXT, started_at TEXT, ended_at TEXT, created_at TEXT);
            CREATE UNIQUE INDEX uq_glass_active_teacher ON teacher_glass_bindings(glass_number) WHERE ended_at IS NULL;
            INSERT INTO teacher_glass_bindings VALUES ('old', 'alice', '11', '2026-01-01', NULL, '2026-01-01');
        """)
    assert store.active_teacher_binding(identity_id="alice")["binding_id"] == "old"
    assert store.active_teacher_binding(identity_id="alice")["weekday"] is None
    store.set_teacher_binding("alice", "11", 0)
    store.set_teacher_binding("bob", "11", 2)
    with pytest.raises(store.TeacherBindingConflict):
        store.set_teacher_binding("charlie", "11", 0)
    # Sunday UTC is Monday in Beijing. Upload date is irrelevant.
    assert store.teacher_binding_for_glass_at("11", "2026-10-18T17:00:00Z")["identity_id"] == "alice"
    assert store.teacher_binding_for_glass_at("11", "2026-10-21T09:00:00+08:00")["identity_id"] == "bob"
    assert store.teacher_binding_for_glass_at("11", "2026-10-12T09:00:00+08:00") is None
    assert any(item["binding_id"] == "old" and item["ended_at"] for item in store.list_teacher_bindings())
    store.set_teacher_binding("student-a", "11", 0, role="student")
    with pytest.raises(store.TeacherBindingConflict):
        store.set_teacher_binding("student-b", "11", 0, role="student")
    assert store.teacher_binding_for_glass_at("11", "2026-10-19T09:00:00+08:00", role="student")["identity_id"] == "student-a"
    assert store.teacher_binding_for_glass_at("11", "2026-10-19T09:00:00+08:00")["identity_id"] == "alice"
