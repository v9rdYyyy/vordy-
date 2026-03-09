from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Bucket, GatherRecord, ModeratorRecord, ParticipantRecord

UTC = timezone.utc


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    PRAGMA journal_mode = WAL;
                    PRAGMA foreign_keys = ON;

                    CREATE TABLE IF NOT EXISTS gathers (
                        gather_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id       INTEGER NOT NULL,
                        channel_id     INTEGER NOT NULL,
                        message_id     INTEGER NOT NULL DEFAULT 0,
                        thread_id      INTEGER,
                        voice_channel_id INTEGER,
                        title          TEXT NOT NULL,
                        comment        TEXT,
                        creator_id     INTEGER NOT NULL,
                        creator_name   TEXT NOT NULL,
                        event_at       TEXT NOT NULL,
                        main_slots     INTEGER NOT NULL,
                        extra_slots    INTEGER NOT NULL DEFAULT 0,
                        role_ids_json  TEXT NOT NULL DEFAULT '[]',
                        image_url      TEXT,
                        create_thread  INTEGER NOT NULL DEFAULT 0,
                        created_at     TEXT NOT NULL,
                        closed_at      TEXT
                    );

                    CREATE TABLE IF NOT EXISTS participants (
                        gather_id      INTEGER NOT NULL,
                        user_id        INTEGER NOT NULL,
                        display_name   TEXT NOT NULL,
                        bucket         TEXT NOT NULL,
                        checked_in     INTEGER NOT NULL DEFAULT 0,
                        joined_at      TEXT NOT NULL,
                        PRIMARY KEY (gather_id, user_id),
                        FOREIGN KEY (gather_id) REFERENCES gathers(gather_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS moderators (
                        gather_id      INTEGER NOT NULL,
                        user_id        INTEGER NOT NULL,
                        added_by       INTEGER NOT NULL,
                        added_at       TEXT NOT NULL,
                        PRIMARY KEY (gather_id, user_id),
                        FOREIGN KEY (gather_id) REFERENCES gathers(gather_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS guild_log_channels (
                        guild_id       INTEGER PRIMARY KEY,
                        channel_id     INTEGER NOT NULL,
                        updated_at     TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_gathers_guild ON gathers(guild_id);
                    CREATE INDEX IF NOT EXISTS idx_gathers_open ON gathers(guild_id, closed_at);
                    CREATE INDEX IF NOT EXISTS idx_participants_gather ON participants(gather_id, bucket, joined_at);
                    CREATE INDEX IF NOT EXISTS idx_mods_gather ON moderators(gather_id, added_at);
                    """
                )
                columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(gathers)").fetchall()
                }
                if "voice_channel_id" not in columns:
                    conn.execute("ALTER TABLE gathers ADD COLUMN voice_channel_id INTEGER")
                conn.commit()
            finally:
                conn.close()

    async def create_gather(
        self,
        guild_id: int,
        channel_id: int,
        title: str,
        comment: str | None,
        creator_id: int,
        creator_name: str,
        event_at: datetime,
        main_slots: int,
        extra_slots: int,
        role_ids: list[int],
        image_url: str | None,
        create_thread: bool,
    ) -> int:
        created_at = _utcnow().isoformat()
        async with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO gathers (
                        guild_id, channel_id, title, comment, creator_id, creator_name,
                        event_at, main_slots, extra_slots, role_ids_json, image_url,
                        create_thread, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        channel_id,
                        title,
                        comment,
                        creator_id,
                        creator_name,
                        event_at.astimezone(UTC).isoformat(),
                        main_slots,
                        extra_slots,
                        json.dumps(role_ids, ensure_ascii=False),
                        image_url,
                        int(create_thread),
                        created_at,
                    ),
                )
                conn.commit()
                return int(cursor.lastrowid)
            finally:
                conn.close()

    async def set_message_targets(
        self,
        gather_id: int,
        message_id: int,
        thread_id: int | None,
    ) -> None:
        async with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE gathers SET message_id = ?, thread_id = ? WHERE gather_id = ?",
                    (message_id, thread_id, gather_id),
                )
                conn.commit()
            finally:
                conn.close()

    async def set_voice_channel(self, gather_id: int, channel_id: int | None) -> None:
        async with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE gathers SET voice_channel_id = ? WHERE gather_id = ?",
                    (channel_id, gather_id),
                )
                conn.commit()
            finally:
                conn.close()

    async def get_gather(self, gather_id: int) -> GatherRecord | None:
        async with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM gathers WHERE gather_id = ?",
                    (gather_id,),
                ).fetchone()
            finally:
                conn.close()

        return _row_to_gather(row) if row else None

    async def list_open_gathers(self) -> list[GatherRecord]:
        async with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM gathers WHERE closed_at IS NULL AND message_id != 0 ORDER BY created_at ASC"
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_gather(row) for row in rows]

    async def list_guild_open_gathers(self, guild_id: int) -> list[GatherRecord]:
        async with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM gathers WHERE guild_id = ? AND closed_at IS NULL ORDER BY created_at DESC",
                    (guild_id,),
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_gather(row) for row in rows]

    async def close_gather(self, gather_id: int) -> None:
        async with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE gathers SET closed_at = ? WHERE gather_id = ? AND closed_at IS NULL",
                    (_utcnow().isoformat(), gather_id),
                )
                conn.commit()
            finally:
                conn.close()

    async def upsert_participant(
        self,
        gather_id: int,
        user_id: int,
        display_name: str,
        bucket: Bucket,
    ) -> None:
        joined_at = _utcnow().isoformat()
        async with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO participants (gather_id, user_id, display_name, bucket, checked_in, joined_at)
                    VALUES (?, ?, ?, ?, 0, ?)
                    ON CONFLICT(gather_id, user_id) DO UPDATE SET
                        display_name = excluded.display_name,
                        bucket = excluded.bucket,
                        checked_in = CASE
                            WHEN participants.bucket != excluded.bucket THEN 0
                            ELSE participants.checked_in
                        END
                    """,
                    (gather_id, user_id, display_name, bucket, joined_at),
                )
                conn.commit()
            finally:
                conn.close()

    async def remove_participant(self, gather_id: int, user_id: int) -> bool:
        async with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM participants WHERE gather_id = ? AND user_id = ?",
                    (gather_id, user_id),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    async def get_participant(self, gather_id: int, user_id: int) -> ParticipantRecord | None:
        async with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM participants WHERE gather_id = ? AND user_id = ?",
                    (gather_id, user_id),
                ).fetchone()
            finally:
                conn.close()
        return _row_to_participant(row) if row else None

    async def list_participants(self, gather_id: int) -> list[ParticipantRecord]:
        async with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM participants WHERE gather_id = ? ORDER BY joined_at ASC, user_id ASC",
                    (gather_id,),
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_participant(row) for row in rows]

    async def update_bucket(self, gather_id: int, user_id: int, bucket: Bucket) -> bool:
        async with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "UPDATE participants SET bucket = ?, checked_in = 0 WHERE gather_id = ? AND user_id = ?",
                    (bucket, gather_id, user_id),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    async def toggle_checked(self, gather_id: int, user_id: int) -> bool | None:
        async with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT checked_in FROM participants WHERE gather_id = ? AND user_id = ?",
                    (gather_id, user_id),
                ).fetchone()
                if row is None:
                    return None
                new_value = 0 if row["checked_in"] else 1
                conn.execute(
                    "UPDATE participants SET checked_in = ? WHERE gather_id = ? AND user_id = ?",
                    (new_value, gather_id, user_id),
                )
                conn.commit()
                return bool(new_value)
            finally:
                conn.close()

    async def set_logs_channel(self, guild_id: int, channel_id: int) -> None:
        updated_at = _utcnow().isoformat()
        async with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO guild_log_channels (guild_id, channel_id, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        channel_id = excluded.channel_id,
                        updated_at = excluded.updated_at
                    """,
                    (guild_id, channel_id, updated_at),
                )
                conn.commit()
            finally:
                conn.close()

    async def get_logs_channel(self, guild_id: int) -> int | None:
        async with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT channel_id FROM guild_log_channels WHERE guild_id = ?",
                    (guild_id,),
                ).fetchone()
            finally:
                conn.close()
        return int(row["channel_id"]) if row is not None else None

    async def clear_logs_channel(self, guild_id: int) -> bool:
        async with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM guild_log_channels WHERE guild_id = ?",
                    (guild_id,),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    async def add_moderator(self, gather_id: int, user_id: int, added_by: int) -> None:
        added_at = _utcnow().isoformat()
        async with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO moderators (gather_id, user_id, added_by, added_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(gather_id, user_id) DO NOTHING
                    """,
                    (gather_id, user_id, added_by, added_at),
                )
                conn.commit()
            finally:
                conn.close()

    async def remove_moderator(self, gather_id: int, user_id: int) -> bool:
        async with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM moderators WHERE gather_id = ? AND user_id = ?",
                    (gather_id, user_id),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    async def list_moderators(self, gather_id: int) -> list[ModeratorRecord]:
        async with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM moderators WHERE gather_id = ? ORDER BY added_at ASC, user_id ASC",
                    (gather_id,),
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_moderator(row) for row in rows]

    async def is_moderator(self, gather_id: int, user_id: int) -> bool:
        async with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT 1 FROM moderators WHERE gather_id = ? AND user_id = ?",
                    (gather_id, user_id),
                ).fetchone()
            finally:
                conn.close()
        return row is not None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn



def _row_to_gather(row: sqlite3.Row) -> GatherRecord:
    return GatherRecord(
        gather_id=row["gather_id"],
        guild_id=row["guild_id"],
        channel_id=row["channel_id"],
        message_id=row["message_id"],
        thread_id=row["thread_id"],
        voice_channel_id=row["voice_channel_id"],
        title=row["title"],
        comment=row["comment"],
        creator_id=row["creator_id"],
        creator_name=row["creator_name"],
        event_at=datetime.fromisoformat(row["event_at"]),
        main_slots=row["main_slots"],
        extra_slots=row["extra_slots"],
        role_ids=list(json.loads(row["role_ids_json"] or "[]")),
        image_url=row["image_url"],
        create_thread=bool(row["create_thread"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
    )



def _row_to_participant(row: sqlite3.Row) -> ParticipantRecord:
    return ParticipantRecord(
        gather_id=row["gather_id"],
        user_id=row["user_id"],
        display_name=row["display_name"],
        bucket=row["bucket"],
        checked_in=bool(row["checked_in"]),
        joined_at=datetime.fromisoformat(row["joined_at"]),
    )



def _row_to_moderator(row: sqlite3.Row) -> ModeratorRecord:
    return ModeratorRecord(
        gather_id=row["gather_id"],
        user_id=row["user_id"],
        added_by=row["added_by"],
        added_at=datetime.fromisoformat(row["added_at"]),
    )



def _utcnow() -> datetime:
    return datetime.now(tz=UTC)
