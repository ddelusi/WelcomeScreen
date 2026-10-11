        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Moderator: {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

    await interaction.response.send_message(embed=embed)
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

    await interaction.response.send_message(embed=embed)
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