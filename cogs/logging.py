import discord
from discord.ext import commands
from common import channel, embed

class EventLogging(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def send(self,guild,key,title,description):
        ch=channel(guild,self.bot,key) or channel(guild,self.bot,"mod_log")
        if ch:
            try: await ch.send(embed=embed(title,description,"info"))
            except discord.HTTPException: pass
    @commands.Cog.listener()
    async def on_message_delete(self,message):
        if message.guild and not message.author.bot:
            await self.send(message.guild,"message_log","Message deleted",f"Author: {message.author} in {message.channel.mention}\n{message.content[:1500]}")
    @commands.Cog.listener()
    async def on_message_edit(self,before,after):
        if before.guild and not before.author.bot and before.content!=after.content:
            await self.send(before.guild,"message_log","Message edited",f"Author: {before.author} in {before.channel.mention}\nBefore: {before.content[:700]}\nAfter: {after.content[:700]}")
    @commands.Cog.listener()
    async def on_member_remove(self,member):
        await self.send(member.guild,"member_log","Member left",str(member))
    @commands.Cog.listener()
    async def on_member_update(self,before,after):
        if before.roles!=after.roles:
            added=set(after.roles)-set(before.roles); removed=set(before.roles)-set(after.roles)
            await self.send(after.guild,"role_log","Member roles changed",f"{after}\nAdded: {', '.join(r.name for r in added) or 'None'}\nRemoved: {', '.join(r.name for r in removed) or 'None'}")
    @commands.Cog.listener()
    async def on_member_ban(self,guild,user):
        await self.send(guild,"member_log","Member banned",str(user))
async def setup(bot): await bot.add_cog(EventLogging(bot))
