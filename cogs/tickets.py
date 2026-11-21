import io
import logging
import re

import discord
from discord.ext import commands

from common import admin, audit, configured_role, embed, status_embed

log = logging.getLogger('TowelBot.tickets')


async def get_ticket_category(bot, guild):
    category_id = await bot.db.get_config(guild.id, 'channels', 'ticket_category')
    if category_id is None:
        category_id = bot.config.get('channels', {}).get('ticket_category')

    category = None
    if category_id is not None:
        try:
            category = guild.get_channel(int(category_id))
        except (TypeError, ValueError):
            category = None

    if category is None:
        category = (
            discord.utils.get(guild.categories, name='🎫・Tickets')
            or discord.utils.get(guild.categories, name='🎫-Tickets')
        )
        if category is not None:
            await bot.db.set_config(guild.id, 'channels', 'ticket_category', category.id)

    if not isinstance(category, discord.CategoryChannel):
        raise ValueError(
            'No ticket category is configured. An administrator can set one with '
            '`/ticket-category` or `/setup` → Configure channels → More channel settings.'
        )
    return category


class TicketView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label='Open ticket', style=discord.ButtonStyle.primary, emoji='🎫', custom_id='nation:tickets:open')
    async def open(self, interaction, button):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                embed=status_embed('Server required', 'Open tickets from inside a server.', 'error')
            )

        existing = await self.bot.db.fetchone(
            "SELECT id,channel_id FROM tickets WHERE guild_id=? AND user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (guild.id, interaction.user.id),
        )
        if existing:
            channel = guild.get_channel(existing['channel_id'])
            if channel is not None:
                return await interaction.response.send_message(
                    embed=status_embed('You already have a ticket', f'Continue here: {channel.mention}', 'warning')
                )
            await self.bot.db.execute(
                "UPDATE tickets SET status='closed',closed_at=CURRENT_TIMESTAMP WHERE id=?",
                (existing['id'],),
            )

        try:
            category = await get_ticket_category(self.bot, guild)
        except ValueError as exc:
            return await interaction.response.send_message(
                embed=status_embed('Ticket setup needed', str(exc), 'error')
            )

        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.manage_channels:
            return await interaction.response.send_message(
                embed=status_embed('Missing bot permission', 'TowelBot needs **Manage Channels** to open tickets.', 'error')
            )

        await interaction.response.defer(thinking=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            ),
        }
        staff = await configured_role(guild, self.bot, 'staff')
        if staff:
            overwrites[staff] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )

        slug = re.sub(r'[^a-z0-9-]+', '-', interaction.user.name.lower()).strip('-')[:60] or 'member'
        channel = None
        ticket_id = None
        try:
            channel = await guild.create_text_channel(
                f'ticket-{slug}-{str(interaction.user.id)[-4:]}',
                category=category,
                overwrites=overwrites,
                reason=f'Support ticket opened by {interaction.user}',
            )
            cursor = await self.bot.db.execute(
                'INSERT INTO tickets(guild_id,channel_id,user_id) VALUES(?,?,?)',
                (guild.id, channel.id, interaction.user.id),
            )
            ticket_id = cursor.lastrowid

            ticket_embed = embed('🎫 Support ticket', 'A staff member will be with you shortly.', 'info')
            ticket_embed.add_field(name='Opened by', value=interaction.user.mention, inline=True)
            ticket_embed.add_field(name='Ticket ID', value=f'`#{ticket_id}`', inline=True)
            await channel.send(
                content=interaction.user.mention,
                embed=ticket_embed,
                view=TicketCloseView(self.bot, ticket_id),
                allowed_mentions=discord.AllowedMentions(users=[interaction.user]),
            )
            await interaction.followup.send(
                embed=status_embed('Ticket opened', f'Your private ticket is ready: {channel.mention}', 'success')
            )
        except discord.Forbidden:
            if ticket_id is not None:
                await self.bot.db.execute(
                    "UPDATE tickets SET status='closed',closed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (ticket_id,),
                )
            if channel is not None:
                try:
                    await channel.delete(reason='Ticket setup failed')
                except discord.HTTPException:
                    log.exception('Could not remove incomplete ticket channel %s', channel.id)
            await interaction.followup.send(
                embed=status_embed('Could not open ticket', 'Discord denied a channel or message action. Check TowelBot’s category permissions.', 'error')
            )
        except discord.HTTPException as exc:
            if ticket_id is not None:
                await self.bot.db.execute(
                    "UPDATE tickets SET status='closed',closed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (ticket_id,),
                )
            if channel is not None:
                try:
                    await channel.delete(reason='Ticket setup failed')
                except discord.HTTPException:
                    log.exception('Could not remove incomplete ticket channel %s', channel.id)
            await interaction.followup.send(
                embed=status_embed('Could not open ticket', f'Discord returned `{exc.status}`: {exc.text[:300]}', 'error')
            )
        except Exception:
            log.exception('Unexpected failure while opening a ticket in guild %s', guild.id)
            if ticket_id is not None:
                await self.bot.db.execute(
                    "UPDATE tickets SET status='closed',closed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (ticket_id,),
                )
            if channel is not None:
                try:
                    await channel.delete(reason='Ticket setup failed')
                except discord.HTTPException:
                    log.exception('Could not remove incomplete ticket channel %s', channel.id)
            await interaction.followup.send(
                embed=status_embed('Could not open ticket', 'An unexpected error occurred. Check bot.log and try again.', 'error')
            )


class TicketCloseView(discord.ui.View):
    def __init__(self, bot, ticket_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_id = ticket_id
        button = discord.ui.Button(
            label='Close ticket',
            style=discord.ButtonStyle.danger,
            emoji='🔒',
            custom_id=f'nation:tickets:close:{ticket_id}',
        )
        button.callback = self.close_ticket
        self.add_item(button)

    async def close_ticket(self, interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                embed=status_embed('Server required', 'Tickets can only be closed inside a server.', 'error')
            )
        row = await self.bot.db.fetchone(
            "SELECT * FROM tickets WHERE id=? AND guild_id=? AND status='open'",
            (self.ticket_id, guild.id),
        )
        if not row:
            return await interaction.response.send_message(
                embed=status_embed('Ticket already closed', 'This ticket is no longer open.', 'warning')
            )

        staff = await configured_role(guild, self.bot, 'staff')
        is_staff = isinstance(interaction.user, discord.Member) and (
            interaction.user.guild_permissions.manage_channels or (staff and staff in interaction.user.roles)
        )
        if interaction.user.id != row['user_id'] and not is_staff:
            return await interaction.response.send_message(
                embed=status_embed('Not authorized', 'Only the ticket opener, ticket staff, or a channel manager can close this ticket.', 'error')
            )

        await interaction.response.defer(thinking=True)
        lines = []
        async for message in interaction.channel.history(limit=500, oldest_first=True):
            body = message.content or ''
            if message.attachments:
                body += '\n' + '\n'.join(attachment.url for attachment in message.attachments)
            lines.append(f'[{message.created_at.isoformat()}] {message.author}: {body}')
        transcript = '\n'.join(lines)[-180000:]
        await self.bot.db.execute(
            "UPDATE tickets SET status='closed',closed_at=CURRENT_TIMESTAMP WHERE id=?",
            (self.ticket_id,),
        )
        transcript_saved = False
        try:
            logging_settings = await self.bot.db.fetchone(
                'SELECT channel_id,enabled FROM logging_config WHERE guild_id=?',
                (guild.id,),
            )
            log_channel = None
            if logging_settings and logging_settings['enabled'] and logging_settings['channel_id']:
                log_channel = guild.get_channel(logging_settings['channel_id'])
            if log_channel is None:
                legacy_log_id = self.bot.config.get('channels', {}).get('ticket_log')
                log_channel = guild.get_channel(int(legacy_log_id)) if legacy_log_id else None
            if log_channel is not None:
                transcript_file = discord.File(
                    io.BytesIO(transcript.encode('utf-8')),
                    filename=f'ticket-{self.ticket_id}-transcript.txt',
                )
                await log_channel.send(
                    content=f'Transcript for ticket `#{self.ticket_id}` — opened by <@{row["user_id"]}>',
                    file=transcript_file,
                )
                transcript_saved = True
        except (discord.HTTPException, TypeError, ValueError) as exc:
            log.warning('Could not send transcript for ticket %s: %s', self.ticket_id, exc)

        await audit(self.bot, guild, interaction.user, 'Ticket closed', interaction.channel, str(self.ticket_id))
        for item in self.children:
            setattr(item, 'disabled', True)
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            log.warning('Could not disable the close button for ticket %s', self.ticket_id)
        if isinstance(interaction.user, discord.Member):
            opener = guild.get_member(row['user_id'])
            if opener is not None:
                overwrite = interaction.channel.overwrites_for(opener)
                overwrite.send_messages = False
                await interaction.channel.set_permissions(
                    opener,
                    overwrite=overwrite,
                    reason=f'Ticket #{self.ticket_id} closed',
                )
        await interaction.channel.edit(
            name=f'closed-{interaction.channel.name}'[:100],
            reason=f'Ticket #{self.ticket_id} archived',
        )
        await interaction.followup.send(
            embed=status_embed(
                'Ticket archived',
                f'Ticket `#{self.ticket_id}` is now read-only and remains in its ticket category.'
                + (' A transcript was saved to the logging channel.' if transcript_saved else ' The archived channel remains available to staff as the transcript.'),
                'success',
            )
        )


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(TicketView(self.bot))
        open_tickets = await self.bot.db.fetchall(
            "SELECT id FROM tickets WHERE status='open'"
        )
        for ticket in open_tickets:
            self.bot.add_view(TicketCloseView(self.bot, ticket['id']))

    @commands.hybrid_command(name='ticket-category', description='Set the category that contains support tickets.')
    @admin()
    async def set_category(self, ctx, category: discord.CategoryChannel):
        await self.bot.db.set_config(ctx.guild.id, 'channels', 'ticket_category', category.id)
        await ctx.send(
            embed=status_embed('Ticket category set', f'New tickets will be created inside {category.mention}.', 'success')
        )
        await audit(self.bot, ctx.guild, ctx.author, 'Ticket category configured', category)

    @commands.hybrid_command(name='ticket-panel', description='Post the support ticket panel.')
    @admin()
    async def panel(self, ctx):
        try:
            category = await get_ticket_category(self.bot, ctx.guild)
        except ValueError as exc:
            return await ctx.send(embed=status_embed('Ticket setup needed', str(exc), 'error'))
        await ctx.send(
            embed=embed('🎫 Support', f'Open a private support ticket. Tickets will be grouped in {category.mention}.', 'brand'),
            view=TicketView(self.bot),
        )
        await audit(self.bot, ctx.guild, ctx.author, 'Ticket panel posted', category)


async def setup(bot):
    await bot.add_cog(Tickets(bot))
