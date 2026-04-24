import os
import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands

log = logging.getLogger("winner_bot")

# ✅ переменные
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
WINNERS_CHANNEL_ID = os.getenv("WINNERS_CHANNEL_ID")
ARCHIVE_CHANNEL_ID = os.getenv("ARCHIVE_CHANNEL_ID")

if not TOKEN:
    raise Exception("No DISCORD_BOT_TOKEN")

if not (GUILD_ID and WINNERS_CHANNEL_ID and ARCHIVE_CHANNEL_ID):
    raise Exception("Missing IDs")

# 👉 преобразуем в int после проверки
GUILD_ID = int(GUILD_ID)
WINNERS_CHANNEL_ID = int(WINNERS_CHANNEL_ID)
ARCHIVE_CHANNEL_ID = int(ARCHIVE_CHANNEL_ID)

print("Bot starting...")

MODAL_ID = "winner_application_modal"
OPEN_MODAL_BUTTON_ID = "open_winner_application"

PENDING_TIMEOUT_SECONDS = 120


class WinnerModal(discord.ui.Modal, title="Заявка на победителя"):
    winner_name = discord.ui.TextInput(label="Имя победителя", style=discord.TextStyle.short, required=True, max_length=100)
    winner_static = discord.ui.TextInput(label="Статик", style=discord.TextStyle.short, required=True, max_length=50)
    winner_event = discord.ui.TextInput(label="Тип мероприятия", style=discord.TextStyle.short, required=True, max_length=200)
    winner_payout = discord.ui.TextInput(label="Выплата", style=discord.TextStyle.short, required=True, max_length=100)

    def __init__(self, bot: "WinnerBot"):
        super().__init__(custom_id=MODAL_ID)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("❌ Только на сервере.", ephemeral=True)
            return

        name = str(self.winner_name).strip()
        static = str(self.winner_static).strip()
        event = str(self.winner_event).strip()
        payout = str(self.winner_payout).strip()

        await interaction.response.send_message(
            content=(
                f"<@{interaction.user.id}>, заявка принята!\n"
                f"Теперь **отправь сюда скрин** (Ctrl+V) в течение 2 минут.\n\n"
                f"**Имя:** {name} • **Статик:** {static} • "
                f"**Мероприятие:** {event} • **Выплата:** {payout}"
            ),
            allowed_mentions=discord.AllowedMentions(users=[interaction.user]),
        )
        prompt_message = await interaction.original_response()

        self.bot.pending[interaction.user.id] = {
            "winner_name": name,
            "winner_static": static,
            "event_type": event,
            "payout": payout,
            "prompt_message_id": prompt_message.id,
            "channel_id": interaction.channel.id,
            "author_tag": str(interaction.user),
            "expires_at": asyncio.get_event_loop().time() + PENDING_TIMEOUT_SECONDS,
        }

        async def expire():
            await asyncio.sleep(PENDING_TIMEOUT_SECONDS + 1)
            still = self.bot.pending.get(interaction.user.id)
            if still and still["prompt_message_id"] == prompt_message.id:
                self.bot.pending.pop(interaction.user.id, None)
                try:
                    await prompt_message.edit(content=f"<@{interaction.user.id}> ⏱ Время вышло. Создай заявку заново.")
                except discord.HTTPException:
                    pass

        asyncio.create_task(expire())


class OpenModalView(discord.ui.View):
    def __init__(self, bot: "WinnerBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Подать заявку", style=discord.ButtonStyle.success, custom_id=OPEN_MODAL_BUTTON_ID)
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WinnerModal(self.bot))


class WinnerBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.guild_messages = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.pending: dict[int, dict] = {}

    async def setup_hook(self):
        self.add_view(OpenModalView(self))

        guild_obj = discord.Object(id=int(GUILD_ID))

        @self.tree.command(name="winner", description="Создать заявку на победителя мероприятия", guild=guild_obj)
        async def winner_cmd(interaction: discord.Interaction):
            await interaction.response.send_modal(WinnerModal(self))

        @self.tree.command(name="winner_panel", description="Опубликовать сообщение с кнопкой для подачи заявки", guild=guild_obj)
        @app_commands.default_permissions(manage_channels=True)
        async def winner_panel_cmd(interaction: discord.Interaction):
            await interaction.response.send_message(
                content=(
                    "Нажмите кнопку ниже, чтобы подать заявку на победителя мероприятия.\n\n"
                    "В заявке нужно указать **имя победителя**, **статик**, **тип мероприятия** "
                    "и **выплату**, после чего бот попросит отправить **скрин** "
                    "(можно вставить через Ctrl+V).\n\n"
                    "Сервер: Event Laff DM"
                ),
                view=OpenModalView(self),
            )

        @self.tree.command(name="winner_remove", description="Удалить победителя из обоих каналов по имени", guild=guild_obj)
        @app_commands.describe(имя="Имя победителя")
        @app_commands.default_permissions(manage_messages=True)
        async def winner_remove_cmd(interaction: discord.Interaction, имя: str):
            if interaction.guild is None:
                await interaction.response.send_message("❌ Только на сервере.", ephemeral=True)
                return
            winners_ch = interaction.guild.get_channel(int(WINNERS_CHANNEL_ID))
            archive_ch = interaction.guild.get_channel(int(ARCHIVE_CHANNEL_ID))
            if winners_ch is None or archive_ch is None:
                await interaction.response.send_message("❌ Каналы не найдены.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            rw = await self._delete_winner_in_channel(winners_ch, имя)
            ra = await self._delete_winner_in_channel(archive_ch, имя)
            if rw == 0 and ra == 0:
                await interaction.followup.send(f"❌ **{имя}** не найден в последних 100 сообщениях.", ephemeral=True)
            else:
                await interaction.followup.send(
                    f"✅ Удалено записей о **{имя}**: {rw} в <#{WINNERS_CHANNEL_ID}>, {ra} в <#{ARCHIVE_CHANNEL_ID}>.",
                    ephemeral=True,
                )

        await self.tree.sync(guild=guild_obj)

    async def _delete_winner_in_channel(self, channel: discord.TextChannel, name: str) -> int:
        deleted = 0
        target = name.lower()
        async for msg in channel.history(limit=100):
            if msg.author.id != self.user.id or not msg.embeds:
                continue
            embed = msg.embeds[0]
            for field in embed.fields:
                if "Имя победителя" in field.name:
                    cleaned = field.value.replace("*", "").strip().lower()
                    if cleaned == target:
                        try:
                            await msg.delete()
                            deleted += 1
                        except discord.HTTPException:
                            pass
                        break
        return deleted

    async def on_ready(self):
        log.info("Winner bot logged in as %s", self.user)

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        data = self.pending.get(message.author.id)
        if not data or message.channel.id != data["channel_id"]:
            return
        if asyncio.get_event_loop().time() > data["expires_at"]:
            self.pending.pop(message.author.id, None)
            return
        attachment = next((a for a in message.attachments if a.content_type and a.content_type.startswith("image/")), None)
        if attachment is None:
            return
        self.pending.pop(message.author.id, None)
        ok, reason = await self._publish_winner(message.guild, data, attachment.url)
        try:
            ch = message.guild.get_channel(data["channel_id"])
            if ch is not None:
                try:
                    prompt = await ch.fetch_message(data["prompt_message_id"])
                    await prompt.delete()
                except discord.HTTPException:
                    pass
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        except Exception:
            pass
        if not ok:
            try:
                await message.channel.send(
                    content=f"<@{message.author.id}> ❌ {reason}",
                    allowed_mentions=discord.AllowedMentions(users=[message.author]),
                )
            except discord.HTTPException:
                pass

    async def _publish_winner(self, guild: discord.Guild, data: dict, image_url: str) -> tuple[bool, str]:
        winners_ch = guild.get_channel(int(WINNERS_CHANNEL_ID))
        archive_ch = guild.get_channel(int(ARCHIVE_CHANNEL_ID))
        if winners_ch is None or archive_ch is None:
            return False, "Не найдены каналы."

        announcement = (
            "@everyone @here\n"
            "# 🏆 ПОБЕДИТЕЛЬ МЕРОПРИЯТИЯ\n"
            f"## В мероприятии **«{data['event_type']}»** победил игрок "
            f"**{data['winner_name']}** `{data['winner_static']}`\n"
            f"💰 Выплата: **{data['payout']}**\n"
            "_Поздравляем победителя!_ 🎉"
        )

        winner_embed = discord.Embed(
            title="🏆 Победитель мероприятия",
            description=(
                f"## В мероприятии **{data['event_type']}** победил игрок\n"
                f"# 🥇 {data['winner_name']}\n"
                f"### Статик: `{data['winner_static']}`"
            ),
            color=0xFFD700,
            timestamp=datetime.now(timezone.utc),
        )
        winner_embed.add_field(name="👤 Имя победителя", value=f"**{data['winner_name']}**", inline=True)
        winner_embed.add_field(name="🆔 Статик", value=f"**{data['winner_static']}**", inline=True)
        winner_embed.add_field(name="🎮 Тип мероприятия", value=f"**{data['event_type']}**", inline=False)
        winner_embed.add_field(name="💰 Выплата", value=f"**{data['payout']}**", inline=False)
        winner_embed.set_image(url=image_url)
        winner_embed.set_footer(text=f"Заявку отправил: {data['author_tag']}")

        archive_embed = winner_embed.copy()
        archive_embed.title = "📦 Архив: победитель мероприятия"
        archive_embed.colour = discord.Colour(0x99AAB5)

        try:
            await winners_ch.send(
                content=announcement,
                embed=winner_embed,
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )
            await archive_ch.send(embed=archive_embed)
            return True, ""
        except discord.HTTPException as exc:
            log.exception("publish failed: %s", exc)
            return False, "Ошибка публикации, проверь права бота."


async def run_winner_bot():
    if not (TOKEN and GUILD_ID and WINNERS_CHANNEL_ID and ARCHIVE_CHANNEL_ID):
        log.warning("Winner bot env vars missing (DISCORD_BOT_TOKEN/DISCORD_GUILD_ID/WINNERS_CHANNEL_ID/ARCHIVE_CHANNEL_ID); not starting.")
        return
    bot = WinnerBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_winner_bot())
