"""Small SQLite store for PKU identities, roles, and the twelve glasses.

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
ROLE_SOURCES = {"portal", "admin", "test"}
GLASS_NUMBERS = tuple(f"{number:02d}" for number in range(1, 13))


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
                portal_role TEXT,
                role_source TEXT,
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
        if "portal_role" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN portal_role TEXT")
        if "role_source" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN role_source TEXT")
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
            "SELECT identity_id, role, portal_role, role_source, created_at, last_login_at, "
            "profile_json, profile_updated_at "
            "FROM users WHERE identity_id = ?",
            (identity_id,),
        ).fetchone()
    return _row_to_dict(row)


def upsert_user(
    identity_id: str,
    role: str | None = None,
    profile: dict[str, Any] | None = None,
    *,
    portal_role: str | None = None,
    role_source: str | None = None,
) -> dict[str, Any]:
    identity_id = identity_id.strip()
    if not identity_id:
        raise ValueError("identity_id is required")
    if role is not None and role not in ROLES:
        raise ValueError("role must be student or teacher")
    if portal_role is not None and portal_role not in ROLES:
        raise ValueError("portal_role must be student or teacher")
    if role_source is not None and role_source not in ROLE_SOURCES:
        raise ValueError("invalid role_source")

    init_store()
    now = _now()
    profile_json = json.dumps(profile, ensure_ascii=False) if profile is not None else None
    with closing(_connect()) as connection:
        existing = connection.execute(
            "SELECT identity_id, role, portal_role, role_source, created_at "
            "FROM users WHERE identity_id = ?",
            (identity_id,),
        ).fetchone()
        if existing is None:
            effective_role = role or portal_role
            effective_source = role_source or ("portal" if portal_role else None)
            connection.execute(
                "INSERT INTO users(identity_id, role, portal_role, role_source, created_at, "
                "last_login_at, profile_json, profile_updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identity_id,
                    effective_role,
                    portal_role,
                    effective_source,
                    now,
                    now,
                    profile_json,
                    now if profile is not None else None,
                ),
            )
        else:
            updates = ["last_login_at = ?"]
            values: list[Any] = [now]
            if portal_role is not None:
                updates.append("portal_role = ?")
                values.append(portal_role)
                if existing["role_source"] != "admin":
                    updates.extend(("role = ?", "role_source = ?"))
                    values.extend((portal_role, role_source or "portal"))
            elif role is not None and existing["role"] != role:
                updates.append("role = ?")
                values.append(role)
            if portal_role is None and role_source is not None:
                updates.append("role_source = ?")
                values.append(role_source)
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
        "role": role or portal_role,
        "portal_role": portal_role,
        "role_source": role_source or ("portal" if portal_role else None),
        "created_at": now,
        "last_login_at": now,
        "profile": profile,
        "profile_updated_at": now if profile is not None else None,
    }


def list_users() -> list[dict[str, Any]]:
    init_store()
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT identity_id, role, portal_role, role_source, created_at, last_login_at, "
            "profile_json, profile_updated_at "
            "FROM users ORDER BY last_login_at DESC"
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def reset_user_role(identity_id: str) -> dict[str, Any] | None:
    init_store()
    with closing(_connect()) as connection:
        connection.execute(
            "UPDATE users SET role = portal_role, role_source = CASE WHEN portal_role IS NULL "
            "THEN NULL ELSE 'portal' END WHERE identity_id = ?",
            (identity_id.strip(),),
        )
        connection.commit()
    return find_user(identity_id.strip())


def set_user_role(identity_id: str, role: str | None) -> dict[str, Any] | None:
    """Apply an administrator override, or restore the latest portal-derived role."""
    identity_id = identity_id.strip()
    if role is not None and role not in ROLES:
        raise ValueError("role must be student, teacher, or null")
    init_store()
    with closing(_connect()) as connection:
        existing = connection.execute(
            "SELECT portal_role FROM users WHERE identity_id = ?",
            (identity_id,),
        ).fetchone()
        if existing is None:
            return None
        if role is None:
            portal_role = existing["portal_role"]
            connection.execute(
                "UPDATE users SET role = ?, role_source = ? WHERE identity_id = ?",
                (
                    portal_role,
                    ("test" if identity_id.startswith("demo-") else "portal") if portal_role else None,
                    identity_id,
                ),
            )
        else:
            connection.execute(
                "UPDATE users SET role = ?, role_source = 'admin' WHERE identity_id = ?",
                (role, identity_id),
            )
        connection.commit()
    return find_user(identity_id)


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
        raise ValueError("glass_number must be 01 through 12")

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
