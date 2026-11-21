import discord
from discord.ext import commands

from common import audit, configured_role, status_embed


def police_officer():
    async def predicate(ctx):
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return False
        if ctx.author.guild_permissions.manage_guild:
            return True
        configured = await configured_role(ctx.guild, ctx.bot, 'police')
        return configured is not None and configured in ctx.author.roles

    return commands.check(predicate)


class Police(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_group(
        name='police',
        description='Role-gated police roleplay records and actions.',
    )
    @commands.guild_only()
    async def police(self, ctx):
        await ctx.send(embed=status_embed(
            'Police commands',
            'Use `/police set-role`, `/police report`, `/police citation`, `/police arrest`, '
            '`/police release`, `/police records`, or `/police warrant`.',
        ))

    police.invoke_without_command = True

    @police.command(name='set-role', description='Set the role allowed to use police commands.')
    @commands.has_guild_permissions(manage_guild=True)
    async def police_set_role(self, ctx, role: discord.Role):
        await self.bot.db.set_config(ctx.guild.id, 'roles', 'police', role.id)
        await audit(self.bot, ctx.guild, ctx.author, 'Police role configured', role, role.name)
        await ctx.send(embed=status_embed(
            'Police role configured',
            f'{role.mention} can now use police roleplay commands. Server managers retain access.',
            'success',
        ), allowed_mentions=discord.AllowedMentions.none())

    @police.command(name='report', description='Record a roleplay police report about a member.')
    @police_officer()
    async def police_report(self, ctx, member: discord.Member, *, details: str):
        await self._record(ctx, member, 'report', details)

    @police.command(name='citation', description='Record a roleplay citation; this does not change Discord permissions.')
    @police_officer()
    async def police_citation(self, ctx, member: discord.Member, *, details: str):
        await self._record(ctx, member, 'citation', details)

    @police.command(name='arrest', description='Apply the configured jailed role and record a roleplay arrest.')
    @police_officer()
    async def police_arrest(self, ctx, member: discord.Member, *, reason: str):
        reason = reason.strip()
        if not await self._validate_details(ctx, reason):
            return
        jailed = await configured_role(ctx.guild, self.bot, 'jailed')
        if jailed is None:
            return await ctx.send(embed=status_embed(
                'Jailed role not configured',
                'Select a Jailed role in `/setup` → Configure roles before using police arrest.',
                'error',
            ))
        if jailed in member.roles:
            return await ctx.send(embed=status_embed(
                'Member already jailed',
                f'{member.mention} already has the configured Jailed role.',
                'warning',
            ), allowed_mentions=discord.AllowedMentions.none())
        try:
            await member.add_roles(jailed, reason=f'Police roleplay arrest by {ctx.author}: {reason[:400]}')
        except discord.Forbidden:
            return await ctx.send(embed=status_embed(
                'Arrest action denied',
                'Discord denied the role update. Check TowelBot permissions and role hierarchy.',
                'error',
            ))
        except discord.HTTPException as error:
            return await ctx.send(embed=status_embed(
                'Arrest action failed',
                f'Discord returned `{error.status}` while applying the Jailed role.',
                'error',
            ))
        record_id = await self._record(ctx, member, 'arrest', reason)
        await ctx.send(embed=status_embed(
            'Roleplay arrest recorded',
            f'{member.mention} received the Jailed role. Police record `#{record_id}`.',
            'warning',
        ), allowed_mentions=discord.AllowedMentions.none())

    @police.command(name='release', description='Remove the configured jailed role and record a roleplay release.')
    @police_officer()
    async def police_release(self, ctx, member: discord.Member, *, reason: str = 'Released by police'):
        reason = reason.strip()
        if not await self._validate_details(ctx, reason):
            return
        jailed = await configured_role(ctx.guild, self.bot, 'jailed')
        if jailed is None:
            return await ctx.send(embed=status_embed(
                'Jailed role not configured',
                'Select a Jailed role in `/setup` → Configure roles before using police release.',
                'error',
            ))
        if jailed not in member.roles:
            return await ctx.send(embed=status_embed(
                'Member is not jailed',
                f'{member.mention} does not have the configured Jailed role.',
                'warning',
            ), allowed_mentions=discord.AllowedMentions.none())
        try:
            await member.remove_roles(jailed, reason=f'Police roleplay release by {ctx.author}: {reason[:400]}')
        except discord.Forbidden:
            return await ctx.send(embed=status_embed(
                'Release action denied',
                'Discord denied the role update. Check TowelBot permissions and role hierarchy.',
                'error',
            ))
        except discord.HTTPException as error:
            return await ctx.send(embed=status_embed(
                'Release action failed',
                f'Discord returned `{error.status}` while removing the Jailed role.',
                'error',
            ))
        record_id = await self._record(ctx, member, 'release', reason)
        await ctx.send(embed=status_embed(
            'Roleplay release recorded',
            f'{member.mention} had the Jailed role removed. Police record `#{record_id}`.',
            'success',
        ), allowed_mentions=discord.AllowedMentions.none())

    @police.group(name='warrant', description='Manage fictional server roleplay warrants.')
    @police_officer()
    async def police_warrant(self, ctx):
        await ctx.send(embed=status_embed(
            'Warrant commands',
            'Use `/police warrant issue`, `/police warrant clear`, or `/police warrant list`.',
        ))

    police_warrant.invoke_without_command = True

    @police_warrant.command(name='issue', description='Record an active roleplay warrant.')
    @police_officer()
    async def police_warrant_issue(self, ctx, member: discord.Member, *, details: str):
        record_id = await self._record(ctx, member, 'warrant', details, status='active')
        if record_id is None:
            return
        await ctx.send(embed=status_embed(
            'Roleplay warrant recorded',
            f'An active warrant for {member.mention} was recorded as `#{record_id}`. This does not trigger a real-world action.',
            'warning',
        ), allowed_mentions=discord.AllowedMentions.none())

    @police_warrant.command(name='clear', description='Clear the latest active roleplay warrant for a member.')
    @police_officer()
    async def police_warrant_clear(self, ctx, member: discord.Member, *, reason: str = 'Warrant cleared'):
        reason = reason.strip()
        if not await self._validate_details(ctx, reason):
            return
        record_id = None
        async with self.bot.db.transaction() as conn:
            async with conn.execute(
                '''SELECT id FROM police_records WHERE guild_id=? AND subject_id=?
                   AND action='warrant' AND status='active' ORDER BY id DESC LIMIT 1''',
                (ctx.guild.id, member.id),
            ) as cursor:
                warrant = await cursor.fetchone()
            if warrant is not None:
                update_cursor = await conn.execute(
                    "UPDATE police_records SET status='cleared' WHERE guild_id=? AND id=? AND status='active'",
                    (ctx.guild.id, warrant['id']),
                )
                if update_cursor.rowcount == 1:
                    record_cursor = await conn.execute(
                        '''INSERT INTO police_records(guild_id,subject_id,officer_id,action,status,details)
                           VALUES(?,?,?,'warrant_clear','cleared',?)''',
                        (ctx.guild.id, member.id, ctx.author.id, reason),
                    )
                    await conn.execute(
                        'INSERT INTO cases(guild_id,user_id,moderator_id,action,reason) VALUES(?,?,?,?,?)',
                        (ctx.guild.id, member.id, ctx.author.id, 'Police warrant cleared', reason),
                    )
                    record_id = record_cursor.lastrowid
        if record_id is None:
            return await ctx.send(embed=status_embed(
                'No active warrant',
                f'No active roleplay warrant was found for {member.mention}.',
                'warning',
            ), allowed_mentions=discord.AllowedMentions.none())
        await audit(self.bot, ctx.guild, ctx.author, 'Roleplay warrant cleared', member, reason[:1000])
        await ctx.send(embed=status_embed(
            'Roleplay warrant cleared',
            f'The latest active warrant for {member.mention} was cleared. Record `#{record_id}`.',
            'success',
        ), allowed_mentions=discord.AllowedMentions.none())

    @police_warrant.command(name='list', description='List active roleplay warrants.')
    @police_officer()
    async def police_warrant_list(self, ctx, member: discord.Member | None = None):
        if member is None:
            rows = await self.bot.db.fetchall(
                '''SELECT id,subject_id,details FROM police_records WHERE guild_id=?
                   AND action='warrant' AND status='active' ORDER BY id DESC LIMIT 20''',
                (ctx.guild.id,),
            )
        else:
            rows = await self.bot.db.fetchall(
                '''SELECT id,subject_id,details FROM police_records WHERE guild_id=? AND subject_id=?
                   AND action='warrant' AND status='active' ORDER BY id DESC LIMIT 20''',
                (ctx.guild.id, member.id),
            )
        lines = [
            f'`#{row["id"]}` <@{row["subject_id"]}> — {row["details"]}'
            for row in rows
        ]
        await ctx.send(
            embed=status_embed('Active roleplay warrants', '\n'.join(lines)[:4000] or 'No active warrants.'),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @police.command(name='records', description='View a member’s recent police roleplay records.')
    @police_officer()
    async def police_records(self, ctx, member: discord.Member):
        rows = await self.bot.db.fetchall(
            '''SELECT id,action,status,details FROM police_records
               WHERE guild_id=? AND subject_id=? ORDER BY id DESC LIMIT 15''',
            (ctx.guild.id, member.id),
        )
        lines = [
            f'`#{row["id"]}` **{row["action"].replace("_", " ").title()}** '
            f'({row["status"]}) — {row["details"]}'
            for row in rows
        ]
        await ctx.send(
            embed=status_embed(f'Police roleplay records • {member.display_name}',
                               '\n'.join(lines)[:4000] or 'No police roleplay records.'),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _validate_details(self, ctx: commands.Context, details: str) -> bool:
        details = details.strip()
        if not details or len(details) > 1000:
            await ctx.send(embed=status_embed(
                'Invalid record details',
                'Details must contain 1–1,000 characters.',
                'error',
            ))
            return False
        return True

    async def _record(
        self,
        ctx: commands.Context,
        member: discord.Member,
        action: str,
        details: str,
        status: str = 'recorded',
    ) -> int | None:
        details = details.strip()
        if not await self._validate_details(ctx, details):
            return None
        guild = ctx.guild
        if guild is None:
            raise commands.NoPrivateMessage()
        async with self.bot.db.transaction() as conn:
            cursor = await conn.execute(
                '''INSERT INTO police_records(guild_id,subject_id,officer_id,action,status,details)
                   VALUES(?,?,?,?,?,?)''',
                (guild.id, member.id, ctx.author.id, action, status, details),
            )
            await conn.execute(
                'INSERT INTO cases(guild_id,user_id,moderator_id,action,reason) VALUES(?,?,?,?,?)',
                (guild.id, member.id, ctx.author.id, f'Police {action.replace("_", " ")}', details),
            )
            record_id = cursor.lastrowid
        await audit(self.bot, guild, ctx.author, f'Police {action}', member, details)
        return record_id


async def setup(bot):
    await bot.add_cog(Police(bot))
