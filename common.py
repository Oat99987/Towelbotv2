import discord
from datetime import datetime, timezone

COLORS = {'success': 0x2ECC71, 'warning': 0xF1C40F, 'error': 0xE74C3C, 'info': 0x3498DB}


def embed(title, description='', kind='info'):
    result = discord.Embed(title=title, description=description, color=COLORS.get(kind, COLORS['info']), timestamp=datetime.now(timezone.utc))
    result.set_footer(text='TowelBot • Administration')
    return result


def cfg_id(bot, section, key):
    value = bot.config.get(section, {}).get(key)
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def role(guild, bot, key):
    role_id = cfg_id(bot, 'roles', key)
    return guild.get_role(role_id) if role_id else None


def channel(guild, bot, key):
    channel_id = cfg_id(bot, 'channels', key)
    return guild.get_channel(channel_id) if channel_id else None


async def audit(bot, guild, actor, action, target=None, details=''):
    await bot.db.audit(guild.id, getattr(actor, 'id', None), action, getattr(target, 'id', None), details)
    log_channel = channel(guild, bot, 'mod_log')
    if log_channel:
        try:
            await log_channel.send(embed=embed(action, details or 'No additional details.', 'warning'))
        except discord.HTTPException:
            pass


def admin():
    async def predicate(ctx):
        return bool(ctx.guild and (ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild))
    from discord.ext import commands
    return commands.check(predicate)


def staff_role_check(bot):
    async def predicate(ctx):
        configured = role(ctx.guild, bot, 'staff')
        return ctx.author.guild_permissions.administrator or bool(configured and configured in ctx.author.roles)
    from discord.ext import commands
    return commands.check(predicate)
