import discord
import logging
from discord import app_commands
from discord.ext import commands
from common import embed, audit


class Setup(commands.Cog):
    """One-time general server setup. Border permissions are deliberately handled by /border setup."""

    def __init__(self, bot):
        self.bot = bot

    async def ensure_role(self, guild, name, color):
        found = discord.utils.get(guild.roles, name=name)
        if found:
            return found
        return await guild.create_role(name=name, color=color, reason='TowelBot /setup')

    async def ensure_category(self, guild, name, overwrites):
        category = discord.utils.get(guild.categories, name=name)
        if category is None:
            return await guild.create_category(name, overwrites=overwrites, reason='TowelBot /setup')
        await category.edit(overwrites=overwrites, reason='TowelBot /setup permission sync')
        return category

    async def ensure_text(self, guild, name, category, overwrites=None):
        existing = discord.utils.get(category.text_channels, name=name)
        if existing:
            await existing.edit(overwrites=overwrites or category.overwrites, reason='TowelBot /setup')
            return existing
        return await guild.create_text_channel(name, category=category, overwrites=overwrites, reason='TowelBot /setup')

    @app_commands.command(name='setup', description='Create or repair the standard TowelBot server structure.')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_server(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        guild = interaction.guild
        me = guild.me
        if not me or not me.guild_permissions.manage_channels or not me.guild_permissions.manage_roles:
            return await interaction.followup.send(embed=embed('Setup failed', 'I need **Manage Channels** and **Manage Roles**. Also move my bot role above the roles I should manage.', 'error'))

        try:
            staff = await self.ensure_role(guild, 'Nation Staff', discord.Color.blurple())
            muted = await self.ensure_role(guild, 'Muted', discord.Color.dark_grey())
            jailed = await self.ensure_role(guild, 'Jailed', discord.Color.dark_red())
            self.bot.config.setdefault('roles', {}).update({'staff': staff.id, 'border_officer': staff.id, 'muted': muted.id, 'jailed': jailed.id})

            everyone = guild.default_role
            staff_ow = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                staff: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            }
            public_ow = {
                everyone: discord.PermissionOverwrite(view_channel=True, read_message_history=True),
                me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True),
            }
            staff_cat = await self.ensure_category(guild, 'Administration', staff_ow)
            community_cat = await self.ensure_category(guild, 'Community', public_ow)

            channels = {
                'welcome': await self.ensure_text(guild, '👋-welcome', community_cat, public_ow),
                'announcement': await self.ensure_text(guild, '📢-announcements', community_cat, public_ow),
                'mod_log': await self.ensure_text(guild, '🛡️-mod-log', staff_cat, staff_ow),
                'automod_log': await self.ensure_text(guild, '🤖-automod-log', staff_cat, staff_ow),
                'application_review': await self.ensure_text(guild, '📋-application-review', staff_cat, staff_ow),
                'message_log': await self.ensure_text(guild, '💬-message-log', staff_cat, staff_ow),
                'member_log': await self.ensure_text(guild, '👥-member-log', staff_cat, staff_ow),
                'role_log': await self.ensure_text(guild, '🎭-role-log', staff_cat, staff_ow),
                'ticket_log': await self.ensure_text(guild, '🎫-ticket-log', staff_cat, staff_ow),
            }
            self.bot.config.setdefault('channels', {}).update({key: value.id for key, value in channels.items()})
            self.bot.config.setdefault('feature_toggles', {}).update({
                'border': True, 'moderation': True, 'automod': True, 'elections': True,
                'community': True, 'reaction_roles': True, 'reminders_polls': True,
                'starboard': True, 'tickets': True, 'voice': True,
                'announcements_stats': True, 'logging': True,
            })
            self.bot.config.setdefault('automod', {})['enabled'] = True
            self.bot.config.setdefault('levels', {})['enabled'] = True
            self.bot.save_config()
            await audit(self.bot, guild, interaction.user, 'Full bot setup completed', details='General roles, categories, channels and feature toggles created. Run /border setup separately to configure immigrant/citizen access.')
            await interaction.followup.send(embed=embed('TowelBot setup complete', 'Created or repaired the standard roles, community channels, administration channels and feature settings.\n\n**Next:** run `/border setup` to create the Immigrant/Citizen roles, lock every channel to immigrants, and configure your application questions.', 'success'))
        except discord.Forbidden:
            await interaction.followup.send(embed=embed('Setup failed', 'Discord denied a setup action. Check Manage Roles / Manage Channels and the bot role hierarchy.', 'error'))
        except Exception:
            logging.getLogger('nationbot.setup').exception('Setup failed')
            await interaction.followup.send(embed=embed('Setup failed', 'An unexpected error occurred. Check bot.log for details.', 'error'))


async def setup(bot):
    await bot.add_cog(Setup(bot))
