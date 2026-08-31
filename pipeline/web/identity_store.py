"""Small SQLite store for PKU identities, roles, and the eight glasses.

This is intentionally a local, course-project-sized store.  Uploaded media
remains in runtime_assets; this module only stores identity and calibration
metadata.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any

from pipeline.resource_paths import runtime_asset_path


ROLES = {"student", "teacher"}
GLASS_NUMBERS = tuple(f"{number:02d}" for number in range(1, 9))


def _db_path() -> Path:
    return runtime_asset_path("auth", "identity.sqlite3")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_store() -> None:
    with closing(_connect()) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                identity_id TEXT PRIMARY KEY,
                role TEXT,
                created_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL,
                profile_json TEXT,
                profile_updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS glasses (
                glass_number TEXT PRIMARY KEY,
                device_id TEXT UNIQUE,
                calibrated_at TEXT
            );
            """
        )
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        if "profile_json" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN profile_json TEXT")
        if "profile_updated_at" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN profile_updated_at TEXT")
        connection.executemany(
            "INSERT OR IGNORE INTO glasses(glass_number) VALUES (?)",
            ((number,) for number in GLASS_NUMBERS),
        )
        connection.commit()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    if "profile_json" in value:
        raw_profile = value.pop("profile_json", None)
        try:
            value["profile"] = json.loads(raw_profile) if raw_profile else None
        except (TypeError, json.JSONDecodeError):
            value["profile"] = None
    return value


def find_user(identity_id: str) -> dict[str, Any] | None:
    init_store()
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT identity_id, role, created_at, last_login_at, profile_json, profile_updated_at "
            "FROM users WHERE identity_id = ?",
            (identity_id,),
        ).fetchone()
    return _row_to_dict(row)


def upsert_user(
    identity_id: str,
    role: str | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identity_id = identity_id.strip()
    if not identity_id:
        raise ValueError("identity_id is required")
    if role is not None and role not in ROLES:
        raise ValueError("role must be student or teacher")

    init_store()
    now = _now()
    profile_json = json.dumps(profile, ensure_ascii=False) if profile is not None else None
    with closing(_connect()) as connection:
        existing = connection.execute(
            "SELECT identity_id, role, created_at FROM users WHERE identity_id = ?",
            (identity_id,),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO users(identity_id, role, created_at, last_login_at, profile_json, profile_updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (identity_id, role, now, now, profile_json, now if profile is not None else None),
            )
        else:
            updates = ["last_login_at = ?"]
            values: list[Any] = [now]
            if role is not None and existing["role"] != role:
                updates.append("role = ?")
                values.append(role)
            if profile is not None:
                updates.extend(("profile_json = ?", "profile_updated_at = ?"))
                values.extend((profile_json, now))
            values.append(identity_id)
            connection.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE identity_id = ?",
                values,
            )
        connection.commit()
    return find_user(identity_id) or {
        "identity_id": identity_id,
        "role": role,
        "created_at": now,
        "last_login_at": now,
        "profile": profile,
        "profile_updated_at": now if profile is not None else None,
    }


def list_users() -> list[dict[str, Any]]:
    init_store()
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT identity_id, role, created_at, last_login_at, profile_json, profile_updated_at "
            "FROM users ORDER BY last_login_at DESC"
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def reset_user_role(identity_id: str) -> dict[str, Any] | None:
    init_store()
    with closing(_connect()) as connection:
        connection.execute(
            "UPDATE users SET role = NULL WHERE identity_id = ?",
            (identity_id.strip(),),
        )
        connection.commit()
    return find_user(identity_id.strip())


def list_glasses() -> list[dict[str, Any]]:
    init_store()
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT glass_number, device_id, calibrated_at FROM glasses ORDER BY glass_number"
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def calibrate_glass(glass_number: str, device_id: str | None) -> dict[str, Any]:
    glass_number = glass_number.strip()
    device_id = (device_id or "").strip() or None
    if glass_number not in GLASS_NUMBERS:
        raise ValueError("glass_number must be 01 through 08")

    init_store()
    with closing(_connect()) as connection:
        if device_id:
            connection.execute(
                "UPDATE glasses SET device_id = NULL, calibrated_at = NULL WHERE device_id = ? AND glass_number != ?",
                (device_id, glass_number),
            )
        connection.execute(
            "UPDATE glasses SET device_id = ?, calibrated_at = ? WHERE glass_number = ?",
            (device_id, _now() if device_id else None, glass_number),
        )
        connection.commit()
        row = connection.execute(
            "SELECT glass_number, device_id, calibrated_at FROM glasses WHERE glass_number = ?",
            (glass_number,),
        ).fetchone()
    return _row_to_dict(row) or {"glass_number": glass_number, "device_id": device_id}


def glass_for_device(device_id: str) -> str | None:
    init_store()
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT glass_number FROM glasses WHERE device_id = ?",
            (device_id.strip(),),
        ).fetchone()
    return str(row["glass_number"]) if row else None
