import asyncio
import datetime
import os
import re
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv(override=True)
TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 1494367513658527865))

# Damage thresholds
DAMAGE_THRESHOLD_BAN = 25  # Reaching >25 damages triggers auto-ban
APPEAL_URL = "https://discord.gg/CzaCzedvA4"

# Custom Emojis
EMOJI_SUCCESS = "<a:SuccessfulSpin:1547127487824142346>"
EMOJI_FAILURE = "<a:UnsuccessfulSpin:1547127600693116999>"

# Color Palette: Green (Success), Yellow (Warning), Red (Failure/Error)
COLOR_SUCCESS = discord.Color.from_rgb(46, 204, 113)  # Green
COLOR_WARNING = discord.Color.from_rgb(241, 196, 15)  # Yellow
COLOR_FAILURE = discord.Color.from_rgb(231, 76, 60)  # Red

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# In-memory storage
damages = {}  # {guild_id: {user_id: count}}
afk_users = {}  # {guild_id: {user_id: {"reason": str, "nickname": str}}}
joinable_ranks = {}  # {guild_id: [role_ids]}
warnings = {}  # {guild_id: {user_id: [{"id": int, "reason": str, "moderator": str, "timestamp": str}]}}


def parse_duration(duration_str: str) -> datetime.timedelta | None:
    """Parses duration strings like '10m', '2h', '3d', '14d', '2w' into a timedelta object."""
    match = re.fullmatch(r"(\d+)([smhdw])", duration_str.lower().strip())
    if not match:
        return None

    amount, unit = int(match.group(1)), match.group(2)
    units = {
        's': datetime.timedelta(seconds=amount),
        'm': datetime.timedelta(minutes=amount),
        'h': datetime.timedelta(hours=amount),
        'd': datetime.timedelta(days=amount),
        'w': datetime.timedelta(weeks=amount)
    }
    return units.get(unit)


async def send_dm(target: discord.User, action: str, guild_name: str, reason: str, duration: str = None,
                  color=COLOR_FAILURE):
    """Helper function to DM a user with a Dyno-style notification."""
    try:
        action_text = action.lower()
        if duration:
            action_text += f" ({duration})"

        description_text = f"You were {action_text} in {guild_name} for {reason}"

        if "banned" in action_text:
            description_text += f"\n\nIf you believe this was a mistake then appeal at {APPEAL_URL}"

        embed = discord.Embed(
            description=description_text,
            color=color
        )
        embed.set_footer(text=f"Message from server: {guild_name}")

        await target.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


async def log_action(guild, title, description, color=COLOR_SUCCESS):
    channel = guild.get_channel(LOG_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            description=f"**{title}** | {description}",
            color=color,
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        await channel.send(embed=embed)


async def send_mod_embed(ctx: commands.Context, message: str, color=COLOR_SUCCESS, ephemeral: bool = False):
    """Sends a Dyno-style response embed with custom side accent color and optional ephemeral visibility."""
    embed = discord.Embed(
        description=message,
        color=color
    )
    await ctx.send(embed=embed, ephemeral=ephemeral)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot online as {bot.user}")


# Global Error Handler for Prefix & App/Slash Commands
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await send_mod_embed(ctx,
                             f"{EMOJI_FAILURE} **Permission Denied.** || You need a specific permission to run this command.",
                             COLOR_FAILURE, ephemeral=True)
    elif isinstance(error, commands.MissingRequiredArgument):
        await send_mod_embed(ctx,
                             f"{EMOJI_FAILURE} **Invalid Command Usage.** || Missing parameter: `{error.param.name}`",
                             COLOR_FAILURE, ephemeral=True)
    elif isinstance(error, commands.MemberNotFound):
        await send_mod_embed(ctx,
                             f"{EMOJI_FAILURE} **Target Not Found.** || Could not find that member in this server.",
                             COLOR_FAILURE, ephemeral=True)
    elif isinstance(error, commands.UserNotFound):
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **User Not Found.** || Could not find that user.", COLOR_FAILURE,
                             ephemeral=True)
    else:
        await send_mod_embed(ctx,
                             f"{EMOJI_FAILURE} **Command Failed.** || An error occurred while executing the command.",
                             COLOR_FAILURE, ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        embed = discord.Embed(
            description=f"{EMOJI_FAILURE} **Permission Denied.** || You need a specific permission to run this command.",
            color=COLOR_FAILURE
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


# --- AFK System Listener ---
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    guild_id = message.guild.id
    user_id = message.author.id

    if guild_id in afk_users and user_id in afk_users[guild_id]:
        afk_info = afk_users[guild_id].pop(user_id)

        try:
            if message.author.nick and message.author.nick.startswith("[AFK] "):
                original_nick = afk_info.get("nickname")
                await message.author.edit(nick=original_nick)
        except (discord.Forbidden, discord.HTTPException):
            pass

        embed = discord.Embed(
            description=f"Welcome back {message.author.mention}, I removed your AFK.",
            color=COLOR_SUCCESS
        )
        await message.channel.send(embed=embed, delete_after=6)

    if guild_id in afk_users and message.mentions:
        for mentioned in message.mentions:
            if mentioned.id in afk_users[guild_id]:
                reason = afk_users[guild_id][mentioned.id]["reason"]
                embed = discord.Embed(
                    description=f"ℹ️ **{mentioned.display_name}** is AFK: {reason}",
                    color=COLOR_WARNING
                )
                await message.channel.send(embed=embed)

    await bot.process_commands(message)


# --- Mod Logging: Message Deletion ---
@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild:
        return
    await log_action(
        message.guild,
        "Message Deleted",
        f"**Author:** {message.author.mention} || **Channel:** {message.channel.mention} || **Content:** {message.content if message.content else 'No text content'}",
        COLOR_WARNING
    )


# --- Warnings System ---

@bot.hybrid_command(name="warn", description="Warn a member.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def warn(ctx: commands.Context, target: discord.Member, reason: str = "No reason provided",
               ephemeral: bool = False):
    if target == ctx.author:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **You cannot warn yourself.**", COLOR_FAILURE, ephemeral=ephemeral)
        return
    if target.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **You cannot warn a member with an equal or higher role.**",
                             COLOR_FAILURE, ephemeral=ephemeral)
        return

    guild_id = ctx.guild.id
    user_id = target.id

    if guild_id not in warnings:
        warnings[guild_id] = {}
    if user_id not in warnings[guild_id]:
        warnings[guild_id][user_id] = []

    warn_id = len(warnings[guild_id][user_id]) + 1
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    warnings[guild_id][user_id].append({
        "id": warn_id,
        "reason": reason,
        "moderator": str(ctx.author),
        "timestamp": timestamp
    })

    await send_dm(target, "warned", ctx.guild.name, reason, color=COLOR_WARNING)
    await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **Warned {target.name}.** || Reason: {reason}", COLOR_SUCCESS,
                         ephemeral=ephemeral)
    await log_action(
        ctx.guild,
        "User Warned",
        f"**User:** {target.mention} || **Staff:** {ctx.author.mention} || **Warn ID:** #{warn_id} || **Reason:** {reason}",
        COLOR_WARNING
    )


@bot.hybrid_command(name="checkwarnings", description="View all warnings for a member.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def checkwarnings(ctx: commands.Context, target: discord.Member, ephemeral: bool = False):
    guild_id = ctx.guild.id
    user_warnings = warnings.get(guild_id, {}).get(target.id, [])

    if not user_warnings:
        await send_mod_embed(ctx, f"ℹ️ **{target.name}** has no active warnings.", COLOR_SUCCESS, ephemeral=ephemeral)
        return

    warn_list = []
    for w in user_warnings:
        warn_list.append(
            f"• **ID #{w['id']}** | **Reason:** {w['reason']} | **By:** {w['moderator']} ({w['timestamp']})")

    description = f"**Warnings for {target.mention} ({len(user_warnings)} total):**\n\n" + "\n".join(warn_list)
    await send_mod_embed(ctx, description, COLOR_WARNING, ephemeral=ephemeral)


@bot.hybrid_command(name="deletewarnings", description="Clear all warnings for a member.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def deletewarnings(ctx: commands.Context, target: discord.Member, ephemeral: bool = False):
    guild_id = ctx.guild.id
    if guild_id in warnings and target.id in warnings[guild_id] and warnings[guild_id][target.id]:
        warnings[guild_id][target.id] = []
        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **Cleared all warnings for {target.name}.**", COLOR_SUCCESS,
                             ephemeral=ephemeral)
        await log_action(
            ctx.guild,
            "Warnings Cleared",
            f"**User:** {target.mention} || **Staff:** {ctx.author.mention}",
            COLOR_SUCCESS
        )
    else:
        await send_mod_embed(ctx, f"⚠️ **{target.name} has no warnings to delete.**", COLOR_WARNING,
                             ephemeral=ephemeral)


# --- Damage Management System ---

async def apply_damage(guild, target: discord.Member, channel, amount: int, reason: str, staff_member: discord.Member):
    guild_id = guild.id
    user_id = target.id

    if guild_id not in damages:
        damages[guild_id] = {}
    damages[guild_id][user_id] = damages[guild_id].get(user_id, 0) + amount

    total = damages[guild_id][user_id]

    await send_dm(target, f"given {amount} damage(s)", guild.name, f"{reason} (Total: {total})", color=COLOR_WARNING)

    await log_action(
        guild,
        "Damage Added",
        f"**User:** {target.mention} || **Amount:** +{amount} || **Total:** {total} || **Staff:** {staff_member.mention} || **Reason:** {reason}",
        COLOR_WARNING
    )

    if total > DAMAGE_THRESHOLD_BAN:
        ban_reason = f"Exceeded damage limit (>25) with {total} damages. Last penalty reason: {reason}"

        await send_dm(target, "automatically banned", guild.name, ban_reason, color=COLOR_FAILURE)
        await target.ban(reason=ban_reason)

        embed = discord.Embed(
            description=f"🚨 **{target.name}** was automatically banned. || Total Damages: **{total}** | Reason: {reason}\nIf you believe this was a mistake then appeal at {APPEAL_URL}",
            color=COLOR_FAILURE
        )
        await channel.send(embed=embed)

        await log_action(
            guild,
            "Automated Damage Ban",
            f"**User:** {target.mention} (`{target.id}`) banned || **Total Damages:** {total} || **Reason:** {ban_reason}",
            COLOR_FAILURE
        )


@bot.hybrid_command(name="damage", description="Give damages to a member.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def damage(ctx: commands.Context, target: discord.Member, amount: int = 1, reason: str = "No reason provided",
                 ephemeral: bool = False):
    if amount <= 0:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Amount must be greater than 0.**", COLOR_FAILURE,
                             ephemeral=ephemeral)
        return
    if target == ctx.author:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **You cannot damage yourself.**", COLOR_FAILURE,
                             ephemeral=ephemeral)
        return
    if target.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **You cannot damage a member with an equal or higher role.**",
                             COLOR_FAILURE, ephemeral=ephemeral)
        return

    await apply_damage(ctx.guild, target, ctx.channel, amount, reason, ctx.author)

    total = damages.get(ctx.guild.id, {}).get(target.id, 0)
    if total <= DAMAGE_THRESHOLD_BAN:
        await send_mod_embed(
            ctx,
            f"{EMOJI_SUCCESS} **Gave {amount} damage(s) to {target.name}.** || Total Damages: **{total}/25** || Reason: {reason}",
            COLOR_SUCCESS,
            ephemeral=ephemeral
        )


@bot.hybrid_command(name="damages", description="Check the damage count of a member.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def view_damages(ctx: commands.Context, target: discord.Member, ephemeral: bool = False):
    guild_id = ctx.guild.id
    count = damages.get(guild_id, {}).get(target.id, 0)
    await send_mod_embed(ctx, f"ℹ️ **{target.name}** currently has **{count}** damage point(s).", COLOR_WARNING,
                         ephemeral=ephemeral)


@bot.hybrid_command(name="removedamage", description="Remove a specific amount of damages from a member.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def removedamage(ctx: commands.Context, target: discord.Member, amount: int = 1,
                       reason: str = "No reason provided", ephemeral: bool = False):
    if amount <= 0:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Amount must be greater than 0.**", COLOR_FAILURE,
                             ephemeral=ephemeral)
        return

    guild_id = ctx.guild.id
    current_count = damages.get(guild_id, {}).get(target.id, 0)

    if current_count == 0:
        await send_mod_embed(ctx, f"⚠️ **{target.name} has no damages to remove.**", COLOR_WARNING, ephemeral=ephemeral)
        return

    new_count = max(0, current_count - amount)
    damages[guild_id][target.id] = new_count

    await send_mod_embed(
        ctx,
        f"{EMOJI_SUCCESS} **Removed {amount} damage(s) from {target.name}.** || Remaining Damages: **{new_count}** || Reason: {reason}",
        COLOR_SUCCESS,
        ephemeral=ephemeral
    )
    await log_action(
        ctx.guild,
        "Damage Removed",
        f"**User:** {target.mention} || **Removed:** -{amount} || **Remaining:** {new_count} || **Staff:** {ctx.author.mention} || **Reason:** {reason}",
        COLOR_SUCCESS
    )


@bot.hybrid_command(name="cleardamages", description="Clear all damages for a member.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def cleardamages(ctx: commands.Context, target: discord.Member, reason: str = "No reason provided",
                       ephemeral: bool = False):
    guild_id = ctx.guild.id
    if guild_id in damages and target.id in damages[guild_id]:
        damages[guild_id][target.id] = 0
        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **Cleared all damages for {target.name}.** || Reason: {reason}",
                             COLOR_SUCCESS, ephemeral=ephemeral)
        await log_action(
            ctx.guild,
            "Damages Cleared",
            f"**User:** {target.mention} || **Staff:** {ctx.author.mention} || **Reason:** {reason}",
            COLOR_SUCCESS
        )
    else:
        await send_mod_embed(ctx, f"⚠️ **{target.name} has no damages to clear.**", COLOR_WARNING, ephemeral=ephemeral)


# --- Dyno Server Management Commands ---

@bot.hybrid_command(name="addemote", description="Adds an emote to the server.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(manage_emojis=True)
@app_commands.default_permissions(manage_emojis=True)
async def addemote(ctx: commands.Context, name: str, url: str, ephemeral: bool = False):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Failed to download image from URL.**", COLOR_FAILURE,
                                     ephemeral=ephemeral)
                return
            image_bytes = await response.read()

    try:
        emoji = await ctx.guild.create_custom_emoji(name=name, image=image_bytes)
        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **Added emote {emoji} (`:{name}:`) to the server.**", COLOR_SUCCESS,
                             ephemeral=ephemeral)
        await log_action(ctx.guild, "Emote Added", f"**Name:** {name} || **Staff:** {ctx.author.mention}",
                         COLOR_SUCCESS)
    except Exception as e:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Failed to add emote:** {e}", COLOR_FAILURE, ephemeral=ephemeral)


@bot.hybrid_command(name="addrole", description="Add a new role with optional color and hoist.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(manage_roles=True)
@app_commands.default_permissions(manage_roles=True)
async def addrole(ctx: commands.Context, name: str, color_hex: str = "#99AAB5", hoist: bool = False,
                  ephemeral: bool = False):
    try:
        clean_hex = color_hex.lstrip("#")
        color = discord.Color(int(clean_hex, 16))
        role = await ctx.guild.create_role(name=name, color=color, hoist=hoist)
        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **Created role {role.mention}.**", COLOR_SUCCESS,
                             ephemeral=ephemeral)
        await log_action(ctx.guild, "Role Created", f"**Role:** {role.name} || **Staff:** {ctx.author.mention}",
                         COLOR_SUCCESS)
    except ValueError:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Invalid Hex Color.** Example: `#3498DB`", COLOR_FAILURE,
                             ephemeral=ephemeral)


@bot.hybrid_command(name="addrank", description="Add a new rank for members to join, role must exist.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(manage_roles=True)
@app_commands.default_permissions(manage_roles=True)
async def addrank(ctx: commands.Context, role: discord.Role, ephemeral: bool = False):
    guild_id = ctx.guild.id
    if guild_id not in joinable_ranks:
        joinable_ranks[guild_id] = []

    if role.id in joinable_ranks[guild_id]:
        await send_mod_embed(ctx, f"⚠️ **{role.name} is already a joinable rank.**", COLOR_WARNING, ephemeral=ephemeral)
        return

    joinable_ranks[guild_id].append(role.id)
    await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **Added {role.name} as a joinable rank.**", COLOR_SUCCESS,
                         ephemeral=ephemeral)


# --- Say & Communication Commands ---

@bot.hybrid_command(name="say", description="Make the bot send a message to the channel.")
@app_commands.describe(
    message="The text for the bot to send.",
    channel="The target channel (optional, defaults to current channel).",
    ephemeral="Whether the command output confirmation should be visible only to you."
)
@commands.has_permissions(manage_messages=True)
@app_commands.default_permissions(manage_messages=True)
async def say(ctx: commands.Context, message: str, channel: discord.TextChannel = None, ephemeral: bool = False):
    target_channel = channel or ctx.channel

    if ctx.interaction is None and target_channel == ctx.channel:
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

    await target_channel.send(message)

    if ctx.interaction or target_channel != ctx.channel:
        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **Message sent to {target_channel.mention}.**", COLOR_SUCCESS,
                             ephemeral=True if ctx.interaction else ephemeral)

    await log_action(
        ctx.guild,
        "Bot Announcement (Say)",
        f"**Staff:** {ctx.author.mention} || **Channel:** {target_channel.mention} || **Content:** {message}",
        COLOR_SUCCESS
    )


# --- Utility & Server Info Commands ---

@bot.hybrid_command(name="membercount", description="Displays the total member count of the server.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
async def membercount(ctx: commands.Context, ephemeral: bool = False):
    total_members = ctx.guild.member_count
    humans = sum(1 for member in ctx.guild.members if not member.bot)
    bots = sum(1 for member in ctx.guild.members if member.bot)

    description = f"**Total Members:** {total_members}\n**Humans:** {humans}\n**Bots:** {bots}"
    await send_mod_embed(ctx, description, COLOR_SUCCESS, ephemeral=ephemeral)


@bot.hybrid_command(name="members", description="Lists members who have a specific role.")
@app_commands.describe(role="The role to search members for.",
                       ephemeral="Whether the command output should be visible only to you.")
async def members(ctx: commands.Context, role: discord.Role, ephemeral: bool = False):
    role_members = role.members

    if not role_members:
        await send_mod_embed(ctx, f"⚠️ **No members found with the role {role.mention}.**", COLOR_WARNING,
                             ephemeral=ephemeral)
        return

    member_list = "\n".join([f"• {member.mention} (`{member.id}`)" for member in role_members[:20]])

    if len(role_members) > 20:
        member_list += f"\n\n*...and {len(role_members) - 20} more.*"

    description = f"**Members with role {role.mention} ({len(role_members)} total):**\n\n{member_list}"
    await send_mod_embed(ctx, description, COLOR_SUCCESS, ephemeral=ephemeral)


@bot.hybrid_command(name="serverinfo", description="Displays detailed information about the server.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
async def serverinfo(ctx: commands.Context, ephemeral: bool = False):
    guild = ctx.guild

    total_members = guild.member_count
    humans = sum(1 for m in guild.members if not m.bot)
    bots = sum(1 for m in guild.members if m.bot)

    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    categories = len(guild.categories)

    created_at = guild.created_at.strftime("%B %d, %Y")

    embed = discord.Embed(
        title=f"Server Information - {guild.name}",
        color=COLOR_SUCCESS,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
    embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="Created On", value=created_at, inline=True)

    embed.add_field(
        name="Members",
        value=f"• Total: **{total_members}**\n• Humans: **{humans}**\n• Bots: **{bots}**",
        inline=True
    )
    embed.add_field(
        name="Channels",
        value=f"• Text: **{text_channels}**\n• Voice: **{voice_channels}**\n• Categories: **{categories}**",
        inline=True
    )
    embed.add_field(
        name="Features & Assets",
        value=f"• Roles: **{len(guild.roles)}**\n• Emojis: **{len(guild.emojis)}**\n• Boost Level: **Level {guild.premium_tier}** ({guild.premium_subscription_count} boosts)",
        inline=True
    )

    if guild.banner:
        embed.set_image(url=guild.banner.url)

    await ctx.send(embed=embed, ephemeral=ephemeral)


@bot.hybrid_command(name="servericon", description="Displays the server's icon.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
async def servericon(ctx: commands.Context, ephemeral: bool = False):
    if not ctx.guild.icon:
        await send_mod_embed(ctx, "⚠️ **This server does not have an icon.**", COLOR_WARNING, ephemeral=ephemeral)
        return

    embed = discord.Embed(
        title=f"{ctx.guild.name}'s Icon",
        color=COLOR_SUCCESS
    )
    embed.set_image(url=ctx.guild.icon.url)
    await ctx.send(embed=embed, ephemeral=ephemeral)


@bot.hybrid_command(name="serverbanner", description="Displays the server's banner if available.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
async def serverbanner(ctx: commands.Context, ephemeral: bool = False):
    if not ctx.guild.banner:
        await send_mod_embed(ctx, "⚠️ **This server does not have a banner.**", COLOR_WARNING, ephemeral=ephemeral)
        return

    embed = discord.Embed(
        title=f"{ctx.guild.name}'s Banner",
        color=COLOR_SUCCESS
    )
    embed.set_image(url=ctx.guild.banner.url)
    await ctx.send(embed=embed, ephemeral=ephemeral)


# --- AFK Command Suite ---

@bot.hybrid_command(name="afk", description="Set an AFK status.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
async def afk(ctx: commands.Context, reason: str = "AFK", ephemeral: bool = False):
    guild_id = ctx.guild.id
    if guild_id not in afk_users:
        afk_users[guild_id] = {}

    current_nick = ctx.author.nick or ctx.author.name
    afk_users[guild_id][ctx.author.id] = {
        "reason": reason,
        "nickname": ctx.author.nick
    }

    try:
        await ctx.author.edit(nick=f"[AFK] {current_nick}"[:32])
    except (discord.Forbidden, discord.HTTPException):
        pass

    await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **I set your AFK:** {reason}", COLOR_SUCCESS, ephemeral=ephemeral)


@bot.hybrid_group(name="afkmod", description="AFK Management tools for moderators.")
@commands.has_permissions(manage_nicknames=True)
@app_commands.default_permissions(manage_nicknames=True)
async def afkmod(ctx: commands.Context):
    pass


@afkmod.command(name="clear", description="Remove the AFK status of a member.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(manage_nicknames=True)
@app_commands.default_permissions(manage_nicknames=True)
async def afkmod_clear(ctx: commands.Context, target: discord.Member, ephemeral: bool = False):
    guild_id = ctx.guild.id
    if guild_id in afk_users and target.id in afk_users[guild_id]:
        afk_info = afk_users[guild_id].pop(target.id)
        try:
            await target.edit(nick=afk_info.get("nickname"))
        except (discord.Forbidden, discord.HTTPException):
            pass
        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **Cleared AFK status for {target.mention}.**", COLOR_SUCCESS,
                             ephemeral=ephemeral)
    else:
        await send_mod_embed(ctx, f"⚠️ **{target.name} is not currently AFK.**", COLOR_WARNING, ephemeral=ephemeral)


@afkmod.command(name="clearall", description="Remove the AFK status of all members.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(manage_nicknames=True)
@app_commands.default_permissions(manage_nicknames=True)
async def afkmod_clearall(ctx: commands.Context, ephemeral: bool = False):
    guild_id = ctx.guild.id
    if guild_id in afk_users and afk_users[guild_id]:
        count = len(afk_users[guild_id])
        afk_users[guild_id].clear()
        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **Cleared AFK status for all {count} members.**", COLOR_SUCCESS,
                             ephemeral=ephemeral)
    else:
        await send_mod_embed(ctx, "⚠️ **No members are currently AFK.**", COLOR_WARNING, ephemeral=ephemeral)


# --- Hybrid Moderation Commands ---

@bot.hybrid_command(name="mute", description="Mute/Timeout a member for a specified duration (e.g. 10m, 3d, 14d, 2w).")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def mute(ctx: commands.Context, target: discord.Member, duration: str = "1h", reason: str = "No reason provided",
               ephemeral: bool = False):
    if target == ctx.author:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **You cannot mute yourself.**", COLOR_FAILURE, ephemeral=ephemeral)
        return
    if target.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **You cannot mute a member with an equal or higher role.**",
                             COLOR_FAILURE, ephemeral=ephemeral)
        return
    if target.top_role >= ctx.guild.me.top_role:
        await send_mod_embed(ctx,
                             f"{EMOJI_FAILURE} **Bot cannot mute this user.** My highest role is lower than or equal to theirs.",
                             COLOR_FAILURE, ephemeral=ephemeral)
        return

    time_delta = parse_duration(duration)
    if not time_delta:
        await send_mod_embed(ctx,
                             f"{EMOJI_FAILURE} **Invalid time format.** Use formats like `10m`, `1h`, `3d`, `14d`, or `2w`.",
                             COLOR_FAILURE, ephemeral=ephemeral)
        return

    if time_delta > datetime.timedelta(days=28):
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Duration exceeds Discord's max limit of 28 days.**",
                             COLOR_FAILURE, ephemeral=ephemeral)
        return

    try:
        await send_dm(target, "muted", ctx.guild.name, reason, duration, color=COLOR_FAILURE)
        await target.timeout(time_delta, reason=reason)

        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **{target.name} has been muted for {duration}.** || {reason}",
                             COLOR_SUCCESS, ephemeral=ephemeral)
        await log_action(
            ctx.guild,
            "User Muted",
            f"**User:** {target.mention} || **Duration:** {duration} || **Staff:** {ctx.author.mention} || **Reason:** {reason}",
            COLOR_WARNING
        )
    except discord.Forbidden:
        await send_mod_embed(ctx,
                             f"{EMOJI_FAILURE} **Bot lacks 'Moderate Members' permission or role rank to mute this user.**",
                             COLOR_FAILURE, ephemeral=ephemeral)
    except Exception as e:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Failed to mute user:** `{e}`", COLOR_FAILURE, ephemeral=ephemeral)


@bot.hybrid_command(name="unmute", description="Unmute/Remove timeout from a member.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def unmute(ctx: commands.Context, target: discord.Member, reason: str = "No reason provided",
                 ephemeral: bool = False):
    if target.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **You cannot unmute a member with an equal or higher role.**",
                             COLOR_FAILURE, ephemeral=ephemeral)
        return

    try:
        await target.timeout(None, reason=reason)
        await send_dm(target, "unmuted", ctx.guild.name, reason, color=COLOR_SUCCESS)

        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **{target.name} has been unmuted.** || {reason}", COLOR_SUCCESS,
                             ephemeral=ephemeral)
        await log_action(
            ctx.guild,
            "User Unmuted",
            f"**User:** {target.mention} || **Staff:** {ctx.author.mention} || **Reason:** {reason}",
            COLOR_SUCCESS
        )
    except discord.Forbidden:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Bot lacks permission to unmute this user.**", COLOR_FAILURE,
                             ephemeral=ephemeral)
    except Exception as e:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Failed to unmute user:** {e}", COLOR_FAILURE, ephemeral=ephemeral)


@bot.hybrid_command(name="timeout", description="Timeout a member for a set duration (e.g. 10m, 3d, 14d, 2w).")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def timeout(ctx: commands.Context, target: discord.Member, duration: str = "1h",
                  reason: str = "No reason provided", ephemeral: bool = False):
    time_delta = parse_duration(duration)
    if not time_delta:
        await send_mod_embed(ctx,
                             f"{EMOJI_FAILURE} **Invalid time format.** Use formats like `10m`, `1h`, `3d`, `14d`, or `2w`.",
                             COLOR_FAILURE, ephemeral=ephemeral)
        return

    if time_delta > datetime.timedelta(days=28):
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Duration exceeds Discord's max limit of 28 days.**",
                             COLOR_FAILURE, ephemeral=ephemeral)
        return

    try:
        await send_dm(target, "timed out", ctx.guild.name, reason, duration, color=COLOR_FAILURE)
        await target.timeout(time_delta, reason=reason)
        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **{target.name} has been timed out for {duration}.** || {reason}",
                             COLOR_SUCCESS, ephemeral=ephemeral)
        await log_action(
            ctx.guild,
            "User Timed Out",
            f"**User:** {target.mention} || **Duration:** {duration} || **Staff:** {ctx.author.mention} || **Reason:** {reason}",
            COLOR_WARNING
        )
    except discord.Forbidden:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Bot lacks permission to timeout this user.**", COLOR_FAILURE,
                             ephemeral=ephemeral)
    except Exception as e:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Failed to timeout user:** {e}", COLOR_FAILURE,
                             ephemeral=ephemeral)


@bot.hybrid_command(name="untimeout", description="Remove a timeout from a member.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(moderate_members=True)
@app_commands.default_permissions(moderate_members=True)
async def untimeout(ctx: commands.Context, target: discord.Member, reason: str = "No reason provided",
                    ephemeral: bool = False):
    try:
        await target.timeout(None, reason=reason)
        await send_dm(target, "un-timed out", ctx.guild.name, reason, color=COLOR_SUCCESS)
        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **Removed timeout from {target.name}.** || {reason}", COLOR_SUCCESS,
                             ephemeral=ephemeral)
        await log_action(
            ctx.guild,
            "User Timeout Removed",
            f"**User:** {target.mention} || **Staff:** {ctx.author.mention} || **Reason:** {reason}",
            COLOR_SUCCESS
        )
    except discord.Forbidden:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Bot lacks permission to remove timeout from this user.**",
                             COLOR_FAILURE, ephemeral=ephemeral)
    except Exception as e:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Failed to remove timeout:** {e}", COLOR_FAILURE,
                             ephemeral=ephemeral)


@bot.hybrid_command(name="kick", description="Kick a member from the server.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(kick_members=True)
@app_commands.default_permissions(kick_members=True)
async def kick(ctx: commands.Context, target: discord.Member, reason: str = "No reason provided",
               ephemeral: bool = False):
    await send_dm(target, "kicked", ctx.guild.name, reason, color=COLOR_FAILURE)
    await target.kick(reason=reason)
    await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **{target.name} has been kicked.** || {reason}", COLOR_SUCCESS,
                         ephemeral=ephemeral)
    await log_action(
        ctx.guild,
        "User Kicked",
        f"**User:** {target.mention} || **Staff:** {ctx.author.mention} || **Reason:** {reason}",
        COLOR_FAILURE
    )


@bot.hybrid_command(name="softban", description="Ban and immediately unban to clear recent messages.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(ban_members=True)
@app_commands.default_permissions(ban_members=True)
async def softban(ctx: commands.Context, target: discord.Member, reason: str = "No reason provided",
                  ephemeral: bool = False):
    await send_dm(target, "softbanned", ctx.guild.name, reason, color=COLOR_FAILURE)
    await target.ban(reason=f"Softban: {reason}", delete_message_days=1)
    await ctx.guild.unban(target, reason="Softban cleanup")
    await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **{target.name} has been softbanned.** || {reason}", COLOR_SUCCESS,
                         ephemeral=ephemeral)
    await log_action(
        ctx.guild,
        "User Softbanned",
        f"**User:** {target.mention} || **Staff:** {ctx.author.mention} || **Reason:** {reason}",
        COLOR_FAILURE
    )


@bot.hybrid_command(name="ban", description="Permanently ban a member from the server.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(ban_members=True)
@app_commands.default_permissions(ban_members=True)
async def ban(ctx: commands.Context, target: discord.User, reason: str = "No reason provided", ephemeral: bool = False):
    try:
        await send_dm(target, "banned", ctx.guild.name, reason, color=COLOR_FAILURE)
        await ctx.guild.ban(target, reason=reason)
        await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **{target.name} has been banned.** || {reason}", COLOR_SUCCESS,
                             ephemeral=ephemeral)
        await log_action(
            ctx.guild,
            "User Banned",
            f"**User:** {target.mention} || **Staff:** {ctx.author.mention} || **Reason:** {reason}",
            COLOR_FAILURE
        )
    except discord.Forbidden:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Bot lacks permission to ban this user.**", COLOR_FAILURE,
                             ephemeral=ephemeral)
    except Exception as e:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Failed to ban user:** {e}", COLOR_FAILURE, ephemeral=ephemeral)


@bot.hybrid_command(name="unban", description="Unban a user using their Username or User ID.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(ban_members=True)
@app_commands.default_permissions(ban_members=True)
async def unban(ctx: commands.Context, target: str, reason: str = "No reason provided", ephemeral: bool = False):
    try:
        async for ban_entry in ctx.guild.bans():
            user = ban_entry.user
            if str(user.id) == target or f"{user.name}#{user.discriminator}" == target or user.name == target:
                await ctx.guild.unban(user, reason=reason)
                await send_mod_embed(ctx, f"{EMOJI_SUCCESS} **{user.name} has been unbanned.** || {reason}",
                                     COLOR_SUCCESS, ephemeral=ephemeral)
                await log_action(
                    ctx.guild,
                    "User Unbanned",
                    f"**User:** {user.mention} (`{user.id}`) || **Staff:** {ctx.author.mention} || **Reason:** {reason}",
                    COLOR_SUCCESS
                )
                return

        await send_mod_embed(ctx, f"⚠️ **User `{target}` was not found in the ban list.**", COLOR_WARNING,
                             ephemeral=ephemeral)
    except discord.Forbidden:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Bot lacks permission to unban users.**", COLOR_FAILURE,
                             ephemeral=ephemeral)
    except Exception as e:
        await send_mod_embed(ctx, f"{EMOJI_FAILURE} **Failed to unban user:** {e}", COLOR_FAILURE, ephemeral=ephemeral)


# --- Channel Controls ---

@bot.hybrid_command(name="lock", description="Lock the current channel for @everyone.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def lock(ctx: commands.Context, ephemeral: bool = False):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await send_mod_embed(ctx, f"{EMOJI_SUCCESS} 🔒 **Channel locked.**", COLOR_SUCCESS, ephemeral=ephemeral)
    await log_action(
        ctx.guild,
        "Channel Locked",
        f"**Channel:** {ctx.channel.mention} || **Staff:** {ctx.author.mention}",
        COLOR_WARNING
    )


@bot.hybrid_command(name="unlock", description="Unlock the current channel for @everyone.")
@app_commands.describe(ephemeral="Whether the command output should be visible only to you.")
@commands.has_permissions(manage_channels=True)
@app_commands.default_permissions(manage_channels=True)
async def unlock(ctx: commands.Context, ephemeral: bool = False):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = True
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await send_mod_embed(ctx, f"{EMOJI_SUCCESS} 🔓 **Channel unlocked.**", COLOR_SUCCESS, ephemeral=ephemeral)
    await log_action(
        ctx.guild,
        "Channel Unlocked",
        f"**Channel:** {ctx.channel.mention} || **Staff:** {ctx.author.mention}",
        COLOR_SUCCESS
    )


bot.run(TOKEN)