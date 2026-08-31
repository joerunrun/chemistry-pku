"""SQLite persistence for post-class glass data claims."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from pathlib import Path
from typing import Any, Iterable
import uuid

from pipeline.resource_paths import runtime_asset_path


class ClaimConflict(ValueError):
    """Raised when an uploaded item has already been claimed."""

    def __init__(self, items: list[dict[str, Any]]):
        super().__init__("some items have already been claimed")
        self.items = items


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


def init_claim_store() -> None:
    with closing(_connect()) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS claim_batches (
                claim_id TEXT PRIMARY KEY,
                identity_id TEXT NOT NULL,
                role TEXT NOT NULL,
                glass_number TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS claim_items (
                claim_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                source_role TEXT NOT NULL,
                device_id TEXT NOT NULL,
                experiment_type TEXT NOT NULL,
                item_created_at TEXT NOT NULL,
                PRIMARY KEY (claim_id, item_type, source_role, item_id),
                UNIQUE (item_type, source_role, item_id),
                FOREIGN KEY (claim_id) REFERENCES claim_batches(claim_id)
            );
            """
        )
        connection.commit()


def create_claim(
    *,
    identity_id: str,
    role: str,
    glass_number: str,
    started_at: str,
    ended_at: str,
    items: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    init_claim_store()
    claim_id = f"claim_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    normalized_items = [
        {
            "item_type": str(item.get("item_type", "")).strip(),
            "item_id": str(item.get("item_id", "")).strip(),
            "source_role": str(item.get("source_role", "")).strip(),
            "device_id": str(item.get("device_id", "")).strip(),
            "experiment_type": str(item.get("experiment_type", "")).strip(),
            "item_created_at": str(item.get("item_created_at", "")).strip(),
        }
        for item in items
    ]
    normalized_items = [
        item for item in normalized_items
        if item["item_type"] in {"record", "photo"}
        and item["item_id"]
        and item["source_role"] in {"student", "teacher"}
    ]
    if not normalized_items:
        raise ValueError("没有可认领的数据")

    with closing(_connect()) as connection:
        connection.execute("BEGIN")
        placeholders = ",".join("(?, ?, ?)" for _ in normalized_items)
        params: list[str] = []
        for item in normalized_items:
            params.extend((item["item_type"], item["source_role"], item["item_id"]))
        rows = connection.execute(
            f"SELECT item_type, source_role, item_id, claim_id FROM claim_items "
            f"WHERE (item_type, source_role, item_id) IN ({placeholders})",
            params,
        ).fetchall()
        if rows:
            connection.rollback()
            raise ClaimConflict([dict(row) for row in rows])

        created_at = _now()
        connection.execute(
            "INSERT INTO claim_batches(claim_id, identity_id, role, glass_number, started_at, ended_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (claim_id, identity_id, role, glass_number, started_at, ended_at, created_at),
        )
        connection.executemany(
            "INSERT INTO claim_items(claim_id, item_type, item_id, source_role, device_id, experiment_type, item_created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    claim_id,
                    item["item_type"],
                    item["item_id"],
                    item["source_role"],
                    item["device_id"],
                    item["experiment_type"],
                    item["item_created_at"],
                )
                for item in normalized_items
            ],
        )
        connection.commit()

    return {
        "claim_id": claim_id,
        "identity_id": identity_id,
        "role": role,
        "glass_number": glass_number,
        "started_at": started_at,
        "ended_at": ended_at,
        "created_at": created_at,
        "items": normalized_items,
    }


def list_claims(identity_id: str | None = None) -> list[dict[str, Any]]:
    init_claim_store()
    with closing(_connect()) as connection:
        if identity_id:
            batches = connection.execute(
                "SELECT * FROM claim_batches WHERE identity_id = ? ORDER BY created_at DESC",
                (identity_id,),
            ).fetchall()
        else:
            batches = connection.execute(
                "SELECT * FROM claim_batches ORDER BY created_at DESC"
            ).fetchall()
        claims = []
        for batch in batches:
            items = connection.execute(
                "SELECT item_type, item_id, source_role, device_id, experiment_type, item_created_at "
                "FROM claim_items WHERE claim_id = ? ORDER BY item_created_at",
                (batch["claim_id"],),
            ).fetchall()
            item = dict(batch)
            item["items"] = [dict(row) for row in items]
            claims.append(item)
    return claims


def claimed_item_keys() -> set[tuple[str, str, str]]:
    init_claim_store()
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT item_type, source_role, item_id FROM claim_items"
        ).fetchall()
    return {(row["item_type"], row["source_role"], row["item_id"]) for row in rows}


def claim_item_owners() -> dict[tuple[str, str, str], dict[str, Any]]:
    """Return claim ownership keyed by (item type, source role, item id)."""
    init_claim_store()
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT i.item_type, i.source_role, i.item_id, i.claim_id, "
            "b.identity_id, b.role, b.glass_number, b.started_at, b.ended_at, b.created_at "
            "FROM claim_items i JOIN claim_batches b ON b.claim_id = i.claim_id"
        ).fetchall()
    return {
        (row["item_type"], row["source_role"], row["item_id"]): dict(row)
        for row in rows
    }
