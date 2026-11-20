import discord
from discord.ext import commands
from common import embed, admin, audit

class AnnouncementsStats(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @commands.hybrid_command(name="embed-post",description="Post a formatted embed.")
    @admin()
    async def embed_post(self,ctx,target:discord.TextChannel,title,*,description):
        await target.send(embed=embed(title,description))
        await audit(self.bot,ctx.guild,ctx.author,"Embed announcement posted",target,title)
        await ctx.send("Embed posted.")
    @commands.hybrid_command(name="stats",description="Show server activity counters.")
    @commands.has_permissions(manage_guild=True)
    async def stats(self,ctx):
        rows=await self.bot.db.fetchall("SELECT key,value FROM stats WHERE guild_id=?",(ctx.guild.id,))
        values={r["key"]:r["value"] for r in rows}
        await ctx.send(embed=embed("Server statistics",f"Members: `{ctx.guild.member_count}`\nTracked messages: `{values.get('messages',0)}`\nTracked joins: `{values.get('joins',0)}`"))
async def setup(bot): await bot.add_cog(AnnouncementsStats(bot))
