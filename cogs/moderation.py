import discord
from discord.ext import commands
from datetime import timedelta
from common import embed, status_embed, admin, audit, role

class Moderation(commands.Cog):
    def __init__(self,bot): self.bot=bot

    async def send_action(self, ctx, title, member, action, reason=None):
        card=status_embed(title, f'**Member:** {member.mention if hasattr(member, "mention") else member}', 'success')
        card.add_field(name='Action', value=action, inline=True)
        if reason:
            card.add_field(name='Reason', value=str(reason)[:1024], inline=False)
        await ctx.send(embed=card)

    async def send_discord_error(self, ctx, exc):
        if isinstance(exc, discord.Forbidden):
            card=status_embed('Action denied', 'Discord denied this moderation action. Check TowelBot permissions and role hierarchy.', 'error')
        elif isinstance(exc, discord.HTTPException):
            card=status_embed('Discord request failed', f'Discord returned `{exc.status}`: {exc.text[:500]}', 'error')
        else:
            card=status_embed('Unexpected error', 'The action could not be completed. Check bot.log for details.', 'error')
        await ctx.send(embed=card)

    async def case(self,guild,user,actor,action,reason,expires=None):
        await self.bot.db.execute("INSERT INTO cases(guild_id,user_id,moderator_id,action,reason,expires_at) VALUES(?,?,?,?,?,?)",
            (guild.id,user.id,actor.id,action,reason,expires))
        await audit(self.bot,guild,actor,action,user,reason)

    @commands.hybrid_command(name="ban",description="Ban a member.")
    @commands.has_permissions(ban_members=True)
    async def ban(self,ctx,member:discord.Member,*,reason="No reason supplied"):
        try:
            await member.ban(reason=reason)
            await self.case(ctx.guild,member,ctx.author,"Ban",reason)
            await self.send_action(ctx,'Member banned',member,'Ban',reason)
        except discord.HTTPException as exc:
            await self.send_discord_error(ctx,exc)

    @commands.hybrid_command(name="unban",description="Unban by user ID.")
    @commands.has_permissions(ban_members=True)
    async def unban(self,ctx,user_id:str,*,reason="No reason supplied"):
        try:
            uid=int(user_id)
        except ValueError:
            return await ctx.send(embed=status_embed("Invalid user ID","Provide a numeric Discord user ID.","error"))
        try:
            user=await self.bot.fetch_user(uid)
            await ctx.guild.unban(user,reason=reason)
            await self.case(ctx.guild,user,ctx.author,"Unban",reason)
            await self.send_action(ctx,'User unbanned',user,'Unban',reason)
        except discord.HTTPException as exc:
            await self.send_discord_error(ctx,exc)

    @commands.hybrid_command(name="kick",description="Kick a member.")
    @commands.has_permissions(kick_members=True)
    async def kick(self,ctx,member:discord.Member,*,reason="No reason supplied"):
        try:
            await member.kick(reason=reason)
            await self.case(ctx.guild,member,ctx.author,"Kick",reason)
            await self.send_action(ctx,'Member kicked',member,'Kick',reason)
        except discord.HTTPException as exc:
            await self.send_discord_error(ctx,exc)

    @commands.hybrid_command(name="timeout",description="Timeout a member for a number of minutes.")
    @commands.has_permissions(moderate_members=True)
    async def timeout(self,ctx,member:discord.Member,minutes:int,*,reason="No reason supplied"):
        if not 1<=minutes<=40320:
            return await ctx.send(embed=status_embed("Invalid timeout","Choose a duration from **1 to 40320 minutes**.","error"))
        try:
            until=discord.utils.utcnow()+timedelta(minutes=minutes)
            await member.timeout(timedelta(minutes=minutes),reason=reason)
            await self.case(ctx.guild,member,ctx.author,"Timeout",reason,int(until.timestamp()))
            await self.bot.db.execute("INSERT INTO temp_actions(guild_id,user_id,action,expires_at) VALUES(?,?,?,?)",(ctx.guild.id,member.id,"untimeout",int(until.timestamp())))
            await self.send_action(ctx,f'Member timed out for {minutes} minutes',member,'Timeout',reason)
        except discord.HTTPException as exc:
            await self.send_discord_error(ctx,exc)

    @commands.hybrid_command(name="untimeout",description="Remove a member timeout.")
    @commands.has_permissions(moderate_members=True)
    async def untimeout(self,ctx,member:discord.Member):
        await member.timeout(None,reason=f"Removed by {ctx.author}")
        await self.case(ctx.guild,member,ctx.author,"Untimeout","Timeout removed")
        await ctx.send(embed=embed("Timeout removed",str(member),"success"))

    @commands.hybrid_command(name="warn",description="Warn a member; optional escalation configured in config.json.")
    @commands.has_permissions(moderate_members=True)
    async def warn(self,ctx,member:discord.Member,*,reason):
        await self.case(ctx.guild,member,ctx.author,"Warning",reason)
        row=await self.bot.db.fetchone("SELECT COUNT(*) n FROM cases WHERE guild_id=? AND user_id=? AND action='Warning'",(ctx.guild.id,member.id))
        cfg=self.bot.config.get("warnings",{}); threshold=int(cfg.get("escalation_count") or 0); mins=int(cfg.get("escalation_timeout_minutes") or 0)
        if threshold and mins and row["n"]>=threshold:
            await member.timeout(timedelta(minutes=mins),reason="Warning escalation")
            await self.case(ctx.guild,member,ctx.author,"Warning escalation",f"{row['n']} warnings; {mins} minute timeout")
        card=status_embed("Warning recorded",f"**Member:** {member.mention}", "warning")
        card.add_field(name="Reason", value=reason[:1024], inline=False)
        await ctx.send(embed=card)

    @commands.hybrid_command(name="cases",description="View member moderation history.")
    @commands.has_permissions(moderate_members=True)
    async def cases(self,ctx,member:discord.Member):
        rows=await self.bot.db.fetchall("SELECT id,action,reason,created_at FROM cases WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 15",(ctx.guild.id,member.id))
        desc="\n".join(f"**#{r['id']} {r['action']}** — {r['reason'] or 'No reason'} ({r['created_at']})" for r in rows) or "No cases."
        card=embed(f"📋 Moderation history • {member.display_name}",desc,"info")
        card.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=card)

    @commands.hybrid_command(name="purge",description="Delete up to 100 recent messages.")
    @commands.has_permissions(manage_messages=True)
    async def purge(self,ctx,amount:int):
        amount=max(1,min(amount,100)); deleted=await ctx.channel.purge(limit=amount+1)
        await audit(self.bot,ctx.guild,ctx.author,"Purge",ctx.channel,f"{len(deleted)-1} messages")
        await ctx.send(embed=status_embed("Messages deleted",f"Removed **{len(deleted)-1}** recent messages.","success"),delete_after=5)

    @commands.hybrid_command(name="slowmode",description="Set channel slowmode in seconds.")
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self,ctx,seconds:int):
        if not 0<=seconds<=21600: return await ctx.send(embed=embed("Command error","Slowmode must be 0–21600 seconds.","error"))
        await ctx.channel.edit(slowmode_delay=seconds)
        await audit(self.bot,ctx.guild,ctx.author,"Slowmode changed",ctx.channel,str(seconds))
        await ctx.send(embed=embed("Slowmode updated",f"{seconds} seconds","success"))

    async def _lockdown_internal(self,guild):
        border_id=self.bot.config.get("channels",{}).get("border")
        for ch in guild.text_channels:
            if border_id and ch.id==int(border_id): continue
            ow=ch.overwrites_for(guild.default_role); ow.send_messages=False
            try: await ch.set_permissions(guild.default_role,overwrite=ow,reason="Automatic anti-raid lockdown")
            except discord.Forbidden: pass

    @commands.hybrid_command(name="lockdown",description="Lock all text channels except configured border channels.")
    @admin()
    async def lockdown(self,ctx):
        border_id=self.bot.config.get("channels",{}).get("border")
        changed=0
        for ch in ctx.guild.text_channels:
            if border_id and ch.id==int(border_id): continue
            ow=ch.overwrites_for(ctx.guild.default_role); ow.send_messages=False
            try: await ch.set_permissions(ctx.guild.default_role,overwrite=ow,reason=f"Server lockdown by {ctx.author}"); changed+=1
            except discord.Forbidden: pass
        await audit(self.bot,ctx.guild,ctx.author,"Server lockdown",details=f"Locked {changed} text channels")
        await ctx.send(embed=embed("Server lockdown enabled",f"Locked {changed} channels.","warning"))

    @commands.hybrid_command(name="unlockdown",description="Remove server lockdown send restrictions.")
    @admin()
    async def unlockdown(self,ctx):
        changed=0
        for ch in ctx.guild.text_channels:
            ow=ch.overwrites_for(ctx.guild.default_role); ow.send_messages=None
            try: await ch.set_permissions(ctx.guild.default_role,overwrite=ow,reason=f"Lockdown lifted by {ctx.author}"); changed+=1
            except discord.Forbidden: pass
        await audit(self.bot,ctx.guild,ctx.author,"Server lockdown lifted",details=f"Updated {changed} channels")
        await ctx.send(embed=embed("Lockdown lifted",f"Updated {changed} channels.","success"))

    @commands.hybrid_command(name="nickname",description="Change a member nickname.")
    @commands.has_permissions(manage_nicknames=True)
    async def nickname(self,ctx,member:discord.Member,*,name:str|None=None):
        nickname = name if name is not None else None
        await member.edit(nick=nickname,reason=f"Changed by {ctx.author}")
        await self.case(ctx.guild,member,ctx.author,"Nickname",f"New nickname: {nickname or 'None'}")
        await ctx.send(embed=embed("Nickname updated",str(member),"success"))

    @commands.hybrid_command(name="role-add",description="Add a role to a member.")
    @commands.has_permissions(manage_roles=True)
    async def role_add(self,ctx,member:discord.Member,role_to_add:discord.Role):
        await member.add_roles(role_to_add,reason=f"Added by {ctx.author}")
        await self.case(ctx.guild,member,ctx.author,"Role added",role_to_add.name)
        await ctx.send(embed=embed("Role added",f"{role_to_add.mention} → {member}","success"))

    @commands.hybrid_command(name="role-remove",description="Remove a role from a member.")
    @commands.has_permissions(manage_roles=True)
    async def role_remove(self,ctx,member:discord.Member,role_to_remove:discord.Role):
        await member.remove_roles(role_to_remove,reason=f"Removed by {ctx.author}")
        await self.case(ctx.guild,member,ctx.author,"Role removed",role_to_remove.name)
        await ctx.send(embed=embed("Role removed",f"{role_to_remove.name} ← {member}","success"))

    @commands.hybrid_command(name="hardmute",description="Apply configured muted role.")
    @commands.has_permissions(manage_roles=True)
    async def hardmute(self,ctx,member:discord.Member,*,reason="No reason supplied"):
        muted=role(ctx.guild,self.bot,"muted")
        if not muted: return await ctx.send(embed=embed("Command error","Configure roles.muted first.","error"))
        await member.add_roles(muted,reason=reason); await self.case(ctx.guild,member,ctx.author,"Hardmute",reason)
        await ctx.send(embed=embed("Hardmute applied",str(member),"success"))

    @commands.hybrid_command(name="unhardmute",description="Remove configured muted role.")
    @commands.has_permissions(manage_roles=True)
    async def unhardmute(self,ctx,member:discord.Member):
        muted=role(ctx.guild,self.bot,"muted")
        if not muted: return await ctx.send(embed=embed("Command error","Configure roles.muted first.","error"))
        await member.remove_roles(muted,reason=f"Released by {ctx.author}")
        await self.case(ctx.guild,member,ctx.author,"Unhardmute","Muted role removed")
        await ctx.send(embed=embed("Hardmute removed",str(member),"success"))

    @commands.hybrid_command(name="jail",description="Move a member into the configured jail role.")
    @commands.has_permissions(manage_roles=True)
    async def jail(self,ctx,member:discord.Member,*,reason="No reason supplied"):
        jailed=role(ctx.guild,self.bot,"jailed")
        if not jailed: return await ctx.send(embed=embed("Command error","Configure roles.jailed first.","error"))
        await member.add_roles(jailed,reason=reason); await self.case(ctx.guild,member,ctx.author,"Jail",reason)
        await ctx.send(embed=embed("Member jailed",str(member),"warning"))

    @commands.hybrid_command(name="release",description="Explicitly release a jailed member.")
    @commands.has_permissions(manage_roles=True)
    async def release(self,ctx,member:discord.Member):
        jailed=role(ctx.guild,self.bot,"jailed")
        if not jailed: return await ctx.send(embed=embed("Command error","Configure roles.jailed first.","error"))
        await member.remove_roles(jailed,reason=f"Released by {ctx.author}")
        await self.case(ctx.guild,member,ctx.author,"Jail release","Explicit release")
        await ctx.send(embed=embed("Member released",str(member),"success"))

async def setup(bot): await bot.add_cog(Moderation(bot))
