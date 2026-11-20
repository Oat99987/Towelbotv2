import time, discord
from discord.ext import commands, tasks
from common import embed, admin, audit

class RemindersPolls(commands.Cog):
    def __init__(self,bot): self.bot=bot; self.dispatch.start()
    def cog_unload(self): self.dispatch.cancel()
    @tasks.loop(seconds=15)
    async def dispatch(self):
        now=int(time.time())
        rows=await self.bot.db.fetchall("SELECT * FROM reminders WHERE sent=0 AND due_at<=?",(now,))
        for r in rows:
            ch=self.bot.get_channel(r["channel_id"])
            if ch:
                try: await ch.send(f"<@{r['user_id']}> Reminder: {r['content']}")
                except discord.HTTPException: pass
            if r["interval_seconds"]>0:
                await self.bot.db.execute("UPDATE reminders SET due_at=?,sent=0 WHERE id=?",(now+r["interval_seconds"],r["id"]))
            else: await self.bot.db.execute("UPDATE reminders SET sent=1 WHERE id=?",(r["id"],))
        polls=await self.bot.db.fetchall("SELECT * FROM polls WHERE closed=0 AND closes_at IS NOT NULL AND closes_at<=?",(now,))
        for p in polls:
            ch=self.bot.get_channel(p["channel_id"]); msg=None
            if ch:
                try: msg=await ch.fetch_message(p["message_id"])
                except discord.HTTPException: pass
            if msg:
                counts={}
                for reaction in msg.reactions:
                    counts[str(reaction.emoji)]=reaction.count-1
                await ch.send(embed=embed(f"Poll closed: {p['question']}","\n".join(f"{k}: {v}" for k,v in counts.items()),"success"))
            await self.bot.db.execute("UPDATE polls SET closed=1 WHERE id=?",(p["id"],))
    @dispatch.before_loop
    async def before_dispatch(self): await self.bot.wait_until_ready()
    @commands.hybrid_command(name="remind",description="Set a reminder in seconds from now.")
    async def remind(self,ctx,seconds:int,*,message):
        if not 10<=seconds<=2592000: return await ctx.send(embed=embed("Command error","Reminder must be 10 seconds to 30 days.","error"))
        await self.bot.db.execute("INSERT INTO reminders(guild_id,user_id,channel_id,content,due_at) VALUES(?,?,?,?,?)",(ctx.guild.id,ctx.author.id,ctx.channel.id,message,int(time.time())+seconds))
        await ctx.send(embed=embed("Reminder scheduled",f"I'll remind you in {seconds} seconds.","success"))
    @commands.hybrid_command(name="poll-create",description="Create a poll with 2–5 options and duration.")
    @admin()
    async def poll(self,ctx,duration_minutes:int,question:str,*,options:str):
        choices=[x.strip() for x in options.split("|") if x.strip()]
        if not 2<=len(choices)<=5: return await ctx.send(embed=embed("Command error","Provide 2–5 options separated by `|`.","error"))
        emojis=["🇦","🇧","🇨","🇩","🇪"]
        e=embed(question,"\n".join(f"{emojis[i]} {v}" for i,v in enumerate(choices)))
        msg=await ctx.send(embed=e)
        for emoji in emojis[:len(choices)]: await msg.add_reaction(emoji)
        await self.bot.db.execute("INSERT INTO polls(guild_id,channel_id,message_id,question,options,closes_at) VALUES(?,?,?,?,?,?)",
            (ctx.guild.id,ctx.channel.id,msg.id,question,"|".join(choices),int(time.time())+duration_minutes*60))
        await audit(self.bot,ctx.guild,ctx.author,"Poll created",msg,question)
async def setup(bot): await bot.add_cog(RemindersPolls(bot))
