import discord
from discord.ext import commands
from common import embed, admin, audit

class Core(commands.Cog):
    def __init__(self, bot): self.bot = bot

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
