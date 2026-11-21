import json

import discord
from discord import app_commands
from discord.ext import commands
from common import embed, status_embed, audit, configured_role

VERIFICATION_NAME = '🚧-verification'


def is_staff_channel(channel):
    category_name = (getattr(getattr(channel, 'category', None), 'name', '') or '').lower()
    name = getattr(channel, 'name', '').lower()
    markers = ('admin', 'staff', 'mod-log', 'automod-log', 'application-review', 'ticket-log', 'message-log', 'member-log', 'role-log')
    return any(marker in category_name or marker in name for marker in markers)


class AddQuestionModal(discord.ui.Modal, title='Add a border question'):
    question = discord.ui.TextInput(label='Question to ask immigrants', placeholder='Why do you want to join our nation?', max_length=45)

    def __init__(self, cog, guild_id, user_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id or interaction.guild_id != self.guild_id:
            return await interaction.response.send_message(embed=status_embed('Setup session protected','This setup belongs to another administrator.','error'))
        draft = self.cog.setup_drafts.setdefault((self.guild_id, self.user_id), [])
        if len(draft) >= 25:
            return await interaction.response.send_message(embed=status_embed('Question limit reached','You can add up to **25** application questions.','warning'))
        label = str(self.question.value).strip()
        if not label:
            return await interaction.response.send_message(embed=status_embed('Question required','Enter a question before saving.','error'))
        draft.append(label)
        await interaction.response.send_message(
            embed=embed('Question saved', f'**Question {len(draft)}:** {discord.utils.escape_markdown(label)}\n\nAdd another question, or finish the border setup.', 'success'),
            view=QuestionWizard(self.cog, self.guild_id, self.user_id)
        )


class QuestionWizard(discord.ui.View):
    def __init__(self, cog, guild_id, user_id):
        super().__init__(timeout=900)
        self.cog, self.guild_id, self.user_id = cog, guild_id, user_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id or interaction.guild_id != self.guild_id:
            await interaction.response.send_message(embed=status_embed('Setup session protected','This setup belongs to another administrator.','error'))
            return False
        return True

    @discord.ui.button(label='Add question', style=discord.ButtonStyle.primary, emoji='➕')
    async def add_question(self, interaction, button):
        await interaction.response.send_modal(AddQuestionModal(self.cog, self.guild_id, self.user_id))

    @discord.ui.button(label='Finish border setup', style=discord.ButtonStyle.success, emoji='✅')
    async def finish(self, interaction, button):
        questions = self.cog.setup_drafts.get((self.guild_id, self.user_id), [])
        if not questions:
            return await interaction.response.send_message(embed=status_embed('Add a question','Add at least one application question before finishing setup.','warning'))
        await interaction.response.defer(thinking=True)
        try:
            await self.cog.save_questions_and_panel(interaction.guild, questions)
            self.cog.setup_drafts.pop((self.guild_id, self.user_id), None)
            await audit(self.cog.bot, interaction.guild, interaction.user, 'Border setup completed', details=f'{len(questions)} application questions configured.')
            for child in self.children:
                setattr(child, 'disabled', True)
            try:
                await interaction.message.edit(embed=embed('Border setup complete', f'Saved **{len(questions)}** application questions and posted the application panel in **{VERIFICATION_NAME}**. Immigrants can only view that verification channel.', 'success'), view=self)
            except (discord.NotFound, discord.HTTPException):
                pass
            await interaction.followup.send(embed=status_embed('Border setup complete', f'Saved **{len(questions)}** application questions and published the public application panel.', 'success'))
        except discord.Forbidden:
            await interaction.followup.send(embed=embed('Border setup failed', 'Discord denied a role, channel, or message operation. Check TowelBot permissions and role hierarchy.', 'error'))
        except discord.NotFound as exc:
            await interaction.followup.send(embed=embed('Border setup failed', f'The message or resource no longer exists (Discord error {exc.code}). Run `/border setup` again.', 'error'))
        except discord.HTTPException as exc:
            await interaction.followup.send(embed=embed('Border setup failed', f'Discord rejected the operation: {exc.status} — {exc.text[:300]}', 'error'))
        except Exception as exc:
            self.cog.logger.exception('Border setup wizard finish failed')
            await interaction.followup.send(embed=embed('Border setup failed', f'Unexpected error: `{type(exc).__name__}: {str(exc)[:500]}`. Check bot.log.', 'error'))


class ApplicationModal(discord.ui.Modal):
    def __init__(self, cog, questions, page, answers):
        super().__init__(title=f'Citizenship application • Page {page + 1}', timeout=600)
        self.cog, self.questions, self.page, self.answers = cog, questions, page, answers
        self.items = []
        for index, question in enumerate(questions[page * 5:(page + 1) * 5]):
            field = discord.ui.TextInput(label=question[:45], style=discord.TextStyle.paragraph, required=True, max_length=1000)
            self.items.append((question, field))
            self.add_item(field)

    async def on_submit(self, interaction):
        for label, field in self.items:
            self.answers[label] = str(field.value).strip()
        next_page = self.page + 1
        if next_page * 5 < len(self.questions):
            return await interaction.response.send_modal(ApplicationModal(self.cog, self.questions, next_page, self.answers))
        await interaction.response.defer(thinking=True)
        try:
            await self.cog.submit_application(interaction, self.answers)
            await interaction.followup.send(embed=status_embed(
                'Application submitted',
                'Your citizenship application was sent to the border officers. A review decision will be recorded in your application history.',
                'success',
            ))
        except Exception as exc:
            self.cog.logger.exception('Application submission failed')
            await interaction.followup.send(embed=status_embed(
                'Application not submitted',
                f'{type(exc).__name__}: {str(exc)[:500]}\nPlease contact a server administrator.',
                'error',
            ))


class BorderPanel(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label='Apply for citizenship', style=discord.ButtonStyle.success, emoji='📝', custom_id='nation:border:apply:v2')
    async def apply(self, interaction, button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(embed=status_embed('Server required','Use this application inside the server.','error'))
        citizen = await self.cog.get_role(interaction.guild, 'citizen')
        if citizen and citizen in interaction.user.roles:
            return await interaction.response.send_message(embed=status_embed('Already a citizen','Your Discord account already has the configured Citizen role.','info'))
        existing = await self.cog.bot.db.fetchone("SELECT id FROM applications WHERE guild_id=? AND user_id=? AND status='pending'", (interaction.guild.id, interaction.user.id))
        if existing:
            return await interaction.response.send_message(embed=status_embed('Application pending','You already have a pending citizenship application.','warning'))
        rows = await self.cog.bot.db.fetchall('SELECT label FROM border_questions WHERE guild_id=? ORDER BY position', (interaction.guild.id,))
        questions = [row['label'] for row in rows]
        if not questions:
            return await interaction.response.send_message(embed=status_embed('Applications unavailable','No citizenship questions are configured yet.','warning'))
        await interaction.response.send_modal(ApplicationModal(self.cog, questions, 0, {}))


class ReviewView(discord.ui.View):
    def __init__(self, cog, application_id, applicant_id):
        super().__init__(timeout=60 * 60 * 24 * 7)
        self.cog, self.application_id, self.applicant_id = cog, application_id, applicant_id

    async def interaction_check(self, interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                embed=status_embed('Server required', 'Review applications inside the server.', 'error')
            )
            return False
        staff = await self.cog.get_role(guild, 'staff')
        allowed = interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild or (staff and staff in interaction.user.roles)
        if not allowed:
            await interaction.response.send_message(embed=status_embed('Nation Staff only','Only administrators or configured Nation Staff can review applications.','error'))
            return False
        return True

    @discord.ui.button(label='Approve', style=discord.ButtonStyle.success, emoji='✅')
    async def approve(self, interaction, button):
        await interaction.response.defer(thinking=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send(
                embed=status_embed('Server required', 'Review applications inside the server.', 'error')
            )
        row = await self.cog.bot.db.fetchone('SELECT status FROM applications WHERE id=? AND guild_id=?', (self.application_id, guild.id))
        if not row or row['status'] != 'pending':
            return await interaction.followup.send(embed=status_embed('Already reviewed','This application has already been approved or denied.','warning'))
        try:
            member = await self.cog.get_member(guild, self.applicant_id)
        except discord.HTTPException as error:
            self.cog.logger.warning(
                'Could not fetch applicant %s in guild %s while reviewing application %s: %s',
                self.applicant_id,
                guild.id,
                self.application_id,
                error,
            )
            return await interaction.followup.send(embed=status_embed(
                'Applicant lookup failed',
                f'Discord could not confirm the applicant is still in the server (error {error.status}). Try again.',
                'error',
            ))
        if member is None:
            return await interaction.followup.send(embed=status_embed(
                'Applicant no longer in server',
                'The applicant has left the server, so this application cannot be approved.',
                'error',
            ))
        citizen = await self.cog.get_role(guild, 'citizen')
        if citizen is None:
            return await interaction.followup.send(embed=status_embed(
                'Citizen role not configured',
                'Select the correct Citizen role in `/setup` → Configure roles, or rerun `/border setup`, then review again.',
                'error',
            ))
        immigrant = await self.cog.get_role(guild, 'immigrant')
        try:
            if immigrant and immigrant in member.roles:
                await member.remove_roles(immigrant, reason=f'Application #{self.application_id} approved')
            await member.add_roles(citizen, reason=f'Application #{self.application_id} approved')
        except discord.Forbidden:
            return await interaction.followup.send(embed=status_embed('Role hierarchy issue','Move TowelBot above the Immigrant and Citizen roles, then try again.','error'))
        await self.cog.bot.db.execute("UPDATE applications SET status='approved',reviewer_id=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?", (interaction.user.id, self.application_id))
        try:
            await member.send(f'Your citizenship application in **{guild.name}** has been approved. Welcome, citizen!')
        except discord.HTTPException:
            pass
        for child in self.children:
            setattr(child, 'disabled', True)
        await interaction.message.edit(view=self)
        await audit(self.cog.bot, guild, interaction.user, 'Citizenship application approved', member, f'Application #{self.application_id}')
        await interaction.followup.send(embed=status_embed('Application approved','Immigrant role removed and Citizen role assigned.','success'))

    @discord.ui.button(label='Deny', style=discord.ButtonStyle.danger, emoji='✖️')
    async def deny(self, interaction, button):
        await interaction.response.send_modal(DenyApplicationModal(self.cog, self.application_id, self.applicant_id))


class DenyApplicationModal(discord.ui.Modal, title='Deny citizenship application'):
    reason = discord.ui.TextInput(label='Reason for denial', style=discord.TextStyle.paragraph, required=True, max_length=900)

    def __init__(self, cog, application_id, applicant_id):
        super().__init__(timeout=300)
        self.cog, self.application_id, self.applicant_id = cog, application_id, applicant_id

    async def on_submit(self, interaction):
        await interaction.response.defer(thinking=True)
        row = await self.cog.bot.db.fetchone("SELECT status FROM applications WHERE id=? AND guild_id=?", (self.application_id, interaction.guild.id))
        if not row or row['status'] != 'pending':
            return await interaction.followup.send(embed=status_embed('Already reviewed','This application has already been approved or denied.','warning'))
        reason = str(self.reason.value)
        await self.cog.bot.db.execute("UPDATE applications SET status='denied',reviewer_id=?,reason=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?", (interaction.user.id, reason, self.application_id))
        member = interaction.guild.get_member(self.applicant_id)
        if member:
            try:
                await member.send(f'Your citizenship application in **{interaction.guild.name}** was not approved. Reason: {reason}')
            except discord.HTTPException:
                pass
        await audit(self.cog.bot, interaction.guild, interaction.user, 'Citizenship application denied', member, f'Application #{self.application_id}: {reason}')
        await interaction.followup.send(embed=status_embed('Application denied','The member remains an immigrant and retains verification-area access.','warning'))


class Border(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = __import__('logging').getLogger('TowelBot.border')
        self.setup_drafts = {}

    async def get_role(self, guild, key):
        return await configured_role(guild, self.bot, key)

    async def get_member(self, guild, user_id):
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None

    async def ensure_role(self, guild, name, key, color):
        found = discord.utils.get(guild.roles, name=name)
        if found is None:
            found = await guild.create_role(name=name, color=color, hoist=True, reason='Nation bot border setup')
        if guild.me and found >= guild.me.top_role:
            raise PermissionError(
                f'The **{name}** role is above TowelBot in the role hierarchy. Move TowelBot above it and run setup again.'
            )
        self.bot.config.setdefault('roles', {})[key] = found.id
        await self.bot.db.set_config(guild.id, 'roles', key, found.id)
        return found

    async def ensure_verification_channel(self, guild, immigrant, citizen):
        channel = discord.utils.get(guild.text_channels, name=VERIFICATION_NAME)
        everyone = guild.default_role
        overwrites = dict(channel.overwrites) if channel else {}
        overwrites[everyone] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)
        overwrites[immigrant] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True)
        overwrites[citizen] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True)
        bot_member = guild.me
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, manage_messages=True)
        if channel is None:
            channel = await guild.create_text_channel(VERIFICATION_NAME, overwrites=overwrites, reason='Nation bot border setup')
        else:
            await channel.edit(overwrites=overwrites, reason='Nation bot border setup')
        self.bot.config.setdefault('channels', {})['verification'] = channel.id
        self.bot.config['channels']['border'] = channel.id
        return channel

    async def apply_all_channel_permissions(self, guild, immigrant, citizen):
        verification = discord.utils.get(guild.text_channels, name=VERIFICATION_NAME)
        bot_member = guild.me
        changed = 0
        for target in list(guild.channels):
            if target.id == getattr(verification, 'id', None):
                continue
            overwrites = dict(target.overwrites)
            overwrites[immigrant] = discord.PermissionOverwrite(view_channel=False)
            if is_staff_channel(target):
                overwrites[citizen] = discord.PermissionOverwrite(view_channel=False)
            else:
                overwrites[citizen] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, connect=True, speak=True)
            if bot_member:
                overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, manage_messages=True, connect=True, speak=True)
            try:
                await target.edit(overwrites=overwrites, reason='Nation bot: immigrant/citizen access policy')
                changed += 1
            except discord.Forbidden:
                self.logger.warning('Could not update permissions for channel/category %s', target.id)
            except discord.NotFound:
                self.logger.warning('Channel/category %s no longer exists', target.id)
            except discord.HTTPException as exc:
                self.logger.warning('Could not update channel/category %s: %s', target.id, exc.text)
        return changed

    async def save_questions_and_panel(self, guild, questions):
        await self.bot.db.execute('DELETE FROM border_questions WHERE guild_id=?', (guild.id,))
        for position, label in enumerate(questions, 1):
            await self.bot.db.execute('INSERT INTO border_questions(guild_id,position,label,required,max_length) VALUES(?,?,?,?,?)', (guild.id, position, label, 1, 1000))
        verification = discord.utils.get(guild.text_channels, name=VERIFICATION_NAME)
        if verification is None:
            raise RuntimeError(f'{VERIFICATION_NAME} channel is missing')
        async for message in verification.history(limit=50):
            if message.author.id == self.bot.user.id and message.embeds and message.embeds[0].title == 'Citizenship applications':
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
        await verification.send(embed=embed('Citizenship applications', 'Welcome to the border. Press the button below and answer each question to apply for citizenship. All other server channels are hidden from immigrants.', 'info'), view=BorderPanel(self))

    async def run_border_setup(self, guild):
        me = guild.me
        if not me or not me.guild_permissions.manage_roles or not me.guild_permissions.manage_channels:
            raise PermissionError('I need Manage Roles and Manage Channels permissions.')
        immigrant = await self.ensure_role(guild, 'Immigrant', 'immigrant', discord.Color.orange())
        citizen = await self.ensure_role(guild, 'Citizen', 'citizen', discord.Color.green())
        verification = await self.ensure_verification_channel(guild, immigrant, citizen)
        changed = await self.apply_all_channel_permissions(guild, immigrant, citizen)
        # Move all existing non-bot, non-citizen members into the restricted immigrant state.
        assigned = 0
        for member in guild.members:
            if member.bot or citizen in member.roles or immigrant in member.roles:
                continue
            try:
                await member.add_roles(immigrant, reason='Border setup: existing member requires citizenship')
                assigned += 1
            except discord.Forbidden:
                self.logger.warning('Could not assign Immigrant to %s', member.id)
        self.bot.save_config()
        return immigrant, citizen, verification, changed, assigned

    async def submit_application(self, interaction, answers):
        guild = interaction.guild
        if guild is None:
            raise ValueError('Citizenship applications can only be submitted inside a server.')
        review = await self.get_application_review_channel(guild)
        cursor = await self.bot.db.execute('INSERT INTO applications(guild_id,user_id,answers) VALUES(?,?,?)', (guild.id, interaction.user.id, json.dumps(answers, ensure_ascii=False)))
        app_id = cursor.lastrowid
        description = '\n\n'.join(f'**{discord.utils.escape_markdown(key)}**\n{discord.utils.escape_markdown(value)}' for key, value in answers.items())
        card = embed(f'Citizenship application #{app_id}', description[:4000], 'info')
        card.add_field(name='Applicant', value=f'{interaction.user.mention}\n`{interaction.user.id}`', inline=False)
        await review.send(embed=card, view=ReviewView(self, app_id, interaction.user.id))

    async def get_application_review_channel(self, guild):
        review_id = await self.bot.db.get_config(guild.id, 'channels', 'application_review')
        if review_id is None:
            review_id = self.bot.config.get('channels', {}).get('application_review')

        review = None
        if review_id is not None:
            try:
                review = guild.get_channel(int(review_id))
            except (TypeError, ValueError):
                review = None
        else:
            review = (
                discord.utils.get(guild.text_channels, name='📋・application-review')
                or discord.utils.get(guild.text_channels, name='📋-application-review')
            )
            if review is not None:
                await self.bot.db.set_config(guild.id, 'channels', 'application_review', review.id)

        if review is None or review.type not in (discord.ChannelType.text, discord.ChannelType.news):
            raise ValueError(
                'Application review channel is not configured. An administrator can set it with '
                '`/border application-channel`.'
            )
        return review

    @commands.Cog.listener()
    async def on_member_join(self, member):
        immigrant = await self.get_role(member.guild, 'immigrant')
        citizen = await self.get_role(member.guild, 'citizen')
        if immigrant and not member.bot and not (citizen and citizen in member.roles):
            try:
                await member.add_roles(immigrant, reason='New member receives immigrant access')
            except discord.Forbidden:
                self.logger.warning('Cannot assign immigrant role in guild %s', member.guild.id)

    border = app_commands.Group(name='border', description='Citizenship border and immigration system')

    @border.command(name='application-channel', description='Set the channel where citizenship applications are reviewed.')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_application_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                embed=status_embed('Server required', 'Run this command inside a server.', 'error')
            )
        await self.bot.db.set_config(guild.id, 'channels', 'application_review', channel.id)
        await interaction.response.send_message(
            embed=status_embed('Application channel set', f'New citizenship applications will be sent to {channel.mention}.', 'success')
        )

    @border.command(name='setup', description='Configure immigrant/citizen roles, lock all channels, and create the verification channel.')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def border_setup(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            guild = interaction.guild
            if guild is None:
                return await interaction.followup.send(embed=status_embed('Server required','Run border setup inside a server.','error'))
            immigrant, citizen, verification, changed, assigned = await self.run_border_setup(guild)
            self.setup_drafts[(guild.id, interaction.user.id)] = []
            await interaction.followup.send(embed=embed('Border access configured', f'Created/checked {immigrant.mention} and {citizen.mention}.\n\nUpdated permissions on **{changed}** existing channels/categories.\nAssigned **{assigned}** existing members the Immigrant role.\nVerification channel: {verification.mention}\n\n**Next:** add your application questions one at a time below, then finish setup to publish the application panel.', 'success'), view=QuestionWizard(self, guild.id, interaction.user.id))
        except PermissionError as exc:
            await interaction.followup.send(embed=embed('Border setup failed', str(exc), 'error'))
        except discord.Forbidden:
            await interaction.followup.send(embed=embed('Border setup failed', 'Discord blocked a role/channel change. Give me Manage Roles and Manage Channels, then move my bot role above Immigrant and Citizen.', 'error'))
        except discord.NotFound as exc:
            await interaction.followup.send(embed=embed('Border setup failed', f'A Discord resource was missing (error {exc.code}). Refresh the server and run `/border setup` again.', 'error'))
        except discord.HTTPException as exc:
            await interaction.followup.send(embed=embed('Border setup failed', f'Discord rejected the setup: {exc.status} — {exc.text[:300]}', 'error'))
        except Exception as exc:
            self.logger.exception('Border setup failed')
            await interaction.followup.send(embed=embed('Border setup failed', 'An unexpected error occurred. Check bot.log for details.', 'error'))


async def setup(bot):
    cog = Border(bot)
    await bot.add_cog(cog)
    bot.add_view(BorderPanel(cog))
