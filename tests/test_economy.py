import asyncio

import pytest


def run(coroutine):
    return asyncio.run(coroutine)


async def connected_database(path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from database import Database

    database = Database(path)
    await database.connect()
    return database


async def configure_economy(service, guild_id, starting_balance='1000'):
    await service.set_roles(guild_id, 9001, 9002)
    await service.set_currency(guild_id, 'Credits', 'CRD', 'C$', 1)
    policies = {
        'starting_balance': starting_balance,
        'work_min': '5',
        'work_max': '15',
        'daily_reward': '50',
        'business_license_fee': '0',
        'loan_max': '1000',
        'work_cooldown_seconds': '3600',
        'daily_cooldown_seconds': '86400',
        'savings_interest_bps': '200',
        'trading_fee_bps': '100',
        'loan_rate_bps': '1000',
        'loan_term_days': '30',
    }
    for key, value in policies.items():
        await service.set_policy(guild_id, key, value, 1)
    await service.configure(guild_id, True)


def test_economy_enable_is_server_specific_and_transfers_are_atomic(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.economy import EconomyError, EconomyService

    async def scenario():
        database = await connected_database(tmp_path / 'economy.sqlite3')
        service = EconomyService(database)
        await configure_economy(service, 101)
        await service.configure(101, True)
        await service.configure(202, False)

        sender, _settings = await service.balance(101, 1)
        receiver, _settings = await service.balance(101, 2)
        assert sender['wallet'] == 100_000
        assert receiver['wallet'] == 100_000

        await service.bank_move(101, 1, 100, 'deposit')
        await service.bank_move(101, 1, 50, 'withdraw')
        sender, _settings = await service.balance(101, 1)
        assert sender['wallet'] == 95_000
        assert sender['bank'] == 5_000

        reward = await service.claim_income(101, 3, 'work')
        assert 500 <= reward <= 1_500
        with pytest.raises(EconomyError, match='Try again'):
            await service.claim_income(101, 3, 'work')

        await service.transfer(101, 1, 2, '100.25', 'lunch')
        sender, _settings = await service.balance(101, 1)
        receiver, _settings = await service.balance(101, 2)
        assert sender['wallet'] == 84_975
        assert receiver['wallet'] == 110_025

        concurrent = await asyncio.gather(
            service.transfer(101, 1, 2, 800),
            service.transfer(101, 1, 3, 800),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in concurrent) == 1
        sender, _settings = await service.balance(101, 1)
        assert sender['wallet'] == 4_975

        with pytest.raises(EconomyError, match='Not enough money'):
            await service.transfer(101, 1, 2, 1_000_000)
        sender, _settings = await service.balance(101, 1)
        assert sender['wallet'] == 4_975

        with pytest.raises(EconomyError, match='disabled'):
            await service.balance(202, 1)
        await service.configure(101, False)
        with pytest.raises(EconomyError, match='disabled'):
            await service.balance(101, 1)
        await service.configure(101, True)
        sender, _settings = await service.balance(101, 1)
        assert sender['wallet'] == 4_975

        await database.close()

    run(scenario())


def test_database_connection_lifecycle_is_guarded(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from database import Database

    async def scenario():
        database = Database(tmp_path / 'lifecycle.sqlite3')
        with pytest.raises(RuntimeError, match='not connected'):
            await database.fetchone('SELECT 1')

        await database.connect()
        result = await database.fetchone('SELECT 1 AS value')
        assert result is not None
        assert dict(result) == {'value': 1}
        with pytest.raises(RuntimeError, match='already connected'):
            await database.connect()

        await database.close()
        await database.close()
        with pytest.raises(RuntimeError, match='not connected'):
            await database.fetchone('SELECT 1')

    run(scenario())


def test_economy_cog_registers_slash_commands():
    import discord
    from discord.ext import commands

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.economy import Economy

    async def scenario():
        bot = commands.Bot(command_prefix='!', intents=discord.Intents.none())
        setattr(bot, 'db', object())
        await bot.add_cog(Economy(bot))
        slash_commands = bot.tree.get_commands()
        names = {command.name for command in slash_commands}
        assert {'economy', 'balance', 'portfolio', 'work', 'daily', 'pay', 'bank', 'market', 'trade', 'forex', 'bond', 'company', 'treasury', 'loan'} <= names
        for command in slash_commands:
            command.to_dict(bot.tree)
        prefix_group = bot.get_command('economy')
        assert isinstance(prefix_group, commands.Group)
        assert {'enable', 'disable', 'roles', 'currency', 'policy', 'balance', 'portfolio', 'work', 'daily', 'pay', 'history'} <= {
            command.name for command in prefix_group.commands
        }
        assert {'market', 'bank', 'trade', 'forex', 'blicense', 'bond', 'company', 'treasury', 'loan'} <= {
            command.name for command in prefix_group.commands
        }
        bond_group = prefix_group.get_command('bond')
        company_group = prefix_group.get_command('company')
        assert isinstance(bond_group, commands.Group)
        assert isinstance(company_group, commands.Group)
        assert bond_group.get_command('issue') is not None
        assert company_group.get_command('create') is not None
        await bot.close()

    run(scenario())


def test_set_prefix_persists_for_one_guild_without_changing_fallback():
    from collections.abc import Awaitable, Callable
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from typing import cast
    from discord.ext import commands

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.core import Core

    class FakeDatabase:
        def __init__(self):
            self.values = {}

        async def set_config(self, guild_id, section, key, value):
            self.values[(guild_id, section, key)] = value

        async def audit(self, *args):
            return None

    class FakeGuild:
        id = 321

        def get_channel(self, channel_id):
            return None

    class FakeBot:
        config = {'prefix': '!'}
        db = FakeDatabase()

    sent = []

    async def send(**kwargs):
        sent.append(kwargs)

    ctx = SimpleNamespace(
        guild=FakeGuild(),
        author=SimpleNamespace(id=654),
        send=send,
    )

    async def scenario():
        core = Core(FakeBot())
        callback = cast(
            Callable[[Core, commands.Context, str], Awaitable[None]],
            Core.set_prefix.callback,
        )
        await callback(core, cast(commands.Context, ctx), '?')

    run(scenario())
    assert FakeBot.db.values[(321, 'core', 'prefix')] == '?'
    assert FakeBot.config == {'prefix': '!'}
    assert sent


def test_prefix_resolver_uses_guild_prefix_with_global_fallback():
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.core import resolve_command_prefix

    class FakeDatabase:
        async def get_config(self, guild_id, section, key):
            assert (section, key) == ('core', 'prefix')
            return {321: '?', 654: None}.get(guild_id)

    class FakeBot:
        user = SimpleNamespace(id=1)
        config = {'prefix': '!'}
        db = FakeDatabase()

    async def scenario():
        guild_prefixes = await resolve_command_prefix(FakeBot(), SimpleNamespace(guild=SimpleNamespace(id=321)))
        fallback_prefixes = await resolve_command_prefix(FakeBot(), SimpleNamespace(guild=SimpleNamespace(id=654)))
        dm_prefixes = await resolve_command_prefix(FakeBot(), SimpleNamespace(guild=None))
        assert '?' in guild_prefixes
        assert '!' in fallback_prefixes
        assert '!' in dm_prefixes

    run(scenario())


def test_economy_requires_governance_setup_and_checks_configured_roles(tmp_path):
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.economy import EconomyError, EconomyService

    async def scenario():
        database = await connected_database(tmp_path / 'governance.sqlite3')
        service = EconomyService(database)

        with pytest.raises(EconomyError, match='Central Bank and Treasury roles'):
            await service.configure(505, True)
        with pytest.raises(EconomyError, match='different roles'):
            await service.set_roles(505, 9001, 9001)

        await service.set_roles(505, 9001, 9002)
        await service.set_currency(505, 'Credits', 'CRD', 'C$', 1)
        with pytest.raises(EconomyError, match='Missing:'):
            await service.configure(505, True)

        await configure_economy(service, 506)
        assert await service.role_authorized(506, SimpleNamespace(roles=[SimpleNamespace(id=9001)]), 'central_bank')
        assert not await service.role_authorized(506, SimpleNamespace(roles=[SimpleNamespace(id=9001)]), 'treasury')
        await database.close()

    run(scenario())


def test_economy_migration_removes_only_unheld_legacy_assets_and_preserves_bonds(tmp_path):
    import sqlite3

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from database import Database

    path = tmp_path / 'legacy-economy.sqlite3'
    connection = sqlite3.connect(path)
    connection.executescript('''
        CREATE TABLE economy_bonds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            principal INTEGER NOT NULL,
            yield_bps INTEGER NOT NULL,
            term_days INTEGER NOT NULL,
            purchased_at INTEGER NOT NULL,
            matures_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );
        INSERT INTO economy_bonds
            (guild_id,user_id,principal,yield_bps,term_days,purchased_at,matures_at)
            VALUES(1,2,10000,500,30,100,200);
        CREATE TABLE economy_assets (
            guild_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            price INTEGER NOT NULL,
            volatility_bps INTEGER NOT NULL DEFAULT 0,
            quote_day TEXT NOT NULL,
            company_id INTEGER,
            PRIMARY KEY(guild_id,symbol)
        );
        CREATE TABLE economy_holdings (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            quantity_milli INTEGER NOT NULL,
            average_cost INTEGER NOT NULL,
            PRIMARY KEY(guild_id,user_id,symbol)
        );
        CREATE TABLE economy_price_history (
            guild_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            quote_day TEXT NOT NULL,
            price INTEGER NOT NULL,
            PRIMARY KEY(guild_id,symbol,quote_day)
        );
        INSERT INTO economy_assets VALUES
            (1,'TWL','Legacy unheld asset','stock',100,0,'2024-01-01',NULL),
            (1,'ORB','Legacy held asset','stock',100,0,'2024-01-01',NULL);
        INSERT INTO economy_holdings VALUES(1,2,'ORB',1000,100);
        INSERT INTO economy_price_history VALUES
            (1,'TWL','2024-01-01',100),
            (1,'ORB','2024-01-01',100);
    ''')
    connection.commit()
    connection.close()

    async def scenario():
        database = Database(path)
        await database.connect()
        columns = await database.fetchall('PRAGMA table_info(economy_bonds)')
        assert {'offering_id', 'units'} <= {column['name'] for column in columns}
        bond = await database.fetchone('SELECT principal,offering_id,units FROM economy_bonds WHERE id=1')
        assert bond is not None
        assert tuple(bond) == (10_000, None, 1)
        assets = await database.fetchall('SELECT symbol FROM economy_assets ORDER BY symbol')
        assert [row['symbol'] for row in assets] == ['ORB']
        await database.close()

    run(scenario())


def test_simulated_stock_forex_bonds_and_loans(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.economy import EconomyError, EconomyService

    async def scenario():
        database = await connected_database(tmp_path / 'markets.sqlite3')
        service = EconomyService(database)
        await configure_economy(service, 303)
        await service.configure(303, True)

        assert await service.market(303) == []
        with pytest.raises(EconomyError, match='active business license'):
            await service.create_company(303, 1, 'Unlicensed Works', 'UNL', 10, 1000, 250)
        await service.issue_license(303, 1, 9001)
        await service.create_company(303, 1, 'Example Works', 'EXW', 10, 1000, 250)
        await service.create_forex_asset(303, 'EUR', 'Euro player listing', 100, 100)

        await service.trade(303, 1, 'EXW', 1, 'sell')
        _asset, gross, fee, _delta = await service.trade(303, 1, 'EXW', 0.5, 'buy')
        assert gross > 0
        assert fee >= 0
        holding = await database.fetchone(
            'SELECT quantity_milli FROM economy_holdings WHERE guild_id=? AND user_id=? AND symbol=?',
            (303, 1, 'EXW'),
        )
        assert holding is not None
        assert holding['quantity_milli'] == 999_500
        with pytest.raises(EconomyError, match='do not own'):
            await service.trade(303, 1, 'EXW', 1000, 'sell')

        output, _fee, _base = await service.exchange(303, 1, 'CRD', 'EUR', '100.00')
        assert output > 0
        fx_holding = await database.fetchone(
            'SELECT quantity_milli FROM economy_holdings WHERE guild_id=? AND user_id=? AND symbol=?',
            (303, 1, 'EUR'),
        )
        assert fx_holding is not None
        assert fx_holding['quantity_milli'] == output
        holdings, _account = await service.portfolio(303, 1)
        assert {holding['symbol'] for holding in holdings} == {'EXW', 'EUR'}
        converted, _fee, _base = await service.exchange(303, 1, 'EUR', 'CRD', output / 1000)
        assert converted > 0

        offering_id = await service.issue_bond(303, 9002, 'Community Bond', 10, 500, 7, 5)
        bond_id, _principal, _yield, _maturity = await service.buy_bond(303, 1, offering_id, 1)
        await database.execute('UPDATE economy_bonds SET matures_at=0 WHERE id=?', (bond_id,))
        paid, count = await service.redeem_bonds(303, 1)
        assert paid >= 1_000
        assert count == 1

        loan_id, principal = await service.take_loan(303, 1, 20)
        assert loan_id > 0
        paid, remaining = await service.repay_loan(303, 1, principal / 100)
        assert paid >= principal
        assert remaining == 0
        assert await service.loan_status(303, 1) is None

        await database.close()

    run(scenario())


def test_company_payroll_and_validation(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.economy import EconomyError, EconomyService

    async def scenario():
        database = await connected_database(tmp_path / 'companies.sqlite3')
        service = EconomyService(database)
        await configure_economy(service, 404, '2000')
        await service.issue_license(404, 10, 9001)

        company_id, issued_shares = await service.create_company(404, 10, 'Example Works', 'EXW', 1, 1000, 100)
        assert company_id > 0
        assert issued_shares == 1000
        await service.company_cash(404, 10, 'EXW', 1500, 'deposit')
        await service.company_hire(404, 10, 'EXW', 20, 125)
        total, employees = await service.company_payroll(404, 10, 'EXW')
        assert total == 12_500
        assert employees == 1
        worker, _settings = await service.balance(404, 20)
        assert worker['wallet'] == 212_500
        await service.trade(404, 10, 'EXW', 500, 'sell')
        await service.trade(404, 20, 'EXW', 100, 'buy')
        founder_before, _settings = await service.balance(404, 10)
        worker_before, _settings = await service.balance(404, 20)
        paid, shareholders = await service.company_dividend(404, 10, 'EXW', 1_000)
        assert paid == 100_000
        assert shareholders == 2
        founder_after, _settings = await service.balance(404, 10)
        worker_after, _settings = await service.balance(404, 20)
        assert founder_after['wallet'] - founder_before['wallet'] == 83_334
        assert worker_after['wallet'] - worker_before['wallet'] == 16_666

        with pytest.raises(EconomyError, match='company owner'):
            await service.company_cash(404, 20, 'EXW', 1, 'withdraw')
        with pytest.raises(EconomyError, match='already in use'):
            await service.create_company(404, 10, 'Another Works', 'EXW', 1, 1000, 100)

        await database.close()

    run(scenario())
