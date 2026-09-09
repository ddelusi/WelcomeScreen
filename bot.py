import os
import threading
import time
import asyncio
import random
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
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', '1494367513658527865'))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Custom Emojis
SUCCESSFUL_SPIN = "<a:SuccessfulSpin:1547127487824142346>"
UNSUCCESSFUL_SPIN = "<a:UnsuccessfulSpin:1547127600693116999>"

# Configuration Storage
user_damage = {}
user_warnings = {}
logging_config = {
    "msg_log_channel": None,
    "media_log_channel": None
}

async def log_action(guild: discord.Guild, title: str, description: str, color: discord.Color):
    channel = guild.get_channel(LOG_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())
        await channel.send(embed=embed)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

# --- Event Listeners for Custom Logging ---

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # Message Deletion Logging
    if logging_config["msg_log_channel"]:
        log_chan = message.guild.get_channel(logging_config["msg_log_channel"])
        if log_chan and message.content:
            embed = discord.Embed(
                title="Message Deleted",
                description=f"**Author:** {message.author.mention}\n**Channel:** {message.channel.mention}\n\n**Content:**\n{message.content}",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            await log_chan.send(embed=embed)

    # Image/GIF Deletion or Posting Log
    if logging_config["media_log_channel"]:
        media_chan = message.guild.get_channel(logging_config["media_log_channel"])
        if media_chan:
            # Check attachments or embedded links (GIFs/Images)
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

# --- Moderation & Utility Commands ---

@bot.tree.command(name="setlogchannel", description="Set dynamic logging channels for messages or media")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(log_type=[
    app_commands.Choice(name="Message Logs (Deleted Messages)", value="msg"),
    app_commands.Choice(name="Media Logs (Images/GIFs)", value="media")
])
async def setlogchannel(interaction: discord.Interaction, log_type: app_commands.Choice[str], channel: discord.TextChannel):
    if log_type.value == "msg":
        logging_config["msg_log_channel"] = channel.id
        await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Deleted message logging channel set to {channel.mention}.")
    elif log_type.value == "media":
        logging_config["media_log_channel"] = channel.id
        await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Media logging channel set to {channel.mention}.")

@bot.tree.command(name="embed", description="Create and send a custom embed")
@app_commands.checks.has_permissions(manage_messages=True)
async def embed(interaction: discord.Interaction, title: str, description: str, color_hex: str = "3498db", image_url: str = None):
    try:
        color_int = int(color_hex.lstrip('#'), 16)
    except ValueError:
        color_int = 0x3498db

    embed_obj = discord.Embed(title=title, description=description, color=discord.Color(color_int))
    if image_url:
        embed_obj.set_image(url=image_url)

    await interaction.response.send_message(f"{SUCCESSFUL_SPIN} Embed created!", ephemeral=True)
    await interaction.channel.send(embed=embed_obj)

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

@bot.tree.command(name="giveaway", description="Start a quick giveaway with reaction entry")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway(interaction: discord.Interaction, prize: str, duration_seconds: int):
    embed = discord.Embed(
        title="🎉 Giveaway!",
        description=f"**Prize:** {prize}\n**React with 🎉 to enter!**\n**Time:** {duration_seconds} seconds",
        color=discord.Color.purple()
    )
    await interaction.response.send_message("Starting giveaway...", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    await msg.add_reaction("🎉")

    await asyncio.sleep(duration_seconds)

    # Fetch updated message to get reactions
    msg = await interaction.channel.fetch_message(msg.id)
    reaction = discord.utils.get(msg.reactions, emoji="🎉")
    users = [user async for user in reaction.users() if not user.bot]

    if not users:
        await interaction.channel.send(f"Giveaway for **{prize}** ended, but nobody entered!")
    else:
        winner = random.choice(users)
        await interaction.channel.send(f"🎉 Congratulations {winner.mention}, you won **{prize}**!")

# --- Core Moderation Commands ---

@bot.tree.command(name="warn", description="Warn a user and apply damage points")
@app_commands.checks.has_permissions(kick_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, points: int, reason: str = "No reason provided"):
    if member.bot:
        await interaction.response.send_message(f"{UNSUCCESSFUL_SPIN} You cannot warn bots.", ephemeral=True)
        return

    user_damage[member.id] = user_damage.get(member.id, 0) + points
    current_damage = user_damage[member.id]

    if member.id not in user_warnings:
        user_warnings[member.id] = []

    warn_entry = {"points": points, "reason": reason, "by": interaction.user.display_name}
    user_warnings[member.id].append(warn_entry)

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
    total_points = user_damage.get(member.id, 0)
    warns = user_warnings.get(member.id, [])

    if not warns:
        await interaction.response.send_message(
            f"{SUCCESSFUL_SPIN} **{member.display_name}** has no recorded warnings or damage points.")
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
    user_damage.pop(member.id, None)
    user_warnings.pop(member.id, None)

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