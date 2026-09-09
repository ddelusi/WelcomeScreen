import os
import threading
import time
import asyncio
import random
import re
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
    flask_app.run(host='0.0.0.0', port=8080)

def keep_alive_ping():
    time.sleep(20)
    while True:
        try:
            requests.get("https://welcomescreen-4ulq.onrender.com")
        except Exception:
            pass
        time.sleep(600)  # Pings every 10 minutes

# Run web server in background threads
threading.Thread(target=run_flask, daemon=True).start()
threading.Thread(target=keep_alive_ping, daemon=True).start()

# --- Discord Bot Setup & Dynamic Prefix ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

server_prefixes = {}  # guild_id -> custom prefix string

def get_prefix(bot, message):
    if not message.guild:
        return "!"
    return server_prefixes.get(message.guild.id, "!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=get_prefix, intents=intents)

# Custom Emojis
SUCCESSFUL_SPIN = "<a:SuccessfulSpin:1547127487824142346>"
UNSUCCESSFUL_SPIN = "<a:UnsuccessfulSpin:1547127600693116999>"

# Server-Specific Storage: guild_id -> config/data dicts
server_configs = {}
user_damage = {}      # (guild_id, user_id) -> total damage points
user_warnings = {}    # (guild_id, user_id) -> list of warning dicts
active_giveaways = {} # message_id -> giveaway data dict

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
            await channel.send(embed=embed)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id}) - Globally synced commands across all servers")

# --- Event Listeners for Dynamic Per-Server Logging & Unban Resets ---

@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    key = (guild.id, user.id)
    if key in user_damage or key in user_warnings:
        user_damage.pop(key, None)
        user_warnings.pop(key, None)
        await log_action(
            guild,
            "Damage Points Reset",
            f"**User:** {user.mention} ({user.id})\n**Reason:** Automatically reset all damage points and warnings upon being unbanned.",
            discord.Color.blue()
        )

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    config = get_server_config(message.guild.id)

    if config["msg_log_channel"]:
        log_chan = message.guild.get_channel(config["msg_log_channel"])
        if log_chan and message.content:
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
            has_media = any(att.content_type and att.content_type.startswith(('image/', 'video/')) for att in message.attachments)
            has_gif_link = "tenor.com" in message.content or "giphy.com" in message.content or message.content.endswith(('.png', '.jpg', '.jpeg', '.gif'))

            if has_media or has_gif_link:
                embed = discord.Embed(
                    title="Media Detected (Deleted)",
                    description=f"**Author:** {message.author.mention}\n**Channel:** {message.channel.mention}\n\n**Content/URL:**\n{message.content}",
                    color=discord.Color.gold(),
                    timestamp=discord.utils.utcnow()
                )
                if message.attachments:
                    embed.set_footer(text=f"Attachment Name: {message.attachments[0].filename}")
                await media_chan.send(embed=embed)

# --- Dynamic Prefix Command ---

@bot.tree.command(name="setprefix", description="Change the bot prefix for text commands in this server")
@app_commands.checks.has_permissions(administrator=True)
async def setprefix(interaction: discord.Interaction, prefix: str):
    server_prefixes[interaction.guild_id] = prefix
    await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Bot prefix updated to `{prefix}` for this server!")

@bot.command(name="setprefix")
@commands.has_permissions(administrator=True)
async def setprefix_prefix(ctx, prefix: str):
    server_prefixes[ctx.guild.id] = prefix
    await ctx.send(f"{SUCCESSFUL_SPIN} Bot prefix updated to `{prefix}` for this server!")

# --- Speak / Say Commands ---

@bot.tree.command(name="say", description="Make the bot say a message in a specified channel")
@app_commands.checks.has_permissions(manage_messages=True)
async def say(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
    target_channel = channel or interaction.channel
    try:
        await target_channel.send(message)
        await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Message sent to {target_channel.mention}!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} I don't have permission to send messages in {target_channel.mention}.", ephemeral=True)

@bot.command(name="say")
@commands.has_permissions(manage_messages=True)
async def say_prefix(ctx, channel: discord.TextChannel, *, message: str):
    try:
        await channel.send(message)
        await ctx.message.delete()
    except discord.Forbidden:
        await ctx.send(f"{UNSUCCESSFUL_SPIN} I don't have permission to send messages in {channel.mention}.")

@bot.tree.command(name="speak", description="Make the bot say a message in the current channel")
@app_commands.checks.has_permissions(manage_messages=True)
async def speak(interaction: discord.Interaction, message: str):
    try:
        await interaction.channel.send(message)
        await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Message sent!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} I don't have permission to send messages here.", ephemeral=True)

@bot.command(name="speak")
@commands.has_permissions(manage_messages=True)
async def speak_prefix(ctx, *, message: str):
    try:
        await ctx.channel.send(message)
        await ctx.message.delete()
    except discord.Forbidden:
        await ctx.send(f"{UNSUCCESSFUL_SPIN} I don't have permission to send messages here.")

# --- Embed Builder Modal & Commands ---

class EmbedModal(discord.ui.Modal, title="Create Custom Embed"):
    channel_mention_input = discord.ui.TextInput(
        label="Channel (#channel or Channel ID)",
        placeholder="Ex: #announcements or 1234567890 (leave blank for current)",
        required=False
    )
    embed_title = discord.ui.TextInput(
        label="Title",
        placeholder="Enter embed title...",
        required=True
    )
    embed_description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="Enter embed description...",
        required=True
    )
    embed_color = discord.ui.TextInput(
        label="Color (Hex)",
        placeholder="Ex: 3498db or #ff0000",
        default="3498db",
        required=False
    )
    embed_image = discord.ui.TextInput(
        label="Image URL",
        placeholder="https://example.com/image.png",
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        target_channel = interaction.channel
        channel_raw = self.channel_mention_input.value.strip()

        if channel_raw:
            cleaned_id = re.sub(r"\D", "", channel_raw)
            if cleaned_id.isdigit():
                found_channel = interaction.guild.get_channel(int(cleaned_id))
                if found_channel and isinstance(found_channel, discord.TextChannel):
                    target_channel = found_channel

        try:
            color_hex = self.embed_color.value.lstrip('#')
            color_int = int(color_hex, 16)
        except ValueError:
            color_int = 0x3498db

        embed_obj = discord.Embed(
            title=self.embed_title.value,
            description=self.embed_description.value,
            color=discord.Color(color_int)
        )

        if self.embed_image.value.strip():
            embed_obj.set_image(url=self.embed_image.value.strip())

        try:
            await target_channel.send(embed=embed_obj)
            await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Embed successfully sent to {target_channel.mention}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} I don't have permission to send messages in {target_channel.mention}.", ephemeral=True)

@bot.tree.command(name="embed", description="Opens the form to build and send a custom embed")
@app_commands.checks.has_permissions(manage_messages=True)
async def embed(interaction: discord.Interaction):
    await interaction.response.send_modal(EmbedModal())

# --- Giveaway Management System ---

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

            try:
                dm_embed = discord.Embed(
                    title="🎉 Giveaway Entry Confirmed!",
                    description=f"You have successfully entered the giveaway for **{prize_name}** in **{interaction.guild.name}**!",
                    color=discord.Color.green(),
                    timestamp=discord.utils.utcnow()
                )
                await interaction.user.send(embed=dm_embed)
            except discord.Forbidden:
                pass

class GiveawayModal(discord.ui.Modal, title="Create a Giveaway"):
    duration_input = discord.ui.TextInput(label="Duration", placeholder="Ex: 10 minutes", required=True)
    winners_input = discord.ui.TextInput(label="Number of Winners", default="1", placeholder="1", required=True)
    prize_input = discord.ui.TextInput(label="Prize", placeholder="Enter the prize...", required=True)
    description_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, placeholder="Enter additional details...", required=False)

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
            await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Winners must be a positive number.", ephemeral=True)
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

    winner_users = [guild.get_member(uid) for uid in view.entries if guild.get_member(uid) and not guild.get_member(uid).bot]

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

# --- Moderation & Utility Commands ---

@bot.tree.command(name="setlogchannel", description="Set dynamic logging channels for messages, media, or general logs")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(log_type=[
    app_commands.Choice(name="General Audit Logs", value="general"),
    app_commands.Choice(name="Message Logs (Deleted Messages)", value="msg"),
    app_commands.Choice(name="Media Logs (Images/GIFs)", value="media")
])
async def setlogchannel(interaction: discord.Interaction, log_type: app_commands.Choice[str], channel: discord.TextChannel):
    config = get_server_config(interaction.guild_id)
    config[f"{log_type.value}_log_channel" if log_type.value != "general" else "log_channel"] = channel.id
    await interaction.response.send_message(f"{SUCCESSFUL_SPIN} {log_type.name} set to {channel.mention}.")

# --- Damage & Warning Slash + Prefix Commands ---

@bot.tree.command(name="damage", description="Apply or remove damage points for a user")
@app_commands.checks.has_permissions(kick_members=True)
async def damage(interaction: discord.Interaction, member: discord.Member, points: int, reason: str = "No reason provided"):
    if member.bot:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} You cannot apply damage to bots.", ephemeral=True)
        return

    key = (interaction.guild_id, member.id)
    user_damage[key] = max(0, user_damage.get(key, 0) + points)
    current_damage = user_damage[key]

    if current_damage >= 25:
        try:
            await member.ban(reason=f"Reached 25+ damage points (Auto-ban). Last update: {reason}")
            await interaction.response.send_message(f"{SUCCESSFUL_SPIN} **{member.display_name}** reached **{current_damage}** damage points and was **automatically banned**.")
            await log_action(interaction.guild, "Auto-Ban Executed", f"**User:** {member.mention} ({member.id})\n**Reason:** Accumulated {current_damage} damage points.", discord.Color.red())
        except discord.Forbidden:
            await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} **{member.display_name}** reached **{current_damage}** damage points, but I do not have permission to ban them.", ephemeral=True)
    else:
        action_text = "Added" if points >= 0 else "Removed"
        await interaction.response.send_message(f"{SUCCESSFUL_SPIN} {action_text} **{abs(points)}** damage point(s) for **{member.display_name}**. Total Damage: **{current_damage}/25**.")
        await log_action(interaction.guild, "Damage Points Updated", f"**User:** {member.mention}\n**Point Change:** {points:+d}\n**Total Points:** {current_damage}/25\n**Reason:** {reason}\n**Moderator:** {interaction.user.mention}", discord.Color.orange() if points > 0 else discord.Color.green())

@bot.command(name="damage")
@commands.has_permissions(kick_members=True)
async def damage_prefix(ctx, member: discord.Member, points: int, *, reason: str = "No reason provided"):
    if member.bot:
        await ctx.send(f"{UNSUCCESSFUL_SPIN} You cannot apply damage to bots.")
        return

    key = (ctx.guild.id, member.id)
    user_damage[key] = max(0, user_damage.get(key, 0) + points)
    current_damage = user_damage[key]

    if current_damage >= 25:
        try:
            await member.ban(reason=f"Reached 25+ damage points (Auto-ban). Last update: {reason}")
            await ctx.send(f"{SUCCESSFUL_SPIN} **{member.display_name}** reached **{current_damage}** damage points and was **automatically banned**.")
            await log_action(ctx.guild, "Auto-Ban Executed", f"**User:** {member.mention} ({member.id})\n**Reason:** Accumulated {current_damage} damage points.", discord.Color.red())
        except discord.Forbidden:
            await ctx.send(f"{UNSUCCESSFUL_SPIN} **{member.display_name}** reached **{current_damage}** damage points, but I do not have permission to ban them.")
    else:
        action_text = "Added" if points >= 0 else "Removed"
        await ctx.send(f"{SUCCESSFUL_SPIN} {action_text} **{abs(points)}** damage point(s) for **{member.display_name}**. Total Damage: **{current_damage}/25**.")
        await log_action(ctx.guild, "Damage Points Updated", f"**User:** {member.mention}\n**Point Change:** {points:+d}\n**Total Points:** {current_damage}/25\n**Reason:** {reason}\n**Moderator:** {ctx.author.mention}", discord.Color.orange() if points > 0 else discord.Color.green())

@bot.tree.command(name="checkdamage", description="Check the current damage points of a user")
async def checkdamage(interaction: discord.Interaction, member: discord.Member):
    key = (interaction.guild_id, member.id)
    pts = user_damage.get(key, 0)
    embed = discord.Embed(title=f"Damage Report — {member.display_name}", description=f"**Current Damage:** {pts}/25", color=discord.Color.red() if pts >= 15 else discord.Color.blue())
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.command(name="checkdamage")
async def checkdamage_prefix(ctx, member: discord.Member = None):
    target = member or ctx.author
    key = (ctx.guild.id, target.id)
    pts = user_damage.get(key, 0)
    embed = discord.Embed(title=f"Damage Report — {target.display_name}", description=f"**Current Damage:** {pts}/25", color=discord.Color.red() if pts >= 15 else discord.Color.blue())
    embed.set_thumbnail(url=target.display_avatar.url)
    await ctx.send(embed=embed)

@bot.tree.command(name="warn", description="Warn a user and apply damage points")
@app_commands.checks.has_permissions(kick_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, points: int, reason: str = "No reason provided"):
    if member.bot:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} You cannot warn bots.", ephemeral=True)
        return

    key = (interaction.guild_id, member.id)
    user_damage[key] = user_damage.get(key, 0) + points
    current_damage = user_damage[key]

    if key not in user_warnings:
        user_warnings[key] = []

    user_warnings[key].append({"points": points, "reason": reason, "by": interaction.user.display_name})

    if current_damage >= 25:
        try:
            await member.ban(reason=f"Reached 25+ damage points (Auto-ban). Last warning: {reason}")
            await interaction.response.send_message(f"{SUCCESSFUL_SPIN} **{member.display_name}** accumulated **{current_damage}** damage points and was **automatically banned**.")
            await log_action(interaction.guild, "Auto-Ban Executed", f"**User:** {member.mention} ({member.id})\n**Reason:** Accumulated {current_damage} damage points.", discord.Color.red())
        except discord.Forbidden:
            await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} **{member.display_name}** reached **{current_damage}** damage points, but I do not have permission to ban them.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Warned **{member.display_name}** (+{points} points). Total Damage: **{current_damage}/25**.")
        await log_action(interaction.guild, "Member Warned", f"**User:** {member.mention}\n**Added Points:** {points}\n**Total Points:** {current_damage}/25\n**Reason:** {reason}\n**Moderator:** {interaction.user.mention}", discord.Color.orange())

@bot.command(name="warn")
@commands.has_permissions(kick_members=True)
async def warn_prefix(ctx, member: discord.Member, points: int, *, reason: str = "No reason provided"):
    if member.bot:
        await ctx.send(f"{UNSUCCESSFUL_SPIN} You cannot warn bots.")
        return

    key = (ctx.guild.id, member.id)
    user_damage[key] = user_damage.get(key, 0) + points
    current_damage = user_damage[key]

    if key not in user_warnings:
        user_warnings[key] = []

    user_warnings[key].append({"points": points, "reason": reason, "by": ctx.author.display_name})

    if current_damage >= 25:
        try:
            await member.ban(reason=f"Reached 25+ damage points (Auto-ban). Last warning: {reason}")
            await ctx.send(f"{SUCCESSFUL_SPIN} **{member.display_name}** accumulated **{current_damage}** damage points and was **automatically banned**.")
            await log_action(ctx.guild, "Auto-Ban Executed", f"**User:** {member.mention} ({member.id})\n**Reason:** Accumulated {current_damage} damage points.", discord.Color.red())
        except discord.Forbidden:
            await ctx.send(f"{UNSUCCESSFUL_SPIN} **{member.display_name}** reached **{current_damage}** damage points, but I do not have permission to ban them.")
    else:
        await ctx.send(f"{SUCCESSFUL_SPIN} Warned **{member.display_name}** (+{points} points). Total Damage: **{current_damage}/25**.")
        await log_action(ctx.guild, "Member Warned", f"**User:** {member.mention}\n**Added Points:** {points}\n**Total Points:** {current_damage}/25\n**Reason:** {reason}\n**Moderator:** {ctx.author.mention}", discord.Color.orange())

@bot.tree.command(name="warnings", description="View warnings and damage for a user")
async def warnings(interaction: discord.Interaction, member: discord.Member):
    key = (interaction.guild_id, member.id)
    total_points = user_damage.get(key, 0)
    warns = user_warnings.get(key, [])

    if not warns:
        await interaction.response.send_message(f"{SUCCESSFUL_SPIN} **{member.display_name}** has no recorded warnings or damage points in this server.")
        return

    embed = discord.Embed(title=f"Warnings for {member.display_name}", description=f"**Total Damage:** {total_points}/25", color=discord.Color.blue())
    for idx, w in enumerate(warns, 1):
        embed.add_field(name=f"Warning #{idx} ({w['points']} pts)", value=f"**Reason:** {w['reason']}\n**Moderator:** {w['by']}", inline=False)

    await interaction.response.send_message(embed=embed)

@bot.command(name="warnings")
async def warnings_prefix(ctx, member: discord.Member = None):
    target = member or ctx.author
    key = (ctx.guild.id, target.id)
    total_points = user_damage.get(key, 0)
    warns = user_warnings.get(key, [])

    if not warns:
        await ctx.send(f"{SUCCESSFUL_SPIN} **{target.display_name}** has no recorded warnings or damage points in this server.")
        return

    embed = discord.Embed(title=f"Warnings for {target.display_name}", description=f"**Total Damage:** {total_points}/25", color=discord.Color.blue())
    for idx, w in enumerate(warns, 1):
        embed.add_field(name=f"Warning #{idx} ({w['points']} pts)", value=f"**Reason:** {w['reason']}\n**Moderator:** {w['by']}", inline=False)

    await ctx.send(embed=embed)

@bot.tree.command(name="clearwarnings", description="Reset warnings and damage points for a user")
@app_commands.checks.has_permissions(administrator=True)
async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
    key = (interaction.guild_id, member.id)
    user_damage.pop(key, None)
    user_warnings.pop(key, None)
    await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Cleared all damage points and warnings for **{member.display_name}**.")
    await log_action(interaction.guild, "Warnings Cleared", f"**User:** {member.mention}\n**Cleared By:** {interaction.user.mention}", discord.Color.green())

@bot.command(name="clearwarnings")
@commands.has_permissions(administrator=True)
async def clearwarnings_prefix(ctx, member: discord.Member):
    key = (ctx.guild.id, member.id)
    user_damage.pop(key, None)
    user_warnings.pop(key, None)
    await ctx.send(f"{SUCCESSFUL_SPIN} Cleared all damage points and warnings for **{member.display_name}**.")
    await log_action(ctx.guild, "Warnings Cleared", f"**User:** {member.mention}\n**Cleared By:** {ctx.author.mention}", discord.Color.green())

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

@bot.command(name="addemote")
@commands.has_permissions(manage_emojis=True)
async def addemote_prefix(ctx, name: str, url: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                await ctx.send(f"{UNSUCCESSFUL_SPIN} Failed to download image from the provided URL.")
                return
            image_data = await resp.read()

    try:
        new_emoji = await ctx.guild.create_custom_emoji(name=name, image=image_data)
        await ctx.send(f"{SUCCESSFUL_SPIN} Added emoji {new_emoji} (`:{name}:`)!")
    except discord.HTTPException as e:
        await ctx.send(f"{UNSUCCESSFUL_SPIN} Failed to add emoji: {e}")

bot.run(TOKEN)