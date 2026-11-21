import discord
from discord.ext import commands
from common import embed, status_embed, admin, audit

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

    @commands.hybrid_command(name="help", description="Show available command groups.")
    async def help(self, ctx):
        text = (
            "**Border:** `border-panel`, `border-question-add`, `border-question-remove`, `border-question-list`\n"
            "**Moderation:** `ban`, `unban`, `kick`, `timeout`, `untimeout`, `warn`, `cases`, `purge`, `slowmode`, `lockdown`, `unlockdown`, `hardmute`, `jail`, `release`\n"
            "**Elections:** `election-create`, `candidate-add`, `election-open`, `election-results`, `election-close`\n"
            "**Community:** `welcome-set`, `level`, `level-adjust`, `custom-add`, `custom-remove`\n"
            "**Other:** `role-panel`, `remind`, `poll-create`, `ticket-panel`, `voice-lobby-set`, `embed-post`, `stats`"
        )
        await ctx.send(embed=embed("TowelBot Commands", text))

    @commands.hybrid_command(name="set-prefix", description="Change the command prefix.")
    @admin()
    async def set_prefix(self, ctx, prefix: str):
        if not 1 <= len(prefix) <= 5:
            return await ctx.send(embed=embed("Command error", "Prefix must be 1–5 characters.", "error"))
        self.bot.config["prefix"] = prefix
        import json
        from pathlib import Path
        (Path(__file__).resolve().parents[1] / "config.json").write_text(
            json.dumps(self.bot.config, indent=2), encoding="utf-8")
        await audit(self.bot, ctx.guild, ctx.author, "Prefix changed", details=prefix)
        await ctx.send(embed=embed("Prefix updated", f"New prefix: `{prefix}`", "success"))

    @commands.hybrid_command(name="health", description="Show bot and database health.")
    @admin()
    async def health(self, ctx):
        await ctx.send(embed=embed("Bot health",
            f"Latency: `{round(self.bot.latency * 1000)} ms`\nDatabase: `connected`"))

async def setup(bot):
    await bot.add_cog(Core(bot))
