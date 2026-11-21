# TowelBot

TowelBot is a multi-server Discord bot with persistent SQLite storage and modular
`discord.py` cogs for citizenship, government, courts, territories, profiles,
leveling, moderation, tickets, elections, and logging.

## Requirements

- Python 3.10+
- Discord bot created in the Discord Developer Portal
- Server-side bot permissions: **Manage Roles**, **Manage Channels**, **View Channels**, **Send Messages**, **Embed Links**, **Read Message History**, and **Moderate Members** for moderation features
- Bot role positioned above the `Immigrant`, `Citizen`, `Nation Staff`, `Muted`, and `Jailed` roles

Enable the **Server Members Intent** and **Message Content Intent** in the Developer Portal. Presence intent is not required by the core setup.

## Install and run

1. Extract this folder and open it in VS Code.
2. Open the integrated terminal in this folder.
3. Run `python -m pip install -r requirements.txt`.
4. Copy `.env.example` to `.env` and put your bot token after `DISCORD_TOKEN=`.
5. Run `python bot.py`.

Keep `.env` private. Never post your bot token in chat or commit it to Git.

## First-time server setup

Run these commands as a server administrator:

### 1. `/setup`

Opens the setup dashboard. It does not create channels or categories automatically.
Administrators may select existing channels and roles or explicitly confirm creation
of missing TowelBot resources.

### 2. `/border setup`

This is the border access wizard. It:

- Creates or finds the `Immigrant` and `Citizen` roles.
- Creates the exact verification channel `🚧-verification`.
- Applies the access policy to existing categories and channels.
- Hides every channel except `🚧-verification` from the Immigrant role.
- Gives Citizens access to non-staff channels while keeping administration/log channels staff-only.
- Assigns the Immigrant role to existing non-citizen members.
- Then asks you to add application questions one at a time.
- Publishes the citizenship application panel when you finish.

New members are automatically assigned the Immigrant role. Approving an application removes Immigrant and grants Citizen. Denying an application leaves the person as an Immigrant; it does not kick or ban them.

**Permission note:** Discord permissions are authoritative. The bot needs Manage Roles and Manage Channels, and its highest role must be above the roles it assigns. Existing channel-specific permission overwrites are preserved for unrelated roles; the Immigrant and Citizen overwrites are explicitly set by the border wizard. Review your staff channel names/categories before running it.

## Slash commands

- `/setup` — general bot/server setup
- `/border setup` — immigrant/citizen access and application wizard

The remaining feature commands are provided by the individual cogs. Prefix defaults to `!`.

## Database and logs

- SQLite database: `data/nation.sqlite3`
- Runtime log: `bot.log`
- Server configuration: `config.json`

Back up the `data` folder and `config.json` before moving hosts or reinstalling.

## Troubleshooting "The application did not respond"

- Keep the bot process running while using the command.
- Check `bot.log` for the exact Python exception.
- Make sure the bot has Manage Roles and Manage Channels.
- Move the bot role above Immigrant and Citizen.
- Enable Server Members Intent.
- Re-invite the bot with the `bot` and `applications.commands` OAuth scopes.
- Restart the bot after replacing files.

Run `python -m compileall -q .` and `python -m pytest -q` before deployment.
Discord role hierarchy and permission overwrites require a live server test.
