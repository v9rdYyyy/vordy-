from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Bucket = Literal["main", "extra", "reserve"]


@dataclass(slots=True)
class GatherRecord:
    gather_id: int
    guild_id: int
    channel_id: int
    message_id: int
    thread_id: int | None
    title: str
    comment: str | None
    creator_id: int
    creator_name: str
    event_at: datetime
    main_slots: int
    extra_slots: int
    voice_channel_id: int | None = None
    role_ids: list[int] = field(default_factory=list)
    image_url: str | None = None
    create_thread: bool = False
    created_at: datetime | None = None
    closed_at: datetime | None = None

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None


@dataclass(slots=True)
class ParticipantRecord:
    gather_id: int
    user_id: int
    display_name: str
    bucket: Bucket
    checked_in: bool
    joined_at: datetime


@dataclass(slots=True)
class ModeratorRecord:
    gather_id: int
    user_id: int
    added_by: int
    added_at: datetime
