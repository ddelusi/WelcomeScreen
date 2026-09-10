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

# --- Discord Bot Setup ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

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
    # Sync slash commands globally across all joined guilds
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id}) - Active across {len(bot.guilds)} servers")

# --- Event Listeners for Dynamic Per-Server Logging, Ping Response & Unban Resets ---

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Check if the bot was pinged/mentioned
    if bot.user in message.mentions:
        greeting = random.choice(["hi", "hello", "hey", "no"])
        await message.channel.send(f"{greeting} {message.author.mention}")

    await bot.process_commands(message)

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

# --- Speak / Say Slash Commands ---

@bot.tree.command(name="say", description="Make the bot say a message in a specified channel")
@app_commands.checks.has_permissions(manage_messages=True)
async def say(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
    target_channel = channel or interaction.channel
    try:
        await target_channel.send(message)
        await interaction.response.send_message(
            f"{SUCCESSFUL_SPIN} Message sent to {target_channel.mention}!",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} I don't have permission to send messages in {target_channel.mention}.",
            ephemeral=True
        )

@bot.tree.command(name="speak", description="Make the bot say a message in the current channel")
@app_commands.checks.has_permissions(manage_messages=True)
async def speak(interaction: discord.Interaction, message: str):
    try:
        await interaction.channel.send(message)
        await interaction.response.send_message(
            f"{SUCCESSFUL_SPIN} Message sent!",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            f"{UNSUCCESSFUL_SPIN} I don't have permission to send messages here.",
            ephemeral=True
        )

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
            await interaction.response.send_message(
                f"{SUCCESSFUL_SPIN} Embed successfully sent to {target_channel.mention}!",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"{UNSUCCESSFUL_SPIN} I don't have permission to send messages in {target_channel.mention}.",
                ephemeral=True
            )

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
    duration_input = discord.ui.TextInput(
        label="Duration",
        placeholder="Ex: 10 minutes",
        required=True
    )
    winners_input = discord.ui.TextInput(
        label="Number of Winners",
        default="1",
        placeholder="1",
        required=True
    )
    prize_input = discord.ui.TextInput(
        label="Prize",
        placeholder="Enter the prize...",
        required=True
    )
    description_input = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        placeholder="Enter additional giveaway details...",
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        seconds = parse_duration(self.duration_input.value)
        if not seconds or seconds <= 0:
            await interaction.response.send_message(
                f"{UNSUCCESSFUL_SPIN} Invalid duration format! Use examples like `10 minutes`, `1 hour`, or `2 days`.",
                ephemeral=True
            )
            return

        try:
            winner_count = int(self.winners_input.value)
            if winner_count < 1:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                f"{UNSUCCESSFUL_SPIN} Number of winners must be a valid positive number.",
                ephemeral=True
            )
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

    winner_users = []
    for uid in view.entries:
        u = guild.get_member(uid)
        if u and not u.bot:
            winner_users.append(u)

    if not winner_users:
        ended_embed = discord.Embed(
            title=f"**{prize}**",
            description=(
                f"Ended: <t:{end_timestamp}:f>\n"
                f"Hosted by: {host.mention} (`@{host.name}`)\n"
                f"Entries: **0**\n"
                f"Winners: Could not determine a winner."
            ),
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
            description=(
                f"Ended: <t:{end_timestamp}:f>\n"
                f"Hosted by: {host.mention} (`@{host.name}`)\n"
                f"Entries: **{len(view.entries)}**\n"
                f"Winners: {winner_mentions}"
            ),
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        try:
            await msg.edit(embed=ended_embed, view=None)
        except discord.HTTPException:
            pass
        await msg.channel.send(content=f"Congratulations {winner_mentions}! You won **{prize}**!")

# --- Giveaway Control Slash Commands ---

@bot.tree.command(name="giveaway", description="starts a giveaway (interactive)")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway(interaction: discord.Interaction):
    await interaction.response.send_modal(GiveawayModal())

@bot.tree.command(name="giveawayend", description="Manually end an active giveaway immediately")
@app_commands.checks.has_permissions(administrator=True)
async def giveawayend(interaction: discord.Interaction, message_id: str):
    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Invalid Message ID format.", ephemeral=True)
        return

    data = active_giveaways.get(msg_id)
    if not data or not data["active"] or data.get("guild_id") != interaction.guild_id:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} No active giveaway found with that Message ID in this server.", ephemeral=True)
        return

    await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Ending giveaway now...", ephemeral=True)
    await finalize_giveaway(msg_id, interaction.guild)

@bot.tree.command(name="giveawayreroll", description="Reroll winner(s) for a giveaway")
@app_commands.checks.has_permissions(administrator=True)
async def giveawayreroll(interaction: discord.Interaction, message_id: str):
    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Invalid Message ID format.", ephemeral=True)
        return

    data = active_giveaways.get(msg_id)
    if not data or data.get("guild_id") != interaction.guild_id:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Giveaway data not found for that Message ID in this server.", ephemeral=True)
        return

    view = data["view"]
    winner_users = []
    for uid in view.entries:
        u = interaction.guild.get_member(uid)
        if u and not u.bot:
            winner_users.append(u)

    if not winner_users:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Cannot reroll: No valid entrants found.", ephemeral=True)
        return

    winner_count = data["winner_count"]
    new_winners = random.sample(winner_users, k=min(winner_count, len(winner_users)))
    winner_mentions = ", ".join([w.mention for w in new_winners])

    await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Winner(s) rerolled!", ephemeral=True)
    await interaction.channel.send(f"🎉 New winner(s) for **{data['prize']}**: {winner_mentions}!")

@bot.tree.command(name="giveawaydelete", description="Delete an active giveaway and remove its message")
@app_commands.checks.has_permissions(administrator=True)
async def giveawaydelete(interaction: discord.Interaction, message_id: str):
    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Invalid Message ID format.", ephemeral=True)
        return

    data = active_giveaways.get(msg_id)
    if not data or data.get("guild_id") != interaction.guild_id:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} Giveaway not found in this server.", ephemeral=True)
        return

    data["active"] = False
    msg = data["msg"]
    try:
        await msg.delete()
    except discord.HTTPException:
        pass

    active_giveaways.pop(msg_id, None)
    await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Giveaway deleted successfully.", ephemeral=True)

@bot.tree.command(name="giveawaylist", description="List all currently running giveaways in this server")
@app_commands.checks.has_permissions(administrator=True)
async def giveawaylist(interaction: discord.Interaction):
    active_list = [g for g in active_giveaways.values() if g["active"] and g.get("guild_id") == interaction.guild_id]

    if not active_list:
        await interaction.response.send_message(f"{SUCCESSFUL_SPIN} There are currently no active giveaways in this server.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎉 Active Giveaways",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )

    for g in active_list:
        embed.add_field(
            name=f"Prize: {g['prize']}",
            value=(
                f"**ID:** `{g['msg'].id}`\n"
                f"**Channel:** <#{g['channel_id']}>\n"
                f"**Ends:** <t:{g['end_timestamp']}:R>\n"
                f"**Entries:** {len(g['view'].entries)}"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

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

    if log_type.value == "general":
        config["log_channel"] = channel.id
        await interaction.response.send_message(f"{SUCCESSFUL_SPIN} General log channel set to {channel.mention}.")
    elif log_type.value == "msg":
        config["msg_log_channel"] = channel.id
        await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Deleted message logging channel set to {channel.mention}.")
    elif log_type.value == "media":
        config["media_log_channel"] = channel.id
        await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Media logging channel set to {channel.mention}.")

@bot.tree.command(name="poll", description="Create a simple reaction poll")
@app_commands.checks.has_permissions(manage_messages=True)
async def poll(interaction: discord.Interaction, question: str, option1: str, option2: str, option3: str = None):
    poll_embed = discord.Embed(title="📊 Poll", description=f"**{question}**", color=discord.Color.blue())
    poll_embed.add_field(name="1️⃣ Option 1", value=option1, inline=False)
    poll_embed.add_field(name="2️⃣ Option 2", value=option2, inline=False)
    if option3:
        poll_embed.add_field(name="3️⃣ Option 3", value=option3, inline=False)

    await interaction.response.send_message("Creating poll...", ephemeral=True)
    poll_msg = await interaction.channel.send(embed=poll_embed)
    await poll_msg.add_reaction("1️⃣")
    await poll_msg.add_reaction("2️⃣")
    if option3:
        await poll_msg.add_reaction("3️⃣")

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

    warn_entry = {"points": points, "reason": reason, "by": interaction.user.display_name}
    user_warnings[key].append(warn_entry)

    if current_damage >= 25:
        try:
            await member.ban(reason=f"Reached 25+ damage points (Auto-ban). Last warning: {reason}")
            await interaction.response.send_message(
                f"{SUCCESSFUL_SPIN} **{member.display_name}** accumulated **{current_damage}** damage points and was **automatically banned**."
            )
            await log_action(
                interaction.guild,
                "Auto-Ban Executed",
                f"**User:** {member.mention} ({member.id})\n**Reason:** Accumulated {current_damage} damage points.",
                discord.Color.red()
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"{UNSUCCESSFUL_SPIN} **{member.display_name}** reached **{current_damage}** damage points, but I do not have permission to ban them.",
                ephemeral=True
            )
    else:
        await interaction.response.send_message(
            f"{SUCCESSFUL_SPIN} Warned **{member.display_name}** (+{points} points). Total Damage: **{current_damage}/25**."
        )
        await log_action(
            interaction.guild,
            "Member Warned",
            f"**User:** {member.mention}\n**Added Points:** {points}\n**Total Points:** {current_damage}/25\n**Reason:** {reason}\n**Moderator:** {interaction.user.mention}",
            discord.Color.orange()
        )

@bot.tree.command(name="warnings", description="View warnings and damage for a user")
async def warnings(interaction: discord.Interaction, member: discord.Member):
    key = (interaction.guild_id, member.id)
    total_points = user_damage.get(key, 0)
    warns = user_warnings.get(key, [])

    if not warns:
        await interaction.response.send_message(
            f"{SUCCESSFUL_SPIN} **{member.display_name}** has no recorded warnings or damage points in this server.")
        return

    embed = discord.Embed(
        title=f"Warnings for {member.display_name}",
        description=f"**Total Damage:** {total_points}/25",
        color=discord.Color.blue()
    )
    for idx, w in enumerate(warns, 1):
        embed.add_field(
            name=f"Warning #{idx} ({w['points']} pts)",
            value=f"**Reason:** {w['reason']}\n**Moderator:** {w['by']}",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="clearwarnings", description="Reset warnings and damage points for a user")
@app_commands.checks.has_permissions(administrator=True)
async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
    key = (interaction.guild_id, member.id)
    user_damage.pop(key, None)
    user_warnings.pop(key, None)

    await interaction.response.send_message(
        f"{SUCCESSFUL_SPIN} Cleared all damage points and warnings for **{member.display_name}**.")
    await log_action(
        interaction.guild,
        "Warnings Cleared",
        f"**User:** {member.mention}\n**Cleared By:** {interaction.user.mention}",
        discord.Color.green()
    )

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

bot.run(TOKEN)