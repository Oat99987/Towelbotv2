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
The approval action uses the per-server Citizen role saved in `/setup`, or the role created/found by `/border setup`.

**Permission note:** Discord permissions are authoritative. The bot needs Manage Roles and Manage Channels, and its highest role must be above the roles it assigns. Existing channel-specific permission overwrites are preserved for unrelated roles; the Immigrant and Citizen overwrites are explicitly set by the border wizard. Review your staff channel names/categories before running it.

Set the application review destination separately with `/border application-channel channel:#your-channel`. This does not require `/setup`.

## Slash commands

- `/setup` — general bot/server setup
- `/border setup` — immigrant/citizen access and application wizard
- `/border application-channel` — set the citizenship application review channel
- `/ticket-category` — choose the category where support tickets are grouped
- `/set-prefix` — set this server's prefix (Manage Server required)
- `/economy enabled:true|false` — enable or disable this server's virtual economy (Manage Server required)

### Moderation and police roleplay

- `/warnings <member>` and `/note <member> <details>` — review warnings or add a moderation case note
- `/police set-role <role>` — set the Police role (Manage Server required)
- `/police report` and `/police citation` — record roleplay reports and citations
- `/police arrest` and `/police release` — apply or remove the configured Jailed role and log the action
- `/police warrant issue`, `/police warrant clear`, and `/police warrant list` — manage server-local roleplay warrants
- `/police records <member>` — review a member's police roleplay record

Police commands require the configured Police role or Manage Server permission; server managers configure that role.
Configure the Jailed role in `/setup` before using police arrest or release. Warrants and citations are fictional
server records; they do not initiate real-world action, and citations do not change Discord permissions.

### General-purpose utilities

- `/userinfo [member]` — view a member's profile, roles, and ID
- `/roleinfo <role>` — view role membership, color, and permissions
- `/choose <options>` — choose from 2–20 options separated by `|`
- `/roll [dice]` — roll dice such as `d20` or `2d6` (up to 100 dice)
- `/coinflip` — flip a virtual coin

These utilities are also available with the configured server prefix.

### Virtual economy

The economy is disabled until configured and is isolated per server. It has no
seeded market assets or preset financial policy. A server manager first runs
`/economy-roles central_bank:<role> treasury:<role>` with two different roles.
The Central Bank role controls currency, monetary policies, forex listings, and
business licenses. The Treasury role controls the trading fee, bond offerings,
and grants.

Before enabling, configure the currency with `/economy-currency`, then set every
required value using `/economy-policy key:<key> value:<value>`. Money-valued
policies use the configured currency and accept decimal amounts; cooldowns are
seconds; rates are basis points (100 basis points = 1%); loan term is days.
The required policy keys are:

- Money: `starting_balance`, `work_min`, `work_max`, `daily_reward`,
  `business_license_fee`, `loan_max`
- Integer settings: `work_cooldown_seconds`, `daily_cooldown_seconds`,
  `savings_interest_bps`, `trading_fee_bps`, `loan_rate_bps`, `loan_term_days`

The configured Treasury role can also set the trading fee with
`/treasury fee basis_points:<value>`. Enable the economy with
`/economy enabled:true` once all settings and both roles are configured. Accounts
are created when a member first uses an economy feature; their starting balance
comes from the server policy.

The Central Bank lists forex assets with `/market create`; each listing sets its
own starting price and volatility. Company founders need an active business
license from `/blicense issue`. A licensed member chooses a company's name,
symbol, share price, share count, and simulated volatility with `/company create`.
Treasury members create bond offerings with `/bond issue`, choosing the face
value, yield, term, and number of units. There are no built-in stocks, currencies,
companies, or bond terms. Market prices are fictional and simulated once per UTC
day; there are no live quotes or real-money transactions.

Commands include `/balance`, `/portfolio`, `/work`, `/daily`, `/pay`,
`/bank deposit`, `/bank withdraw`, `/market list`, `/market history`,
`/trade buy`, `/trade sell`, `/forex exchange`, `/blicense status`,
`/bond issue`, `/bond list`, `/bond buy`, `/bond portfolio`, `/bond redeem`,
`/loan take`, `/loan repay`, `/company create`, `/company hire`,
`/company payroll`, `/company dividend`, `/treasury balance`, `/treasury fee`,
and `/treasury grant`.

All economy features are also available through the prefix group, for example
`!economy balance`, `!economy market list`, and `!economy bond list`. Configure a
server-specific prefix with `/set-prefix` or `!set-prefix`; the `!` shown here is
only the fallback prefix. Economy balances are virtual and have no cash value.

The remaining feature commands are provided by the individual cogs. The fallback
prefix defaults to `!`.

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
