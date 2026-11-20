import discord
from discord.ext import commands
from common import channel, embed, audit

class Starboard(commands.Cog):
    def __init__(self,bot): self.bot=bot
    @commands.Cog.listener()
    async def on_raw_reaction_add(self,payload):
        if str(payload.emoji)!=self.bot.config.get("starboard",{}).get("emoji","⭐"): return
        guild=self.bot.get_guild(payload.guild_id)
        if not guild or payload.member and payload.member.bot: return
        target=channel(guild,self.bot,"starboard")
        if not target: return
        try:
            source=guild.get_channel(payload.channel_id); msg=await source.fetch_message(payload.message_id)
        except (AttributeError,discord.HTTPException): return
        reaction=discord.utils.get(msg.reactions,emoji=str(payload.emoji))
        threshold=int(self.bot.config.get("starboard",{}).get("threshold",5))
        if not reaction or reaction.count<threshold: return
        exists=await self.bot.db.fetchone("SELECT 1 FROM starboard_posts WHERE guild_id=? AND message_id=?",(guild.id,msg.id))
        if exists: return
        e=embed("Starboard",msg.content or "[No text content]")
        e.add_field(name="Original",value=f"[Jump to message]({msg.jump_url})")
        if msg.attachments: e.set_image(url=msg.attachments[0].url)
        post=await target.send(embed=e)
        await self.bot.db.execute("INSERT INTO starboard_posts VALUES(?,?,?)",(guild.id,msg.id,post.id))
async def setup(bot): await bot.add_cog(Starboard(bot))
