from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

import dateparser
import discord
from discord import app_commands
from discord.ext import commands

from .config import Settings
from .models import Bucket, GatherRecord, ParticipantRecord
from .panel import EmbedFactory
from .storage import Storage

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("sbornik_bot")

UTC = timezone.utc
ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
ID_RE = re.compile(r"\d+")
MESSAGE_URL_TEMPLATE = "https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
VOICE_URL_TEMPLATE = "https://discord.com/channels/{guild_id}/{channel_id}"
EPHEMERAL_DELETE_DELAY_SECONDS = 1.0
BUCKET_LABELS: dict[Bucket, str] = {
    "main": "основу",
    "extra": "доп. слоты",
    "reserve": "резерв",
}
BUCKET_FROM_LABELS: dict[Bucket, str] = {
    "main": "основы",
    "extra": "доп. слотов",
    "reserve": "резерва",
}
MOVE_MEMBER_PAGE_SIZE = 25

ALLOWED_CREATE_COMMAND_USER_IDS: set[int] = {
    504936984326832128
}

SYNC_GUILD_ID: int | None = 1444268473256513569



@dataclass(slots=True)
class GatherSnapshot:
    gather: GatherRecord
    participants: list[ParticipantRecord]

    @property
    def by_bucket(self) -> dict[Bucket, list[ParticipantRecord]]:
        grouped: dict[Bucket, list[ParticipantRecord]] = {
            "main": [],
            "extra": [],
            "reserve": [],
        }
        for entry in self.participants:
            grouped[entry.bucket].append(entry)
        return grouped


class GatherView(discord.ui.View):
    def __init__(self, bot: "SbornikBot", gather_id: int, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.gather_id = gather_id
        action_map = {
            0: f"gather:{gather_id}:join_main",
            1: f"gather:{gather_id}:join_extra",
            2: f"gather:{gather_id}:leave",
            3: f"gather:{gather_id}:check",
            4: f"gather:{gather_id}:manage",
        }
        for index, item in enumerate(self.children):
            item.disabled = disabled
            if hasattr(item, "custom_id"):
                item.custom_id = action_map[index]

    @discord.ui.button(
        label="Присоединиться",
        style=discord.ButtonStyle.primary,
        custom_id="gather:join_main:placeholder",
        row=0,
    )
    async def join_main(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["GatherView"],
    ) -> None:
        del button
        await self.bot.join_gather(interaction, self.gather_id, preferred="main")

    @discord.ui.button(
        label="Присоединиться к доп. слоту",
        style=discord.ButtonStyle.primary,
        custom_id="gather:join_extra:placeholder",
        row=0,
    )
    async def join_extra(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["GatherView"],
    ) -> None:
        del button
        await self.bot.join_gather(interaction, self.gather_id, preferred="extra")

    @discord.ui.button(
        label="Покинуть",
        style=discord.ButtonStyle.danger,
        custom_id="gather:leave:placeholder",
        row=0,
    )
    async def leave(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["GatherView"],
    ) -> None:
        del button
        await self.bot.leave_gather(interaction, self.gather_id)

    @discord.ui.button(
        label="Отметиться",
        style=discord.ButtonStyle.success,
        custom_id="gather:check:placeholder",
        row=0,
    )
    async def check_in(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["GatherView"],
    ) -> None:
        del button
        await self.bot.toggle_check_in(interaction, self.gather_id)

    @discord.ui.button(
        label="Управление",
        style=discord.ButtonStyle.danger,
        custom_id="gather:manage:placeholder",
        row=1,
    )
    async def manage(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["GatherView"],
    ) -> None:
        del button
        await self.bot.open_management(interaction, self.gather_id)


class ManagementDashboardView(discord.ui.View):
    def __init__(self, bot: "SbornikBot", gather_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.gather_id = gather_id

    @discord.ui.button(label="Выгнать участника", style=discord.ButtonStyle.danger, row=0)
    async def kick(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ManagementDashboardView"],
    ) -> None:
        del button
        await self.bot.open_kick_menu(interaction, self.gather_id)

    @discord.ui.button(label="Переместить", style=discord.ButtonStyle.danger, row=0)
    async def move(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ManagementDashboardView"],
    ) -> None:
        del button
        await self.bot.open_move_menu(interaction, self.gather_id)

    @discord.ui.button(label="Закрыть с тэгом", style=discord.ButtonStyle.danger, row=0)
    async def close_with_tag(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ManagementDashboardView"],
    ) -> None:
        del button
        await self.bot.close_gather_from_panel(interaction, self.gather_id, tag_users=True)

    @discord.ui.button(label="Закрыть бесшумно", style=discord.ButtonStyle.danger, row=0)
    async def close_silent(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ManagementDashboardView"],
    ) -> None:
        del button
        await self.bot.close_gather_from_panel(interaction, self.gather_id, tag_users=False)

    @discord.ui.button(label="Выбрать войс", style=discord.ButtonStyle.danger, row=0)
    async def voice(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ManagementDashboardView"],
    ) -> None:
        del button
        await self.bot.open_voice_picker(interaction, self.gather_id)

    @discord.ui.button(label="Переместить в войс", style=discord.ButtonStyle.danger, row=1)
    async def add_to_voice(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ManagementDashboardView"],
    ) -> None:
        del button
        await self.bot.add_checked_members_to_voice(interaction, self.gather_id)

    @discord.ui.button(label="Напомнить в ЛС", style=discord.ButtonStyle.danger, row=1)
    async def remind(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ManagementDashboardView"],
    ) -> None:
        del button
        await self.bot.send_reminders(interaction, self.gather_id)

    @discord.ui.button(label="Добавить модератора", style=discord.ButtonStyle.danger, row=1)
    async def add_mod(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ManagementDashboardView"],
    ) -> None:
        del button
        await self.bot.open_add_moderator(interaction, self.gather_id)

    @discord.ui.button(label="Удалить модератора", style=discord.ButtonStyle.danger, row=1)
    async def remove_mod(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["ManagementDashboardView"],
    ) -> None:
        del button
        await self.bot.open_remove_moderator(interaction, self.gather_id)


class KickMemberSelect(discord.ui.Select["KickMemberView"]):
    def __init__(self, bot: "SbornikBot", gather_id: int, entries: list[ParticipantRecord]) -> None:
        self.bot = bot
        self.gather_id = gather_id
        self._names = {entry.user_id: entry.display_name for entry in entries}

        options = [
            discord.SelectOption(
                label=_truncate(entry.display_name, 100),
                value=str(entry.user_id),
                description=f"Сейчас в: {BUCKET_LABELS[entry.bucket]}",
            )
            for entry in entries[:25]
        ]

        super().__init__(
            placeholder="Выберите участника",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        snapshot = await self.bot.snapshot(self.gather_id)
        if snapshot is None:
            await interaction.response.edit_message(content="Сбор уже удалён.", view=None)
            return

        if not await self.bot.ensure_manager(interaction, snapshot.gather):
            return

        user_id = int(self.values[0])
        removed_entry = next((entry for entry in snapshot.participants if entry.user_id == user_id), None)
        removed = await self.bot.storage.remove_participant(self.gather_id, user_id)
        if removed:
            await self.bot.refresh_gather(self.gather_id)
            name = discord.utils.escape_markdown(self._names.get(user_id, str(user_id)))
            if removed_entry is not None and interaction.guild is not None and interaction.user is not None:
                await self.bot.log_member_removed(
                    snapshot.gather,
                    removed_entry,
                    actor=interaction.user,
                    reason="kick",
                )
            await interaction.response.edit_message(
                content=f"Участник **{name}** убран из сбора.",
                view=None,
            )
        else:
            await interaction.response.edit_message(
                content="Этот участник уже отсутствует в сборе.",
                view=None,
            )


class KickMemberView(discord.ui.View):
    def __init__(self, bot: "SbornikBot", gather_id: int, entries: list[ParticipantRecord]) -> None:
        super().__init__(timeout=180)
        self.add_item(KickMemberSelect(bot, gather_id, entries))


class MoveMemberSelect(discord.ui.Select["MoveMemberView"]):
    def __init__(
        self,
        bot: "SbornikBot",
        gather_id: int,
        entries: list[ParticipantRecord],
        *,
        page: int,
        total_pages: int,
    ) -> None:
        self.bot = bot
        self.gather_id = gather_id
        self._entries = {entry.user_id: entry for entry in entries}
        self.page = page
        self.total_pages = total_pages

        start = page * MOVE_MEMBER_PAGE_SIZE
        page_entries = entries[start : start + MOVE_MEMBER_PAGE_SIZE]
        options = [
            discord.SelectOption(
                label=_truncate(entry.display_name, 100),
                value=str(entry.user_id),
                description=f"Сейчас в: {BUCKET_LABELS[entry.bucket]}",
            )
            for entry in page_entries
        ]
        placeholder = "Кого переместить?"
        if total_pages > 1:
            placeholder = f"Кого переместить? Стр. {page + 1}/{total_pages}"
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        gather = await self.bot.storage.get_gather(self.gather_id)
        if gather is None:
            await interaction.response.edit_message(content="Сбор уже удалён.", view=None)
            return
        if not await self.bot.ensure_manager(interaction, gather):
            return

        user_id = int(self.values[0])
        entry = self._entries[user_id]
        view = MoveTargetView(self.bot, gather.gather_id, entry.user_id, entry.display_name)
        await interaction.response.send_message(
            f"Куда переместить **{discord.utils.escape_markdown(entry.display_name)}**?",
            view=view,
            ephemeral=True,
        )


class MoveMemberView(discord.ui.View):
    def __init__(
        self,
        bot: "SbornikBot",
        gather_id: int,
        entries: list[ParticipantRecord],
        *,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.gather_id = gather_id
        self.entries = sorted(entries, key=lambda entry: (entry.bucket, entry.display_name.casefold()))
        self.page = page
        self.total_pages = max(1, (len(self.entries) + MOVE_MEMBER_PAGE_SIZE - 1) // MOVE_MEMBER_PAGE_SIZE)
        self.add_item(
            MoveMemberSelect(
                bot,
                gather_id,
                self.entries,
                page=self.page,
                total_pages=self.total_pages,
            )
        )
        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["MoveMemberView"],
    ) -> None:
        del button
        new_page = max(0, self.page - 1)
        view = MoveMemberView(self.bot, self.gather_id, self.entries, page=new_page)
        await interaction.response.edit_message(content=view.menu_title, view=view)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["MoveMemberView"],
    ) -> None:
        del button
        new_page = min(self.total_pages - 1, self.page + 1)
        view = MoveMemberView(self.bot, self.gather_id, self.entries, page=new_page)
        await interaction.response.edit_message(content=view.menu_title, view=view)

    @property
    def menu_title(self) -> str:
        if self.total_pages <= 1:
            return "Кого переместить?"
        return f"Кого переместить? Страница {self.page + 1}/{self.total_pages}"


class MoveTargetView(discord.ui.View):
    def __init__(self, bot: "SbornikBot", gather_id: int, user_id: int, display_name: str) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.gather_id = gather_id
        self.user_id = user_id
        self.display_name = display_name

    @discord.ui.button(label="В основу", style=discord.ButtonStyle.danger)
    async def to_main(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["MoveTargetView"],
    ) -> None:
        del button
        await self.bot.move_participant(interaction, self.gather_id, self.user_id, "main", self.display_name)

    @discord.ui.button(label="В доп. слоты", style=discord.ButtonStyle.danger)
    async def to_extra(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["MoveTargetView"],
    ) -> None:
        del button
        await self.bot.move_participant(interaction, self.gather_id, self.user_id, "extra", self.display_name)

    @discord.ui.button(label="В резерв", style=discord.ButtonStyle.danger)
    async def to_reserve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["MoveTargetView"],
    ) -> None:
        del button
        await self.bot.move_participant(interaction, self.gather_id, self.user_id, "reserve", self.display_name)


class VoiceChannelSelect(discord.ui.ChannelSelect["VoicePickerView"]):
    def __init__(self, bot: "SbornikBot", gather_id: int) -> None:
        self.bot = bot
        self.gather_id = gather_id
        super().__init__(
            placeholder="Выберите голосовой канал",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.voice, discord.ChannelType.stage_voice],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        gather = await self.bot.storage.get_gather(self.gather_id)
        if gather is None:
            await interaction.response.edit_message(content="Сбор уже удалён.", view=None)
            return
        if not await self.bot.ensure_manager(interaction, gather):
            return
        channel = self.values[0]
        await self.bot.storage.set_voice_channel(self.gather_id, channel.id)
        updated_gather = await self.bot.storage.get_gather(self.gather_id)
        if updated_gather is None:
            await interaction.response.edit_message(content="Сбор уже удалён.", view=None)
            return
        await self.bot.post_voice_reference(updated_gather, channel)
        await interaction.response.edit_message(
            content=f"Голосовой канал сохранён и отправлен: {channel.mention}",
            view=None,
        )


class VoicePickerView(discord.ui.View):
    def __init__(self, bot: "SbornikBot", gather_id: int) -> None:
        super().__init__(timeout=180)
        self.add_item(VoiceChannelSelect(bot, gather_id))


class AddModeratorSelect(discord.ui.UserSelect["AddModeratorView"]):
    def __init__(self, bot: "SbornikBot", gather_id: int) -> None:
        self.bot = bot
        self.gather_id = gather_id
        super().__init__(placeholder="Выберите участника", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        gather = await self.bot.storage.get_gather(self.gather_id)
        if gather is None:
            await interaction.response.edit_message(content="Сбор уже удалён.", view=None)
            return
        if not await self.bot.ensure_creator(interaction, gather):
            return
        selected = self.values[0]
        if selected.id == gather.creator_id:
            await interaction.response.edit_message(
                content="Создатель уже имеет все права модератора.",
                view=None,
            )
            return
        if selected.bot:
            await interaction.response.edit_message(
                content="Бота нельзя добавить модератором сбора.",
                view=None,
            )
            return
        await self.bot.storage.add_moderator(self.gather_id, selected.id, interaction.user.id)
        await interaction.response.edit_message(
            content=f"Пользователь <@{selected.id}> теперь модератор этого сбора.",
            view=None,
        )


class AddModeratorView(discord.ui.View):
    def __init__(self, bot: "SbornikBot", gather_id: int) -> None:
        super().__init__(timeout=180)
        self.add_item(AddModeratorSelect(bot, gather_id))


class RemoveModeratorSelect(discord.ui.Select["RemoveModeratorView"]):
    def __init__(self, bot: "SbornikBot", gather_id: int, options: list[discord.SelectOption]) -> None:
        self.bot = bot
        self.gather_id = gather_id
        super().__init__(
            placeholder="Кого убрать из модераторов?",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        gather = await self.bot.storage.get_gather(self.gather_id)
        if gather is None:
            await interaction.response.edit_message(content="Сбор уже удалён.", view=None)
            return
        if not await self.bot.ensure_creator(interaction, gather):
            return
        user_id = int(self.values[0])
        removed = await self.bot.storage.remove_moderator(self.gather_id, user_id)
        if removed:
            await interaction.response.edit_message(
                content=f"Модератор <@{user_id}> удалён.",
                view=None,
            )
        else:
            await interaction.response.edit_message(
                content="Этот пользователь уже не модератор.",
                view=None,
            )


class RemoveModeratorView(discord.ui.View):
    def __init__(self, bot: "SbornikBot", gather_id: int, options: list[discord.SelectOption]) -> None:
        super().__init__(timeout=180)
        self.add_item(RemoveModeratorSelect(bot, gather_id, options))


class GatherCog(commands.Cog):
    def __init__(self, bot: "SbornikBot") -> None:
        self.bot = bot

    @app_commands.command(name="логи", description="Включить логи сборов в текущем канале")
    @app_commands.guild_only()
    async def enable_logs(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Эта команда работает только на сервере.",
                ephemeral=True,
            )
            return

        if not self.bot.can_use_create_command(interaction):
            await interaction.response.send_message(
                f"Нет доступа. Твой ID: {interaction.user.id}",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "Эту команду можно использовать только в текстовом канале или ветке.",
                ephemeral=True,
            )
            return

        await self.bot.storage.set_logs_channel(interaction.guild.id, interaction.channel.id)
        await interaction.response.send_message(
            "Логи сборов включены. Теперь события по сборам будут отправляться в этот канал.",
            ephemeral=True,
        )

    @app_commands.command(name="стоплоги", description="Отключить логи сборов")
    @app_commands.guild_only()
    async def disable_logs(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Эта команда работает только на сервере.",
                ephemeral=True,
            )
            return

        if not self.bot.can_use_create_command(interaction):
            await interaction.response.send_message(
                f"Нет доступа. Твой ID: {interaction.user.id}",
                ephemeral=True,
            )
            return

        removed = await self.bot.storage.clear_logs_channel(interaction.guild.id)
        await interaction.response.send_message(
            "Логи сборов отключены." if removed else "Логи сборов уже были отключены.",
            ephemeral=True,
        )

    @enable_logs.error
    async def enable_logs_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        logger.exception("Ошибка в /логи", exc_info=error)
        message = "Не удалось включить логи. Проверьте права бота и логи консоли."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @disable_logs.error
    async def disable_logs_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        logger.exception("Ошибка в /стоплоги", exc_info=error)
        message = "Не удалось отключить логи. Проверьте логи консоли."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="сбор", description="Создать новый сбор")
    @app_commands.guild_only()
    async def create_gather(
        self,
        interaction: discord.Interaction,
        название: str,
        дата: str,
        слоты: app_commands.Range[int, 1, 99],
        доп_слоты: app_commands.Range[int, 0, 99] = 0,
        роли: str | None = None,
        комментарий: str | None = None,
        изображение: discord.Attachment | None = None,
    ) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Эта команда работает только на сервере.",
                ephemeral=True,
            )
            return

        if not self.bot.can_use_create_command(interaction):
            await interaction.response.send_message(
                f"Нет доступа. Твой ID: {interaction.user.id}",
                ephemeral=True,
            )
            return

        event_at = self.bot.parse_event_datetime(дата)
        if event_at is None:
            await interaction.response.send_message(
                "Не удалось разобрать дату. Пример: `08.03.2026 19:00` или `завтра 18:30`.",
                ephemeral=True,
            )
            return

        if event_at <= datetime.now(tz=UTC):
            await interaction.response.send_message(
                "Дата сбора должна быть в будущем.",
                ephemeral=True,
            )
            return

        try:
            role_ids = self.bot.parse_roles(interaction.guild, роли)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        creator_name = _display_name(interaction.user)
        image_url = изображение.url if изображение is not None else None

        await interaction.response.defer(ephemeral=True)

        gather_id = await self.bot.storage.create_gather(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            title=название.strip(),
            comment=комментарий.strip() if комментарий else None,
            creator_id=interaction.user.id,
            creator_name=creator_name,
            event_at=event_at,
            main_slots=int(слоты),
            extra_slots=int(доп_слоты),
            role_ids=role_ids,
            image_url=image_url,
            create_thread=True,
        )

        gather = await self.bot.storage.get_gather(gather_id)
        assert gather is not None
        view = GatherView(self.bot, gather_id)
        role_mentions = self.bot.role_mentions(interaction.guild, gather.role_ids)
        embed = EmbedFactory.build_gather(gather, [], role_mentions)

        message = await interaction.channel.send(
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        thread_id: int | None = None
        thread_note = ""
        try:
            thread = await message.create_thread(name=_truncate(f"{название}", 100))
            thread_id = thread.id
            try:
                await thread.send(
                    f"Ветка для сбора **{discord.utils.escape_markdown(название)}** создана."
                )
            except discord.HTTPException:
                pass
            thread_note = f"\nВетка создана: <#{thread.id}>"
        except discord.HTTPException:
            thread_note = (
                "\nНе удалось создать ветку. Проверь права `Create Public Threads` и `Send Messages in Threads`."
            )

        await self.bot.storage.set_message_targets(gather_id, message.id, thread_id)
        await self.bot.refresh_gather(gather_id)

        await interaction.followup.send(
            f"Сбор опубликован.{thread_note}",
            ephemeral=True,
        )

    @create_gather.error
    async def create_gather_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        logger.exception("Ошибка в /сбор", exc_info=error)
        message = "Не удалось создать сбор. Проверь логи бота."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class SbornikBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        intents.guilds = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self.storage = Storage(settings.database_path)
        self.tzinfo = ZoneInfo(settings.timezone)

    async def setup_hook(self) -> None:
        await self.storage.initialize()
        await self.add_cog(GatherCog(self))
        await self.restore_persistent_views()

        sync_guild_id = SYNC_GUILD_ID or self.settings.guild_id

        if sync_guild_id:
            guild = discord.Object(id=sync_guild_id)
            self.tree.clear_commands(guild=guild)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info(
                "Команды принудительно пересинхронизированы с сервером %s: %s шт.",
                sync_guild_id,
                len(synced),
            )
        else:
            synced = await self.tree.sync()
            logger.warning(
                "Ни SYNC_GUILD_ID в bot.py, ни DISCORD_GUILD_ID в настройках не указаны. Новые slash-команды могут появляться с задержкой, потому что синхронизируются глобально."
            )
            logger.info("Глобальные команды синхронизированы: %s шт.", len(synced))

    async def restore_persistent_views(self) -> None:
        gathers = await self.storage.list_open_gathers()
        for gather in gathers:
            self.add_view(GatherView(self, gather.gather_id))
        logger.info("Восстановлено persistent views для %s активных сборов.", len(gathers))

    async def on_ready(self) -> None:
        if self.user is not None:
            logger.info("Бот вошёл как %s (%s)", self.user, self.user.id)

    def can_use_create_command(self, interaction: discord.Interaction) -> bool:
        perms = interaction.permissions
        is_admin = perms is not None and perms.administrator
        return is_admin or interaction.user.id in ALLOWED_CREATE_COMMAND_USER_IDS

    def parse_event_datetime(self, raw_value: str) -> datetime | None:
        parsed = dateparser.parse(
            raw_value,
            languages=["ru", "en"],
            settings={
                "TIMEZONE": self.settings.timezone,
                "TO_TIMEZONE": "UTC",
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
                "DATE_ORDER": "DMY",
            },
        )
        if parsed is None:
            return None
        return parsed.astimezone(UTC)

    def parse_roles(self, guild: discord.Guild, roles_text: str | None) -> list[int]:
        if roles_text is None or not roles_text.strip():
            return []

        candidates: list[discord.Role] = []
        unresolved: list[str] = []

        mentions = ROLE_MENTION_RE.findall(roles_text)
        for role_id_raw in mentions:
            role = guild.get_role(int(role_id_raw))
            if role is not None and role not in candidates:
                candidates.append(role)

        cleaned = ROLE_MENTION_RE.sub(" ", roles_text)
        tokens = [token.strip() for token in re.split(r"[,;\n]+", cleaned) if token.strip()]
        for token in tokens:
            if token.startswith("@"):
                token = token[1:]
            role: discord.Role | None = None
            if ID_RE.fullmatch(token):
                role = guild.get_role(int(token))
            if role is None:
                role = discord.utils.find(lambda item: item.name.casefold() == token.casefold(), guild.roles)
            if role is None:
                unresolved.append(token)
            elif role not in candidates:
                candidates.append(role)

        if unresolved:
            raise ValueError(
                "Не удалось найти роли: " + ", ".join(f"`{token}`" for token in unresolved)
            )

        return [role.id for role in candidates]

    def role_mentions(self, guild: discord.Guild | None, role_ids: list[int]) -> list[str]:
        if guild is None:
            return [f"<@&{role_id}>" for role_id in role_ids]
        mentions: list[str] = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            mentions.append(role.mention if role is not None else f"<@&{role_id}>")
        return mentions

    async def send_gather_log(self, guild_id: int, content: str) -> None:
        channel_id = await self.storage.get_logs_channel(guild_id)
        if channel_id is None:
            return

        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        try:
            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("Не удалось отправить лог сбора в канал %s", channel_id)

    async def log_member_joined(self, gather: GatherRecord, user: discord.abc.User, bucket: Bucket) -> None:
        await self.send_gather_log(
            gather.guild_id,
            self._format_gather_log(
                gather,
                f"➕ <@{user.id}> зашёл в {BUCKET_LABELS[bucket]}",
            ),
        )

    async def log_member_left(
        self,
        gather: GatherRecord,
        user: discord.abc.User,
        bucket: Bucket,
    ) -> None:
        await self.send_gather_log(
            gather.guild_id,
            self._format_gather_log(
                gather,
                f"➖ <@{user.id}> вышел из {BUCKET_FROM_LABELS[bucket]}",
            ),
        )

    async def log_member_removed(
        self,
        gather: GatherRecord,
        participant: ParticipantRecord,
        *,
        actor: discord.abc.User,
        reason: str,
    ) -> None:
        if reason == "kick":
            action = (
                f"🛑 <@{actor.id}> выгнал <@{participant.user_id}> "
                f"из {BUCKET_FROM_LABELS[participant.bucket]}"
            )
        else:
            action = (
                f"➖ <@{participant.user_id}> вышел из {BUCKET_FROM_LABELS[participant.bucket]}"
            )
        await self.send_gather_log(gather.guild_id, self._format_gather_log(gather, action))

    async def log_member_moved(
        self,
        gather: GatherRecord,
        user_id: int,
        source_bucket: Bucket,
        target_bucket: Bucket,
        *,
        actor: discord.abc.User | None = None,
    ) -> None:
        prefix = f"🔄 <@{user_id}> перемещён"
        if actor is not None and actor.id != user_id:
            prefix = f"🔄 <@{actor.id}> переместил <@{user_id}>"
        elif actor is not None and actor.id == user_id:
            prefix = f"🔄 <@{user_id}> перешёл"
        action = f"{prefix} из {BUCKET_FROM_LABELS[source_bucket]} в {BUCKET_LABELS[target_bucket]}"
        await self.send_gather_log(
            gather.guild_id,
            self._format_gather_log(gather, action),
        )
        if actor is not None and actor.id != user_id:
            await self.send_thread_move_log(
                gather,
                actor_id=actor.id,
                user_id=user_id,
                source_bucket=source_bucket,
                target_bucket=target_bucket,
            )

    async def send_thread_move_log(
        self,
        gather: GatherRecord,
        *,
        actor_id: int,
        user_id: int,
        source_bucket: Bucket,
        target_bucket: Bucket,
    ) -> None:
        thread = await self.fetch_thread(gather.thread_id)
        if thread is None:
            return
        try:
            await thread.send(
                (
                    f"🔄 <@{actor_id}> переместил <@{user_id}> "
                    f"из {BUCKET_FROM_LABELS[source_bucket]} в {BUCKET_LABELS[target_bucket]}"
                ),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException:
            logger.warning(
                "Не удалось отправить лог перемещения в ветку для сбора %s",
                gather.gather_id,
            )

    def _format_gather_log(self, gather: GatherRecord, action: str) -> str:
        message_url = MESSAGE_URL_TEMPLATE.format(
            guild_id=gather.guild_id,
            channel_id=gather.channel_id,
            message_id=gather.message_id,
        )
        return (
            f"{action}\n"
            f"Сбор: **{discord.utils.escape_markdown(gather.title)}**\n"
            f"Ссылка: {message_url}"
        )

    async def snapshot(self, gather_id: int) -> GatherSnapshot | None:
        gather = await self.storage.get_gather(gather_id)
        if gather is None:
            return None
        participants = await self.storage.list_participants(gather_id)
        return GatherSnapshot(gather=gather, participants=participants)

    async def refresh_gather(self, gather_id: int) -> None:
        snapshot = await self.snapshot(gather_id)
        if snapshot is None:
            return

        message = await self.fetch_gather_message(snapshot.gather)
        if message is None:
            logger.warning("Не удалось найти сообщение сбора %s", gather_id)
            return

        guild = self.get_guild(snapshot.gather.guild_id)
        role_mentions = self.role_mentions(guild, snapshot.gather.role_ids)
        embed = EmbedFactory.build_gather(snapshot.gather, snapshot.participants, role_mentions)
        view = GatherView(self, gather_id, disabled=snapshot.gather.is_closed)
        await message.edit(
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def fetch_gather_message(self, gather: GatherRecord) -> discord.Message | None:
        channel = self.get_channel(gather.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(gather.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None
        try:
            return await channel.fetch_message(gather.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def fetch_thread(self, thread_id: int | None) -> discord.Thread | None:
        if thread_id is None:
            return None
        channel = self.get_channel(thread_id)
        if isinstance(channel, discord.Thread):
            return channel
        try:
            fetched = await self.fetch_channel(thread_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        return fetched if isinstance(fetched, discord.Thread) else None

    async def ensure_manager(self, interaction: discord.Interaction, gather: GatherRecord) -> bool:
        if interaction.guild is None or interaction.user is None:
            await self._safe_respond(
                interaction,
                "Эта кнопка работает только на сервере.",
                auto_delete=False,
            )
            return False
        if await self.user_can_manage(gather, interaction.user):
            return True
        await self._safe_respond(
            interaction,
            "У вас недостаточно прав.",
            auto_delete=False,
        )
        return False

    async def ensure_creator(self, interaction: discord.Interaction, gather: GatherRecord) -> bool:
        if interaction.user is None:
            await self._safe_respond(
                interaction,
                "Эта кнопка работает только на сервере.",
                auto_delete=False,
            )
            return False
        if interaction.user.id == gather.creator_id:
            return True
        await self._safe_respond(
            interaction,
            "Эта кнопка доступна только создателю сбора.",
            auto_delete=False,
        )
        return False

    async def user_can_manage(self, gather: GatherRecord, user: discord.abc.User) -> bool:
        if user.id == gather.creator_id:
            return True
        if _is_admin(user):
            return True
        return await self.storage.is_moderator(gather.gather_id, user.id)

    async def join_gather(
        self,
        interaction: discord.Interaction,
        gather_id: int,
        *,
        preferred: Bucket,
    ) -> None:
        snapshot = await self.snapshot(gather_id)
        if snapshot is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        gather = snapshot.gather
        if gather.is_closed:
            await self._safe_respond(interaction, "Этот сбор уже закрыт.")
            return
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await self._safe_respond(interaction, "Эта кнопка работает только на сервере.")
            return
        if gather.role_ids and not any(role.id in gather.role_ids for role in interaction.user.roles):
            await self._safe_respond(
                interaction,
                "У вас нет подходящей роли для участия в этом сборе.",
            )
            return

        counts = self._counts_excluding(snapshot.participants, interaction.user.id)
        target = self._choose_bucket(gather, preferred, counts)
        if target is None:
            await self._safe_respond(interaction, "Доп. слоты для этого сбора отключены.")
            return

        existing = next((p for p in snapshot.participants if p.user_id == interaction.user.id), None)
        if existing is not None and existing.bucket == target:
            if preferred == "extra" and gather.extra_slots <= 0:
                await self._safe_respond(interaction, "Доп. слоты для этого сбора отключены.")
            else:
                await self._safe_respond(
                    interaction,
                    f"Вы уже записаны в {BUCKET_LABELS[target]}.",
                )
            return

        await self.storage.upsert_participant(
            gather_id=gather_id,
            user_id=interaction.user.id,
            display_name=interaction.user.display_name,
            bucket=target,
        )
        await self.refresh_gather(gather_id)
        await self.add_user_to_thread(gather, interaction.user)
        if existing is None:
            await self.log_member_joined(gather, interaction.user, target)
        else:
            await self.log_member_moved(
                gather,
                interaction.user.id,
                existing.bucket,
                target,
                actor=interaction.user,
            )

        message = f"Вы записаны в {BUCKET_LABELS[target]}."
        if preferred == "main" and target == "extra":
            message = "Основные слоты заполнены, вы добавлены в доп. слоты."
        elif target == "reserve":
            message = "Свободных мест нет, вы добавлены в резерв."
        if gather.thread_id:
            message += f"\nВетка: <#{gather.thread_id}>"
        await self._safe_respond(interaction, message)

    async def leave_gather(self, interaction: discord.Interaction, gather_id: int) -> None:
        gather = await self.storage.get_gather(gather_id)
        if gather is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if gather.is_closed:
            await self._safe_respond(interaction, "Этот сбор уже закрыт.")
            return
        if interaction.guild is None or interaction.user is None:
            await self._safe_respond(interaction, "Эта кнопка работает только на сервере.")
            return
        existing = await self.storage.get_participant(gather_id, interaction.user.id)
        removed = await self.storage.remove_participant(gather_id, interaction.user.id)
        if not removed:
            await self._safe_respond(interaction, "Вы ещё не записаны в этот сбор.")
            return
        await self.refresh_gather(gather_id)
        if existing is not None:
            await self.log_member_left(gather, interaction.user, existing.bucket)
        await self._safe_respond(interaction, "Вы покинули сбор.")

    async def toggle_check_in(self, interaction: discord.Interaction, gather_id: int) -> None:
        gather = await self.storage.get_gather(gather_id)
        if gather is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if gather.is_closed:
            await self._safe_respond(interaction, "Этот сбор уже закрыт.")
            return
        if interaction.guild is None or interaction.user is None:
            await self._safe_respond(interaction, "Эта кнопка работает только на сервере.")
            return
        toggled = await self.storage.toggle_checked(gather_id, interaction.user.id)
        if toggled is None:
            await self._safe_respond(
                interaction,
                "Сначала запишитесь в сбор, потом можно отмечаться.",
            )
            return
        await self.refresh_gather(gather_id)
        await self._safe_respond(
            interaction,
            "Отметка поставлена." if toggled else "Отметка снята.",
        )

    async def open_management(self, interaction: discord.Interaction, gather_id: int) -> None:
        gather = await self.storage.get_gather(gather_id)
        if gather is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if not await self.ensure_manager(interaction, gather):
            return
        view = ManagementDashboardView(self, gather_id)
        await self._safe_respond(
            interaction,
            "Выберите действие для управления сбором:",
            view=view,
            auto_delete=False,
        )

    async def open_kick_menu(self, interaction: discord.Interaction, gather_id: int) -> None:
        snapshot = await self.snapshot(gather_id)
        if snapshot is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if not await self.ensure_manager(interaction, snapshot.gather):
            return
        if not snapshot.participants:
            await self._safe_respond(interaction, "В этом сборе пока нет участников.")
            return
        view = KickMemberView(self, gather_id, snapshot.participants)
        await self._safe_respond(
            interaction,
            "Кого выгнать из сбора?",
            view=view,
            auto_delete=False,
        )

    async def open_move_menu(self, interaction: discord.Interaction, gather_id: int) -> None:
        snapshot = await self.snapshot(gather_id)
        if snapshot is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if not await self.ensure_manager(interaction, snapshot.gather):
            return
        if not snapshot.participants:
            await self._safe_respond(interaction, "В этом сборе пока нет участников.")
            return
        view = MoveMemberView(self, gather_id, snapshot.participants)
        await self._safe_respond(
            interaction,
            view.menu_title,
            view=view,
            auto_delete=False,
        )

    async def move_participant(
        self,
        interaction: discord.Interaction,
        gather_id: int,
        user_id: int,
        bucket: Bucket,
        display_name: str,
    ) -> None:
        snapshot = await self.snapshot(gather_id)
        if snapshot is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if not await self.ensure_manager(interaction, snapshot.gather):
            return
        if bucket == "extra" and snapshot.gather.extra_slots <= 0:
            await self._safe_respond(interaction, "Доп. слоты для этого сбора отключены.")
            return

        current = next((entry for entry in snapshot.participants if entry.user_id == user_id), None)
        if current is None:
            await self._safe_respond(interaction, "Участник уже отсутствует в сборе.")
            return
        if current.bucket == bucket:
            await self._safe_respond(
                interaction,
                f"**{discord.utils.escape_markdown(display_name)}** уже находится в {BUCKET_LABELS[bucket]}.",
            )
            return

        counts = self._counts_excluding(snapshot.participants, user_id)
        if bucket == "main" and counts["main"] >= snapshot.gather.main_slots:
            await self._safe_respond(interaction, "В основе уже нет свободных мест.")
            return
        if bucket == "extra" and counts["extra"] >= snapshot.gather.extra_slots:
            await self._safe_respond(interaction, "В доп. слотах уже нет свободных мест.")
            return

        moved = await self.storage.update_bucket(gather_id, user_id, bucket)
        if not moved:
            await self._safe_respond(interaction, "Участник уже отсутствует в сборе.")
            return
        await self.refresh_gather(gather_id)
        if interaction.user is not None:
            await self.log_member_moved(
                snapshot.gather,
                user_id,
                current.bucket,
                bucket,
                actor=interaction.user,
            )
        await self._safe_respond(
            interaction,
            f"**{discord.utils.escape_markdown(display_name)}** перемещён в {BUCKET_LABELS[bucket]}.",
            auto_delete=False,
        )

    async def close_gather_from_panel(
        self,
        interaction: discord.Interaction,
        gather_id: int,
        *,
        tag_users: bool,
    ) -> None:
        snapshot = await self.snapshot(gather_id)
        if snapshot is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if not await self.ensure_manager(interaction, snapshot.gather):
            return
        if snapshot.gather.is_closed:
            await self._safe_respond(interaction, "Этот сбор уже закрыт.")
            return

        await self.storage.close_gather(gather_id)
        await self.refresh_gather(gather_id)
        await self.post_closure_message(snapshot, tag_users=tag_users)
        await self.archive_thread(snapshot.gather.thread_id)
        await self._safe_respond(
            interaction,
            "Сбор закрыт с тэгом участников." if tag_users else "Сбор закрыт без тэгов.",
            auto_delete=False,
        )

    async def post_closure_message(self, snapshot: GatherSnapshot, *, tag_users: bool) -> None:
        target = await self.resolve_post_target(snapshot.gather)
        if target is None:
            return

        header = f"Сбор **{discord.utils.escape_markdown(snapshot.gather.title)}** закрыт."
        try:
            await target.send(header, allowed_mentions=discord.AllowedMentions.none())
            if tag_users and snapshot.participants:
                chunk = ""
                for mention in (f"<@{entry.user_id}>" for entry in snapshot.participants):
                    if len(chunk) + len(mention) + 1 > 1800:
                        await target.send(chunk, allowed_mentions=discord.AllowedMentions(users=True))
                        chunk = mention
                    else:
                        chunk = f"{chunk} {mention}".strip()
                if chunk:
                    await target.send(chunk, allowed_mentions=discord.AllowedMentions(users=True))
        except discord.HTTPException:
            logger.warning("Не удалось отправить сообщение о закрытии сбора %s", snapshot.gather.gather_id)

    async def resolve_post_target(
        self,
        gather: GatherRecord,
    ) -> discord.abc.Messageable | None:
        thread = await self.fetch_thread(gather.thread_id)
        if thread is not None:
            return thread
        channel = self.get_channel(gather.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(gather.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
        return None

    async def archive_thread(self, thread_id: int | None) -> None:
        thread = await self.fetch_thread(thread_id)
        if thread is None:
            return
        try:
            await thread.edit(archived=True, locked=False)
        except discord.HTTPException:
            pass

    async def open_voice_picker(self, interaction: discord.Interaction, gather_id: int) -> None:
        gather = await self.storage.get_gather(gather_id)
        if gather is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if not await self.ensure_manager(interaction, gather):
            return
        view = VoicePickerView(self, gather_id)
        await self._safe_respond(
            interaction,
            "Выберите существующий голосовой канал для сбора.",
            view=view,
            auto_delete=False,
        )

    async def post_voice_reference(self, gather: GatherRecord, channel: discord.abc.GuildChannel) -> None:
        target = await self.resolve_post_target(gather)
        if target is None:
            return
        try:
            await target.send(
                f"Голосовой канал для сбора **{discord.utils.escape_markdown(gather.title)}**: {channel.mention}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.warning("Не удалось отправить ссылку на voice для сбора %s", gather.gather_id)

    async def resolve_selected_voice_channel(
        self,
        gather: GatherRecord,
    ) -> discord.VoiceChannel | discord.StageChannel | None:
        if getattr(gather, "voice_channel_id", None) is None:
            return None
        channel = self.get_channel(gather.voice_channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(gather.voice_channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return channel
        return None

    def build_voice_link(self, guild_id: int, channel_id: int) -> str:
        return VOICE_URL_TEMPLATE.format(guild_id=guild_id, channel_id=channel_id)

    async def send_voice_link_dm(
        self,
        user_id: int,
        gather: GatherRecord,
        channel: discord.VoiceChannel | discord.StageChannel,
        moderator_name: str,
    ) -> bool:
        voice_link = self.build_voice_link(gather.guild_id, channel.id)
        event_ts = int(gather.event_at.astimezone(UTC).timestamp())
        dm_text = (
            f"🔊 Модератор **{discord.utils.escape_markdown(moderator_name)}** приглашает вас в войс для сбора.\n"
            f"Сбор: **{discord.utils.escape_markdown(gather.title)}**\n"
            f"Дата: <t:{event_ts}:F>\n"
            f"Войс: {channel.name}\n"
            f"Ссылка: {voice_link}"
        )
        try:
            user = self.get_user(user_id) or await self.fetch_user(user_id)
            await user.send(dm_text, allowed_mentions=discord.AllowedMentions.none())
            return True
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return False

    async def fetch_member_for_voice_action(
        self,
        guild: discord.Guild,
        user_id: int,
    ) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def add_checked_members_to_voice(
        self,
        interaction: discord.Interaction,
        gather_id: int,
    ) -> None:
        snapshot = await self.snapshot(gather_id)
        if snapshot is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if not await self.ensure_manager(interaction, snapshot.gather):
            return
        if interaction.guild is None:
            await self._safe_respond(interaction, "Эта кнопка работает только на сервере.", auto_delete=False)
            return
        if not snapshot.participants:
            await self._safe_respond(interaction, "В этом сборе пока нет участников.", auto_delete=False)
            return

        channel = await self.resolve_selected_voice_channel(snapshot.gather)
        if channel is None:
            await self._safe_respond(
                interaction,
                "Сначала выберите голосовой канал через кнопку **Выбрать войс**.",
                auto_delete=False,
            )
            return

        moved = 0
        dm_sent = 0
        dm_failed = 0
        moderator_name = interaction.user.display_name if isinstance(interaction.user, discord.Member) else interaction.user.name
        reason_actor = interaction.user.id if interaction.user is not None else "unknown"
        move_reason = f"Сбор {snapshot.gather.title}: перенос участников в voice модератором {reason_actor}"

        for entry in snapshot.participants:
            moved_successfully = False
            if entry.checked_in:
                member = await self.fetch_member_for_voice_action(interaction.guild, entry.user_id)
                if member is not None:
                    try:
                        await member.move_to(channel, reason=move_reason)
                        moved += 1
                        moved_successfully = True
                    except (discord.Forbidden, discord.HTTPException):
                        moved_successfully = False

            if moved_successfully:
                continue

            if await self.send_voice_link_dm(entry.user_id, snapshot.gather, channel, moderator_name):
                dm_sent += 1
            else:
                dm_failed += 1

        await self._safe_respond(
            interaction,
            (
                f"Готово. Войс: {channel.mention}\n"
                f"Автоматически перемещено: **{moved}**\n"
                f"Отправлено приглашений в ЛС: **{dm_sent}**\n"
                f"Не удалось отправить в ЛС: **{dm_failed}**"
            ),
            auto_delete=False,
        )

    async def send_reminders(self, interaction: discord.Interaction, gather_id: int) -> None:
        snapshot = await self.snapshot(gather_id)
        if snapshot is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if not await self.ensure_manager(interaction, snapshot.gather):
            return
        if not snapshot.participants:
            await self._safe_respond(interaction, "Некому отправлять напоминание — участников пока нет.")
            return

        message_url = MESSAGE_URL_TEMPLATE.format(
            guild_id=snapshot.gather.guild_id,
            channel_id=snapshot.gather.channel_id,
            message_id=snapshot.gather.message_id,
        )
        event_ts = int(snapshot.gather.event_at.astimezone(UTC).timestamp())
        dm_text = (
            "❗ Напоминание о Сборе ❗\n"
            f"Дата: <t:{event_ts}:F>\n"
            f"Название: {snapshot.gather.title}\n"
            f"Ссылка: {message_url}"
        )

        sent = 0
        failed = 0
        for entry in snapshot.participants:
            try:
                user = self.get_user(entry.user_id) or await self.fetch_user(entry.user_id)
                await user.send(dm_text, allowed_mentions=discord.AllowedMentions.none())
                sent += 1
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                failed += 1

        await self._safe_respond(
            interaction,
            f"Напоминание отправлено: **{sent}**. Не удалось доставить: **{failed}**.",
            auto_delete=False,
        )

    async def open_add_moderator(self, interaction: discord.Interaction, gather_id: int) -> None:
        gather = await self.storage.get_gather(gather_id)
        if gather is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if not await self.ensure_creator(interaction, gather):
            return
        view = AddModeratorView(self, gather_id)
        await self._safe_respond(
            interaction,
            "Выберите нового модератора сбора.",
            view=view,
            auto_delete=False,
        )

    async def open_remove_moderator(self, interaction: discord.Interaction, gather_id: int) -> None:
        gather = await self.storage.get_gather(gather_id)
        if gather is None:
            await self._safe_respond(interaction, "Сбор не найден.")
            return
        if not await self.ensure_creator(interaction, gather):
            return
        moderators = await self.storage.list_moderators(gather_id)
        if not moderators:
            await self._safe_respond(interaction, "У этого сбора нет добавленных модераторов.")
            return

        guild = interaction.guild
        options: list[discord.SelectOption] = []
        for record in moderators[:25]:
            member = guild.get_member(record.user_id) if guild is not None else None
            label = member.display_name if member is not None else f"ID {record.user_id}"
            options.append(
                discord.SelectOption(label=_truncate(label, 100), value=str(record.user_id))
            )
        view = RemoveModeratorView(self, gather_id, options)
        await self._safe_respond(
            interaction,
            "Выберите модератора для удаления.",
            view=view,
            auto_delete=False,
        )

    async def add_user_to_thread(self, gather: GatherRecord, member: discord.Member) -> None:
        thread = await self.fetch_thread(gather.thread_id)
        if thread is None:
            return
        try:
            await thread.add_user(member)
        except discord.HTTPException:
            pass

    async def _safe_respond(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        view: discord.ui.View | None = None,
        auto_delete: bool = True,
    ) -> None:
        if interaction.response.is_done():
            if view is None:
                message = await interaction.followup.send(
                    content,
                    ephemeral=True,
                    wait=auto_delete,
                )
            else:
                message = await interaction.followup.send(
                    content,
                    view=view,
                    ephemeral=True,
                    wait=auto_delete,
                )
            if auto_delete and message is not None:
                asyncio.create_task(self._delete_followup_message_later(message))
            return

        if view is None:
            await interaction.response.send_message(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, view=view, ephemeral=True)
        if auto_delete:
            asyncio.create_task(self._delete_original_response_later(interaction))

    async def _delete_original_response_later(
        self,
        interaction: discord.Interaction,
        delay: float = EPHEMERAL_DELETE_DELAY_SECONDS,
    ) -> None:
        await asyncio.sleep(delay)
        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def _delete_followup_message_later(
        self,
        message: discord.WebhookMessage,
        delay: float = EPHEMERAL_DELETE_DELAY_SECONDS,
    ) -> None:
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    def _counts_excluding(
        self,
        participants: Iterable[ParticipantRecord],
        exclude_user_id: int,
    ) -> dict[Bucket, int]:
        counts: dict[Bucket, int] = {"main": 0, "extra": 0, "reserve": 0}
        for entry in participants:
            if entry.user_id == exclude_user_id:
                continue
            counts[entry.bucket] += 1
        return counts

    def _choose_bucket(
        self,
        gather: GatherRecord,
        preferred: Bucket,
        counts: dict[Bucket, int],
    ) -> Bucket | None:
        if preferred == "main":
            if counts["main"] < gather.main_slots:
                return "main"
            if gather.extra_slots > 0 and counts["extra"] < gather.extra_slots:
                return "extra"
            return "reserve"
        if gather.extra_slots <= 0:
            return None
        if counts["extra"] < gather.extra_slots:
            return "extra"
        return "reserve"



def _display_name(user: discord.abc.User) -> str:
    if isinstance(user, discord.Member):
        return user.display_name
    return getattr(user, "global_name", None) or user.name



def _is_admin(user: discord.abc.User) -> bool:
    return isinstance(user, discord.Member) and user.guild_permissions.administrator



def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"
