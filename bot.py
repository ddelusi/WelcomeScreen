import os
import threading
import time
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
            # Replace with your actual Render URL after deploying
            requests.get("https://YOUR-APP-NAME.onrender.com")
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

# Damage / Warning Storage
user_damage = {}
user_warnings = {}


async def log_action(guild: discord.Guild, title: str, description: str, color: discord.Color):
    channel = guild.get_channel(LOG_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())
        await channel.send(embed=embed)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


# --- Moderation & Utility Commands ---

@bot.tree.command(name="warn", description="Warn a user and apply damage points")
@app_commands.checks.has_permissions(kick_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, points: int,
               reason: str = "No reason provided"):
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