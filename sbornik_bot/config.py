from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "sbornik_bot.sqlite3"


@dataclass(slots=True)
class Settings:
    token: str
    guild_id: int | None
    database_path: Path
    timezone: str



def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")

    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Не найден DISCORD_BOT_TOKEN. Заполните .env по примеру из .env.example."
        )

    guild_raw = os.getenv("DISCORD_GUILD_ID", "").strip()
    guild_id = int(guild_raw) if guild_raw else None

    db_raw = os.getenv("SBORNIK_BOT_DB_PATH", "").strip()
    database_path = Path(db_raw) if db_raw else DEFAULT_DB_PATH
    database_path.parent.mkdir(parents=True, exist_ok=True)

    timezone = os.getenv("BOT_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"

    return Settings(
        token=token,
        guild_id=guild_id,
        database_path=database_path,
        timezone=timezone,
    )
