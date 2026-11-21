import discord
from discord import app_commands
from discord.ext import commands

from common import embed


class EventLogging(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def send(self, guild, title, description):
        row = await self.bot.db.fetchone(
            'SELECT channel_id,enabled FROM logging_config WHERE guild_id=?',
            (guild.id,),
        )
        if not row or not row['enabled'] or not row['channel_id']:
            return
        channel = guild.get_channel(row['channel_id'])
        if channel:
            try:
                await channel.send(embed=embed(title, description, 'info'))
            except discord.HTTPException:
                pass

    logs = app_commands.Group(name='logs', description='Unified TowelBot logging settings')

    @logs.command(name='channel', description='Set the unified logging channel.')
    @app_commands.checks.has_permissions(administrator=True)
    async def logs_channel(self, interaction, channel: discord.TextChannel):
        await self.bot.db.execute(
            'INSERT INTO logging_config(guild_id,channel_id,enabled) VALUES(?,?,1) '
            'ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id',
            (interaction.guild_id, channel.id),
        )
        await interaction.response.send_message(embed=embed('Logging channel set', channel.mention, 'success'))

    @logs.command(name='on', description='Enable unified logging.')
    @app_commands.checks.has_permissions(administrator=True)
    async def logs_on(self, interaction):
        await self.bot.db.execute(
            'INSERT INTO logging_config(guild_id,enabled) VALUES(?,1) '
            'ON CONFLICT(guild_id) DO UPDATE SET enabled=1',
            (interaction.guild_id,),
        )
        await interaction.response.send_message(embed=embed('Unified logging enabled', 'Log events will be sent to the configured channel.', 'success'))

    @logs.command(name='off', description='Disable unified logging.')
    @app_commands.checks.has_permissions(administrator=True)
    async def logs_off(self, interaction):
        await self.bot.db.execute(
            'INSERT INTO logging_config(guild_id,enabled) VALUES(?,0) '
            'ON CONFLICT(guild_id) DO UPDATE SET enabled=0',
            (interaction.guild_id,),
        )
        await interaction.response.send_message(embed=embed('Unified logging disabled', 'Log events are no longer sent.', 'warning'))

    @logs.command(name='status', description='View unified logging status.')
    @app_commands.checks.has_permissions(administrator=True)
    async def logs_status(self, interaction):
        row = await self.bot.db.fetchone(
            'SELECT channel_id,enabled FROM logging_config WHERE guild_id=?',
            (interaction.guild_id,),
        )
        if not row:
            return await interaction.response.send_message('Unified logging is not configured.')
        channel = interaction.guild.get_channel(row['channel_id']) if row['channel_id'] else None
        await interaction.response.send_message(embed=embed(
            'Unified logging status',
            f"Enabled: {'yes' if row['enabled'] else 'no'}\nChannel: {channel.mention if channel else 'Not set'}",
        ))

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.send(member.guild, 'Member joined', f'{member.mention}\nAccount: {member.created_at:%Y-%m-%d}\nMember count: {member.guild.member_count}')

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await self.send(member.guild, 'Member left', f'{member.mention}\nUser ID: `{member.id}`')

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        await self.send(guild, 'Member banned', f'{user.mention}\nUser ID: `{user.id}`')

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        await self.send(guild, 'Member unbanned', f'{user.mention}\nUser ID: `{user.id}`')

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.guild and not message.author.bot:
            await self.send(
                message.guild,
                'Message deleted',
                f'Author: {message.author.mention}\nChannel: {message.channel.mention}\nContent: {message.content[:1500]}',
            )

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.guild and not before.author.bot and before.content != after.content:
            await self.send(
                before.guild,
                'Message edited',
                f'Author: {before.author.mention}\nChannel: {before.channel.mention}\nBefore: {before.content[:700]}\nAfter: {after.content[:700]}',
            )

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles != after.roles:
            added = set(after.roles) - set(before.roles)
            removed = set(before.roles) - set(after.roles)
            await self.send(
                after.guild,
                'Member roles changed',
                f'Member: {after.mention}\nAdded: {", ".join(r.name for r in added) or "None"}\nRemoved: {", ".join(r.name for r in removed) or "None"}',
            )
        if before.nick != after.nick:
            await self.send(after.guild, 'Nickname changed', f'Member: {after.mention}\nBefore: {before.nick or "None"}\nAfter: {after.nick or "None"}')


async def setup(bot):
    await bot.add_cog(EventLogging(bot))
