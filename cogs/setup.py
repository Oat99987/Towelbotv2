import discord
from discord import app_commands
from discord.ext import commands

from common import embed, status_embed


CHANNEL_KEYS = (
    ('welcome', 'Welcome'),
    ('announcement', 'Announcements'),
    ('logging', 'Unified logging'),
    ('application_review', 'Application review'),
    ('ticket_category', 'Ticket category'),
)


class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, cog, key, label):
        super().__init__(
            placeholder=f'Select {label.lower()} channel',
            channel_types=[discord.ChannelType.text, discord.ChannelType.category],
            min_values=1,
            max_values=1,
        )
        self.cog, self.key = cog, key

    async def callback(self, interaction):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(embed=status_embed('Administrators only', 'You need **Manage Server** permission to change setup.', 'error'))
        selected = self.values[0]
        if not isinstance(selected, (discord.TextChannel, discord.CategoryChannel)):
            return await interaction.response.send_message(embed=status_embed('Invalid selection', 'Please select a text channel or category.', 'error'))
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(embed=status_embed('Server required', 'Run this option inside a server.', 'error'))
        await self.cog.bot.db.set_config(guild.id, 'channels', self.key, selected.id)
        await interaction.response.send_message(
            embed=status_embed('Setting saved', f'**{self.placeholder}**\n{selected.mention}', 'success')
        )


class RoleSelect(discord.ui.RoleSelect):
    def __init__(self, cog, key, label):
        super().__init__(placeholder=f'Select {label.lower()} role', min_values=1, max_values=1)
        self.cog, self.key = cog, key

    async def callback(self, interaction):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(embed=status_embed('Administrators only', 'You need **Manage Server** permission to change setup.', 'error'))
        selected = self.values[0]
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(embed=status_embed('Server required', 'Run this option inside a server.', 'error'))
        await self.cog.bot.db.set_config(guild.id, 'roles', self.key, selected.id)
        await interaction.response.send_message(embed=status_embed('Setting saved', f'**{self.placeholder}**\n{selected.mention}', 'success'))


class CreateResourcesConfirm(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.button(label='Create missing resources', style=discord.ButtonStyle.danger, emoji='🔧')
    async def create_resources(self, interaction, button):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(embed=status_embed('Administrators only', 'You need **Manage Server** permission to change setup.', 'error'))
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(embed=status_embed('Server required', 'Run this option inside a server.', 'error'))
        if not guild.me.guild_permissions.manage_roles or not guild.me.guild_permissions.manage_channels:
            return await interaction.response.send_message(embed=status_embed(
                'Missing bot permissions',
                'TowelBot needs **Manage Roles** and **Manage Channels**.',
                'error',
            ))
        await interaction.response.defer(thinking=True)

        role_specs = (
            ('staff', 'Nation Staff', discord.Color.blurple()),
            ('immigrant', 'Immigrant', discord.Color.orange()),
            ('citizen', 'Citizen', discord.Color.green()),
        )
        for key, name, color in role_specs:
            role = discord.utils.get(guild.roles, name=name)
            if role is None:
                role = await guild.create_role(name=name, color=color, hoist=True, reason='TowelBot confirmed setup')
            await self.cog.bot.db.set_config(guild.id, 'roles', key, role.id)

        community = discord.utils.get(guild.categories, name='🏛️・Community')
        if community is None:
            community = await guild.create_category('🏛️・Community', reason='TowelBot confirmed setup')

        staff_category = discord.utils.get(guild.categories, name='🛡️・Administration')
        if staff_category is None:
            staff_category = await guild.create_category('🛡️・Administration', reason='TowelBot confirmed setup')

        channel_specs = (
            ('welcome', '👋・welcome', community),
            ('announcement', '📢・announcements', community),
            ('logging', '🧾・logs', staff_category),
            ('application_review', '📋・application-review', staff_category),
        )
        for key, name, category in channel_specs:
            channel = discord.utils.get(category.text_channels, name=name)
            if channel is None:
                channel = await guild.create_text_channel(name, category=category, reason='TowelBot confirmed setup')
            await self.cog.bot.db.set_config(guild.id, 'channels', key, channel.id)

        ticket_category = discord.utils.get(guild.categories, name='🎫・Tickets')
        if ticket_category is None:
            ticket_category = await guild.create_category('🎫・Tickets', reason='TowelBot confirmed setup')
        await self.cog.bot.db.set_config(guild.id, 'channels', 'ticket_category', ticket_category.id)
        await interaction.followup.send(embed=status_embed(
            'Resources created',
            'Missing TowelBot roles and channels were created.\nReview the role hierarchy and channel permissions after setup.',
            'success',
        ))


class SetupDashboard(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=900)
        self.cog = cog

    @discord.ui.button(label='Configure channels', style=discord.ButtonStyle.primary, emoji='📡')
    async def configure_channels(self, interaction, button):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(embed=status_embed('Administrators only', 'You need **Manage Server** permission to change setup.', 'error'))
        await interaction.response.send_message(
            embed=embed('📡 Channel settings', 'Choose an existing resource for each feature. No resources are created from this panel.', 'brand'),
            view=ChannelSettingsView(self.cog),
        )

    @discord.ui.button(label='Configure roles', style=discord.ButtonStyle.primary, emoji='🎭')
    async def configure_roles(self, interaction, button):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(embed=status_embed('Administrators only', 'You need **Manage Server** permission to change setup.', 'error'))
        await interaction.response.send_message(
            embed=embed('🎭 Role settings', 'Choose the existing roles used by TowelBot.', 'brand'),
            view=RoleSettingsView(self.cog),
        )

    @discord.ui.button(label='Create missing resources', style=discord.ButtonStyle.primary, emoji='🔧')
    async def confirm_creation(self, interaction, button):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(embed=status_embed('Administrators only', 'You need **Manage Server** permission to change setup.', 'error'))
        await interaction.response.send_message(
            embed=status_embed(
                'Confirm resource creation',
                'TowelBot will create only missing resources with standard TowelBot names. Continue?',
                'warning',
            ),
            view=CreateResourcesConfirm(self.cog),
        )


class ChannelSettingsView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=900)
        for key, label in CHANNEL_KEYS:
            self.add_item(ChannelSelect(cog, key, label))


class RoleSettingsView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=900)
        for key, label in (('staff', 'Staff'), ('citizen', 'Citizen'), ('immigrant', 'Immigrant')):
            self.add_item(RoleSelect(cog, key, label))


class Setup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='setup', description='Open the TowelBot setup dashboard.')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_dashboard(self, interaction):
        status = []
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(embed=status_embed('Server required', 'Run setup inside a server.', 'error'))
        for key, label in CHANNEL_KEYS:
            value = await self.bot.db.get_config(guild.id, 'channels', key)
            status.append(f'{"✅" if value else "⚪"} {label}')
        for key, label in (('staff', 'Staff role'), ('citizen', 'Citizen role'), ('immigrant', 'Immigrant role')):
            value = await self.bot.db.get_config(guild.id, 'roles', key)
            status.append(f'{"✅" if value else "⚪"} {label}')

        dashboard = embed(
            '🛠️ TowelBot Setup Dashboard',
            'Choose existing resources below. TowelBot never creates resources unless you explicitly confirm it.',
            'brand',
        )
        dashboard.add_field(name='Setup status', value='\n'.join(status) or 'No settings found', inline=False)
        dashboard.add_field(name='Configuration', value='Use the dropdowns to assign channels and roles.', inline=False)
        await interaction.response.send_message(
            embed=dashboard,
            view=SetupDashboard(self),
        )


async def setup(bot):
    await bot.add_cog(Setup(bot))
