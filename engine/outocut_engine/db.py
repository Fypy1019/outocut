from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS asset_categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    directory TEXT NOT NULL,
    scanned_at TEXT,
    clip_count INTEGER NOT NULL DEFAULT 0,
    issue_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS asset_clips (
    id TEXT PRIMARY KEY,
    category_id TEXT NOT NULL REFERENCES asset_categories(id) ON DELETE CASCADE,
    path TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS personas (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mix_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voice_profiles (
    voice_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    error TEXT,
    output_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_batches (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_tasks (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES media_batches(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    state TEXT NOT NULL,
    payload TEXT NOT NULL,
    output_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(batch_id, position)
);

CREATE INDEX IF NOT EXISTS idx_media_tasks_batch_state
ON media_tasks(batch_id, state, position);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secrets (
    key TEXT PRIMARY KEY,
    protected_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> None:
        with self.connection() as connection:
            connection.execute(sql, tuple(parameters))
            connection.commit()

    def fetch_one(self, sql: str, parameters: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(sql, tuple(parameters)).fetchone()

    def fetch_all(self, sql: str, parameters: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return list(connection.execute(sql, tuple(parameters)).fetchall())

    def upsert_json(self, table: str, record_id: str, name: str, payload: dict[str, Any]) -> None:
        if table not in {"personas", "mix_templates"}:
            raise ValueError("unsupported table")
        now = utc_now()
        self.execute(
            f"""INSERT INTO {table}(id, name, payload, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, payload=excluded.payload,
                updated_at=excluded.updated_at""",
            (record_id, name, json.dumps(payload, ensure_ascii=False), now, now),
        )
