"""Time-bounded teacher-to-glass assignments stored beside identity data."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from pipeline.resource_paths import runtime_asset_path


GLASS_NUMBERS = tuple(f"{number:02d}" for number in range(1, 13))


class TeacherBindingConflict(ValueError):
    def __init__(self, binding: dict[str, Any]):
        super().__init__("该眼镜已经绑定给其他教师")
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

            CREATE UNIQUE INDEX IF NOT EXISTS uq_teacher_active_binding
            ON teacher_glass_bindings(identity_id) WHERE ended_at IS NULL;

            CREATE UNIQUE INDEX IF NOT EXISTS uq_glass_active_teacher
            ON teacher_glass_bindings(glass_number) WHERE ended_at IS NULL;
            """
        )
        connection.commit()


def _binding(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def active_teacher_binding(
    *, identity_id: str | None = None, glass_number: str | None = None
) -> dict[str, Any] | None:
    init_teacher_binding_store()
    filters = ["ended_at IS NULL"]
    values: list[str] = []
    if identity_id:
        filters.append("identity_id = ?")
        values.append(identity_id.strip())
    if glass_number:
        filters.append("glass_number = ?")
        values.append(glass_number.strip())
    with closing(_connect()) as connection:
        row = connection.execute(
            f"SELECT * FROM teacher_glass_bindings WHERE {' AND '.join(filters)} "
            "ORDER BY started_at DESC LIMIT 1",
            values,
        ).fetchone()
    return _binding(row)


def list_teacher_bindings(*, active_only: bool = False) -> list[dict[str, Any]]:
    init_teacher_binding_store()
    where = "WHERE ended_at IS NULL" if active_only else ""
    with closing(_connect()) as connection:
        rows = connection.execute(
            f"SELECT * FROM teacher_glass_bindings {where} ORDER BY started_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def set_teacher_binding(identity_id: str, glass_number: str) -> dict[str, Any]:
    identity_id = identity_id.strip()
    glass_number = glass_number.strip()
    if not identity_id:
        raise ValueError("identity_id 不能为空")
    if glass_number not in GLASS_NUMBERS:
        raise ValueError("眼镜编号必须是 01 到 12")

    init_teacher_binding_store()
    now = _now()
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        target = connection.execute(
            "SELECT * FROM teacher_glass_bindings WHERE glass_number = ? AND ended_at IS NULL",
            (glass_number,),
        ).fetchone()
        if target is not None and target["identity_id"] != identity_id:
            connection.rollback()
            raise TeacherBindingConflict(dict(target))

        current = connection.execute(
            "SELECT * FROM teacher_glass_bindings WHERE identity_id = ? AND ended_at IS NULL",
            (identity_id,),
        ).fetchone()
        if current is not None and current["glass_number"] == glass_number:
            connection.commit()
            return dict(current)
        if current is not None:
            connection.execute(
                "UPDATE teacher_glass_bindings SET ended_at = ? WHERE binding_id = ?",
                (now, current["binding_id"]),
            )

        binding_id = f"teacher_glass_{uuid.uuid4().hex}"
        connection.execute(
            "INSERT INTO teacher_glass_bindings(binding_id, identity_id, glass_number, "
            "started_at, ended_at, created_at) VALUES (?, ?, ?, ?, NULL, ?)",
            (binding_id, identity_id, glass_number, now, now),
        )
        connection.commit()
    return active_teacher_binding(identity_id=identity_id) or {}


def end_teacher_binding(identity_id: str) -> dict[str, Any] | None:
    identity_id = identity_id.strip()
    init_teacher_binding_store()
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT * FROM teacher_glass_bindings WHERE identity_id = ? AND ended_at IS NULL",
            (identity_id,),
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


def teacher_binding_for_glass_at(glass_number: str, when: str) -> dict[str, Any] | None:
    """Return the teacher assigned at an upload's actual capture/start time."""
    init_teacher_binding_store()
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM teacher_glass_bindings "
            "WHERE glass_number = ? AND started_at <= ? "
            "AND (ended_at IS NULL OR ended_at > ?) "
            "ORDER BY started_at DESC LIMIT 1",
            (glass_number.strip(), when, when),
        ).fetchone()
    return _binding(row)
