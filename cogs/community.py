import time, random, math, json
from pathlib import Path
import discord
from discord.ext import commands
from common import embed, admin, channel, audit

class Community(commands.Cog):
    def __init__(self,bot): self.bot=bot

    @discord.app_commands.command(name="rank", description="View XP and level for a member.")
    async def rank_slash(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        row = await self.bot.db.fetchone(
            "SELECT xp,level FROM xp WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, target.id),
        )
        xp = row["xp"] if row else 0
        level = row["level"] if row else 0
        card = embed(f"{target.display_name}'s rank")
        card.set_thumbnail(url=target.display_avatar.url)
        card.add_field(name="Level", value=str(level), inline=True)
        card.add_field(name="XP", value=str(xp), inline=True)
        await interaction.response.send_message(embed=card)

    @discord.app_commands.command(name="leaderboard", description="View the server XP leaderboard.")
    async def leaderboard_slash(self, interaction: discord.Interaction):
        rows = await self.bot.db.fetchall(
            "SELECT user_id,xp,level FROM xp WHERE guild_id=? ORDER BY xp DESC,level DESC LIMIT 10",
            (interaction.guild_id,),
        )
        lines = []
        for index, row in enumerate(rows, 1):
            lines.append(f"`{index}.` <@{row['user_id']}> — Level {row['level']} · {row['xp']} XP")
        await interaction.response.send_message(embed=embed("TowelBot leaderboard", "\n".join(lines) or "No XP records yet."))
    @commands.hybrid_command(name="welcome-set",description="Configure welcome message and channel.")
    @admin()
    async def welcome_set(self,ctx,target:discord.TextChannel,*,message):
        self.bot.config["channels"]["welcome"]=target.id; self.bot.config["messages"]["welcome"]=message
        (Path(__file__).resolve().parents[1]/"config.json").write_text(json.dumps(self.bot.config,indent=2),encoding="utf-8")
        await audit(self.bot,ctx.guild,ctx.author,"Welcome configuration updated",target,message)
        await ctx.send(embed=embed("Welcome configured","Variables: `{member}`, `{server}`","success"))
    @commands.Cog.listener()
    async def on_member_join(self,member):
        ch=channel(member.guild,self.bot,"welcome"); msg=self.bot.config.get("messages",{}).get("welcome")
        if ch and msg:
            await ch.send(msg.replace("{member}",member.mention).replace("{server}",member.guild.name))
        await self.bot.db.execute("INSERT INTO stats(guild_id,key,value) VALUES(?,?,1) ON CONFLICT(guild_id,key) DO UPDATE SET value=value+1",(member.guild.id,"joins"))
    @commands.Cog.listener()
    async def on_message(self,message):
        if not message.guild or message.author.bot: return
        await self.bot.db.execute("INSERT INTO stats(guild_id,key,value) VALUES(?,?,1) ON CONFLICT(guild_id,key) DO UPDATE SET value=value+1",(message.guild.id,"messages"))
        cfg=self.bot.config.get("levels",{})
        if cfg.get("enabled"):
            now=int(time.time()); row=await self.bot.db.fetchone("SELECT xp,level,last_award FROM xp WHERE guild_id=? AND user_id=?",(message.guild.id,message.author.id))
            if not row or now-row["last_award"]>=int(cfg.get("cooldown_seconds",60)):
                xp=(row["xp"] if row else 0)+random.randint(int(cfg.get("xp_min",5)),int(cfg.get("xp_max",12)))
                lvl=int(math.sqrt(xp/100)); old=row["level"] if row else 0
                await self.bot.db.execute("""INSERT INTO xp(guild_id,user_id,xp,level,last_award) VALUES(?,?,?,?,?)
                ON CONFLICT(guild_id,user_id) DO UPDATE SET xp=excluded.xp,level=excluded.level,last_award=excluded.last_award""",
                (message.guild.id,message.author.id,xp,lvl,now))
                if lvl>old:
                    ch=channel(message.guild,self.bot,"levels") or message.channel
                    template=self.bot.config.get("messages",{}).get("level_up") or "{member} reached level {level}!"
                    await ch.send(template.replace("{member}",message.author.mention).replace("{level}",str(lvl)).replace("{xp}",str(xp)))
        prefix=self.bot.config.get("prefix","!")
        if message.content.startswith(prefix):
            name=message.content[len(prefix):].split(maxsplit=1)[0].lower()
            row=await self.bot.db.fetchone("SELECT response FROM custom_commands WHERE guild_id=? AND name=?",(message.guild.id,name))
            if row: await message.channel.send(row["response"].replace("{member}",message.author.mention).replace("{server}",message.guild.name))
    @commands.hybrid_command(name="level",description="View member level.")
    async def level(self,ctx,member:discord.Member|None=None):
        member=member or ctx.author
        row=await self.bot.db.fetchone("SELECT xp,level FROM xp WHERE guild_id=? AND user_id=?",(ctx.guild.id,member.id))
        await ctx.send(embed=embed(f"Level: {member}",f"Level `{row['level'] if row else 0}` · XP `{row['xp'] if row else 0}`"))
    @commands.hybrid_command(name="level-adjust",description="Adjust member XP.")
    @admin()
    async def level_adjust(self,ctx,member:discord.Member,xp:int):
        if xp<0: return await ctx.send(embed=embed("Command error","XP cannot be negative.","error"))
        level=int(math.sqrt(xp/100))
        await self.bot.db.execute("""INSERT INTO xp(guild_id,user_id,xp,level,last_award) VALUES(?,?,?,?,0)
        ON CONFLICT(guild_id,user_id) DO UPDATE SET xp=excluded.xp,level=excluded.level""",(ctx.guild.id,member.id,xp,level))
        await audit(self.bot,ctx.guild,ctx.author,"XP adjusted",member,str(xp))
        await ctx.send(embed=embed("XP updated",f"{member}: {xp} XP, level {level}","success"))
    @commands.hybrid_command(name="custom-add",description="Create a custom prefix response.")
    @admin()
    async def custom_add(self,ctx,name,*,response):
        await self.bot.db.execute("INSERT OR REPLACE INTO custom_commands(guild_id,name,response) VALUES(?,?,?)",(ctx.guild.id,name.lower(),response))
        await audit(self.bot,ctx.guild,ctx.author,"Custom command added",details=name)
        await ctx.send("Custom command saved.")
    @commands.hybrid_command(name="custom-remove",description="Delete a custom response.")
    @admin()
    async def custom_remove(self,ctx,name):
        await self.bot.db.execute("DELETE FROM custom_commands WHERE guild_id=? AND name=?",(ctx.guild.id,name.lower()))
        await audit(self.bot,ctx.guild,ctx.author,"Custom command removed",details=name)
        await ctx.send("Custom command removed.")
async def setup(bot): await bot.add_cog(Community(bot))
