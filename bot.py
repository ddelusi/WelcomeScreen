import os
import io
import json
import threading
import time
import asyncio
import random
import re
from datetime import datetime, timedelta
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask
import requests

# --- Keep-Alive Web Server Setup for Render Free Tier ---
flask_app = Flask('')


@flask_app.route('/')
def home():
    return "Bot is online and active!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)


def keep_alive_ping():
    time.sleep(20)
    while True:
        try:
            requests.get("https://welcomescreen-4ulq.onrender.com", timeout=10)
        except Exception:
            pass
        time.sleep(600)  # Pings every 10 minutes


# Run web server in background threads
threading.Thread(target=run_flask, daemon=True).start()
threading.Thread(target=keep_alive_ping, daemon=True).start()

# --- Discord Bot Setup ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
DM_LOG_CHANNEL_ID = 1547782664642371685  # Target channel for bot DM logs

intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # Required for on_message and on_message_delete

bot = commands.Bot(command_prefix="!", intents=intents)
bot.synced = False

# Custom Emojis
SUCCESSFUL_SPIN = "<a:SuccessfulSpin:1547127487824142346>"
UNSUCCESSFUL_SPIN = "<a:UnsuccessfulSpin:1547127600693116999>"

# Seconds to wait between users in bulk actions (keeps DMs from looking like spam)
BULK_DM_DELAY = 2.0

# Server-Specific Storage
server_configs = {}
user_damage = {}  # (guild_id, user_id) -> total damage points
user_warnings = {}  # (guild_id, user_id) -> list of warning dicts
active_giveaways = {}  # message_id -> giveaway data dict
seen_dm_users = set()  # Tracks users who received the one-time DM notice

# --- Persistent JSON Storage for Damage & Warnings ---
DATA_FILE = "damage_data.json"
PRESET_FILE = "embed_presets.json"


def save_data():
    data = {
        "damage": {f"{g}_{u}": v for (g, u), v in user_damage.items()},
        "warnings": {f"{g}_{u}": v for (g, u), v in user_warnings.items()}
    }
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


def load_data():
    global user_damage, user_warnings
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                user_damage = {tuple(map(int, k.split("_"))): v for k, v in data.get("damage", {}).items()}
                user_warnings = {tuple(map(int, k.split("_"))): v for k, v in data.get("warnings", {}).items()}
        except Exception as e:
            print(f"Error loading damage data: {e}")


load_data()


def save_preset(name, data):
    presets = {}
    if os.path.exists(PRESET_FILE):
        try:
            with open(PRESET_FILE, "r") as f:
                presets = json.load(f)
        except json.JSONDecodeError:
            presets = {}
    presets[name] = data
    with open(PRESET_FILE, "w") as f:
        json.dump(presets, f, indent=4)


def parse_user_ids(raw_input: str) -> list[int]:
    """Extracts unique numeric User IDs from a string of mentions/IDs."""
    cleaned = raw_input.replace(",", " ").replace("<@", "").replace(">", "").replace("!", "")
    ids = []
    for item in cleaned.split():
        if item.isdigit():
            ids.append(int(item))
    return list(set(ids))


def format_user_list(items, sep="\n", prefix="• ", limit=1024):
    """Makes a list that always fits inside a Discord embed field (max 1024 chars)."""
    if not items:
        return f"{prefix}None"
    out = []
    used = 0
    for i, item in enumerate(items):
        piece = f"{prefix}{item}"
        if used + len(piece) + len(sep) > limit - 40:
            out.append(f"...and {len(items) - i} more")
            break
        out.append(piece)
        used += len(piece) + len(sep)
    return sep.join(out)


def get_server_config(guild_id: int) -> dict:
    if guild_id not in server_configs:
        server_configs[guild_id] = {
            "log_channel": int(os.getenv('LOG_CHANNEL_ID', '0')) or None,
            "msg_log_channel": None,
            "media_log_channel": None
        }
    return server_configs[guild_id]


async def log_action(guild: discord.Guild, title: str, description: str, color: discord.Color):
    config = get_server_config(guild.id)
    log_chan_id = config.get("log_channel")
    if log_chan_id:
        channel = guild.get_channel(log_chan_id)
        if channel:
            embed = discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass


async def send_user_dm(user: discord.User, embed: discord.Embed):
    try:
        await user.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


@bot.event
async def on_ready():
    if not bot.synced:
        await bot.tree.sync()
        bot.synced = True
        print(f"Logged in as {bot.user} (ID: {bot.user.id}) - Globally synced commands across all servers")
    else:
        print(f"Reconnected as {bot.user} (ID: {bot.user.id})")


# --- Global Slash Command Error Handler (Prevents "The application did not respond") ---

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        missing = ", ".join(error.missing_permissions)
        msg = f"{UNSUCCESSFUL_SPIN} You don't have permission to use this command (Missing: `{missing}`)."
    elif isinstance(error, app_commands.BotMissingPermissions):
        missing = ", ".join(error.missing_permissions)
        msg = f"{UNSUCCESSFUL_SPIN} I am missing permissions required to run this command: `{missing}`."
    elif isinstance(error, app_commands.NoPrivateMessage):
        msg = f"{UNSUCCESSFUL_SPIN} This command can only be used inside a server."
    else:
        msg = f"{UNSUCCESSFUL_SPIN} An error occurred while running this command: `{error}`"
        print(f"Command error in /{interaction.command.name if interaction.command else 'unknown'}: {error}")

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


# --- Helper Function for Automated Damage & Punishment Thresholds ---

async def apply_damage_and_punish(guild: discord.Guild, member: discord.Member, points: int, reason: str,
                                  moderator: discord.User):
    key = (guild.id, member.id)
    user_damage[key] = max(0, user_damage.get(key, 0) + points)
    current_damage = user_damage[key]
    save_data()

    punishment_text = "No additional punishment applied."

    if current_damage >= 25:
        try:
            dm_embed = discord.Embed(
                title=f"🔨 Banned from {guild.name}",
                description=f"You reached **{current_damage}/25** damage points and have been automatically banned.",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            dm_embed.add_field(name="Reason", value=reason, inline=False)
            dm_embed.set_footer(text=f"Moderator: {moderator.display_name}")
            await send_user_dm(member, dm_embed)

            await member.ban(reason=f"Reached {current_damage} damage points (Auto-ban). Last reason: {reason}")
            punishment_text = "🔨 **AUTOMATICALLY BANNED** (Reached 25+ damage points)"
            await log_action(guild, "Auto-Ban Executed",
                             f"**Selected User:** {member.mention} ({member.id})\n**Reason:** Accumulated {current_damage} damage points.",
                             discord.Color.red())
        except discord.Forbidden:
            punishment_text = "⚠️ Reached 25+ points, but I lack permissions to ban this member."

    else:
        if points < 0 and current_damage < 3:
            if member.timed_out_until:
                try:
                    await member.timeout(None, reason=f"Damage reduced to {current_damage} by {moderator.display_name}")
                    punishment_text = "🔊 **TIMEOUT REMOVED** (Damage reduced below 3 points)"

                    dm_embed = discord.Embed(
                        title=f"🔊 Timeout Removed in {guild.name}",
                        description=f"Your damage was reduced to **{current_damage}/25**. Your timeout has been lifted.",
                        color=discord.Color.green(),
                        timestamp=discord.utils.utcnow()
                    )
                    dm_embed.set_footer(text=f"Moderator: {moderator.display_name}")
                    await send_user_dm(member, dm_embed)
                except discord.Forbidden:
                    punishment_text = "⚠️ Damage lowered, but I lack permission to remove active timeout."
        else:
            mute_duration = None
            duration_str = ""

            if current_damage >= 14:
                mute_duration = timedelta(days=7)
                duration_str = "7 Days"
            elif current_damage >= 7:
                mute_duration = timedelta(days=7)
                duration_str = "7 Days"
            elif current_damage >= 3:
                mute_duration = timedelta(days=3)
                duration_str = "3 Days"

            if mute_duration and points > 0:
                try:
                    await member.timeout(mute_duration, reason=f"Accumulated {current_damage} damage points.")
                    punishment_text = f"🔇 **AUTOMATICALLY MUTED** for **{duration_str}** (Reached {current_damage} points)"

                    dm_embed = discord.Embed(
                        title=f"🔇 Muted in {guild.name}",
                        description=f"You reached **{current_damage}/25** damage points and were muted for **{duration_str}**.",
                        color=discord.Color.gold(),
                        timestamp=discord.utils.utcnow()
                    )
                    dm_embed.add_field(name="Reason", value=reason, inline=False)
                    dm_embed.set_footer(text=f"Moderator: {moderator.display_name}")
                    await send_user_dm(member, dm_embed)

                    await log_action(
                        guild,
                        "Auto-Mute Executed",
                        f"**Selected User:** {member.mention}\n**Mute Duration:** {duration_str}\n**Total Points:** {current_damage}/25",
                        discord.Color.gold()
                    )
                except discord.Forbidden:
                    punishment_text = f"⚠️ Reached {current_damage} points for a {duration_str} mute, but I lack permission to timeout this member."

    return current_damage, punishment_text


def create_damage_embed(action_type: str, member: discord.Member, points: int, current_damage: int, punishment: str,
                        reason: str, moderator: discord.User) -> discord.Embed:
    if action_type == "add":
        title = "⚡ Damage Applied"
        color = discord.Color.red() if current_damage >= 15 else discord.Color.orange()
        action_text = f"{SUCCESSFUL_SPIN} Added **{points}** damage point(s) to {member.mention}."
    else:
        title = "🛡️ Damage Removed"
        color = discord.Color.green()
        action_text = f"{SUCCESSFUL_SPIN} Removed **{points}** damage point(s) from {member.mention}."

    embed = discord.Embed(
        title=title,
        description=f"{action_text}\n\n**Total Damage:** `{current_damage}/25`\n**Status:** {punishment}",
        color=color,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Moderator: {moderator.display_name}", icon_url=moderator.display_avatar.url)
    return embed


# --- Reaction & Reply Commands ---

@bot.tree.context_menu(name="React as Bot")
async def react_as_bot(interaction: discord.Interaction, message: discord.Message):
    class ReactModal(discord.ui.Modal, title="Add Reaction"):
        emoji_input = discord.ui.TextInput(
            label="Emoji",
            placeholder="Paste standard emoji (👍) or custom format (<:name:id>)",
            required=True
        )

        async def on_submit(self, modal_interaction: discord.Interaction):
            await modal_interaction.response.defer(ephemeral=True)
            try:
                await message.add_reaction(self.emoji_input.value.strip())
                await modal_interaction.followup.send(f"{SUCCESSFUL_SPIN} Reaction added!", ephemeral=True)
            except discord.HTTPException:
                await modal_interaction.followup.send(
                    f"{UNSUCCESSFUL_SPIN} Invalid emoji or missing permissions.", ephemeral=True)

    await interaction.response.send_modal(ReactModal())


@bot.tree.command(name="react", description="Add an emoji reaction to a specific message using its ID or link")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(
    message_input="The Message ID or Message Link to react to",
    emoji="The emoji to add (e.g. 👍, :custom_emoji:, or raw emoji ID)"
)
async def react_slash(interaction: discord.Interaction, message_input: str, emoji: str):
    await interaction.response.defer(ephemeral=True)

    if "/" in message_input:
        try:
            message_id = int(message_input.split("/")[-1])
        except ValueError:
            await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} Invalid Message Link provided.", ephemeral=True)
            return
    else:
        try:
            message_id = int(message_input)
        except ValueError:
            await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} Invalid Message ID or Link provided.", ephemeral=True)
            return

    try:
        target_message = await interaction.channel.fetch_message(message_id)
        await target_message.add_reaction(emoji.strip())
        await interaction.followup.send(f"{SUCCESSFUL_SPIN} Added reaction {emoji} to message!", ephemeral=True)
    except discord.NotFound:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} Could not find a message with that ID in this channel.",
                                        ephemeral=True)
    except discord.HTTPException:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} Invalid emoji or lack permissions to react.",
                                        ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} An error occurred: {e}", ephemeral=True)


@bot.tree.context_menu(name="Reply as Bot")
async def reply_as_bot(interaction: discord.Interaction, message: discord.Message):
    class ReplyModal(discord.ui.Modal, title="Send Reply as Bot"):
        reply_text = discord.ui.TextInput(
            label="Your Message",
            style=discord.TextStyle.paragraph,
            placeholder="Type what the bot should say...",
            required=True
        )

        async def on_submit(self, modal_interaction: discord.Interaction):
            await modal_interaction.response.defer(ephemeral=True)
            try:
                await message.reply(self.reply_text.value)
                await modal_interaction.followup.send(f"{SUCCESSFUL_SPIN} Reply sent!", ephemeral=True)
            except discord.HTTPException:
                await modal_interaction.followup.send(
                    f"{UNSUCCESSFUL_SPIN} I don't have permission to reply in that channel.", ephemeral=True)

    await interaction.response.send_modal(ReplyModal())


@bot.tree.command(name="reply", description="Reply to a specific message using its ID or link")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(
    message_input="The Message ID or Message Link to reply to",
    response="The message you want the bot to send"
)
async def reply_slash(interaction: discord.Interaction, message_input: str, response: str):
    await interaction.response.defer(ephemeral=True)

    if "/" in message_input:
        try:
            message_id = int(message_input.split("/")[-1])
        except ValueError:
            await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} Invalid Message Link provided.", ephemeral=True)
            return
    else:
        try:
            message_id = int(message_input)
        except ValueError:
            await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} Invalid Message ID or Link provided.", ephemeral=True)
            return

    try:
        target_message = await interaction.channel.fetch_message(message_id)
        await target_message.reply(response)
        await interaction.followup.send(f"{SUCCESSFUL_SPIN} Reply sent successfully!", ephemeral=True)
    except discord.NotFound:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} Could not find a message with that ID in this channel.",
                                        ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} I don't have permission to reply in this channel.",
                                        ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} An error occurred: {e}", ephemeral=True)


# --- Event Listeners ---

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
        if message.author.id not in seen_dm_users:
            seen_dm_users.add(message.author.id)
            notice_embed = discord.Embed(
                title="ℹ️ Notice",
                description="Please note that messages sent to this bot's Direct Messages are monitored by staff. Standard server rules still apply in DMs.",
                color=discord.Color.blue()
            )
            try:
                await message.author.send(embed=notice_embed)
            except discord.Forbidden:
                pass

        log_channel = bot.get_channel(DM_LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(
                title="📩 New Direct Message Received",
                description=message.content if message.content else "*[No text content]*",
                color=discord.Color.purple(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_author(
                name=f"{message.author.name} ({message.author.id})",
                icon_url=message.author.display_avatar.url
            )

            if message.attachments:
                attachment_urls = "\n".join([att.url for att in message.attachments])
                embed.add_field(name="Attachments", value=attachment_urls, inline=False)

            await log_channel.send(embed=embed)

    await bot.process_commands(message)


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    key = (guild.id, user.id)
    if key in user_damage or key in user_warnings:
        user_damage.pop(key, None)
        user_warnings.pop(key, None)
        save_data()
        await log_action(
            guild,
            "Damage Points Reset",
            f"**Selected User:** {user.mention} ({user.id})\n**Reason:** Automatically reset all damage points and warnings upon being unbanned.",
            discord.Color.blue()
        )


@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    config = get_server_config(message.guild.id)

    if config["msg_log_channel"] and message.content:
        log_chan = message.guild.get_channel(config["msg_log_channel"])
        if log_chan:
            embed = discord.Embed(
                title="Message Deleted",
                description=f"**Author:** {message.author.mention}\n**Channel:** {message.channel.mention}\n\n**Content:**\n{message.content}",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            await log_chan.send(embed=embed)

    if config["media_log_channel"]:
        media_chan = message.guild.get_channel(config["media_log_channel"])
        if media_chan:
            has_gif_link = any(
                domain in message.content for domain in ["tenor.com", "giphy.com"]) or message.content.endswith(
                ('.png', '.jpg', '.jpeg', '.gif'))

            if message.attachments:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith(('image/', 'video/')):
                        try:
                            file_bytes = await attachment.read()
                            file_to_send = discord.File(fp=io.BytesIO(file_bytes), filename=attachment.filename)

                            embed = discord.Embed(
                                title="Media Deleted",
                                description=f"**Author:** {message.author.mention}\n**Channel:** {message.channel.mention}\n**File Name:** `{attachment.filename}`",
                                color=discord.Color.gold(),
                                timestamp=discord.utils.utcnow()
                            )
                            await media_chan.send(embed=embed, file=file_to_send)
                        except Exception:
                            pass

            elif has_gif_link:
                embed = discord.Embed(
                    title="Media Link Deleted",
                    description=f"**Author:** {message.author.mention}\n**Channel:** {message.channel.mention}\n\n**Content/URL:**\n{message.content}",
                    color=discord.Color.gold(),
                    timestamp=discord.utils.utcnow()
                )
                await media_chan.send(embed=embed)


# --- Info Commands ---

@bot.tree.command(name="userinfo", description="Display detailed information about a member")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    roles = [role.mention for role in reversed(target.roles) if role != interaction.guild.default_role]
    roles_str = ", ".join(roles) if roles else "None"

    embed = discord.Embed(
        title=f"User Info — {target.display_name}",
        color=target.color if target.color != discord.Color.default() else discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Username", value=f"`{target.name}`", inline=True)
    embed.add_field(name="User ID", value=f"`{target.id}`", inline=True)
    embed.add_field(name="Account Created", value=f"<t:{int(target.created_at.timestamp())}:R>", inline=False)
    embed.add_field(name="Joined Server", value=f"<t:{int(target.joined_at.timestamp())}:R>", inline=False)
    embed.add_field(name=f"Roles ({len(roles)})", value=roles_str, inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="serverinfo", description="Display detailed information about this server")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    categories = len(guild.categories)
    bots = sum(1 for m in guild.members if m.bot)
    humans = guild.member_count - bots

    embed = discord.Embed(
        title=f"Server Info — {guild.name}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(name="Owner", value=f"{guild.owner.mention} (`{guild.owner.id}`)", inline=False)
    embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="Created On", value=f"<t:{int(guild.created_at.timestamp())}:R>", inline=True)
    embed.add_field(
        name="Members",
        value=f"Total: **{guild.member_count}**\nHumans: **{humans}**\nBots: **{bots}**",
        inline=True
    )
    embed.add_field(
        name="Channels",
        value=f"Text: **{text_channels}**\nVoice: **{voice_channels}**\nCategories: **{categories}**",
        inline=True
    )
    embed.add_field(name="Roles", value=f"**{len(guild.roles)}** roles", inline=True)
    embed.add_field(name="Boost Level",
                    value=f"Tier **{guild.premium_tier}** ({guild.premium_subscription_count} boosts)", inline=True)

    await interaction.response.send_message(embed=embed)


# --- Purge Suite ---

@bot.tree.command(name="purge", description="Bulk delete messages in the current channel")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge(interaction: discord.Interaction, amount: int, user: discord.Member = None):
    if amount < 1 or amount > 100:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Please enter an amount between 1 and 100.",
                                                ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    def check(m):
        return m.author == user if user else True

    deleted = await interaction.channel.purge(limit=amount, check=check)
    await interaction.followup.send(f"{SUCCESSFUL_SPIN} Deleted **{len(deleted)}** message(s).", ephemeral=True)
    await log_action(
        interaction.guild,
        "Messages Purged",
        f"**Channel:** {interaction.channel.mention}\n**Count:** {len(deleted)}\n**Target Filter:** {user.mention if user else 'All'}\n**Moderator:** {interaction.user.mention}",
        discord.Color.red()
    )


@bot.tree.command(name="purge-human", description="Bulk delete messages sent only by humans")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge_human(interaction: discord.Interaction, amount: int):
    if amount < 1 or amount > 100:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Please enter an amount between 1 and 100.",
                                                ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount, check=lambda m: not m.author.bot)
    await interaction.followup.send(f"{SUCCESSFUL_SPIN} Deleted **{len(deleted)}** human message(s).", ephemeral=True)
    await log_action(
        interaction.guild,
        "Human Messages Purged",
        f"**Channel:** {interaction.channel.mention}\n**Count:** {len(deleted)}\n**Moderator:** {interaction.user.mention}",
        discord.Color.red()
    )


@bot.tree.command(name="purge-bot", description="Bulk delete messages sent only by bots")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge_bot(interaction: discord.Interaction, amount: int):
    if amount < 1 or amount > 100:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Please enter an amount between 1 and 100.",
                                                ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount, check=lambda m: m.author.bot)
    await interaction.followup.send(f"{SUCCESSFUL_SPIN} Deleted **{len(deleted)}** bot message(s).", ephemeral=True)
    await log_action(
        interaction.guild,
        "Bot Messages Purged",
        f"**Channel:** {interaction.channel.mention}\n**Count:** {len(deleted)}\n**Moderator:** {interaction.user.mention}",
        discord.Color.red()
    )


# --- Helper Duration Parser ---

def parse_duration(duration_str: str) -> int:
    units = {
        's': 1, 'sec': 1, 'second': 1, 'seconds': 1,
        'm': 60, 'min': 60, 'minute': 60, 'minutes': 60,
        'h': 3600, 'hr': 3600, 'hour': 3600, 'hours': 3600,
        'd': 86400, 'day': 86400, 'days': 86400
    }
    match = re.match(r"^(\d+)\s*([a-zA-Z]+)$", duration_str.strip())
    if not match:
        return None
    amount, unit = match.groups()
    unit = unit.lower()
    return int(amount) * units.get(unit, 0) if unit in units else None


# --- Bulk Moderation Commands ---

class BulkConfirmView(discord.ui.View):
    def __init__(self, action_type: str, user_ids: list[int], duration: str = None, reason: str = "No reason provided",
                 author_id: int = None):
        super().__init__(timeout=300)
        self.action_type = action_type.lower()
        self.user_ids = user_ids
        self.duration = duration
        self.reason = reason
        self.author_id = author_id

        # Red primary action button
        button_label = f"{action_type.capitalize()} All"
        self.confirm_button = discord.ui.Button(label=button_label, style=discord.ButtonStyle.red)
        self.confirm_button.callback = self.confirm_callback
        self.add_item(self.confirm_button)

        # Grey secondary cancel button
        self.cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        self.cancel_button.callback = self.cancel_callback
        self.add_item(self.cancel_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.author_id and interaction.user.id != self.author_id:
            await interaction.response.send_message(
                f"{UNSUCCESSFUL_SPIN} Only the moderator who ran this command can interact with these buttons.",
                ephemeral=True)
            return False
        return True

    async def confirm_callback(self, interaction: discord.Interaction):
        self.stop()
        for item in self.children:
            item.disabled = True

        processing_embed = discord.Embed(
            title=f"⏳ Processing Bulk {self.action_type.capitalize()}...",
            description=f"Executing bulk action on **{len(self.user_ids)}** user(s). Please wait...",
            color=discord.Color.from_rgb(255, 255, 255),
            timestamp=discord.utils.utcnow()
        )
        await interaction.response.edit_message(embed=processing_embed, view=self)

        successful, failed = [], []

        if self.action_type == "ban":
            for i, uid in enumerate(self.user_ids):
                if i:
                    await asyncio.sleep(BULK_DM_DELAY)
                try:
                    user_obj = interaction.guild.get_member(uid)
                    if user_obj is None:
                        raise LookupError("Not in server, skip DM")
                    dm_embed = discord.Embed(
                        title=f"🔨 Banned from {interaction.guild.name}",
                        description="You have been bulk banned by a moderator.",
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow()
                    )
                    dm_embed.add_field(name="Reason", value=self.reason, inline=False)
                    dm_embed.set_footer(text=f"Moderator: {interaction.user.display_name}")
                    await send_user_dm(user_obj, dm_embed)
                except Exception:
                    pass

                try:
                    await interaction.guild.ban(discord.Object(id=uid),
                                                reason=f"{self.reason} | Executed by {interaction.user}")
                    successful.append(f"<@{uid}>")
                except Exception:
                    failed.append(str(uid))

        elif self.action_type == "kick":
            for i, uid in enumerate(self.user_ids):
                if i:
                    await asyncio.sleep(BULK_DM_DELAY)
                try:
                    member = await interaction.guild.fetch_member(uid)
                    dm_embed = discord.Embed(
                        title=f"👢 Kicked from {interaction.guild.name}",
                        description="You have been bulk kicked by a moderator.",
                        color=discord.Color.orange(),
                        timestamp=discord.utils.utcnow()
                    )
                    dm_embed.add_field(name="Reason", value=self.reason, inline=False)
                    dm_embed.set_footer(text=f"Moderator: {interaction.user.display_name}")
                    await send_user_dm(member, dm_embed)

                    await member.kick(reason=f"{self.reason} | Executed by {interaction.user}")
                    successful.append(member.mention)
                except Exception:
                    failed.append(str(uid))

        elif self.action_type == "mute":
            seconds = parse_duration(self.duration)
            timeout_delta = timedelta(seconds=seconds)
            for i, uid in enumerate(self.user_ids):
                if i:
                    await asyncio.sleep(BULK_DM_DELAY)
                try:
                    member = await interaction.guild.fetch_member(uid)
                    dm_embed = discord.Embed(
                        title=f"🔇 Muted in {interaction.guild.name}",
                        description=f"You have been bulk muted for **{self.duration}**.",
                        color=discord.Color.gold(),
                        timestamp=discord.utils.utcnow()
                    )
                    dm_embed.add_field(name="Reason", value=self.reason, inline=False)
                    dm_embed.set_footer(text=f"Moderator: {interaction.user.display_name}")
                    await send_user_dm(member, dm_embed)

                    await member.timeout(timeout_delta, reason=f"{self.reason} | Executed by {interaction.user}")
                    successful.append(member.mention)
                except Exception:
                    failed.append(str(uid))

        elif self.action_type == "warn":
            for i, uid in enumerate(self.user_ids):
                if i:
                    await asyncio.sleep(BULK_DM_DELAY)
                try:
                    member = await interaction.guild.fetch_member(uid)
                    dm_embed = discord.Embed(
                        title=f"⚠️ Warning Received in {interaction.guild.name}",
                        description="You have received a bulk warning.",
                        color=discord.Color.orange(),
                        timestamp=discord.utils.utcnow()
                    )
                    dm_embed.add_field(name="Reason", value=self.reason, inline=False)
                    dm_embed.set_footer(text=f"Moderator: {interaction.user.display_name}")
                    await send_user_dm(member, dm_embed)

                    successful.append(member.mention)
                except Exception:
                    failed.append(str(uid))

        result_embed = discord.Embed(
            title=f"Bulk {self.action_type.capitalize()} Executed — {interaction.guild.name}",
            color=discord.Color.from_rgb(255, 255, 255),
            timestamp=discord.utils.utcnow()
        )
        if interaction.guild.icon:
            result_embed.set_thumbnail(url=interaction.guild.icon.url)

        user_list_str = format_user_list(successful)

        result_embed.add_field(name=f"Selected Users ({len(successful)}):", value=user_list_str, inline=False)
        if self.duration:
            result_embed.add_field(name="Duration", value=f"` {self.duration} `", inline=True)
        result_embed.add_field(name="Reason", value=f"` {self.reason} `", inline=False)

        if failed:
            result_embed.add_field(name="Failed IDs", value=format_user_list(failed, sep=", ", prefix=""),
                                   inline=False)

        result_embed.set_footer(text="Action completed.")
        await interaction.edit_original_response(embed=result_embed, view=self)

        public_embed = discord.Embed(
            title=f"✅ Bulk {self.action_type.capitalize()} Completed",
            description=f"Successfully executed **bulk {self.action_type}** on **{len(successful)}** user(s).",
            color=discord.Color.from_rgb(255, 255, 255),
            timestamp=discord.utils.utcnow()
        )

        public_user_list = format_user_list(successful, sep=", ", prefix="")

        public_embed.add_field(name="Targeted Users", value=public_user_list, inline=False)
        public_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        if self.duration:
            public_embed.add_field(name="Duration", value=f"` {self.duration} `", inline=True)
        public_embed.add_field(name="Reason", value=f"` {self.reason} `", inline=False)

        await interaction.channel.send(embed=public_embed, silent=False)

    async def cancel_callback(self, interaction: discord.Interaction):
        self.stop()
        for item in self.children:
            item.disabled = True

        cancel_embed = discord.Embed(
            title=f"Bulk {self.action_type.capitalize()} Cancelled — {interaction.guild.name}",
            description="❌ No actions were performed.",
            color=discord.Color.from_rgb(255, 255, 255),
            timestamp=discord.utils.utcnow()
        )
        if interaction.guild.icon:
            cancel_embed.set_thumbnail(url=interaction.guild.icon.url)

        cancel_embed.set_footer(text="Action cancelled by moderator.")
        await interaction.response.edit_message(embed=cancel_embed, view=self)


@bot.tree.command(name="bulkban", description="Ban multiple users at once using IDs or mentions.")
@app_commands.checks.has_permissions(ban_members=True)
async def bulkban(interaction: discord.Interaction, users: str, reason: str = "No reason provided"):
    user_ids = parse_user_ids(users)
    if not user_ids:
        await interaction.response.send_message("No valid user IDs or mentions found.", ephemeral=True)
        return

    user_list_str = format_user_list([f"<@{uid}>" for uid in user_ids])

    embed = discord.Embed(
        title=f"Bulk Ban Confirmation — {interaction.guild.name}",
        color=discord.Color.from_rgb(255, 255, 255),
        timestamp=discord.utils.utcnow()
    )
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)

    embed.add_field(name=f"Selected Users ({len(user_ids)}):", value=user_list_str, inline=False)
    embed.add_field(name="Reason", value=f"` {reason} `", inline=False)
    embed.set_footer(text="Ensure bot role is positioned higher than target members.")

    view = BulkConfirmView("Ban", user_ids, reason=reason, author_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="bulkkick", description="Kick multiple members at once.")
@app_commands.checks.has_permissions(kick_members=True)
async def bulkkick(interaction: discord.Interaction, users: str, reason: str = "No reason provided"):
    user_ids = parse_user_ids(users)
    if not user_ids:
        await interaction.response.send_message("No valid user IDs or mentions found.", ephemeral=True)
        return

    user_list_str = format_user_list([f"<@{uid}>" for uid in user_ids])

    embed = discord.Embed(
        title=f"Bulk Kick Confirmation — {interaction.guild.name}",
        color=discord.Color.from_rgb(255, 255, 255),
        timestamp=discord.utils.utcnow()
    )
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)

    embed.add_field(name=f"Selected Users ({len(user_ids)}):", value=user_list_str, inline=False)
    embed.add_field(name="Reason", value=f"` {reason} `", inline=False)
    embed.set_footer(text="Ensure bot role is positioned higher than target members.")

    view = BulkConfirmView("Kick", user_ids, reason=reason, author_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="bulkmute", description="Timeout multiple members for a set duration (e.g., 1m, 1h, 1d).")
@app_commands.checks.has_permissions(moderate_members=True)
async def bulkmute(interaction: discord.Interaction, users: str, duration: str, reason: str = "No reason provided"):
    user_ids = parse_user_ids(users)
    if not user_ids:
        await interaction.response.send_message("No valid user IDs or mentions found.", ephemeral=True)
        return

    seconds = parse_duration(duration)
    if not seconds or seconds <= 0:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} Invalid duration format! Use e.g. `1m`, `1h`, or `1d`.", ephemeral=True
        )
        return

    if seconds > 28 * 86400:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} Discord timeouts cannot exceed 28 days.", ephemeral=True
        )
        return

    user_list_str = format_user_list([f"<@{uid}>" for uid in user_ids])

    embed = discord.Embed(
        title=f"Bulk Mute Confirmation — {interaction.guild.name}",
        color=discord.Color.from_rgb(255, 255, 255),
        timestamp=discord.utils.utcnow()
    )
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)

    embed.add_field(name=f"Selected Users ({len(user_ids)}):", value=user_list_str, inline=False)
    embed.add_field(name="Duration", value=f"` {duration} `", inline=True)
    embed.add_field(name="Reason", value=f"` {reason} `", inline=False)
    embed.set_footer(text="Ensure bot role is positioned higher than target members.")

    view = BulkConfirmView("Mute", user_ids, duration=duration, reason=reason, author_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="bulkwarn", description="Send a warning message to multiple members.")
@app_commands.checks.has_permissions(manage_messages=True)
async def bulkwarn(interaction: discord.Interaction, users: str, reason: str):
    user_ids = parse_user_ids(users)
    if not user_ids:
        await interaction.response.send_message("No valid user IDs or mentions found.", ephemeral=True)
        return

    user_list_str = format_user_list([f"<@{uid}>" for uid in user_ids])

    embed = discord.Embed(
        title=f"Bulk Warn Confirmation — {interaction.guild.name}",
        color=discord.Color.from_rgb(255, 255, 255),
        timestamp=discord.utils.utcnow()
    )
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)

    embed.add_field(name=f"Selected Users ({len(user_ids)}):", value=user_list_str, inline=False)
    embed.add_field(name="Reason", value=f"` {reason} `", inline=False)
    embed.set_footer(text="Ensure bot role is positioned higher than target members.")

    view = BulkConfirmView("Warn", user_ids, reason=reason, author_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# --- Manual Moderation Commands ---

@bot.tree.command(name="mute", description="Mute (timeout) a member manually")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member, duration: str,
               reason: str = "No reason provided"):
    if member.bot:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} You cannot mute bots.", ephemeral=True)
        return

    seconds = parse_duration(duration)
    if not seconds or seconds <= 0:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} Invalid duration format! Use e.g. `10m`, `2h`, or `3d`.",
            ephemeral=True
        )
        return

    if seconds > 28 * 86400:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} Discord timeouts cannot exceed 28 days.",
            ephemeral=True
        )
        return

    await interaction.response.defer()
    delta = timedelta(seconds=seconds)

    dm_embed = discord.Embed(
        title=f"🔇 Muted in {interaction.guild.name}",
        description=f"You have been muted for **{duration}**.",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow()
    )
    dm_embed.add_field(name="Reason", value=reason, inline=False)
    dm_embed.set_footer(text=f"Moderator: {interaction.user.display_name}")
    await send_user_dm(member, dm_embed)

    try:
        await member.timeout(delta, reason=reason)
        embed = discord.Embed(
            title="🔇 Member Muted",
            description=f"{SUCCESSFUL_SPIN} Muted {member.mention} for **{duration}**.",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Moderator: {interaction.user.display_name}",
                         icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed)
        await log_action(
            interaction.guild,
            "Manual Mute Executed",
            f"**Selected User:** {member.mention}\n**Duration:** {duration}\n**Reason:** {reason}\n**Moderator:** {interaction.user.mention}",
            discord.Color.gold()
        )
    except discord.Forbidden:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} I lack permission to mute this member.", ephemeral=True)


@bot.tree.command(name="unmute", description="Unmute (remove timeout) a member manually")
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.bot:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} You cannot unmute bots.", ephemeral=True)
        return

    await interaction.response.defer()

    dm_embed = discord.Embed(
        title=f"🔊 Unmuted in {interaction.guild.name}",
        description="Your mute/timeout has been removed.",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    dm_embed.add_field(name="Reason", value=reason, inline=False)
    dm_embed.set_footer(text=f"Moderator: {interaction.user.display_name}")
    await send_user_dm(member, dm_embed)

    try:
        await member.timeout(None, reason=reason)
        embed = discord.Embed(
            title="🔊 Member Unmuted",
            description=f"{SUCCESSFUL_SPIN} Removed timeout for {member.mention}.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Moderator: {interaction.user.display_name}",
                         icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed)
        await log_action(interaction.guild, "Manual Unmute Executed",
                         f"**Selected User:** {member.mention}\n**Reason:** {reason}\n**Moderator:** {interaction.user.mention}",
                         discord.Color.green())
    except discord.Forbidden:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} I lack permission to unmute this member.", ephemeral=True)


@bot.tree.command(name="ban", description="Ban a user manually")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.bot:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} You cannot ban bots.", ephemeral=True)
        return

    await interaction.response.defer()

    dm_embed = discord.Embed(
        title=f"🔨 Banned from {interaction.guild.name}",
        description="You have been manually banned by a moderator.",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    dm_embed.add_field(name="Reason", value=reason, inline=False)
    dm_embed.set_footer(text=f"Moderator: {interaction.user.display_name}")
    await send_user_dm(member, dm_embed)

    try:
        await member.ban(reason=reason)
        embed = discord.Embed(
            title="🔨 Member Banned",
            description=f"{SUCCESSFUL_SPIN} Banned {member.mention}.",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Moderator: {interaction.user.display_name}",
                         icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed)
        await log_action(interaction.guild, "Manual Ban Executed",
                         f"**Selected User:** {member.mention} ({member.id})\n**Reason:** {reason}\n**Moderator:** {interaction.user.mention}",
                         discord.Color.red())
    except discord.Forbidden:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} I lack permission to ban this member.", ephemeral=True)


@bot.tree.command(name="unban", description="Unban a user manually by ID")
@app_commands.checks.has_permissions(ban_members=True)
async def unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    if not user_id.isdigit():
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Please provide a valid numerical User ID.",
                                                ephemeral=True)
        return

    await interaction.response.defer()
    uid = int(user_id)
    try:
        user = await bot.fetch_user(uid)
        await interaction.guild.unban(user, reason=reason)

        dm_embed = discord.Embed(
            title=f"🔓 Unbanned from {interaction.guild.name}",
            description="Your ban has been removed.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        dm_embed.add_field(name="Reason", value=reason, inline=False)
        dm_embed.set_footer(text=f"Moderator: {interaction.user.display_name}")
        await send_user_dm(user, dm_embed)

        embed = discord.Embed(
            title="🔓 Member Unbanned",
            description=f"{SUCCESSFUL_SPIN} Unbanned **{user.name}** (`{user.id}`).",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"Moderator: {interaction.user.display_name}",
                         icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed)
        await log_action(interaction.guild, "Manual Unban Executed",
                         f"**Selected User:** {user.mention} ({user.id})\n**Reason:** {reason}\n**Moderator:** {interaction.user.mention}",
                         discord.Color.green())
    except discord.NotFound:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} User ID not found or not currently banned.",
                                        ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} I lack permission to unban users.",
                                        ephemeral=True)


# --- Speak / Say Commands (Fixed with Defer & Full Exception Handling) ---

@bot.tree.command(name="say", description="Make the bot say a message in a specified channel")
@app_commands.checks.has_permissions(manage_messages=True)
async def say(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
    await interaction.response.defer(ephemeral=True)
    target_channel = channel or interaction.channel
    try:
        await target_channel.send(message)
        await interaction.followup.send(
            f"{SUCCESSFUL_SPIN} Message sent to {target_channel.mention}!",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            f"{UNSUCCESSFUL_SPIN} I don't have permission to view or send messages in {target_channel.mention}.",
            ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.followup.send(
            f"{UNSUCCESSFUL_SPIN} Failed to send message: {e}",
            ephemeral=True
        )


@bot.tree.command(name="speak", description="Make the bot say a message in the current channel")
@app_commands.checks.has_permissions(manage_messages=True)
async def speak(interaction: discord.Interaction, message: str):
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.channel.send(message)
        await interaction.followup.send(f"{SUCCESSFUL_SPIN} Message sent!", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            f"{UNSUCCESSFUL_SPIN} I don't have permission to view or send messages in this channel.",
            ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.followup.send(
            f"{UNSUCCESSFUL_SPIN} Failed to send message: {e}",
            ephemeral=True
        )


# --- Advanced Custom Embed Builder & Presets ---

class AdvancedEmbedModal(discord.ui.Modal, title="Advanced Embed Builder"):
    channel_input = discord.ui.TextInput(
        label="Channel (#channel or ID) - Optional",
        placeholder="Leave blank for current channel",
        required=False
    )
    title_and_url = discord.ui.TextInput(
        label="Title | Title Hyperlink URL (Optional)",
        placeholder="My Title | https://example.com",
        required=True,
        max_length=256
    )
    main_desc = discord.ui.TextInput(
        label="Main Description",
        placeholder="Primary text content...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )
    thumbnail_and_color = discord.ui.TextInput(
        label="Thumbnail URL | Hex Color (e.g. #FF5733)",
        placeholder="https://.../image.png | #0099ff",
        required=False,
        max_length=200
    )
    footer_and_preset = discord.ui.TextInput(
        label="Footer Text | Preset Name to Save (Optional)",
        placeholder="Powered by Community | my_preset",
        required=False,
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            target_channel = interaction.channel
            channel_raw = self.channel_input.value.strip()
            if channel_raw:
                cleaned_id = re.sub(r"\D", "", channel_raw)
                if cleaned_id.isdigit():
                    found_ch = interaction.guild.get_channel(int(cleaned_id))
                    if isinstance(found_ch, discord.TextChannel):
                        target_channel = found_ch

            title_val = self.title_and_url.value.strip()
            title_text = title_val
            title_link = None
            if "|" in title_val:
                parts = title_val.split("|", 1)
                title_text = parts[0].strip()
                title_link = parts[1].strip() or None

            thumb_url = None
            embed_color = discord.Color.blue()
            tc_val = self.thumbnail_and_color.value.strip()
            if tc_val:
                if "|" in tc_val:
                    t_parts = tc_val.split("|", 1)
                    thumb_url = t_parts[0].strip() or None
                    color_str = t_parts[1].strip()
                    if color_str.startswith("#"):
                        color_str = color_str.lstrip("#")
                    try:
                        embed_color = discord.Color(int(color_str, 16))
                    except ValueError:
                        pass
                elif tc_val.startswith("http"):
                    thumb_url = tc_val
                elif tc_val.startswith("#") or len(tc_val) == 6:
                    try:
                        embed_color = discord.Color(int(tc_val.lstrip("#"), 16))
                    except ValueError:
                        pass

            footer_text = None
            preset_name = None
            fp_val = self.footer_and_preset.value.strip()
            if fp_val:
                if "|" in fp_val:
                    f_parts = fp_val.split("|", 1)
                    footer_text = f_parts[0].strip() or None
                    preset_name = f_parts[1].strip() or None
                else:
                    footer_text = fp_val

            embed = discord.Embed(
                title=title_text,
                url=title_link,
                description=self.main_desc.value,
                color=embed_color
            )

            if thumb_url:
                embed.set_thumbnail(url=thumb_url)
            if footer_text:
                embed.set_footer(text=footer_text)

            if preset_name:
                try:
                    save_preset(preset_name, {
                        "title": title_text,
                        "url": title_link,
                        "desc": self.main_desc.value,
                        "thumb": thumb_url,
                        "color": str(embed_color.value),
                        "footer": footer_text
                    })
                except Exception as e:
                    print(f"Failed to save preset: {e}")

            await target_channel.send(embed=embed)
            await interaction.followup.send(
                f"{SUCCESSFUL_SPIN} Custom embed successfully sent to {target_channel.mention}!",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"{UNSUCCESSFUL_SPIN} I don't have permission to send messages in that channel.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"{UNSUCCESSFUL_SPIN} An unexpected error occurred: {e}",
                ephemeral=True
            )


@bot.tree.command(name="embed", description="Opens the form to build and send custom embeds with full styling")
@app_commands.checks.has_permissions(manage_messages=True)
async def embed(interaction: discord.Interaction):
    await interaction.response.send_modal(AdvancedEmbedModal())


# --- Giveaway System ---

class GiveawayButton(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.entries = set()

    @discord.ui.button(emoji="🎉", style=discord.ButtonStyle.blurple)
    async def enter_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        giveaway_data = active_giveaways.get(self.message_id)
        prize_name = giveaway_data["prize"] if giveaway_data else "the giveaway"

        if interaction.user.id in self.entries:
            self.entries.remove(interaction.user.id)
            await interaction.response.send_message("You left the giveaway!", ephemeral=True)
        else:
            self.entries.add(interaction.user.id)
            await interaction.response.send_message("You entered the giveaway!", ephemeral=True)

            dm_embed = discord.Embed(
                title="🎉 Giveaway Entry Confirmed!",
                description=f"You have successfully entered the giveaway for **{prize_name}** in **{interaction.guild.name}**!",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            await send_user_dm(interaction.user, dm_embed)


class GiveawayModal(discord.ui.Modal, title="Create a Giveaway"):
    duration_input = discord.ui.TextInput(label="Duration", placeholder="Ex: 10 minutes", required=True)
    winners_input = discord.ui.TextInput(label="Number of Winners", default="1", placeholder="1", required=True)
    prize_input = discord.ui.TextInput(label="Prize", placeholder="Enter the prize...", required=True)
    description_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph,
                                             placeholder="Enter additional details...", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        seconds = parse_duration(self.duration_input.value)
        if not seconds or seconds <= 0:
            await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Invalid duration format!", ephemeral=True)
            return

        try:
            winner_count = int(self.winners_input.value)
            if winner_count < 1:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Winners must be a positive number.",
                                                    ephemeral=True)
            return

        prize = self.prize_input.value
        end_timestamp = int(time.time() + seconds)
        view = GiveawayButton(0)

        def build_embed():
            return discord.Embed(
                title=f"**{prize}**",
                description=(
                    f"Ends: <t:{end_timestamp}:R> (<t:{end_timestamp}:f>)\n"
                    f"Hosted by: {interaction.user.mention} (`@{interaction.user.name}`)\n"
                    f"Entries: **{len(view.entries)}**\n"
                    f"Winners: **{winner_count}**"
                ),
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )

        await interaction.response.send_message("Starting giveaway...", ephemeral=True)
        msg = await interaction.channel.send(embed=build_embed(), view=view)
        view.message_id = msg.id

        active_giveaways[msg.id] = {
            "msg": msg,
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
            "prize": prize,
            "host": interaction.user,
            "end_timestamp": end_timestamp,
            "winner_count": winner_count,
            "view": view,
            "active": True
        }

        start_time = time.time()
        while time.time() - start_time < seconds:
            await asyncio.sleep(5)
            if msg.id not in active_giveaways or not active_giveaways[msg.id]["active"]:
                return
            try:
                await msg.edit(embed=build_embed(), view=view)
            except discord.HTTPException:
                break

        if msg.id in active_giveaways and active_giveaways[msg.id]["active"]:
            await finalize_giveaway(msg.id, interaction.guild)


async def finalize_giveaway(message_id: int, guild: discord.Guild):
    data = active_giveaways.get(message_id)
    if not data or not data["active"]:
        return

    data["active"] = False
    msg = data["msg"]
    prize = data["prize"]
    winner_count = data["winner_count"]
    view = data["view"]
    end_timestamp = data["end_timestamp"]
    host = data["host"]

    winner_users = [guild.get_member(uid) for uid in view.entries if
                    guild.get_member(uid) and not guild.get_member(uid).bot]

    if not winner_users:
        ended_embed = discord.Embed(
            title=f"**{prize}**",
            description=f"Ended: <t:{end_timestamp}:f>\nHosted by: {host.mention}\nEntries: **0**\nWinners: Could not determine a winner.",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        try:
            await msg.edit(embed=ended_embed, view=None)
        except discord.HTTPException:
            pass
        await msg.channel.send(f"Giveaway for **{prize}** ended, but no one entered!")
    else:
        winners = random.sample(winner_users, k=min(winner_count, len(winner_users)))
        winner_mentions = ", ".join([w.mention for w in winners])

        ended_embed = discord.Embed(
            title=f"**{prize}**",
            description=f"Ended: <t:{end_timestamp}:f>\nHosted by: {host.mention}\nEntries: **{len(view.entries)}**\nWinners: {winner_mentions}",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        try:
            await msg.edit(embed=ended_embed, view=None)
        except discord.HTTPException:
            pass
        await msg.channel.send(content=f"Congratulations {winner_mentions}! You won **{prize}**!")


@bot.tree.command(name="giveaway", description="starts a giveaway (interactive)")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway(interaction: discord.Interaction):
    await interaction.response.send_modal(GiveawayModal())


# --- Logging Channel Configuration ---

@bot.tree.command(name="setlogchannel", description="Set dynamic logging channels for messages, media, or general logs")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(log_type=[
    app_commands.Choice(name="General Audit Logs", value="general"),
    app_commands.Choice(name="Message Logs (Deleted Messages)", value="msg"),
    app_commands.Choice(name="Media Logs (Images/GIFs)", value="media")
])
async def setlogchannel(interaction: discord.Interaction, log_type: app_commands.Choice[str],
                        channel: discord.TextChannel):
    config = get_server_config(interaction.guild_id)
    config[f"{log_type.value}_log_channel" if log_type.value != "general" else "log_channel"] = channel.id
    await interaction.response.send_message(f"{SUCCESSFUL_SPIN} {log_type.name} set to {channel.mention}.")


# --- Add Damage Commands ---

@bot.tree.command(name="damage", description="Apply damage points to a user")
@app_commands.checks.has_permissions(kick_members=True)
async def damage(interaction: discord.Interaction, member: discord.Member, points: int,
                 reason: str = "No reason provided"):
    if member.bot:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} You cannot modify damage for bots.",
                                                ephemeral=True)
        return

    await interaction.response.defer()
    pts_to_add = abs(points)
    current_damage, punishment = await apply_damage_and_punish(interaction.guild, member, pts_to_add, reason,
                                                               interaction.user)

    embed = create_damage_embed("add", member, pts_to_add, current_damage, punishment, reason, interaction.user)
    await interaction.followup.send(embed=embed)


# --- Remove Damage Commands ---

@bot.tree.command(name="removedamage", description="Remove damage points from a user")
@app_commands.checks.has_permissions(kick_members=True)
async def removedamage(interaction: discord.Interaction, member: discord.Member, points: int,
                       reason: str = "No reason provided"):
    if member.bot:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} You cannot modify damage for bots.",
                                                ephemeral=True)
        return

    await interaction.response.defer()
    pts_to_remove = -abs(points)
    current_damage, punishment = await apply_damage_and_punish(interaction.guild, member, pts_to_remove, reason,
                                                               interaction.user)

    embed = create_damage_embed("remove", member, abs(points), current_damage, punishment, reason, interaction.user)
    await interaction.followup.send(embed=embed)


# --- Damage Query & Warning Commands ---

@bot.tree.command(name="checkdamage", description="Check the current damage points of a user")
async def checkdamage(interaction: discord.Interaction, member: discord.Member):
    key = (interaction.guild_id, member.id)
    pts = user_damage.get(key, 0)
    embed = discord.Embed(title=f"Damage Report — {member.display_name}", description=f"**Current Damage:** `{pts}/25`",
                          color=discord.Color.red() if pts >= 15 else discord.Color.blue())
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="warn", description="Warn a user")
@app_commands.checks.has_permissions(kick_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.bot:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} You cannot warn bots.", ephemeral=True)
        return

    await interaction.response.defer()
    key = (interaction.guild_id, member.id)
    if key not in user_warnings:
        user_warnings[key] = []

    user_warnings[key].append({"reason": reason, "by": interaction.user.display_name})
    save_data()

    dm_embed = discord.Embed(
        title=f"⚠️ Warning Received in {interaction.guild.name}",
        description="You have received an official warning.",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    dm_embed.add_field(name="Reason", value=reason, inline=False)
    dm_embed.set_footer(text=f"Moderator: {interaction.user.display_name}")
    await send_user_dm(member, dm_embed)

    embed = discord.Embed(
        title="⚠️ User Warned",
        description=f"{SUCCESSFUL_SPIN} Successfully issued a warning to {member.mention}.",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Moderator: {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

    await interaction.followup.send(embed=embed)
    await log_action(
        interaction.guild,
        "User Warned",
        f"**Selected User:** {member.mention} ({member.id})\n**Reason:** {reason}\n**Moderator:** {interaction.user.mention}",
        discord.Color.orange()
    )


@bot.tree.command(name="warnings", description="View warnings for a user")
async def warnings(interaction: discord.Interaction, member: discord.Member):
    key = (interaction.guild_id, member.id)
    total_points = user_damage.get(key, 0)
    warns = user_warnings.get(key, [])

    if not warns:
        await interaction.response.send_message(
            f"{SUCCESSFUL_SPIN} **{member.display_name}** has no recorded warnings in this server.")
        return

    embed = discord.Embed(title=f"Warnings for {member.display_name}",
                          description=f"**Current Damage:** `{total_points}/25`", color=discord.Color.blue())
    for idx, w in enumerate(warns, 1):
        embed.add_field(name=f"Warning #{idx}",
                        value=f"**Reason:** {w['reason']}\n**Moderator:** {w['by']}", inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="clearwarnings", description="Reset warnings and damage points for a user")
@app_commands.checks.has_permissions(administrator=True)
async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    key = (interaction.guild_id, member.id)
    user_damage.pop(key, None)
    user_warnings.pop(key, None)
    save_data()

    if member.timed_out_until:
        try:
            await member.timeout(None, reason=f"Warnings cleared by {interaction.user.display_name}")
        except discord.Forbidden:
            pass

    embed = discord.Embed(
        title="🧹 Warnings & Damage Cleared",
        description=f"{SUCCESSFUL_SPIN} Cleared all damage points and warnings for {member.mention}.",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Cleared by: {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

    await interaction.followup.send(embed=embed)
    await log_action(interaction.guild, "Warnings Cleared",
                     f"**Selected User:** {member.mention}\n**Cleared By:** {interaction.user.mention}",
                     discord.Color.green())


@bot.tree.command(name="addemote", description="Add an external emoji to the server")
@app_commands.checks.has_permissions(manage_emojis=True)
async def addemote(interaction: discord.Interaction, name: str, url: str):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} Failed to download image from the provided URL.")
                return
            image_data = await resp.read()

    try:
        new_emoji = await interaction.guild.create_custom_emoji(name=name, image=image_data)
        await interaction.followup.send(f"{SUCCESSFUL_SPIN} Added emoji {new_emoji} (`:{name}:`)!")
    except discord.HTTPException as e:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} Failed to add emoji: {e}")


# --- Fun / Utility Commands ---

@bot.tree.command(name="cat", description="Send a random cute cat image or GIF!")
@app_commands.choices(
    type=[
        app_commands.Choice(name="🐱 Random (Image or GIF)", value="both"),
        app_commands.Choice(name="🖼️ Image Only", value="jpg,png"),
        app_commands.Choice(name="🎬 GIF Only", value="gif"),
    ]
)
async def cat(interaction: discord.Interaction, type: app_commands.Choice[str] = None):
    await interaction.response.defer()

    mime_types = type.value if type else "both"
    url = "https://api.thecatapi.com/v1/images/search"
    params = {}
    if mime_types != "both":
        params["mime_types"] = mime_types

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=5) as response:
                if response.status != 200:
                    await interaction.followup.send(
                        f"{UNSUCCESSFUL_SPIN} Failed to fetch a cat image from the API. Try again later!")
                    return

                data = await response.json()
                if not data:
                    await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} No cat images found.")
                    return

                image_url = data[0]["url"]

        embed = discord.Embed(
            title="🐾 Here's a cat for you!",
            color=discord.Color.from_rgb(255, 255, 255),
            timestamp=discord.utils.utcnow()
        )
        embed.set_image(url=image_url)
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")

        await interaction.followup.send(embed=embed)

    except Exception:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} An error occurred while fetching the cat image.")


# --- RoleManage-Style Role Automation (IF roles -> THEN give/remove a role) ---

ROLE_RULES_FILE = "role_rules.json"
MAX_RULES_PER_GUILD = 25
role_rules = {}  # guild_id -> list of rule dicts
_role_eval_times = {}  # (guild_id, user_id) -> recent evaluation timestamps (loop guard)


def save_role_rules():
    with open(ROLE_RULES_FILE, "w") as f:
        json.dump({str(g): rules for g, rules in role_rules.items()}, f, indent=4)


def load_role_rules():
    global role_rules
    if os.path.exists(ROLE_RULES_FILE):
        try:
            with open(ROLE_RULES_FILE, "r") as f:
                data = json.load(f)
            role_rules = {int(g): rules for g, rules in data.items()}
        except Exception as e:
            print(f"Error loading role rules: {e}")


load_role_rules()


def parse_role_ids(raw_input: str) -> list[int]:
    """Pulls role IDs out of @role mentions (<@&id>) or raw numeric IDs, keeping order."""
    return list(dict.fromkeys(int(x) for x in re.findall(r"\d{15,20}", raw_input or "")))


def rule_condition_met(role_ids: set, rule: dict) -> bool:
    if_roles = rule["if_roles"]
    if not if_roles:
        return False
    if rule["match"] == "any":
        ok = any(r in role_ids for r in if_roles)
    else:
        ok = all(r in role_ids for r in if_roles)
    if ok and any(r in role_ids for r in rule.get("unless_roles", [])):
        ok = False
    return ok


def describe_rule(rule: dict) -> str:
    ifs = ", ".join(f"<@&{r}>" for r in rule["if_roles"])
    if len(rule["if_roles"]) == 1:
        cond = f"has {ifs}"
    else:
        cond = f"has {'ALL' if rule['match'] == 'all' else 'ANY'} of {ifs}"
    if rule.get("unless_roles"):
        unless = ", ".join(f"<@&{r}>" for r in rule["unless_roles"])
        cond += f" and has none of {unless}"

    if rule["action"] == "give":
        then = f"**GIVE** <@&{rule['then_role']}>"
        if rule.get("auto_remove", True):
            then += " (taken away again if they stop qualifying)"
    else:
        then = f"**REMOVE** <@&{rule['then_role']}>"
    return f"**IF** member {cond}\n**THEN** {then}"


async def apply_role_rules(member: discord.Member, reason: str = "RoleManage rule") -> int:
    """Evaluates every rule for one member and applies any role changes. Returns number of changes."""
    guild = member.guild
    rules = role_rules.get(guild.id)
    if not rules or member.bot:
        return 0

    current = {r.id for r in member.roles}
    desired = {}  # role_id -> True (should have) / False (should not have)

    for rule in rules:
        if rule["action"] == "give" and rule.get("auto_remove", True) and not rule_condition_met(current, rule):
            desired[rule["then_role"]] = False
    for rule in rules:
        if rule["action"] == "give" and rule_condition_met(current, rule):
            desired[rule["then_role"]] = True
    for rule in rules:
        if rule["action"] == "remove" and rule_condition_met(current, rule):
            desired[rule["then_role"]] = False

    to_add, to_remove = [], []
    for role_id, want in desired.items():
        role = guild.get_role(role_id)
        if role is None or role.managed or role >= guild.me.top_role:
            continue
        if want and role_id not in current:
            to_add.append(role)
        elif not want and role_id in current:
            to_remove.append(role)

    if to_add:
        await member.add_roles(*to_add, reason=reason)
    if to_remove:
        await member.remove_roles(*to_remove, reason=reason)
    return len(to_add) + len(to_remove)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.bot or before.roles == after.roles:
        return
    if not role_rules.get(after.guild.id):
        return

    key = (after.guild.id, after.id)
    now = time.time()
    stamps = [t for t in _role_eval_times.get(key, []) if now - t < 30]
    if len(stamps) >= 10:
        print(f"RoleManage loop guard tripped for {after.id} in {after.guild.id} - check for conflicting rules.")
        return
    stamps.append(now)
    _role_eval_times[key] = stamps

    try:
        await apply_role_rules(after)
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f"RoleManage failed to update {after.id} in {after.guild.id}: {e}")


rolemanage_group = app_commands.Group(
    name="rolemanage",
    description="Automatically give or remove roles based on the roles a member already has",
    guild_only=True,
    default_permissions=discord.Permissions(manage_roles=True)
)


@rolemanage_group.command(name="add", description="Create a rule: IF members have certain roles THEN give/remove a role")
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(
    if_roles="Role(s) to check for - @mention them (several allowed) or paste IDs",
    then_role="The role to give or remove when the condition is met",
    action="Give or remove the role (default: give)",
    match="Need ALL of the if-roles or just ANY of them (default: all)",
    unless_roles="Optional: rule does NOT apply if the member has any of these roles",
    auto_remove="For 'give' rules: take the role away again if they stop qualifying (default: yes)"
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Give the role", value="give"),
        app_commands.Choice(name="Remove the role", value="remove"),
    ],
    match=[
        app_commands.Choice(name="Must have ALL of the if-roles", value="all"),
        app_commands.Choice(name="Must have ANY of the if-roles", value="any"),
    ]
)
async def rolemanage_add(interaction: discord.Interaction, if_roles: str, then_role: discord.Role,
                         action: app_commands.Choice[str] = None, match: app_commands.Choice[str] = None,
                         unless_roles: str = None, auto_remove: bool = True):
    guild = interaction.guild
    action_value = action.value if action else "give"
    match_value = match.value if match else "all"

    if_ids = parse_role_ids(if_roles)
    unless_ids = parse_role_ids(unless_roles) if unless_roles else []

    if not if_ids:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} I couldn't find any roles in `if_roles`. @mention the roles or paste their IDs.",
            ephemeral=True)
        return

    unknown = [str(r) for r in if_ids + unless_ids if guild.get_role(r) is None]
    if unknown:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} These aren't roles in this server: {', '.join(unknown)}", ephemeral=True)
        return

    if then_role.is_default() or then_role.managed:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} That role can't be assigned manually (it's @everyone or managed by an integration).",
            ephemeral=True)
        return

    if then_role.id in if_ids or then_role.id in unless_ids:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} The target role can't also be one of the condition roles.", ephemeral=True)
        return

    if then_role >= guild.me.top_role:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} {then_role.mention} is above my highest role, so I can't manage it. "
            "Move my role higher in Server Settings → Roles.", ephemeral=True)
        return

    if interaction.user.id != guild.owner_id and then_role >= interaction.user.top_role:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} You can't create a rule for a role that's at or above your own highest role.",
            ephemeral=True)
        return

    rules = role_rules.setdefault(guild.id, [])
    if len(rules) >= MAX_RULES_PER_GUILD:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} This server already has the maximum of {MAX_RULES_PER_GUILD} rules.",
            ephemeral=True)
        return

    rule = {
        "id": max((r["id"] for r in rules), default=0) + 1,
        "if_roles": if_ids,
        "unless_roles": unless_ids,
        "match": match_value,
        "action": action_value,
        "then_role": then_role.id,
        "auto_remove": auto_remove
    }
    rules.append(rule)
    save_role_rules()

    embed = discord.Embed(
        title=f"✅ Rule #{rule['id']} Created",
        description=describe_rule(rule),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Applies to role changes from now on. Run /rolemanage sync to apply it to existing members.")
    await interaction.response.send_message(embed=embed, ephemeral=True)
    await log_action(
        guild,
        "RoleManage Rule Created",
        f"**Rule #{rule['id']}**\n{describe_rule(rule)}\n**Created by:** {interaction.user.mention}",
        discord.Color.green()
    )


@rolemanage_group.command(name="list", description="Show all RoleManage rules in this server")
@app_commands.checks.has_permissions(manage_roles=True)
async def rolemanage_list(interaction: discord.Interaction):
    rules = role_rules.get(interaction.guild_id, [])
    if not rules:
        await interaction.response.send_message(
            "No rules yet. Create one with `/rolemanage add`.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"RoleManage Rules — {interaction.guild.name}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )
    for rule in rules:
        embed.add_field(name=f"Rule #{rule['id']}", value=describe_rule(rule)[:1024], inline=False)
    embed.set_footer(text="Use /rolemanage remove <rule_id> to delete a rule.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@rolemanage_group.command(name="remove", description="Delete a RoleManage rule by its number")
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(rule_id="The rule number shown in /rolemanage list")
async def rolemanage_remove(interaction: discord.Interaction, rule_id: int):
    rules = role_rules.get(interaction.guild_id, [])
    target = next((r for r in rules if r["id"] == rule_id), None)
    if not target:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} No rule with number {rule_id}. Check `/rolemanage list`.", ephemeral=True)
        return

    rules.remove(target)
    save_role_rules()
    await interaction.response.send_message(
        f"{SUCCESSFUL_SPIN} Deleted rule #{rule_id}. Roles it already handed out are left as they are.",
        ephemeral=True)
    await log_action(
        interaction.guild,
        "RoleManage Rule Deleted",
        f"**Rule #{rule_id}**\n{describe_rule(target)}\n**Deleted by:** {interaction.user.mention}",
        discord.Color.red()
    )


@rolemanage_group.command(name="sync", description="Apply all rules to every current member of the server")
@app_commands.checks.has_permissions(manage_roles=True)
async def rolemanage_sync(interaction: discord.Interaction):
    if not role_rules.get(interaction.guild_id):
        await interaction.response.send_message(
            "No rules to apply. Create one with `/rolemanage add`.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    changed_members = 0
    total_changes = 0
    failed = 0
    async for member in interaction.guild.fetch_members(limit=None):
        if member.bot:
            continue
        try:
            n = await apply_role_rules(member, reason=f"RoleManage sync by {interaction.user}")
            if n:
                changed_members += 1
                total_changes += n
                await asyncio.sleep(0.5)
        except (discord.Forbidden, discord.HTTPException):
            failed += 1

    msg = f"{SUCCESSFUL_SPIN} Sync complete: **{total_changes}** role change(s) across **{changed_members}** member(s)."
    if failed:
        msg += f"\n{UNSUCCESSFUL_SPIN} Couldn't update **{failed}** member(s) (check my role position)."
    await interaction.followup.send(msg, ephemeral=True)
    await log_action(
        interaction.guild,
        "RoleManage Sync Executed",
        f"**Role changes:** {total_changes}\n**Members changed:** {changed_members}\n**Run by:** {interaction.user.mention}",
        discord.Color.blue()
    )


bot.tree.add_command(rolemanage_group)


# --- Profile / Avatar / Banner Lookup (works for users who are not in the server) ---

def format_badges(flags: discord.PublicUserFlags) -> str:
    names = [name.replace("_", " ").title() for name, value in flags if value]
    return ", ".join(names) if names else "None"


def asset_links(asset: discord.Asset, size: int = 1024) -> str:
    formats = ["png", "jpg", "webp"] + (["gif"] if asset.is_animated() else [])
    return " • ".join(f"[{f.upper()}]({asset.with_format(f).with_size(size).url})" for f in formats)


async def fetch_full_user(user: discord.abc.User) -> discord.User:
    """Fetches the complete user from Discord (needed for banner/accent color; works for non-members)."""
    try:
        return await bot.fetch_user(user.id)
    except discord.HTTPException:
        return user


@bot.tree.command(name="profile", description="View a user's profile - works even if they aren't in this server")
@app_commands.describe(user="The user to look up (@mention or paste their user ID)")
async def profile(interaction: discord.Interaction, user: discord.User = None):
    await interaction.response.defer()

    target = await fetch_full_user(user or interaction.user)
    member = interaction.guild.get_member(target.id) if interaction.guild else None

    if target.accent_color:
        color = target.accent_color
    elif member and member.color != discord.Color.default():
        color = member.color
    else:
        color = discord.Color.blue()

    embed = discord.Embed(
        title=f"Profile — {target.global_name or target.name}",
        color=color,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    if target.banner:
        embed.set_image(url=target.banner.with_size(1024).url)

    embed.add_field(name="Username", value=f"`{target.name}`", inline=True)
    embed.add_field(name="User ID", value=f"`{target.id}`", inline=True)
    embed.add_field(name="Type", value="Bot" if target.bot else "User", inline=True)
    embed.add_field(name="Account Created", value=f"<t:{int(target.created_at.timestamp())}:F>\n(<t:{int(target.created_at.timestamp())}:R>)", inline=False)
    embed.add_field(name="Badges", value=format_badges(target.public_flags), inline=False)

    if target.accent_color:
        embed.add_field(name="Accent Color", value=f"`{str(target.accent_color).upper()}`", inline=True)
    embed.add_field(name="Banner", value="Yes" if target.banner else "None", inline=True)

    if member:
        if member.nick:
            embed.add_field(name="Server Nickname", value=member.nick, inline=True)
        if member.joined_at:
            embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:F>\n(<t:{int(member.joined_at.timestamp())}:R>)", inline=False)
        if member.premium_since:
            embed.add_field(name="Boosting Since", value=f"<t:{int(member.premium_since.timestamp())}:R>", inline=True)
        if member.timed_out_until:
            embed.add_field(name="Timed Out Until", value=f"<t:{int(member.timed_out_until.timestamp())}:R>", inline=True)
        roles = [r.mention for r in reversed(member.roles) if r != interaction.guild.default_role]
        embed.add_field(
            name=f"Roles ({len(roles)})",
            value=format_user_list(roles, sep=", ", prefix="", limit=1024) if roles else "None",
            inline=False
        )
        embed.set_footer(text="Member of this server")
    else:
        embed.set_footer(text="Not in this server - showing global profile only")

    await interaction.followup.send(embed=embed)


@bot.tree.command(name="avatar", description="View a user's avatar - works even if they aren't in this server")
@app_commands.describe(user="The user to look up (@mention or paste their user ID)")
async def avatar(interaction: discord.Interaction, user: discord.User = None):
    await interaction.response.defer()

    target = await fetch_full_user(user or interaction.user)
    member = interaction.guild.get_member(target.id) if interaction.guild else None
    name = target.global_name or target.name

    global_asset = target.display_avatar
    embed = discord.Embed(
        title=f"{name}'s Avatar",
        description=asset_links(global_asset),
        color=target.accent_color or discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_image(url=global_asset.with_size(1024).url)
    embeds = [embed]

    if member and member.guild_avatar:
        server_embed = discord.Embed(
            title=f"{name}'s Server Avatar",
            description=asset_links(member.guild_avatar),
            color=target.accent_color or discord.Color.blue()
        )
        server_embed.set_image(url=member.guild_avatar.with_size(1024).url)
        embeds.append(server_embed)

    await interaction.followup.send(embeds=embeds)


@bot.tree.command(name="banner", description="View a user's banner - works even if they aren't in this server")
@app_commands.describe(user="The user to look up (@mention or paste their user ID)")
async def banner(interaction: discord.Interaction, user: discord.User = None):
    await interaction.response.defer()

    target = await fetch_full_user(user or interaction.user)
    name = target.global_name or target.name

    if target.banner:
        embed = discord.Embed(
            title=f"{name}'s Banner",
            description=asset_links(target.banner),
            color=target.accent_color or discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_image(url=target.banner.with_size(1024).url)
        await interaction.followup.send(embed=embed)
    elif target.accent_color:
        embed = discord.Embed(
            title=f"{name} has no banner image",
            description=f"They have a banner color set instead: `{str(target.accent_color).upper()}`",
            color=target.accent_color
        )
        await interaction.followup.send(embed=embed)
    else:
        await interaction.followup.send(f"{UNSUCCESSFUL_SPIN} **{name}** doesn't have a banner.")


bot.run(TOKEN)