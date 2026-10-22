"""Time-bounded glass schedules, separated by student/teacher role."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo
import uuid

from pipeline.resource_paths import runtime_asset_path


GLASS_NUMBERS = tuple(f"{number:02d}" for number in range(1, 13))


class TeacherBindingConflict(ValueError):
    def __init__(self, binding: dict[str, Any]):
        super().__init__("该眼镜在所选星期已分配给同角色的其他用户")
        self.binding = binding


def _db_path() -> Path:
    return runtime_asset_path("auth", "identity.sqlite3")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_teacher_binding_store() -> None:
    with closing(_connect()) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS teacher_glass_bindings (
                binding_id TEXT PRIMARY KEY,
                identity_id TEXT NOT NULL,
                glass_number TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                created_at TEXT NOT NULL
            );

            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(teacher_glass_bindings)")}
        if "weekday" not in columns:
            connection.execute("ALTER TABLE teacher_glass_bindings ADD COLUMN weekday INTEGER")
        if "role" not in columns:
            connection.execute("ALTER TABLE teacher_glass_bindings ADD COLUMN role TEXT NOT NULL DEFAULT 'teacher'")
        connection.executescript("""
            DROP INDEX IF EXISTS uq_glass_active_teacher;
            DROP INDEX IF EXISTS uq_teacher_active_binding;
            DROP INDEX IF EXISTS uq_glass_weekday_teacher;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_active_role_binding
            ON teacher_glass_bindings(identity_id, role) WHERE ended_at IS NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_glass_weekday_role
            ON teacher_glass_bindings(glass_number, weekday, role) WHERE ended_at IS NULL;
        """)
        connection.commit()


def _binding(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def active_teacher_binding(
    *, identity_id: str | None = None, glass_number: str | None = None,
    role: str = "teacher", weekday: int | None = None,
) -> dict[str, Any] | None:
    init_teacher_binding_store()
    filters = ["ended_at IS NULL", "role = ?"]
    values: list[Any] = [role]
    if identity_id:
        filters.append("identity_id = ?")
        values.append(identity_id.strip())
    if glass_number:
        filters.append("glass_number = ?")
        values.append(glass_number.strip())
    if weekday is not None:
        filters.append("weekday = ?")
        values.append(weekday)
    with closing(_connect()) as connection:
        row = connection.execute(
            f"SELECT * FROM teacher_glass_bindings WHERE {' AND '.join(filters)} "
            "ORDER BY started_at DESC LIMIT 1",
            values,
        ).fetchone()
    return _binding(row)


def list_teacher_bindings(*, active_only: bool = False, role: str = "teacher") -> list[dict[str, Any]]:
    init_teacher_binding_store()
    where = "WHERE role = ?" + (" AND ended_at IS NULL" if active_only else "")
    with closing(_connect()) as connection:
        rows = connection.execute(
            f"SELECT * FROM teacher_glass_bindings {where} ORDER BY started_at DESC", (role,)
        ).fetchall()
    return [dict(row) for row in rows]


def set_teacher_binding(identity_id: str, glass_number: str, weekday: int, *, role: str = "teacher") -> dict[str, Any]:
    identity_id = identity_id.strip()
    glass_number = glass_number.strip()
    if not identity_id:
        raise ValueError("identity_id 不能为空")
    if role not in {"teacher", "student"}:
        raise ValueError("角色必须是教师或学生")
    if glass_number not in GLASS_NUMBERS:
        raise ValueError("眼镜编号必须是 01 到 12")
    if type(weekday) is not int or weekday not in range(7):
        raise ValueError("请选择固定使用的星期（0为星期一，6为星期日）")

    init_teacher_binding_store()
    now = _now()
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        target = connection.execute(
            "SELECT * FROM teacher_glass_bindings WHERE glass_number = ? AND weekday = ? AND role = ? AND ended_at IS NULL",
            (glass_number, weekday, role),
        ).fetchone()
        if target is not None and target["identity_id"] != identity_id:
            connection.rollback()
            raise TeacherBindingConflict(dict(target))

        current = connection.execute(
            "SELECT * FROM teacher_glass_bindings WHERE identity_id = ? AND role = ? AND ended_at IS NULL",
            (identity_id, role),
        ).fetchone()
        if current is not None and current["glass_number"] == glass_number and current["weekday"] == weekday:
            connection.commit()
            return dict(current)
        if current is not None:
            connection.execute(
                "UPDATE teacher_glass_bindings SET ended_at = ? WHERE binding_id = ?",
                (now, current["binding_id"]),
            )

        binding_id = f"{role}_glass_{uuid.uuid4().hex}"
        connection.execute(
            "INSERT INTO teacher_glass_bindings(binding_id, identity_id, glass_number, "
            "started_at, ended_at, created_at, weekday, role) VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
            (binding_id, identity_id, glass_number, now, now, weekday, role),
        )
        connection.commit()
    return active_teacher_binding(identity_id=identity_id, role=role) or {}


def end_teacher_binding(identity_id: str, *, role: str = "teacher") -> dict[str, Any] | None:
    identity_id = identity_id.strip()
    init_teacher_binding_store()
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT * FROM teacher_glass_bindings WHERE identity_id = ? AND role = ? AND ended_at IS NULL",
            (identity_id, role),
        ).fetchone()
        if current is None:
            connection.commit()
            return None
        ended_at = _now()
        connection.execute(
            "UPDATE teacher_glass_bindings SET ended_at = ? WHERE binding_id = ?",
            (ended_at, current["binding_id"]),
        )
        connection.commit()
        result = dict(current)
        result["ended_at"] = ended_at
        return result


def teacher_binding_for_glass_at(glass_number: str, when: str, *, role: str = "teacher") -> dict[str, Any] | None:
    """Return the assignment at an upload's actual capture/start time."""
    init_teacher_binding_store()
    captured = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    weekday = captured.astimezone(ZoneInfo("Asia/Shanghai")).weekday()
    when = captured.astimezone(timezone.utc).isoformat()
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM teacher_glass_bindings "
            "WHERE glass_number = ? AND weekday = ? AND role = ? AND started_at <= ? "
            "AND (ended_at IS NULL OR ended_at > ?) "
            "ORDER BY started_at DESC LIMIT 1",
            (glass_number.strip(), weekday, role, when, when),
        ).fetchone()
    return _binding(row)
