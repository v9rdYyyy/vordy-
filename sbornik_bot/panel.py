from __future__ import annotations

from collections import defaultdict
from datetime import timezone

import discord

from .models import GatherRecord, ParticipantRecord

UTC = timezone.utc
CHECK_EMOJI = "✅"


class EmbedFactory:
    COLOR_OPEN = discord.Color.orange()
    COLOR_CLOSED = discord.Color.dark_grey()

    @classmethod
    def build_gather(
        cls,
        gather: GatherRecord,
        participants: list[ParticipantRecord],
        role_mentions: list[str],
    ) -> discord.Embed:
        color = cls.COLOR_CLOSED if gather.is_closed else cls.COLOR_OPEN
        embed = discord.Embed(title=f"/плюсы {gather.title}", color=color)

        if gather.is_closed:
            embed.description = "Сбор закрыт."
        else:
            embed.description = "Используйте кнопки ниже, чтобы записаться в сбор."

        event_ts = int(gather.event_at.astimezone(UTC).timestamp())
        embed.add_field(name="Создал", value=f"<@{gather.creator_id}>", inline=False)
        embed.add_field(
            name="Дата",
            value=f"<t:{event_ts}:F> (<t:{event_ts}:R>)",
            inline=False,
        )
        embed.add_field(
            name="Комментарий",
            value=discord.utils.escape_markdown(gather.comment) if gather.comment else "—",
            inline=False,
        )
        embed.add_field(
            name="Роли",
            value=", ".join(role_mentions) if role_mentions else "Без ограничений",
            inline=False,
        )

        grouped: dict[str, list[ParticipantRecord]] = defaultdict(list)
        for entry in participants:
            grouped[entry.bucket].append(entry)

        embed.add_field(
            name=f"Участники ({len(grouped['main'])}/{gather.main_slots})",
            value=cls._format_people(grouped["main"]),
            inline=False,
        )

        extra_title = f"Доп. слоты ({len(grouped['extra'])}/{gather.extra_slots})"
        extra_value = (
            cls._format_people(grouped["extra"])
            if gather.extra_slots > 0 or grouped["extra"]
            else "Не используются"
        )
        embed.add_field(name=extra_title, value=extra_value, inline=False)

        if grouped["reserve"]:
            embed.add_field(
                name=f"Резерв ({len(grouped['reserve'])})",
                value=cls._format_people(grouped["reserve"]),
                inline=False,
            )

        if gather.thread_id:
            embed.add_field(name="Ветка", value=f"<#{gather.thread_id}>", inline=False)

        if gather.image_url:
            embed.set_image(url=gather.image_url)

        footer_parts = ["Синий — запись", "Красный — выйти", "Зелёный — отметиться"]
        if gather.is_closed:
            footer_parts = ["Сбор закрыт"]
        embed.set_footer(text=" • ".join(footer_parts))
        return embed

    @staticmethod
    def _format_people(entries: list[ParticipantRecord]) -> str:
        if not entries:
            return "—"

        lines: list[str] = []
        for index, entry in enumerate(entries, start=1):
            suffix = f" {CHECK_EMOJI}" if entry.checked_in else ""
            lines.append(f"{index}. <@{entry.user_id}>{suffix}")

        text = "\n".join(lines)
        if len(text) > 1000:
            return text[:980] + "\n..."
        return text
