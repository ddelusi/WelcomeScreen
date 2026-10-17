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