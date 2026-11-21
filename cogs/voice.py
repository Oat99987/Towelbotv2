import discord
from discord.ext import commands
from common import admin, audit, embed

class TempVoice(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @commands.hybrid_command(name="voice-lobby-set",description="Set the temporary voice lobby.")
    @admin()
    async def set_lobby(self,ctx,channel:discord.VoiceChannel):
        await self.bot.db.set_config(ctx.guild.id, "channels", "voice_lobby", channel.id)
        await audit(self.bot,ctx.guild,ctx.author,"Temporary voice lobby configured",channel)
        await ctx.send(f"Temporary voice lobby set to {channel.mention}.")
    @commands.Cog.listener()
    async def on_voice_state_update(self,member,before,after):
        lobby = await self.bot.db.get_config(member.guild.id, "channels", "voice_lobby")
        if lobby is None:
            lobby = self.bot.config.get("channels", {}).get("voice_lobby")
        if after.channel and lobby and after.channel.id == int(lobby):
            ch=await member.guild.create_voice_channel(f"{member.display_name}'s room",category=after.channel.category)
            await ch.set_permissions(member,connect=True,manage_channels=True)
            await member.move_to(ch)
            await self.bot.db.execute("INSERT OR REPLACE INTO voice_rooms VALUES(?,?,?)",(member.guild.id,ch.id,member.id))
        if before.channel:
            row=await self.bot.db.fetchone("SELECT owner_id FROM voice_rooms WHERE guild_id=? AND channel_id=?",(member.guild.id,before.channel.id))
            if row and len(before.channel.members)==0:
                await before.channel.delete(reason="Temporary voice room empty")
                await self.bot.db.execute("DELETE FROM voice_rooms WHERE guild_id=? AND channel_id=?",(member.guild.id,before.channel.id))
    @commands.hybrid_command(name="voice-rename",description="Rename your temporary voice channel.")
    async def rename(self,ctx,*,name):
        if not isinstance(ctx.author.voice,discord.VoiceState) or not ctx.author.voice.channel: return await ctx.send(embed=embed("Command error","Join your room first.","error"))
        ch=ctx.author.voice.channel
        row=await self.bot.db.fetchone("SELECT owner_id FROM voice_rooms WHERE guild_id=? AND channel_id=?",(ctx.guild.id,ch.id))
        if not row or row["owner_id"]!=ctx.author.id: return await ctx.send(embed=embed("Command error","Only the room owner can rename it.","error"))
        await ch.edit(name=name[:95]); await ctx.send("Room renamed.")
    @commands.hybrid_command(name="voice-limit",description="Set your temporary voice room user limit.")
    async def limit(self,ctx,limit:int):
        if not ctx.author.voice or not ctx.author.voice.channel: return await ctx.send(embed=embed("Command error","Join your room first.","error"))
        ch=ctx.author.voice.channel
        row=await self.bot.db.fetchone("SELECT owner_id FROM voice_rooms WHERE guild_id=? AND channel_id=?",(ctx.guild.id,ch.id))
        if not row or row["owner_id"]!=ctx.author.id: return await ctx.send(embed=embed("Command error","Only the room owner can change its limit.","error"))
        if not 0<=limit<=99: return await ctx.send(embed=embed("Command error","Limit must be 0–99.","error"))
        await ch.edit(user_limit=limit); await ctx.send("Room limit updated.")
async def setup(bot): await bot.add_cog(TempVoice(bot))
