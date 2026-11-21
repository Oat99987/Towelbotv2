import random
import re

import discord
from discord.ext import commands
from common import embed, status_embed, admin, audit


DICE_PATTERN = re.compile(r"(?:(\d{1,3}))?d(\d{1,4})", re.IGNORECASE)


async def resolve_command_prefix(bot, message):
    if message.guild is not None:
        prefix = await bot.db.get_config(message.guild.id, "core", "prefix")
        if prefix:
            return commands.when_mentioned_or(prefix)(bot, message)
    return commands.when_mentioned_or(bot.config.get("prefix", "!"))(bot, message)


def parse_dice_notation(notation):
    match = DICE_PATTERN.fullmatch(notation.strip())
    if match is None:
        raise ValueError("Use dice notation such as `d20` or `2d6`.")
    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    if not 1 <= count <= 100 or not 1 <= sides <= 1000:
        raise ValueError("Roll between 1 and 100 dice, with 1 to 1,000 sides each.")
    return count, sides


def parse_choices(options):
    if len(options) > 1000:
        raise ValueError("Keep the full choice list under 1,000 characters.")
    choices = [choice.strip() for choice in options.split("|") if choice.strip()]
    if not 2 <= len(choices) <= 20:
        raise ValueError("Provide 2 to 20 choices separated by `|`.")
    return choices


class Core(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @discord.app_commands.command(name="ping", description="Check TowelBot latency.")
    async def ping_slash(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=status_embed("TowelBot is online", f"Gateway latency: **{round(self.bot.latency * 1000)} ms**","success"))

    @discord.app_commands.command(name="botinfo", description="Show TowelBot information.")
    async def botinfo(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=embed(
            "TowelBot",
            f"Servers: `{len(self.bot.guilds)}`\nLatency: `{round(self.bot.latency * 1000)} ms`\nLibrary: `discord.py`",
        ))

    @discord.app_commands.command(name="serverinfo", description="Show information about this server.")
    @discord.app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("Use this command inside a server.")
        await interaction.response.send_message(embed=embed(
            guild.name,
            f"Members: `{guild.member_count}`\nRoles: `{len(guild.roles)}`\nChannels: `{len(guild.channels)}`",
        ).set_thumbnail(url=guild.icon.url if guild.icon else None))

    @discord.app_commands.command(name="avatar", description="View a member's Discord avatar.")
    async def avatar(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        await interaction.response.send_message(embed=embed(f"{target.display_name}'s avatar").set_image(url=target.display_avatar.url))

    @commands.hybrid_command(name="userinfo", description="Show a member's profile and server roles.")
    @commands.guild_only()
    async def userinfo(self, ctx, member: discord.Member | None = None):
        target = member or ctx.author
        if not isinstance(target, discord.Member):
            return await ctx.send(embed=status_embed("Member unavailable", "Use this command inside a server.", "error"))
        roles = [role.mention for role in target.roles if not role.is_default()]
        card = embed(f"{target.display_name}'s profile")
        card.add_field(name="User", value=f"{target.mention}\n`{target.id}`", inline=True)
        card.add_field(name="Account", value="Bot" if target.bot else "Member", inline=True)
        card.add_field(name="Top role", value=target.top_role.mention, inline=True)
        card.add_field(name="Roles", value=", ".join(roles[:15]) or "No assigned roles", inline=False)
        if len(roles) > 15:
            card.set_footer(text=f"Showing 15 of {len(roles)} roles")
        card.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=card, allowed_mentions=discord.AllowedMentions.none())

    @commands.hybrid_command(name="roleinfo", description="Show details about a server role.")
    @commands.guild_only()
    async def roleinfo(self, ctx, role: discord.Role):
        permissions = [
            permission.replace("_", " ").title()
            for permission, enabled in role.permissions
            if enabled
        ]
        card = embed(f"Role: {role.name}")
        card.add_field(name="Role ID", value=f"`{role.id}`", inline=True)
        card.add_field(name="Members", value=f"`{len(role.members)}`", inline=True)
        card.add_field(name="Color", value=f"`#{role.color.value:06X}`", inline=True)
        card.add_field(
            name="Settings",
            value=f"Hoisted: **{'Yes' if role.hoist else 'No'}** · "
                  f"Mentionable: **{'Yes' if role.mentionable else 'No'}**",
            inline=False,
        )
        card.add_field(name="Permissions", value=", ".join(permissions[:20]) or "No special permissions", inline=False)
        await ctx.send(embed=card, allowed_mentions=discord.AllowedMentions.none())

    @commands.hybrid_command(name="choose", description="Randomly choose from 2–20 options separated by |.")
    async def choose(self, ctx, *, options: str):
        try:
            choices = parse_choices(options)
        except ValueError as error:
            return await ctx.send(embed=status_embed("Invalid choices", str(error), "error"))
        selection = random.SystemRandom().choice(choices)
        await ctx.send(
            embed=status_embed("The choice is", selection),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.hybrid_command(name="roll", description="Roll dice such as d20 or 2d6.")
    async def roll(self, ctx, dice: str = "d20"):
        try:
            count, sides = parse_dice_notation(dice)
        except ValueError as error:
            return await ctx.send(embed=status_embed("Invalid dice", str(error), "error"))
        results = [random.SystemRandom().randint(1, sides) for _ in range(count)]
        details = ", ".join(str(result) for result in results)
        await ctx.send(
            embed=status_embed(f"Roll: {count}d{sides}", f"**Total: {sum(results)}**\nRolls: {details}"),
        )

    @commands.hybrid_command(name="coinflip", description="Flip a virtual coin.")
    async def coinflip(self, ctx):
        result = random.SystemRandom().choice(("Heads", "Tails"))
        await ctx.send(embed=status_embed("Coin flip", f"**{result}**"))

    @commands.hybrid_command(name="help", description="Show available command groups.")
    async def help(self, ctx):
        text = (
            "**Utilities:** `ping`, `botinfo`, `serverinfo`, `userinfo`, `roleinfo`, `avatar`, `choose`, `roll`, `coinflip`\n"
            "**Border:** `border-panel`, `border-question-add`, `border-question-remove`, `border-question-list`\n"
            "**Moderation:** `ban`, `unban`, `kick`, `timeout`, `untimeout`, `warn`, `cases`, `purge`, `slowmode`, `lockdown`, `unlockdown`, `hardmute`, `jail`, `release`\n"
            "**Elections:** `election-create`, `candidate-add`, `election-open`, `election-results`, `election-close`\n"
            "**Community:** `welcome-set`, `level`, `level-adjust`, `custom-add`, `custom-remove`\n"
            "**Other:** `role-panel`, `remind`, `poll-create`, `ticket-panel`, `voice-lobby-set`, `embed-post`, `stats`"
        )
        await ctx.send(embed=embed("TowelBot Commands", text))

    @commands.hybrid_command(name="set-prefix", description="Change the command prefix.")
    @commands.guild_only()
    @admin()
    async def set_prefix(self, ctx, prefix: str):
        if ctx.guild is None:
            return await ctx.send(embed=embed("Command error", "Set the prefix inside a server.", "error"))
        if not 1 <= len(prefix) <= 5 or any(character.isspace() for character in prefix):
            return await ctx.send(embed=embed("Command error", "Prefix must be 1–5 non-space characters.", "error"))
        await self.bot.db.set_config(ctx.guild.id, "core", "prefix", prefix)
        await audit(self.bot, ctx.guild, ctx.author, "Prefix changed", details=prefix)
        await ctx.send(embed=embed("Prefix updated", f"This server's prefix is now `{prefix}`.", "success"))

    @commands.hybrid_command(name="health", description="Show bot and database health.")
    @admin()
    async def health(self, ctx):
        await ctx.send(embed=embed("Bot health",
            f"Latency: `{round(self.bot.latency * 1000)} ms`\nDatabase: `connected`"))

async def setup(bot):
    await bot.add_cog(Core(bot))
