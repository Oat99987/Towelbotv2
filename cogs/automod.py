import time, collections, re, discord
from discord.ext import commands
from common import audit, channel, embed

class AutoMod(commands.Cog):
    def __init__(self,bot):
        self.bot=bot; self.rates=collections.defaultdict(collections.deque)
        self.dupes=collections.defaultdict(collections.deque); self.joins=collections.defaultdict(collections.deque)
        self.invites={}
    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            try: self.invites[guild.id]={i.code:i.uses for i in await guild.invites()}
            except discord.HTTPException: self.invites[guild.id]={}
    @commands.Cog.listener()
    async def on_member_join(self,member):
        try:
            current={i.code:i.uses for i in await member.guild.invites()}
            old=self.invites.get(member.guild.id,{})
            used=next((code for code,uses in current.items() if uses>(old.get(code) or 0)),None)
            inv=next((i for i in await member.guild.invites() if i.code==used),None) if used else None
            await self.bot.db.execute("INSERT OR REPLACE INTO invite_uses(guild_id,user_id,invite_code,inviter_id) VALUES(?,?,?,?)",
                (member.guild.id,member.id,used,inv.inviter.id if inv and inv.inviter else None))
            self.invites[member.guild.id]=current
        except discord.HTTPException: pass
        cfg=self.bot.config.get("anti_raid",{})
        if cfg.get("enabled"):
            now=time.time(); q=self.joins[member.guild.id]; q.append(now)
            while q and now-q[0]>int(cfg.get("window_seconds",20)): q.popleft()
            if len(q)>=int(cfg.get("join_count",10)):
                await audit(self.bot,member.guild,self.bot.user,"Anti-raid threshold",member,f"{len(q)} joins in window")
                if cfg.get("lockdown_on_raid"):
                    await self.bot.get_cog("Moderation")._lockdown_internal(member.guild)
        min_age=int(self.bot.config.get("automod",{}).get("minimum_account_age_days",0))
        if min_age and (discord.utils.utcnow()-member.created_at).days<min_age:
            log=channel(member.guild,self.bot,"automod_log")
            if log: await log.send(embed=embed("New account screening",f"{member.mention} is younger than {min_age} days.","warning"))
    @commands.Cog.listener()
    async def on_message(self,message):
        if not message.guild or message.author.bot: return
        cfg=self.bot.config.get("automod",{})
        if not cfg.get("enabled"): return
        now=time.time(); key=(message.guild.id,message.author.id)
        q=self.rates[key]; q.append(now)
        while q and now-q[0]>int(cfg.get("message_rate_seconds",8)): q.popleft()
        text=message.content.lower().strip(); d=self.dupes[key]; d.append((now,text))
        while d and now-d[0][0]>30: d.popleft()
        reason=None
        if len(q)>int(cfg.get("message_rate_count",6)): reason="Message rate limit"
        elif text and sum(1 for _,v in d if v==text)>=int(cfg.get("duplicate_limit",3)): reason="Duplicate messages"
        elif len(message.mentions)+len(message.role_mentions)>int(cfg.get("mention_limit",5)): reason="Mention flood"
        elif any(w.lower() in text for w in cfg.get("blocked_words",[])): reason="Blocked word"
        domains=re.findall(r"(?:https?://)?(?:www\\.)?([a-z0-9.-]+\\.[a-z]{2,})",text)
        allowed=[x.lower() for x in cfg.get("allowed_domains",[])]
        blocked=[x.lower() for x in cfg.get("blocked_domains",[])]
        if any(any(dom==b or dom.endswith("."+b) for b in blocked) for dom in domains): reason="Blocked link domain"
        if cfg.get("link_filter_enabled") and any(not any(dom==a or dom.endswith("."+a) for a in allowed) for dom in domains): reason="Unapproved link"
        if reason:
            try: await message.delete()
            except discord.HTTPException: pass
            await audit(self.bot,message.guild,self.bot.user,"AutoMod deletion",message.author,reason)
            try: await message.author.send(f"A message in {message.guild.name} was removed: {reason}.")
            except discord.HTTPException: pass

async def setup(bot): await bot.add_cog(AutoMod(bot))
