import os
import json
import logging
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv
from database import Database
from common import embed

BASE = Path(__file__).parent
load_dotenv(BASE / '.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    handlers=[logging.FileHandler(BASE / 'bot.log', encoding='utf-8'), logging.StreamHandler()],
)
log = logging.getLogger('TowelBot')


def read_config():
    path = BASE / 'config.json'
    if not path.exists():
        path.write_text(json.dumps({'prefix': '!', 'guild_id': None, 'roles': {}, 'channels': {}}, indent=2), encoding='utf-8')
    return json.loads(path.read_text(encoding='utf-8'))


class TowelBot(commands.Bot):
    def __init__(self):
        self.config = read_config()
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        intents.moderation = True
        super().__init__(command_prefix=lambda _bot, _msg: self.config.get('prefix', '!'), intents=intents, help_command=None)
        self.db = Database(BASE / 'data' / 'nation.sqlite3')

    def save_config(self):
        (BASE / 'config.json').write_text(json.dumps(self.config, indent=2), encoding='utf-8')

    async def setup_hook(self):
        await self.db.connect()
        modules = [
            'core', 'setup', 'border', 'moderation', 'automod', 'elections', 'community',
            'reaction_roles', 'reminders_polls', 'starboard', 'tickets', 'voice',
            'announcements_stats', 'logging', 'nation_system',
        ]
        for module in modules:
            try:
                await self.load_extension(f'cogs.{module}')
                log.info('Loaded cogs.%s', module)
            except Exception:
                log.exception('Failed loading cogs.%s', module)
        synced_commands = await self.tree.sync()
        log.info('Synced %d global slash commands', len(synced_commands))

    async def close(self):
        await self.db.close()
        await super().close()


bot = TowelBot()


@bot.event
async def on_ready():
    user = bot.user
    if user is None:
        log.error('Connected without a bot user')
        return
    log.info('Connected as %s (%s)', user, user.id)


@bot.before_invoke
async def acknowledge_slash_command(ctx):
    if ctx.interaction is not None and not ctx.interaction.response.is_done():
        await ctx.defer()


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        message = 'You do not have the required Discord permissions.'
    elif isinstance(error, commands.CheckFailure):
        message = 'You are not authorized to use this command.'
    elif isinstance(error, commands.MissingRequiredArgument):
        message = f'Missing argument: `{error.param.name}`.'
    elif isinstance(error, commands.BadArgument):
        message = 'One of the supplied arguments is invalid.'
    else:
        log.exception('Prefix command failed', exc_info=error)
        message = 'The command failed. Check bot.log for details.'
    error_embed = embed('Command error', message, 'error')
    if ctx.interaction is not None:
        await ctx.send(embed=error_embed)
    else:
        await ctx.reply(embed=error_embed, mention_author=False, delete_after=20)


@bot.tree.error
async def app_error(interaction: discord.Interaction, error: Exception):
    log.error('Application command failed', exc_info=(type(error), error, error.__traceback__))
    if isinstance(error, discord.app_commands.MissingPermissions):
        message = 'You do not have the required Discord permissions.'
    elif isinstance(error, discord.app_commands.CheckFailure):
        message = 'You are not authorized to use this command.'
    elif isinstance(error, discord.Forbidden):
        message = 'Discord denied the operation. Check TowelBot permissions and role hierarchy.'
    elif isinstance(error, discord.NotFound):
        message = 'A required Discord message, channel, or role no longer exists. Refresh and try again.'
    elif isinstance(error, discord.HTTPException):
        message = f'Discord rejected the operation ({error.status}): {error.text[:300]}'
    elif isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.HTTPException):
        original = error.original
        message = f'Discord rejected the operation ({original.status}): {original.text[:300]}'
    else:
        message = 'The command failed. Check bot.log for details.'
    error_embed = embed('Command error', message, 'error')
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=error_embed)
        else:
            await interaction.response.send_message(embed=error_embed)
    except discord.HTTPException as response_error:
        log.error('Could not send slash-command error response: %s', response_error)


if __name__ == '__main__':
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise SystemExit('Missing DISCORD_TOKEN. Add it to the .env file.')
    bot.run(token, log_handler=None)
