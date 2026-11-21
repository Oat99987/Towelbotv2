from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import random
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from common import embed, status_embed


YEAR_SECONDS = 365 * 24 * 60 * 60
POLICY_MONEY = {
    'starting_balance',
    'work_min',
    'work_max',
    'daily_reward',
    'business_license_fee',
    'loan_max',
}
POLICY_LIMITS = {
    'work_cooldown_seconds': (1, 365 * 24 * 60 * 60),
    'daily_cooldown_seconds': (1, 365 * 24 * 60 * 60),
    'savings_interest_bps': (0, 100000),
    'trading_fee_bps': (0, 10000),
    'loan_rate_bps': (0, 100000),
    'loan_term_days': (1, 3650),
}
REQUIRED_POLICIES = POLICY_MONEY | set(POLICY_LIMITS)


class EconomyError(ValueError):
    pass


def parse_money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise EconomyError('Enter a valid amount.')
    exponent = amount.as_tuple().exponent
    if not amount.is_finite() or amount <= 0 or not isinstance(exponent, int) or exponent < -2:
        raise EconomyError('Amount must be positive and use no more than two decimal places.')
    return int(amount * 100)

def parse_nonnegative_money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise EconomyError('Enter a valid amount.')
    exponent = amount.as_tuple().exponent
    if not amount.is_finite() or amount < 0 or not isinstance(exponent, int) or exponent < -2:
        raise EconomyError('Amount must be non-negative and use no more than two decimal places.')
    return int(amount * 100)


def parse_quantity(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise EconomyError('Enter a valid quantity.')
    exponent = amount.as_tuple().exponent
    if not amount.is_finite() or amount <= 0 or not isinstance(exponent, int) or exponent < -3:
        raise EconomyError('Quantity must be positive and use no more than three decimal places.')
    return int(amount * 1000)


def format_money(minor, symbol: object = ''):
    sign = '-' if minor < 0 else ''
    absolute = abs(minor)
    return f'{sign}{str(symbol)}{absolute // 100:,}.{absolute % 100:02d}'


def format_quantity(quantity_milli):
    return f'{quantity_milli / 1000:,.3f}'.rstrip('0').rstrip('.')


async def _fetchone(conn, sql, params=()):
    async with conn.execute(sql, params) as cursor:
        return await cursor.fetchone()


async def _fetchall(conn, sql, params=()):
    async with conn.execute(sql, params) as cursor:
        return await cursor.fetchall()


class EconomyService:
    def __init__(self, database):
        self.db = database

    async def configure(self, guild_id, enabled):
        async with self.db.transaction() as conn:
            await conn.execute(
                'INSERT OR IGNORE INTO economy_settings(guild_id) VALUES(?)',
                (guild_id,),
            )
            await conn.execute(
                'INSERT OR IGNORE INTO economy_treasury(guild_id) VALUES(?)',
                (guild_id,),
            )
            if enabled:
                await self._load_configuration(conn, guild_id)
            await conn.execute(
                'UPDATE economy_settings SET enabled=? WHERE guild_id=?',
                (int(enabled), guild_id),
            )
        settings = await self.settings(guild_id)
        if settings is None:
            raise RuntimeError('Economy configuration was not persisted.')
        return settings

    async def settings(self, guild_id):
        async with self.db.transaction() as conn:
            row = await _fetchone(
                conn,
                'SELECT * FROM economy_settings WHERE guild_id=?',
                (guild_id,),
            )
            if row is None:
                return None
            if row['enabled']:
                return dict(await self._load_configuration(conn, guild_id), enabled=True)
            policies = await _fetchall(
                conn,
                'SELECT policy,value FROM economy_policies WHERE guild_id=?',
                (guild_id,),
            )
            return {'enabled': False, **{item['policy']: item['value'] for item in policies}}

    async def _enabled_settings(self, conn, guild_id):
        settings = await _fetchone(
            conn,
            'SELECT * FROM economy_settings WHERE guild_id=?',
            (guild_id,),
        )
        if settings is None or not settings['enabled']:
            raise EconomyError('The economy is disabled in this server. Ask a server manager to run `/economy enabled:true`.')
        return await self._load_configuration(conn, guild_id)

    async def _load_configuration(self, conn, guild_id):
        roles = await _fetchone(
            conn,
            'SELECT central_bank_role_id,treasury_role_id FROM economy_roles WHERE guild_id=?',
            (guild_id,),
        )
        if roles is None:
            raise EconomyError('Configure the Central Bank and Treasury roles with `/economy-roles` before enabling the economy.')
        rows = await _fetchall(
            conn,
            'SELECT policy,value FROM economy_policies WHERE guild_id=?',
            (guild_id,),
        )
        values = {row['policy']: row['value'] for row in rows}
        required = REQUIRED_POLICIES | {'currency_name', 'currency_code', 'currency_symbol'}
        missing = sorted(required - values.keys())
        if missing:
            raise EconomyError(f'Configure all economy policies before enabling it. Missing: {", ".join(missing)}.')
        result = {key: int(value) if key in REQUIRED_POLICIES else value for key, value in values.items()}
        if result['work_min'] > result['work_max']:
            raise EconomyError('The `work_min` policy cannot exceed `work_max`.')
        result['central_bank_role_id'] = roles['central_bank_role_id']
        result['treasury_role_id'] = roles['treasury_role_id']
        return result

    async def set_roles(self, guild_id, central_bank_role_id, treasury_role_id):
        if central_bank_role_id == treasury_role_id:
            raise EconomyError('Central Bank and Treasury must use different roles.')
        await self.db.execute(
            '''INSERT INTO economy_roles(guild_id,central_bank_role_id,treasury_role_id)
               VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET
               central_bank_role_id=excluded.central_bank_role_id,
               treasury_role_id=excluded.treasury_role_id''',
            (guild_id, central_bank_role_id, treasury_role_id),
        )

    async def set_currency(self, guild_id, name, code, symbol, actor_id):
        name, code, symbol = name.strip(), code.strip().upper(), symbol.strip()
        if not 2 <= len(name) <= 32:
            raise EconomyError('Currency name must be 2–32 characters.')
        if not re.fullmatch(r'[A-Z0-9]{2,6}', code):
            raise EconomyError('Currency code must be 2–6 letters or numbers.')
        if not 1 <= len(symbol) <= 4 or any(char.isspace() for char in symbol):
            raise EconomyError('Currency symbol must be 1–4 non-space characters.')
        async with self.db.transaction() as conn:
            current = await _fetchone(
                conn,
                "SELECT value FROM economy_policies WHERE guild_id=? AND policy='currency_code'",
                (guild_id,),
            )
            if current and current['value'] != code:
                used = await _fetchone(
                    '''SELECT 1 FROM economy_accounts WHERE guild_id=?
                       UNION ALL SELECT 1 FROM economy_assets WHERE guild_id=? LIMIT 1''',
                    (guild_id, guild_id),
                )
                if used:
                    raise EconomyError('The base currency code cannot change after accounts or market assets exist.')
            collision = await _fetchone(
                conn,
                'SELECT 1 FROM economy_assets WHERE guild_id=? AND symbol=?',
                (guild_id, code),
            )
            if collision:
                raise EconomyError('That code is already used by a listed asset.')
            await self._store_policy(conn, guild_id, 'currency_name', name, actor_id)
            await self._store_policy(conn, guild_id, 'currency_code', code, actor_id)
            await self._store_policy(conn, guild_id, 'currency_symbol', symbol, actor_id)
        return name, code, symbol

    async def set_policy(self, guild_id, key, value, actor_id):
        key = key.strip().lower()
        if key in POLICY_MONEY:
            stored_value = parse_nonnegative_money(value)
        elif key in POLICY_LIMITS:
            try:
                stored_value = int(value)
            except (TypeError, ValueError):
                raise EconomyError(f'Policy `{key}` must be an integer.')
            minimum, maximum = POLICY_LIMITS[key]
            if not minimum <= stored_value <= maximum:
                raise EconomyError(f'Policy `{key}` must be between {minimum} and {maximum}.')
        else:
            raise EconomyError(f'Unknown policy `{key}`. Valid settings: {", ".join(sorted(REQUIRED_POLICIES))}.')
        async with self.db.transaction() as conn:
            if key in {'work_min', 'work_max'}:
                other_key = 'work_max' if key == 'work_min' else 'work_min'
                other = await _fetchone(
                    conn,
                    'SELECT value FROM economy_policies WHERE guild_id=? AND policy=?',
                    (guild_id, other_key),
                )
                other_value = int(other['value']) if other else None
                if other_value is not None and (
                    (key == 'work_min' and stored_value > other_value)
                    or (key == 'work_max' and stored_value < other_value)
                ):
                    raise EconomyError(f'`{key}` would make the work reward range invalid; update `{other_key}` first.')
            await self._store_policy(conn, guild_id, key, stored_value, actor_id)
        return stored_value

    async def _store_policy(self, conn, guild_id, key, value, actor_id):
        await conn.execute(
            '''INSERT INTO economy_policies(guild_id,policy,value,updated_by)
               VALUES(?,?,?,?) ON CONFLICT(guild_id,policy) DO UPDATE SET
               value=excluded.value,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP''',
            (guild_id, key, str(value), actor_id),
        )

    async def role_authorized(self, guild_id, member, authority):
        if authority not in {'central_bank', 'treasury'}:
            raise ValueError(f'Unknown economy authority: {authority}')
        roles = await self.db.fetchone(
            f'SELECT {authority}_role_id AS role_id FROM economy_roles WHERE guild_id=?',
            (guild_id,),
        )
        return bool(roles and any(role.id == roles['role_id'] for role in member.roles))

    async def issue_license(self, guild_id, user_id, issuer_id):
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            await self._ensure_account(conn, settings, guild_id, user_id)
            existing = await _fetchone(
                conn,
                "SELECT status FROM economy_business_licenses WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            )
            if existing and existing['status'] == 'active':
                raise EconomyError('This member already has an active business license.')
            fee = settings['business_license_fee']
            if fee:
                await self._wallet_change(conn, guild_id, user_id, -fee)
                await self._treasury_change(conn, guild_id, fee)
            await conn.execute(
                '''INSERT INTO economy_business_licenses
                   (guild_id,user_id,status,issued_by,issued_at,revoked_by,revoked_at,reason)
                   VALUES(?,?,'active',?,CURRENT_TIMESTAMP,NULL,NULL,NULL)
                   ON CONFLICT(guild_id,user_id) DO UPDATE SET status='active',
                   issued_by=excluded.issued_by,issued_at=CURRENT_TIMESTAMP,
                   revoked_by=NULL,revoked_at=NULL,reason=NULL''',
                (guild_id, user_id, issuer_id),
            )
        return fee

    async def revoke_license(self, guild_id, user_id, issuer_id, reason):
        async with self.db.transaction() as conn:
            await self._enabled_settings(conn, guild_id)
            cursor = await conn.execute(
                '''UPDATE economy_business_licenses SET status='revoked',revoked_by=?,
                   revoked_at=CURRENT_TIMESTAMP,reason=?
                   WHERE guild_id=? AND user_id=? AND status='active' ''',
                (issuer_id, reason[:300], guild_id, user_id),
            )
            if cursor.rowcount != 1:
                raise EconomyError('This member does not have an active business license.')

    async def create_forex_asset(self, guild_id, symbol, name, price, volatility_bps):
        symbol = symbol.strip().upper()
        name = name.strip()
        price_minor = parse_money(price)
        if not re.fullmatch(r'[A-Z0-9]{2,6}', symbol):
            raise EconomyError('Currency codes must be 2–6 letters or numbers.')
        if not 2 <= len(name) <= 32:
            raise EconomyError('Currency name must be 2–32 characters.')
        if not 0 <= volatility_bps <= 5000:
            raise EconomyError('Daily volatility must be between 0 and 5,000 basis points.')
        today = datetime.now(timezone.utc).date().isoformat()
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            if symbol == settings['currency_code']:
                raise EconomyError('The server base currency cannot also be listed as a forex asset.')
            existing = await _fetchone(
                conn,
                'SELECT 1 FROM economy_assets WHERE guild_id=? AND symbol=?',
                (guild_id, symbol),
            )
            if existing:
                raise EconomyError(f'`{symbol}` is already listed in this server.')
            await conn.execute(
                '''INSERT INTO economy_assets
                   (guild_id,symbol,name,kind,price,volatility_bps,quote_day)
                   VALUES(?,?,?,'forex',?,?,?)''',
                (guild_id, symbol, name, price_minor, volatility_bps, today),
            )
            await conn.execute(
                '''INSERT INTO economy_price_history(guild_id,symbol,quote_day,price)
                   VALUES(?,?,?,?)''',
                (guild_id, symbol, today, price_minor),
            )
        return symbol, name, price_minor

    async def license_status(self, guild_id, user_id):
        return await self.db.fetchone(
            'SELECT * FROM economy_business_licenses WHERE guild_id=? AND user_id=?',
            (guild_id, user_id),
        )

    async def _ensure_account(self, conn, settings, guild_id, user_id):
        cursor = await conn.execute(
            'INSERT OR IGNORE INTO economy_accounts(guild_id,user_id) VALUES(?,?)',
            (guild_id, user_id),
        )
        if cursor.rowcount:
            await conn.execute(
                'UPDATE economy_accounts SET wallet=?,bank_last_interest=? WHERE guild_id=? AND user_id=?',
                (settings['starting_balance'], int(time.time()), guild_id, user_id),
            )
            await self._record(
                conn, guild_id, user_id, 'starter_balance',
                settings['starting_balance'], 0, 'Initial account balance',
            )
        return await _fetchone(
            conn,
            'SELECT * FROM economy_accounts WHERE guild_id=? AND user_id=?',
            (guild_id, user_id),
        )

    async def _record(self, conn, guild_id, user_id, entry_type, wallet_delta=0, bank_delta=0, details=''):
        await conn.execute(
            '''INSERT INTO economy_ledger
               (guild_id,user_id,entry_type,wallet_delta,bank_delta,details)
               VALUES(?,?,?,?,?,?)''',
            (guild_id, user_id, entry_type, wallet_delta, bank_delta, details[:500]),
        )

    async def _wallet_change(self, conn, guild_id, user_id, amount):
        cursor = await conn.execute(
            '''UPDATE economy_accounts SET wallet=wallet+?
               WHERE guild_id=? AND user_id=? AND wallet+?>=0''',
            (amount, guild_id, user_id, amount),
        )
        if cursor.rowcount != 1:
            raise EconomyError('Not enough money in your wallet.')

    async def _bank_change(self, conn, guild_id, user_id, amount):
        cursor = await conn.execute(
            '''UPDATE economy_accounts SET bank=bank+?
               WHERE guild_id=? AND user_id=? AND bank+?>=0''',
            (amount, guild_id, user_id, amount),
        )
        if cursor.rowcount != 1:
            raise EconomyError('Not enough money in your bank account.')

    async def _treasury_change(self, conn, guild_id, amount):
        await conn.execute(
            'INSERT OR IGNORE INTO economy_treasury(guild_id) VALUES(?)',
            (guild_id,),
        )
        await conn.execute(
            'UPDATE economy_treasury SET balance=balance+? WHERE guild_id=?',
            (amount, guild_id),
        )

    async def _apply_bank_interest(self, conn, guild_id, user_id, account, settings):
        now = int(time.time())
        elapsed = max(0, now - account['bank_last_interest'])
        if not elapsed:
            return account
        numerator = account['bank'] * settings['savings_interest_bps'] * elapsed + account['bank_interest_remainder']
        interest, remainder = divmod(numerator, 10000 * YEAR_SECONDS)
        await conn.execute(
            '''UPDATE economy_accounts SET bank=bank+?,bank_last_interest=?,bank_interest_remainder=?
               WHERE guild_id=? AND user_id=?''',
            (interest, now, remainder, guild_id, user_id),
        )
        if interest:
            await self._record(
                conn, guild_id, user_id, 'bank_interest', 0, interest,
                f'{settings["savings_interest_bps"] / 100:.2f}% APR',
            )
            await self._treasury_change(conn, guild_id, -interest)
        return await _fetchone(
            conn,
            'SELECT * FROM economy_accounts WHERE guild_id=? AND user_id=?',
            (guild_id, user_id),
        )

    async def balance(self, guild_id, user_id):
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            account = await self._ensure_account(conn, settings, guild_id, user_id)
            account = await self._apply_bank_interest(conn, guild_id, user_id, account, settings)
            return dict(account), dict(settings)

    async def transfer(self, guild_id, sender_id, recipient_id, amount, note=''):
        minor = parse_money(amount)
        if sender_id == recipient_id:
            raise EconomyError('You cannot pay yourself.')
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            await self._ensure_account(conn, settings, guild_id, sender_id)
            await self._ensure_account(conn, settings, guild_id, recipient_id)
            await self._wallet_change(conn, guild_id, sender_id, -minor)
            await self._wallet_change(conn, guild_id, recipient_id, minor)
            await self._record(conn, guild_id, sender_id, 'payment_sent', -minor, 0, f'To {recipient_id}: {note}')
            await self._record(conn, guild_id, recipient_id, 'payment_received', minor, 0, f'From {sender_id}: {note}')
        return minor

    async def claim_income(self, guild_id, user_id, kind):
        if kind not in {'work', 'daily'}:
            raise EconomyError('Unknown income type.')
        now = int(time.time())
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            await self._ensure_account(conn, settings, guild_id, user_id)
            if kind == 'work':
                cooldown = settings['work_cooldown_seconds']
                field = 'last_work'
                reward = settings['work_min'] + random.SystemRandom().randint(
                    0, settings['work_max'] - settings['work_min'],
                )
            else:
                cooldown = settings['daily_cooldown_seconds']
                field = 'last_daily'
                reward = settings['daily_reward']
            account = await _fetchone(
                conn,
                f'SELECT wallet,bank,{field} AS claimed_at FROM economy_accounts WHERE guild_id=? AND user_id=?',
                (guild_id, user_id),
            )
            remaining = cooldown - (now - account['claimed_at'])
            if remaining > 0:
                raise EconomyError(f'Try again in {remaining // 3600}h {(remaining % 3600) // 60}m.')
            await conn.execute(
                f'UPDATE economy_accounts SET {field}=?,wallet=wallet+? WHERE guild_id=? AND user_id=?',
                (now, reward, guild_id, user_id),
            )
            await self._record(conn, guild_id, user_id, kind, reward, 0, 'Income reward')
        return reward

    async def bank_move(self, guild_id, user_id, amount, direction):
        minor = parse_money(amount)
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            account = await self._ensure_account(conn, settings, guild_id, user_id)
            account = await self._apply_bank_interest(conn, guild_id, user_id, account, settings)
            if direction == 'deposit':
                await self._wallet_change(conn, guild_id, user_id, -minor)
                await self._bank_change(conn, guild_id, user_id, minor)
                wallet_delta, bank_delta = -minor, minor
            else:
                await self._bank_change(conn, guild_id, user_id, -minor)
                await self._wallet_change(conn, guild_id, user_id, minor)
                wallet_delta, bank_delta = minor, -minor
            await self._record(conn, guild_id, user_id, f'bank_{direction}', wallet_delta, bank_delta)
        return minor

    async def _advance_market(self, conn, guild_id):
        today = datetime.now(timezone.utc).date()
        assets = await _fetchall(
            conn,
            'SELECT * FROM economy_assets WHERE guild_id=? ORDER BY symbol',
            (guild_id,),
        )
        for asset in assets:
            last_day = date.fromisoformat(asset['quote_day'])
            days = min((today - last_day).days, 365)
            price = asset['price']
            for offset in range(1, days + 1):
                quote_day = last_day + timedelta(days=offset)
                seed = hashlib.sha256(
                    f'{guild_id}:{asset["symbol"]}:{quote_day.isoformat()}'.encode(),
                ).digest()
                change_bps = random.Random(int.from_bytes(seed[:8], 'big')).randint(
                    -asset['volatility_bps'], asset['volatility_bps'],
                )
                price = max(1, price + price * change_bps // 10000)
                await conn.execute(
                    '''INSERT OR REPLACE INTO economy_price_history(guild_id,symbol,quote_day,price)
                       VALUES(?,?,?,?)''',
                    (guild_id, asset['symbol'], quote_day.isoformat(), price),
                )
            if days:
                await conn.execute(
                    'UPDATE economy_assets SET price=?,quote_day=? WHERE guild_id=? AND symbol=?',
                    (price, today.isoformat(), guild_id, asset['symbol']),
                )
                await conn.execute(
                    '''DELETE FROM economy_price_history WHERE guild_id=? AND symbol=?
                       AND quote_day < ?''',
                    (guild_id, asset['symbol'], (today - timedelta(days=30)).isoformat()),
                )
        return await _fetchall(
            conn,
            'SELECT * FROM economy_assets WHERE guild_id=? ORDER BY kind,symbol',
            (guild_id,),
        )

    async def market(self, guild_id):
        async with self.db.transaction() as conn:
            await self._enabled_settings(conn, guild_id)
            return [dict(row) for row in await self._advance_market(conn, guild_id)]

    async def history(self, guild_id, symbol):
        async with self.db.transaction() as conn:
            await self._enabled_settings(conn, guild_id)
            await self._advance_market(conn, guild_id)
            return await _fetchall(
                conn,
                '''SELECT quote_day,price FROM economy_price_history
                   WHERE guild_id=? AND symbol=? ORDER BY quote_day DESC LIMIT 14''',
                (guild_id, symbol.upper()),
            )

    async def portfolio(self, guild_id, user_id):
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            await self._advance_market(conn, guild_id)
            account = await self._ensure_account(conn, settings, guild_id, user_id)
            account = await self._apply_bank_interest(conn, guild_id, user_id, account, settings)
            holdings = await _fetchall(
                conn,
                '''SELECT h.symbol,h.quantity_milli,h.average_cost,a.name,a.kind,a.price
                   FROM economy_holdings h JOIN economy_assets a
                   ON a.guild_id=h.guild_id AND a.symbol=h.symbol
                   WHERE h.guild_id=? AND h.user_id=? ORDER BY h.symbol''',
                (guild_id, user_id),
            )
            return [dict(row) for row in holdings], dict(account)

    async def _add_holding(self, conn, guild_id, user_id, symbol, quantity_milli, price):
        old = await _fetchone(
            conn,
            '''SELECT quantity_milli,average_cost FROM economy_holdings
               WHERE guild_id=? AND user_id=? AND symbol=?''',
            (guild_id, user_id, symbol),
        )
        if old:
            new_quantity = old['quantity_milli'] + quantity_milli
            average = (
                old['quantity_milli'] * old['average_cost'] + quantity_milli * price
            ) // new_quantity
            await conn.execute(
                '''UPDATE economy_holdings SET quantity_milli=?,average_cost=?
                   WHERE guild_id=? AND user_id=? AND symbol=?''',
                (new_quantity, average, guild_id, user_id, symbol),
            )
        else:
            await conn.execute(
                '''INSERT INTO economy_holdings
                   (guild_id,user_id,symbol,quantity_milli,average_cost) VALUES(?,?,?,?,?)''',
                (guild_id, user_id, symbol, quantity_milli, price),
            )

    async def trade(self, guild_id, user_id, symbol, quantity, direction):
        symbol = symbol.upper()
        quantity_milli = parse_quantity(quantity)
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            await self._advance_market(conn, guild_id)
            await self._ensure_account(conn, settings, guild_id, user_id)
            asset = await _fetchone(
                conn,
                'SELECT * FROM economy_assets WHERE guild_id=? AND symbol=?',
                (guild_id, symbol),
            )
            if asset is None or asset['kind'] != 'stock':
                raise EconomyError(f'`{symbol}` is not a listed stock. Use `/market` to see listed assets.')
            gross = asset['price'] * quantity_milli // 1000
            if gross <= 0:
                raise EconomyError('That quantity is too small to trade.')
            fee = (gross * settings['trading_fee_bps'] + 9999) // 10000
            if direction == 'buy':
                holding = await _fetchone(
                    conn,
                    '''SELECT quantity_milli FROM economy_holdings
                       WHERE guild_id=? AND user_id=? AND symbol=?''',
                    (guild_id, user_id, symbol),
                )
                if asset['company_id'] is not None:
                    issued = await _fetchone(
                        conn,
                        'SELECT shares_total FROM economy_companies WHERE id=? AND guild_id=?',
                        (asset['company_id'], guild_id),
                    )
                    held = await _fetchone(
                        conn,
                        'SELECT COALESCE(SUM(quantity_milli),0) AS quantity FROM economy_holdings WHERE guild_id=? AND symbol=?',
                        (guild_id, symbol),
                    )
                    if issued and held['quantity'] + quantity_milli > issued['shares_total'] * 1000:
                        raise EconomyError('Not enough unowned company shares are available.')
                await self._wallet_change(conn, guild_id, user_id, -(gross + fee))
                await self._add_holding(conn, guild_id, user_id, symbol, quantity_milli, asset['price'])
                wallet_delta = -(gross + fee)
                detail = f'Bought {format_quantity(quantity_milli)} {symbol}; fee {fee} cents'
            else:
                holding = await _fetchone(
                    conn,
                    '''SELECT quantity_milli,average_cost FROM economy_holdings
                       WHERE guild_id=? AND user_id=? AND symbol=?''',
                    (guild_id, user_id, symbol),
                )
                if holding is None or holding['quantity_milli'] < quantity_milli:
                    raise EconomyError(f'You do not own {format_quantity(quantity_milli)} {symbol}.')
                net = gross - fee
                if net <= 0:
                    raise EconomyError('The trading fee is larger than the sale proceeds.')
                await self._wallet_change(conn, guild_id, user_id, net)
                remaining = holding['quantity_milli'] - quantity_milli
                if remaining:
                    await conn.execute(
                        '''UPDATE economy_holdings SET quantity_milli=?
                           WHERE guild_id=? AND user_id=? AND symbol=?''',
                        (remaining, guild_id, user_id, symbol),
                    )
                else:
                    await conn.execute(
                        'DELETE FROM economy_holdings WHERE guild_id=? AND user_id=? AND symbol=?',
                        (guild_id, user_id, symbol),
                    )
                wallet_delta = net
                detail = f'Sold {format_quantity(quantity_milli)} {symbol}; fee {fee} cents'
            treasury_delta = gross + fee if direction == 'buy' else -gross + fee
            await self._treasury_change(conn, guild_id, treasury_delta)
            await self._record(conn, guild_id, user_id, f'stock_{direction}', wallet_delta, 0, detail)
            return dict(asset), gross, fee, wallet_delta

    async def exchange(self, guild_id, user_id, from_code, to_code, amount):
        from_code, to_code = from_code.upper(), to_code.upper()
        if from_code == to_code:
            raise EconomyError('Choose two different currencies.')
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            base_currency = settings['currency_code']
            quantity = parse_money(amount) if from_code == base_currency else parse_quantity(amount)
            await self._advance_market(conn, guild_id)
            await self._ensure_account(conn, settings, guild_id, user_id)
            source = None if from_code == base_currency else await _fetchone(
                conn,
                "SELECT * FROM economy_assets WHERE guild_id=? AND symbol=? AND kind='forex'",
                (guild_id, from_code),
            )
            target = None if to_code == base_currency else await _fetchone(
                conn,
                "SELECT * FROM economy_assets WHERE guild_id=? AND symbol=? AND kind='forex'",
                (guild_id, to_code),
            )
            if from_code != base_currency and source is None:
                raise EconomyError(f'`{from_code}` is not a supported currency. Use `/market` to see available currencies.')
            if to_code != base_currency and target is None:
                raise EconomyError(f'`{to_code}` is not a supported currency. Use `/market` to see available currencies.')
            source_value = quantity if source is None else source['price'] * quantity // 1000
            if source_value <= 0:
                raise EconomyError('That amount is too small to exchange.')
            fee = (source_value * settings['trading_fee_bps'] + 9999) // 10000
            spendable = source_value - fee
            if spendable <= 0:
                raise EconomyError('The exchange fee is larger than the amount.')
            output_quantity = spendable if target is None else spendable * 1000 // target['price']
            if output_quantity <= 0:
                raise EconomyError('That amount is too small to exchange into the selected currency.')
            if source is None:
                await self._wallet_change(conn, guild_id, user_id, -quantity)
            else:
                holding = await _fetchone(
                    conn,
                    '''SELECT quantity_milli FROM economy_holdings
                       WHERE guild_id=? AND user_id=? AND symbol=?''',
                    (guild_id, user_id, from_code),
                )
                if holding is None or holding['quantity_milli'] < quantity:
                    raise EconomyError(f'You do not own {format_quantity(quantity)} {from_code}.')
                remaining = holding['quantity_milli'] - quantity
                if remaining:
                    await conn.execute(
                        '''UPDATE economy_holdings SET quantity_milli=?
                           WHERE guild_id=? AND user_id=? AND symbol=?''',
                        (remaining, guild_id, user_id, from_code),
                    )
                else:
                    await conn.execute(
                        'DELETE FROM economy_holdings WHERE guild_id=? AND user_id=? AND symbol=?',
                        (guild_id, user_id, from_code),
                    )
            if target is None:
                await self._wallet_change(conn, guild_id, user_id, output_quantity)
                wallet_delta = output_quantity - (quantity if source is None else 0)
            else:
                await self._add_holding(conn, guild_id, user_id, to_code, output_quantity, target['price'])
                wallet_delta = -quantity if source is None else 0
            treasury_delta = quantity if source is None else (-output_quantity if target is None else fee)
            await self._treasury_change(conn, guild_id, treasury_delta)
            await self._record(
                conn, guild_id, user_id, 'forex_exchange', wallet_delta, 0,
                f'{format_money(quantity, settings["currency_symbol"]) if source is None else format_quantity(quantity)} {from_code} '
                f'to {format_money(output_quantity, settings["currency_symbol"]) if target is None else format_quantity(output_quantity)} {to_code}; '
                f'fee {fee} cents',
            )
        return output_quantity, fee, base_currency

    async def issue_bond(self, guild_id, issuer_id, name, face_value, yield_bps, term_days, units_total):
        name = name.strip()
        face_value = parse_money(face_value)
        if not 2 <= len(name) <= 64:
            raise EconomyError('Bond name must be 2–64 characters.')
        if not 0 <= yield_bps <= 100000 or not 1 <= term_days <= 3650 or not 1 <= units_total <= 1000000000:
            raise EconomyError('Yield, maturity, or quantity is outside the supported range.')
        async with self.db.transaction() as conn:
            await self._enabled_settings(conn, guild_id)
            existing = await _fetchone(
                conn,
                'SELECT 1 FROM economy_bond_offerings WHERE guild_id=? AND lower(name)=lower(?)',
                (guild_id, name),
            )
            if existing:
                raise EconomyError('A bond offering already uses that name.')
            cursor = await conn.execute(
                '''INSERT INTO economy_bond_offerings
                   (guild_id,name,face_value,yield_bps,term_days,units_total,issuer_id)
                   VALUES(?,?,?,?,?,?,?)''',
                (guild_id, name, face_value, yield_bps, term_days, units_total, issuer_id),
            )
            return cursor.lastrowid

    async def bond_offerings(self, guild_id):
        async with self.db.transaction() as conn:
            await self._enabled_settings(conn, guild_id)
            return await _fetchall(
                conn,
                '''SELECT * FROM economy_bond_offerings WHERE guild_id=? AND status='open'
                   AND units_sold<units_total ORDER BY id''',
                (guild_id,),
            )

    async def buy_bond(self, guild_id, user_id, offering_id, units):
        if not 1 <= units <= 1000000000:
            raise EconomyError('Bond quantity must be positive.')
        now = int(time.time())
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            await self._ensure_account(conn, settings, guild_id, user_id)
            offering = await _fetchone(
                conn,
                '''SELECT * FROM economy_bond_offerings
                   WHERE guild_id=? AND id=? AND status='open' ''',
                (guild_id, offering_id),
            )
            if offering is None or offering['units_sold'] + units > offering['units_total']:
                raise EconomyError('Bond offering not found or not enough units remain.')
            principal = offering['face_value'] * units
            maturity = now + offering['term_days'] * 86400
            await self._wallet_change(conn, guild_id, user_id, -principal)
            await self._treasury_change(conn, guild_id, principal)
            await conn.execute(
                'UPDATE economy_bond_offerings SET units_sold=units_sold+? WHERE id=?',
                (units, offering_id),
            )
            cursor = await conn.execute(
                '''INSERT INTO economy_bonds
                   (guild_id,user_id,offering_id,units,principal,yield_bps,term_days,purchased_at,matures_at)
                   VALUES(?,?,?,?,?,?,?,?,?)''',
                (guild_id, user_id, offering_id, units, principal, offering['yield_bps'],
                 offering['term_days'], now, maturity),
            )
            await self._record(conn, guild_id, user_id, 'bond_purchase', -principal, 0, f'{units} units of bond #{offering_id}')
            return cursor.lastrowid, principal, offering['yield_bps'], maturity

    async def bonds(self, guild_id, user_id):
        async with self.db.transaction() as conn:
            await self._enabled_settings(conn, guild_id)
            return await _fetchall(
                conn,
                '''SELECT b.*,o.name AS offering_name FROM economy_bonds b
                   LEFT JOIN economy_bond_offerings o ON o.id=b.offering_id
                   WHERE b.guild_id=? AND b.user_id=? ORDER BY b.status,b.matures_at''',
                (guild_id, user_id),
            )

    async def redeem_bonds(self, guild_id, user_id):
        now = int(time.time())
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            account = await self._ensure_account(conn, settings, guild_id, user_id)
            await self._apply_bank_interest(conn, guild_id, user_id, account, settings)
            due = await _fetchall(
                conn,
                '''SELECT * FROM economy_bonds WHERE guild_id=? AND user_id=?
                   AND status='active' AND matures_at<=? ORDER BY matures_at''',
                (guild_id, user_id, now),
            )
            if not due:
                raise EconomyError('You have no matured bonds to redeem yet.')
            total = 0
            for bond in due:
                interest = bond['principal'] * bond['yield_bps'] * bond['term_days'] // (10000 * 365)
                payout = bond['principal'] + interest
                total += payout
                await conn.execute(
                    "UPDATE economy_bonds SET status='redeemed' WHERE id=? AND status='active'",
                    (bond['id'],),
                )
            await conn.execute(
                'UPDATE economy_accounts SET bank=bank+? WHERE guild_id=? AND user_id=?',
                (total, guild_id, user_id),
            )
            await self._treasury_change(conn, guild_id, -total)
            await self._record(conn, guild_id, user_id, 'bond_redemption', 0, total, f'Redeemed {len(due)} matured bonds')
            return total, len(due)

    async def take_loan(self, guild_id, user_id, amount):
        principal = parse_money(amount)
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            if principal > settings['loan_max']:
                raise EconomyError(f'The maximum loan is {format_money(settings["loan_max"], settings["currency_symbol"])}.')
            now = int(time.time())
            await self._ensure_account(conn, settings, guild_id, user_id)
            existing = await _fetchone(
                conn,
                "SELECT id FROM economy_loans WHERE guild_id=? AND user_id=? AND status='active'",
                (guild_id, user_id),
            )
            if existing:
                raise EconomyError('Repay your active loan before taking another.')
            await self._wallet_change(conn, guild_id, user_id, principal)
            cursor = await conn.execute(
                '''INSERT INTO economy_loans
                   (guild_id,user_id,principal,balance,rate_bps,issued_at,due_at)
                   VALUES(?,?,?,?,?,?,?)''',
                (guild_id, user_id, principal, principal, settings['loan_rate_bps'],
                 now, now + settings['loan_term_days'] * 86400),
            )
            await self._treasury_change(conn, guild_id, -principal)
            await self._record(
                conn, guild_id, user_id, 'loan_issued', principal, 0,
                f'Loan #{cursor.lastrowid}; {settings["loan_rate_bps"]} bps APR; '
                f'{settings["loan_term_days"]}-day term',
            )
            return cursor.lastrowid, principal

    async def repay_loan(self, guild_id, user_id, amount):
        payment = parse_money(amount)
        now = int(time.time())
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            await self._ensure_account(conn, settings, guild_id, user_id)
            loan = await _fetchone(
                conn,
                "SELECT * FROM economy_loans WHERE guild_id=? AND user_id=? AND status='active'",
                (guild_id, user_id),
            )
            if loan is None:
                raise EconomyError('You do not have an active loan.')
            elapsed = max(0, now - loan['issued_at'])
            numerator = loan['balance'] * loan['rate_bps'] * elapsed + loan['interest_remainder']
            interest, interest_remainder = divmod(numerator, 10000 * YEAR_SECONDS)
            due = loan['balance'] + interest
            if payment > due:
                raise EconomyError(f'Your current payoff amount is {format_money(due, settings["currency_symbol"])}.')
            await self._wallet_change(conn, guild_id, user_id, -payment)
            remaining = due - payment
            await conn.execute(
                '''UPDATE economy_loans SET balance=?,issued_at=?,interest_remainder=?,
                   status=CASE WHEN ?=0 THEN 'repaid' ELSE 'active' END WHERE id=?''',
                (remaining, now, interest_remainder, remaining, loan['id']),
            )
            await self._treasury_change(conn, guild_id, payment)
            await self._record(conn, guild_id, user_id, 'loan_repayment', -payment, 0, f'Loan #{loan["id"]}; remaining {remaining} cents')
            return payment, remaining

    async def loan_status(self, guild_id, user_id):
        async with self.db.transaction() as conn:
            await self._enabled_settings(conn, guild_id)
            return await _fetchone(
                conn,
                "SELECT * FROM economy_loans WHERE guild_id=? AND user_id=? AND status='active'",
                (guild_id, user_id),
            )

    async def create_company(self, guild_id, user_id, name, symbol, starting_price, shares, volatility_bps):
        name, symbol = name.strip(), symbol.strip().upper()
        if not 2 <= len(name) <= 32 or not re.fullmatch(r'[A-Z0-9]{2,5}', symbol):
            raise EconomyError('Company names must be 2–32 characters; stock symbols must be 2–5 letters or numbers.')
        price = parse_money(starting_price)
        if not 1 <= shares <= 1000000000 or not 0 <= volatility_bps <= 5000:
            raise EconomyError('Share count or daily volatility is outside the supported range.')
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            license_row = await _fetchone(
                conn,
                '''SELECT 1 FROM economy_business_licenses
                   WHERE guild_id=? AND user_id=? AND status='active' ''',
                (guild_id, user_id),
            )
            if license_row is None:
                raise EconomyError('You need an active business license from the Central Bank before founding a company.')
            if symbol == settings['currency_code']:
                raise EconomyError('The stock symbol cannot match the server currency code.')
            await self._ensure_account(conn, settings, guild_id, user_id)
            duplicate_name = await _fetchone(
                conn,
                'SELECT 1 FROM economy_companies WHERE guild_id=? AND lower(name)=lower(?)',
                (guild_id, name),
            )
            if duplicate_name:
                raise EconomyError('A company already uses that name.')
            exists = await _fetchone(
                conn,
                'SELECT 1 FROM economy_assets WHERE guild_id=? AND symbol=?',
                (guild_id, symbol),
            )
            if exists:
                raise EconomyError('That stock symbol is already in use.')
            today = datetime.now(timezone.utc).date().isoformat()
            await conn.execute(
                '''INSERT INTO economy_assets
                   (guild_id,symbol,name,kind,price,volatility_bps,quote_day)
                   VALUES(?,?,?,'stock',?,?,?)''',
                (guild_id, symbol, name, price, volatility_bps, today),
            )
            cursor = await conn.execute(
                '''INSERT INTO economy_companies
                   (guild_id,name,symbol,owner_id,treasury,shares_total)
                   VALUES(?,?,?,?,?,?)''',
                (guild_id, name, symbol, user_id, 0, shares),
            )
            company_id = cursor.lastrowid
            await conn.execute(
                'UPDATE economy_assets SET company_id=? WHERE guild_id=? AND symbol=?',
                (company_id, guild_id, symbol),
            )
            await conn.execute(
                '''INSERT INTO economy_price_history(guild_id,symbol,quote_day,price)
                   VALUES(?,?,?,?)''',
                (guild_id, symbol, today, price),
            )
            await conn.execute(
                '''INSERT INTO economy_holdings
                   (guild_id,user_id,symbol,quantity_milli,average_cost)
                   VALUES(?,?,?,?,?)''',
                (guild_id, user_id, symbol, shares * 1000, price),
            )
            await self._record(conn, guild_id, user_id, 'company_founded', 0, 0, f'{name} ({symbol}); {shares} shares')
        return company_id, shares

    async def company(self, guild_id, name_or_symbol):
        token = name_or_symbol.strip()
        return await self.db.fetchone(
            '''SELECT c.*,a.price FROM economy_companies c
               JOIN economy_assets a ON a.guild_id=c.guild_id AND a.symbol=c.symbol
               WHERE c.guild_id=? AND (lower(c.name)=lower(?) OR c.symbol=?)''',
            (guild_id, token, token.upper()),
        )

    async def company_cash(self, guild_id, user_id, name_or_symbol, amount, direction):
        minor = parse_money(amount)
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            await self._ensure_account(conn, settings, guild_id, user_id)
            company = await _fetchone(
                conn,
                '''SELECT * FROM economy_companies WHERE guild_id=?
                   AND (lower(name)=lower(?) OR symbol=?)''',
                (guild_id, name_or_symbol.strip(), name_or_symbol.strip().upper()),
            )
            if company is None:
                raise EconomyError('Company not found.')
            if company['owner_id'] != user_id:
                raise EconomyError('Only the company owner can manage its treasury.')
            if direction == 'deposit':
                await self._wallet_change(conn, guild_id, user_id, -minor)
                await conn.execute('UPDATE economy_companies SET treasury=treasury+? WHERE id=?', (minor, company['id']))
                wallet_delta = -minor
            else:
                cursor = await conn.execute(
                    'UPDATE economy_companies SET treasury=treasury-? WHERE id=? AND treasury>=?',
                    (minor, company['id'], minor),
                )
                if cursor.rowcount != 1:
                    raise EconomyError('The company does not have enough funds.')
                await self._wallet_change(conn, guild_id, user_id, minor)
                wallet_delta = minor
            await self._record(conn, guild_id, user_id, f'company_{direction}', wallet_delta, 0, company['name'])
        return minor

    async def company_hire(self, guild_id, owner_id, name_or_symbol, member_id, salary):
        minor = parse_money(salary)
        async with self.db.transaction() as conn:
            await self._enabled_settings(conn, guild_id)
            company = await _fetchone(
                conn,
                '''SELECT * FROM economy_companies WHERE guild_id=?
                   AND (lower(name)=lower(?) OR symbol=?)''',
                (guild_id, name_or_symbol.strip(), name_or_symbol.strip().upper()),
            )
            if company is None:
                raise EconomyError('Company not found.')
            if company['owner_id'] != owner_id:
                raise EconomyError('Only the company owner can manage employees.')
            await conn.execute(
                '''INSERT INTO economy_company_members(company_id,user_id,salary) VALUES(?,?,?)
                   ON CONFLICT(company_id,user_id) DO UPDATE SET salary=excluded.salary''',
                (company['id'], member_id, minor),
            )
        return minor

    async def company_fire(self, guild_id, owner_id, name_or_symbol, member_id):
        async with self.db.transaction() as conn:
            await self._enabled_settings(conn, guild_id)
            company = await _fetchone(
                conn,
                '''SELECT * FROM economy_companies WHERE guild_id=?
                   AND (lower(name)=lower(?) OR symbol=?)''',
                (guild_id, name_or_symbol.strip(), name_or_symbol.strip().upper()),
            )
            if company is None:
                raise EconomyError('Company not found.')
            if company['owner_id'] != owner_id:
                raise EconomyError('Only the company owner can manage employees.')
            cursor = await conn.execute(
                'DELETE FROM economy_company_members WHERE company_id=? AND user_id=?',
                (company['id'], member_id),
            )
            if cursor.rowcount != 1:
                raise EconomyError('That member is not employed by this company.')

    async def company_payroll(self, guild_id, owner_id, name_or_symbol):
        now = int(time.time())
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            company = await _fetchone(
                conn,
                '''SELECT * FROM economy_companies WHERE guild_id=?
                   AND (lower(name)=lower(?) OR symbol=?)''',
                (guild_id, name_or_symbol.strip(), name_or_symbol.strip().upper()),
            )
            if company is None:
                raise EconomyError('Company not found.')
            if company['owner_id'] != owner_id:
                raise EconomyError('Only the company owner can run payroll.')
            remaining = settings['daily_cooldown_seconds'] - (now - company['last_payroll'])
            if remaining > 0:
                raise EconomyError(f'Payroll can run again in {remaining // 3600}h {(remaining % 3600) // 60}m.')
            employees = await _fetchall(
                conn,
                'SELECT user_id,salary FROM economy_company_members WHERE company_id=?',
                (company['id'],),
            )
            total = sum(row['salary'] for row in employees)
            if total > company['treasury']:
                raise EconomyError(f'Company funds are short. Payroll requires {format_money(total, settings["currency_symbol"])}.')
            for employee in employees:
                await self._ensure_account(conn, settings, guild_id, employee['user_id'])
                await conn.execute(
                    'UPDATE economy_accounts SET wallet=wallet+? WHERE guild_id=? AND user_id=?',
                    (employee['salary'], guild_id, employee['user_id']),
                )
                await self._record(conn, guild_id, employee['user_id'], 'company_payroll', employee['salary'], 0, company['name'])
            await conn.execute(
                'UPDATE economy_companies SET treasury=treasury-?,last_payroll=? WHERE id=?',
                (total, now, company['id']),
            )
        return total, len(employees)

    async def company_dividend(self, guild_id, owner_id, name_or_symbol, amount):
        minor = parse_money(amount)
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            company = await _fetchone(
                conn,
                '''SELECT * FROM economy_companies WHERE guild_id=?
                   AND (lower(name)=lower(?) OR symbol=?)''',
                (guild_id, name_or_symbol.strip(), name_or_symbol.strip().upper()),
            )
            if company is None:
                raise EconomyError('Company not found.')
            if company['owner_id'] != owner_id:
                raise EconomyError('Only the company owner can declare a dividend.')
            if company['treasury'] < minor:
                raise EconomyError('The company does not have enough funds for that dividend.')
            shareholders = await _fetchall(
                conn,
                '''SELECT user_id,quantity_milli FROM economy_holdings
                   WHERE guild_id=? AND symbol=? ORDER BY user_id''',
                (guild_id, company['symbol']),
            )
            shares_held = sum(row['quantity_milli'] for row in shareholders)
            if not shares_held:
                raise EconomyError('There are no outstanding shares eligible for a dividend.')
            payouts = [
                (row['user_id'], minor * row['quantity_milli'] // shares_held)
                for row in shareholders
            ]
            remainder = minor - sum(item[1] for item in payouts)
            if remainder:
                first_user, first_payout = payouts[0]
                payouts[0] = (first_user, first_payout + remainder)
            for shareholder_id, payout in payouts:
                if payout <= 0:
                    continue
                await self._ensure_account(conn, settings, guild_id, shareholder_id)
                await conn.execute(
                    'UPDATE economy_accounts SET wallet=wallet+? WHERE guild_id=? AND user_id=?',
                    (payout, guild_id, shareholder_id),
                )
                await self._record(
                    conn, guild_id, shareholder_id, 'company_dividend', payout, 0,
                    f'{company["name"]} dividend',
                )
            await conn.execute(
                'UPDATE economy_companies SET treasury=treasury-? WHERE id=?',
                (minor, company['id']),
            )
        return minor, len(shareholders)

    async def treasury(self, guild_id):
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            row = await _fetchone(conn, 'SELECT * FROM economy_treasury WHERE guild_id=?', (guild_id,))
            return dict(row), dict(settings)

    async def set_tax(self, guild_id, basis_points, actor_id):
        return await self.set_policy(guild_id, 'trading_fee_bps', basis_points, actor_id)

    async def grant_treasury(self, guild_id, user_id, amount):
        minor = parse_money(amount)
        async with self.db.transaction() as conn:
            settings = await self._enabled_settings(conn, guild_id)
            await self._ensure_account(conn, settings, guild_id, user_id)
            treasury = await _fetchone(conn, 'SELECT balance FROM economy_treasury WHERE guild_id=?', (guild_id,))
            if treasury is None or treasury['balance'] < minor:
                raise EconomyError('The treasury does not have enough available funds to issue this grant.')
            await self._treasury_change(conn, guild_id, -minor)
            await self._wallet_change(conn, guild_id, user_id, minor)
            await self._record(conn, guild_id, user_id, 'treasury_grant', minor, 0, 'Government grant')
        return minor

    async def ledger(self, guild_id, user_id):
        async with self.db.transaction() as conn:
            await self._enabled_settings(conn, guild_id)
            return await _fetchall(
                conn,
                '''SELECT entry_type,wallet_delta,bank_delta,details,created_at
                   FROM economy_ledger WHERE guild_id=? AND user_id=?
                   ORDER BY id DESC LIMIT 10''',
                (guild_id, user_id),
            )


class Economy(commands.Cog):
    blicense = app_commands.Group(name='blicense', description='Central Bank business licenses')
    bank = app_commands.Group(name='bank', description='Personal banking')
    market = app_commands.Group(name='market', description='Simulated stocks and currency quotes')
    trade = app_commands.Group(name='trade', description='Buy and sell listed shares')
    forex = app_commands.Group(name='forex', description='Exchange simulated currencies')
    bond = app_commands.Group(name='bond', description='Government bonds')
    company = app_commands.Group(name='company', description='Found and operate companies')
    treasury = app_commands.Group(name='treasury', description='Server treasury')
    loan = app_commands.Group(name='loan', description='Personal loans')

    def __init__(self, bot):
        self.bot = bot
        self.economy = EconomyService(bot.db)

    async def require_role(self, guild_id, member, authority):
        if not isinstance(member, discord.Member) or not await self.economy.role_authorized(guild_id, member, authority):
            raise EconomyError(f'This action requires the configured {authority.replace("_", " ").title()} role.')

    async def prefix_reply(self, ctx, title, description, kind='info'):
        await ctx.send(embed=status_embed(title, description, kind))

    async def prefix_error(self, ctx, error):
        if isinstance(error, EconomyError):
            return await self.prefix_reply(ctx, 'Economy action unavailable', str(error), 'error')
        raise error

    async def require_prefix_role(self, ctx, authority):
        if not ctx.guild or not await self.economy.role_authorized(ctx.guild.id, ctx.author, authority):
            raise EconomyError(f'This action requires the configured {authority.replace("_", " ").title()} role.')

    async def prefix_execute(self, ctx, operation):
        try:
            await operation()
        except EconomyError as error:
            await self.prefix_error(ctx, error)

    async def currency_symbol(self, guild_id) -> str:
        settings = await self.economy.settings(guild_id)
        return str(settings.get('currency_symbol') or '') if settings else ''

    @commands.group(name='economy', invoke_without_command=True)
    @commands.guild_only()
    async def prefix_economy(self, ctx):
        await self.prefix_reply(
            ctx,
            'Economy commands',
            'Use `economy enable|disable|roles|currency|policy|balance|portfolio|work|daily|pay|history`, '
            'or the `bank`, `market`, `trade`, `forex`, `blicense`, `bond`, `company`, `treasury`, and `loan` groups.',
        )

    @prefix_economy.command(name='enable')
    @commands.has_guild_permissions(manage_guild=True)
    async def prefix_economy_enable(self, ctx):
        await self.prefix_execute(ctx, lambda: self._prefix_toggle(ctx, True))

    @prefix_economy.command(name='disable')
    @commands.has_guild_permissions(manage_guild=True)
    async def prefix_economy_disable(self, ctx):
        await self.prefix_execute(ctx, lambda: self._prefix_toggle(ctx, False))

    async def _prefix_toggle(self, ctx, enabled):
        settings = await self.economy.configure(ctx.guild.id, enabled)
        state = 'enabled' if settings['enabled'] else 'disabled'
        description = (
            'Players and their Central Bank/Treasury roles control this server’s simulated economy.'
            if settings['enabled']
            else 'The server economy is disabled. Its accounts and settings remain saved.'
        )
        await self.prefix_reply(
            ctx, f'Economy {state}',
            description,
            'success',
        )

    @prefix_economy.command(name='roles')
    @commands.has_guild_permissions(manage_guild=True)
    async def prefix_economy_roles(self, ctx, central_bank: discord.Role, treasury: discord.Role):
        await self.prefix_execute(ctx, lambda: self._prefix_set_roles(ctx, central_bank, treasury))

    async def _prefix_set_roles(self, ctx, central_bank, treasury):
        await self.economy.set_roles(ctx.guild.id, central_bank.id, treasury.id)
        await self.prefix_reply(
            ctx, 'Economy roles configured',
            f'Central Bank: {central_bank.mention}\nTreasury: {treasury.mention}', 'success',
        )

    @prefix_economy.command(name='currency')
    async def prefix_economy_currency(self, ctx, name: str, code: str, symbol: str):
        await self.prefix_execute(ctx, lambda: self._prefix_set_currency(ctx, name, code, symbol))

    async def _prefix_set_currency(self, ctx, name, code, symbol):
        await self.require_prefix_role(ctx, 'central_bank')
        name, code, symbol = await self.economy.set_currency(
            ctx.guild.id, name, code, symbol, ctx.author.id,
        )
        await self.prefix_reply(ctx, 'Currency configured', f'**{name}** (`{code}`) uses **{symbol}**.', 'success')

    @prefix_economy.command(name='policy')
    async def prefix_economy_policy(self, ctx, key: str, value: str):
        await self.prefix_execute(ctx, lambda: self._prefix_set_policy(ctx, key, value))

    async def _prefix_set_policy(self, ctx, key, value):
        authority = 'treasury' if key.lower() == 'trading_fee_bps' else 'central_bank'
        await self.require_prefix_role(ctx, authority)
        stored = await self.economy.set_policy(ctx.guild.id, key, value, ctx.author.id)
        currency_symbol = await self.currency_symbol(ctx.guild.id)
        rendered = format_money(stored, currency_symbol) if key.lower() in POLICY_MONEY else str(stored)
        await self.prefix_reply(ctx, 'Economy policy updated', f'`{key.lower()}` is now **{rendered}**.', 'success')

    @prefix_economy.command(name='balance')
    async def prefix_economy_balance(self, ctx, member: discord.Member | None = None):
        async def operation():
            target = member or ctx.author
            account, settings = await self.economy.balance(ctx.guild.id, target.id)
            await self.prefix_reply(
                ctx, f'{target.display_name}’s account',
                f'Wallet: **{format_money(account["wallet"], settings["currency_symbol"])}**\n'
                f'Bank: **{format_money(account["bank"], settings["currency_symbol"])}**\n'
                f'Total: **{format_money(account["wallet"] + account["bank"], settings["currency_symbol"])}**',
            )
        await self.prefix_execute(ctx, operation)

    @prefix_economy.command(name='portfolio')
    async def prefix_economy_portfolio(self, ctx):
        async def operation():
            holdings, account = await self.economy.portfolio(ctx.guild.id, ctx.author.id)
            settings = await self.economy.settings(ctx.guild.id)
            if settings is None:
                raise EconomyError('The economy is not configured.')
            symbol = settings['currency_symbol']
            lines = [
                f'`{row["symbol"]}` {format_quantity(row["quantity_milli"])} {row["name"]} — '
                f'{format_money(row["price"] * row["quantity_milli"] // 1000, symbol)}'
                for row in holdings
            ]
            lines.append(f'Cash: {format_money(account["wallet"] + account["bank"], symbol)}')
            await self.prefix_reply(ctx, 'Your portfolio', '\n'.join(lines)[:4000])
        await self.prefix_execute(ctx, operation)

    @prefix_economy.command(name='work')
    async def prefix_economy_work(self, ctx):
        async def operation():
            reward = await self.economy.claim_income(ctx.guild.id, ctx.author.id, 'work')
            await self.prefix_reply(ctx, 'Shift complete', f'You earned **{format_money(reward, await self.currency_symbol(ctx.guild.id))}**.', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_economy.command(name='daily')
    async def prefix_economy_daily(self, ctx):
        async def operation():
            reward = await self.economy.claim_income(ctx.guild.id, ctx.author.id, 'daily')
            await self.prefix_reply(ctx, 'Daily allowance claimed', f'You received **{format_money(reward, await self.currency_symbol(ctx.guild.id))}**.', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_economy.command(name='pay')
    async def prefix_economy_pay(self, ctx, member: discord.Member, amount: float, *, note=''):
        async def operation():
            paid = await self.economy.transfer(ctx.guild.id, ctx.author.id, member.id, amount, note)
            await self.prefix_reply(ctx, 'Payment sent', f'{member.mention} received **{format_money(paid, await self.currency_symbol(ctx.guild.id))}**.', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_economy.command(name='history')
    async def prefix_economy_history(self, ctx):
        async def operation():
            rows = await self.economy.ledger(ctx.guild.id, ctx.author.id)
            symbol = await self.currency_symbol(ctx.guild.id)
            lines = [
                f'`{row["created_at"]}` **{row["entry_type"]}** · '
                f'wallet {format_money(row["wallet_delta"], symbol)} · bank {format_money(row["bank_delta"], symbol)}'
                for row in rows
            ]
            await self.prefix_reply(ctx, 'Recent transactions', '\n'.join(lines)[:4000] or 'No transactions yet.')
        await self.prefix_execute(ctx, operation)

    @prefix_economy.group(name='bank', invoke_without_command=True)
    async def prefix_bank(self, ctx):
        await self.prefix_reply(ctx, 'Bank commands', 'Use `bank deposit <amount>`, `bank withdraw <amount>`, or `bank balance`.')

    @prefix_bank.command(name='deposit')
    async def prefix_bank_deposit(self, ctx, amount: float):
        await self.prefix_execute(ctx, lambda: self._prefix_bank_move(ctx, amount, 'deposit'))

    @prefix_bank.command(name='withdraw')
    async def prefix_bank_withdraw(self, ctx, amount: float):
        await self.prefix_execute(ctx, lambda: self._prefix_bank_move(ctx, amount, 'withdraw'))

    async def _prefix_bank_move(self, ctx, amount, direction):
        moved = await self.economy.bank_move(ctx.guild.id, ctx.author.id, amount, direction)
        await self.prefix_reply(ctx, 'Bank transfer complete', f'Moved **{format_money(moved, await self.currency_symbol(ctx.guild.id))}** {direction}.', 'success')

    @prefix_bank.command(name='balance')
    async def prefix_bank_balance(self, ctx):
        async def operation():
            account, settings = await self.economy.balance(ctx.guild.id, ctx.author.id)
            await self.prefix_reply(
                ctx, 'Bank account',
                f'Bank: **{format_money(account["bank"], settings["currency_symbol"])}** · '
                f'Savings rate: **{settings["savings_interest_bps"] / 100:.2f}% APR**',
            )
        await self.prefix_execute(ctx, operation)

    @prefix_economy.group(name='market', invoke_without_command=True)
    async def prefix_market(self, ctx):
        await self.prefix_execute(ctx, lambda: self._prefix_market_list(ctx))

    async def _prefix_market_list(self, ctx):
        assets = await self.economy.market(ctx.guild.id)
        symbol = await self.currency_symbol(ctx.guild.id)
        lines = [
            f'`{row["symbol"]}` **{row["name"]}** — {format_money(row["price"], symbol)} · {row["kind"]}'
            for row in assets
        ]
        await self.prefix_reply(ctx, 'Simulated markets', '\n'.join(lines)[:4000] or 'No assets are listed.')

    @prefix_market.command(name='list')
    async def prefix_market_list_command(self, ctx):
        await self.prefix_execute(ctx, lambda: self._prefix_market_list(ctx))

    @prefix_market.command(name='history')
    async def prefix_market_history(self, ctx, symbol: str):
        async def operation():
            rows = await self.economy.history(ctx.guild.id, symbol)
            currency_symbol = await self.currency_symbol(ctx.guild.id)
            lines = [f'`{row["quote_day"]}` — {format_money(row["price"], currency_symbol)}' for row in rows]
            await self.prefix_reply(ctx, f'{symbol.upper()} price history', '\n'.join(lines)[:4000] or 'No quotes recorded.')
        await self.prefix_execute(ctx, operation)

    @prefix_market.command(name='create')
    async def prefix_market_create(self, ctx, symbol: str, name: str, price: float, volatility_bps: int):
        async def operation():
            await self.require_prefix_role(ctx, 'central_bank')
            asset = await self.economy.create_forex_asset(ctx.guild.id, symbol, name, price, volatility_bps)
            await self.prefix_reply(ctx, 'Forex currency listed', f'`{asset[0]}` **{asset[1]}** — {format_money(asset[2], await self.currency_symbol(ctx.guild.id))} per unit.', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_economy.group(name='trade', invoke_without_command=True)
    async def prefix_trade(self, ctx):
        await self.prefix_reply(ctx, 'Trade commands', 'Use `trade buy <symbol> <quantity>` or `trade sell <symbol> <quantity>`.')

    @prefix_trade.command(name='buy')
    async def prefix_trade_buy(self, ctx, symbol: str, quantity: float):
        await self.prefix_execute(ctx, lambda: self._prefix_trade(ctx, symbol, quantity, 'buy'))

    @prefix_trade.command(name='sell')
    async def prefix_trade_sell(self, ctx, symbol: str, quantity: float):
        await self.prefix_execute(ctx, lambda: self._prefix_trade(ctx, symbol, quantity, 'sell'))

    async def _prefix_trade(self, ctx, symbol, quantity, direction):
        asset, gross, fee, wallet_delta = await self.economy.trade(ctx.guild.id, ctx.author.id, symbol, quantity, direction)
        symbol_text = await self.currency_symbol(ctx.guild.id)
        total = gross + fee if direction == 'buy' else wallet_delta
        await self.prefix_reply(ctx, f'Shares {direction}ed', f'{format_quantity(parse_quantity(quantity))} `{asset["symbol"]}` · '
                              f'value {format_money(gross, symbol_text)} · fee {format_money(fee, symbol_text)} · '
                              f'total {format_money(total, symbol_text)}.', 'success')

    @prefix_economy.group(name='forex', invoke_without_command=True)
    async def prefix_forex(self, ctx):
        await self.prefix_reply(ctx, 'Forex command', 'Use `forex exchange <from_code> <to_code> <amount>`.')

    @prefix_forex.command(name='exchange')
    async def prefix_forex_exchange(self, ctx, from_currency: str, to_currency: str, amount: float):
        async def operation():
            output, fee, base_currency = await self.economy.exchange(
                ctx.guild.id, ctx.author.id, from_currency, to_currency, amount,
            )
            symbol = await self.currency_symbol(ctx.guild.id)
            source = format_money(parse_money(amount), symbol) if from_currency.upper() == base_currency else format_quantity(parse_quantity(amount))
            destination = format_money(output, symbol) if to_currency.upper() == base_currency else format_quantity(output)
            await self.prefix_reply(ctx, 'Currency exchanged', f'{source} {from_currency.upper()} → '
                                  f'{destination} {to_currency.upper()} · fee {format_money(fee, symbol)}.', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_economy.group(name='blicense', invoke_without_command=True)
    async def prefix_blicense(self, ctx):
        await self.prefix_reply(ctx, 'Business license commands', 'Use `blicense status [member]`, `blicense issue <member>`, or `blicense revoke <member> [reason]`.')

    @prefix_blicense.command(name='issue')
    async def prefix_blicense_issue(self, ctx, member: discord.Member):
        async def operation():
            await self.require_prefix_role(ctx, 'central_bank')
            fee = await self.economy.issue_license(ctx.guild.id, member.id, ctx.author.id)
            await self.prefix_reply(ctx, 'Business license issued', f'{member.mention} may found companies. Fee: {format_money(fee, await self.currency_symbol(ctx.guild.id))}.', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_blicense.command(name='revoke')
    async def prefix_blicense_revoke(self, ctx, member: discord.Member, *, reason='No reason supplied'):
        async def operation():
            await self.require_prefix_role(ctx, 'central_bank')
            await self.economy.revoke_license(ctx.guild.id, member.id, ctx.author.id, reason)
            await self.prefix_reply(ctx, 'Business license revoked', f'{member.mention} — {reason}', 'warning')
        await self.prefix_execute(ctx, operation)

    @prefix_blicense.command(name='status')
    async def prefix_blicense_status(self, ctx, member: discord.Member | None = None):
        target = member or ctx.author
        license_row = await self.economy.license_status(ctx.guild.id, target.id)
        status = license_row['status'] if license_row else 'not issued'
        await self.prefix_reply(ctx, 'Business license status', f'{target.mention}: **{status}**.')

    @prefix_economy.group(name='bond', invoke_without_command=True)
    async def prefix_bond(self, ctx):
        await self.prefix_reply(ctx, 'Bond commands', 'Use `bond issue|list|buy|portfolio|redeem`.')

    @prefix_bond.command(name='issue')
    async def prefix_bond_issue(self, ctx, name: str, face_value: float, yield_bps: int, term_days: int, units: int):
        async def operation():
            await self.require_prefix_role(ctx, 'treasury')
            offering_id = await self.economy.issue_bond(
                ctx.guild.id, ctx.author.id, name, face_value, yield_bps, term_days, units,
            )
            symbol = await self.currency_symbol(ctx.guild.id)
            await self.prefix_reply(ctx, f'Bond offering #{offering_id} created',
                                    f'**{name}** · {format_money(parse_money(face_value), symbol)}/unit · '
                                    f'{yield_bps / 100:.2f}% APR · {term_days} days · {units:,} units.', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_bond.command(name='list')
    async def prefix_bond_list(self, ctx):
        async def operation():
            offerings = await self.economy.bond_offerings(ctx.guild.id)
            symbol = await self.currency_symbol(ctx.guild.id)
            lines = [
                f'`#{row["id"]}` **{row["name"]}** · {format_money(row["face_value"], symbol)}/unit · '
                f'{row["yield_bps"] / 100:.2f}% APR · {row["term_days"]} days · '
                f'{row["units_total"] - row["units_sold"]:,} units available'
                for row in offerings
            ]
            await self.prefix_reply(ctx, 'Open bond offerings', '\n'.join(lines)[:4000] or 'There are no open offerings.')
        await self.prefix_execute(ctx, operation)

    @prefix_bond.command(name='buy')
    async def prefix_bond_buy(self, ctx, offering_id: int, units: int):
        async def operation():
            bond_id, principal, yield_bps, matures = await self.economy.buy_bond(
                ctx.guild.id, ctx.author.id, offering_id, units,
            )
            maturity = datetime.fromtimestamp(matures, timezone.utc).date().isoformat()
            await self.prefix_reply(ctx, f'Bond #{bond_id} purchased',
                                    f'Principal: {format_money(principal, await self.currency_symbol(ctx.guild.id))} · '
                                    f'{yield_bps / 100:.2f}% APR · matures {maturity}.', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_bond.command(name='portfolio')
    async def prefix_bond_portfolio(self, ctx):
        async def operation():
            rows = await self.economy.bonds(ctx.guild.id, ctx.author.id)
            symbol = await self.currency_symbol(ctx.guild.id)
            lines = [
                f'`#{row["id"]}` {row["offering_name"] or "Legacy bond"} · '
                f'{format_money(row["principal"], symbol)} · {row["yield_bps"] / 100:.2f}% APR · '
                f'{row["term_days"]} days · {row["status"]}'
                for row in rows
            ]
            await self.prefix_reply(ctx, 'Bond portfolio', '\n'.join(lines)[:4000] or 'You do not hold any bonds.')
        await self.prefix_execute(ctx, operation)

    @prefix_bond.command(name='redeem')
    async def prefix_bond_redeem(self, ctx):
        async def operation():
            amount, count = await self.economy.redeem_bonds(ctx.guild.id, ctx.author.id)
            await self.prefix_reply(ctx, 'Bonds redeemed',
                                    f'Redeemed {count} bond(s) for {format_money(amount, await self.currency_symbol(ctx.guild.id))} into your bank.',
                                    'success')
        await self.prefix_execute(ctx, operation)

    @prefix_economy.group(name='company', invoke_without_command=True)
    async def prefix_company(self, ctx):
        await self.prefix_reply(ctx, 'Company commands', 'Use `company create|view|deposit|withdraw|hire|fire|payroll|dividend`.')

    @prefix_company.command(name='create')
    async def prefix_company_create(self, ctx, name: str, symbol: str, share_price: float, shares: int, volatility_bps: int):
        async def operation():
            _, issued = await self.economy.create_company(
                ctx.guild.id, ctx.author.id, name, symbol, share_price, shares, volatility_bps,
            )
            await self.prefix_reply(ctx, f'{name} founded',
                                    f'`{symbol.upper()}` · {issued:,} shares at '
                                    f'{format_money(parse_money(share_price), await self.currency_symbol(ctx.guild.id))} each.',
                                    'success')
        await self.prefix_execute(ctx, operation)

    @prefix_company.command(name='view')
    async def prefix_company_view(self, ctx, *, name_or_symbol: str):
        async def operation():
            await self.economy.market(ctx.guild.id)
            company = await self.economy.company(ctx.guild.id, name_or_symbol)
            if company is None:
                raise EconomyError('Company not found.')
            symbol = await self.currency_symbol(ctx.guild.id)
            await self.prefix_reply(ctx, company['name'],
                                    f'`{company["symbol"]}` · share price {format_money(company["price"], symbol)} · '
                                    f'treasury {format_money(company["treasury"], symbol)} · '
                                    f'{company["shares_total"]:,} shares · founder <@{company["owner_id"]}>.')
        await self.prefix_execute(ctx, operation)

    @prefix_company.command(name='deposit')
    async def prefix_company_deposit(self, ctx, name_or_symbol: str, amount: float):
        await self.prefix_execute(ctx, lambda: self._prefix_company_cash(ctx, name_or_symbol, amount, 'deposit'))

    @prefix_company.command(name='withdraw')
    async def prefix_company_withdraw(self, ctx, name_or_symbol: str, amount: float):
        await self.prefix_execute(ctx, lambda: self._prefix_company_cash(ctx, name_or_symbol, amount, 'withdraw'))

    async def _prefix_company_cash(self, ctx, name_or_symbol, amount, direction):
        moved = await self.economy.company_cash(ctx.guild.id, ctx.author.id, name_or_symbol, amount, direction)
        await self.prefix_reply(ctx, f'Company funds {direction}ed',
                                f'Moved {format_money(moved, await self.currency_symbol(ctx.guild.id))} {direction} the company treasury.',
                                'success')

    @prefix_company.command(name='hire')
    async def prefix_company_hire(self, ctx, name_or_symbol: str, member: discord.Member, daily_salary: float):
        async def operation():
            salary = await self.economy.company_hire(ctx.guild.id, ctx.author.id, name_or_symbol, member.id, daily_salary)
            await self.prefix_reply(ctx, 'Employee hired',
                                    f'{member.mention} earns {format_money(salary, await self.currency_symbol(ctx.guild.id))} per daily payroll.',
                                    'success')
        await self.prefix_execute(ctx, operation)

    @prefix_company.command(name='fire')
    async def prefix_company_fire(self, ctx, name_or_symbol: str, member: discord.Member):
        async def operation():
            await self.economy.company_fire(ctx.guild.id, ctx.author.id, name_or_symbol, member.id)
            await self.prefix_reply(ctx, 'Employee removed', f'{member.mention} is no longer on the company payroll.', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_company.command(name='payroll')
    async def prefix_company_payroll(self, ctx, *, name_or_symbol: str):
        async def operation():
            total, count = await self.economy.company_payroll(ctx.guild.id, ctx.author.id, name_or_symbol)
            await self.prefix_reply(ctx, 'Payroll complete',
                                    f'Paid {count} employee(s), {format_money(total, await self.currency_symbol(ctx.guild.id))} total.',
                                    'success')
        await self.prefix_execute(ctx, operation)

    @prefix_company.command(name='dividend')
    async def prefix_company_dividend(self, ctx, name_or_symbol: str, total_amount: float):
        async def operation():
            paid, shareholders = await self.economy.company_dividend(
                ctx.guild.id, ctx.author.id, name_or_symbol, total_amount,
            )
            await self.prefix_reply(ctx, 'Dividend paid',
                                    f'Distributed {format_money(paid, await self.currency_symbol(ctx.guild.id))} '
                                    f'to {shareholders} shareholder(s).', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_economy.group(name='treasury', invoke_without_command=True)
    async def prefix_treasury(self, ctx):
        await self.prefix_reply(ctx, 'Treasury commands', 'Use `treasury balance`, `treasury fee <basis_points>`, or `treasury grant <member> <amount>`.')

    @prefix_treasury.command(name='balance')
    async def prefix_treasury_balance(self, ctx):
        async def operation():
            treasury, settings = await self.economy.treasury(ctx.guild.id)
            await self.prefix_reply(ctx, 'Server treasury',
                                    f'Balance: {format_money(treasury["balance"], settings["currency_symbol"])} · '
                                    f'Trading fee: {settings["trading_fee_bps"] / 100:.2f}%.')
        await self.prefix_execute(ctx, operation)

    @prefix_treasury.command(name='fee')
    async def prefix_treasury_fee(self, ctx, basis_points: int):
        async def operation():
            await self.require_prefix_role(ctx, 'treasury')
            value = await self.economy.set_tax(ctx.guild.id, basis_points, ctx.author.id)
            await self.prefix_reply(ctx, 'Trading fee updated', f'New rate: {value / 100:.2f}%.', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_treasury.command(name='grant')
    async def prefix_treasury_grant(self, ctx, member: discord.Member, amount: float):
        async def operation():
            await self.require_prefix_role(ctx, 'treasury')
            granted = await self.economy.grant_treasury(ctx.guild.id, member.id, amount)
            await self.prefix_reply(ctx, 'Treasury grant issued',
                                    f'{member.mention} received {format_money(granted, await self.currency_symbol(ctx.guild.id))}.',
                                    'success')
        await self.prefix_execute(ctx, operation)

    @prefix_economy.group(name='loan', invoke_without_command=True)
    async def prefix_loan(self, ctx):
        await self.prefix_reply(ctx, 'Loan commands', 'Use `loan take <amount>`, `loan repay <amount>`, or `loan status`.')

    @prefix_loan.command(name='take')
    async def prefix_loan_take(self, ctx, amount: float):
        async def operation():
            loan_id, principal = await self.economy.take_loan(ctx.guild.id, ctx.author.id, amount)
            await self.prefix_reply(ctx, f'Loan #{loan_id} issued',
                                    f'{format_money(principal, await self.currency_symbol(ctx.guild.id))} at configured interest and term.',
                                    'success')
        await self.prefix_execute(ctx, operation)

    @prefix_loan.command(name='repay')
    async def prefix_loan_repay(self, ctx, amount: float):
        async def operation():
            paid, remaining = await self.economy.repay_loan(ctx.guild.id, ctx.author.id, amount)
            symbol = await self.currency_symbol(ctx.guild.id)
            detail = 'Loan fully repaid.' if remaining == 0 else f'Remaining balance: {format_money(remaining, symbol)}.'
            await self.prefix_reply(ctx, 'Loan payment received', f'Paid {format_money(paid, symbol)}. {detail}', 'success')
        await self.prefix_execute(ctx, operation)

    @prefix_loan.command(name='status')
    async def prefix_loan_status(self, ctx):
        async def operation():
            loan = await self.economy.loan_status(ctx.guild.id, ctx.author.id)
            if loan is None:
                return await self.prefix_reply(ctx, 'Loan status', 'You have no active loan.')
            elapsed = max(0, int(time.time()) - loan['issued_at'])
            interest = (loan['balance'] * loan['rate_bps'] * elapsed + loan['interest_remainder']) // (10000 * YEAR_SECONDS)
            await self.prefix_reply(ctx, f'Loan #{loan["id"]}',
                                    f'Current payoff: {format_money(loan["balance"] + interest, await self.currency_symbol(ctx.guild.id))} · '
                                    f'{loan["rate_bps"] / 100:.2f}% APR.')
        await self.prefix_execute(ctx, operation)

    async def reply(self, interaction, title, description, kind='info'):
        card = status_embed(title, description, kind)
        if interaction.response.is_done():
            await interaction.followup.send(embed=card)
        else:
            await interaction.response.send_message(embed=card)

    async def defer(self, interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)

    async def handle_error(self, interaction, error):
        if isinstance(error, EconomyError):
            return await self.reply(interaction, 'Economy action unavailable', str(error), 'error')
        raise error

    @app_commands.command(name='economy', description='Enable or disable the server economy.')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def economy_toggle(self, interaction: discord.Interaction, enabled: bool):
        await self.defer(interaction)
        try:
            settings = await self.economy.configure(interaction.guild_id, enabled)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        state = 'enabled' if settings['enabled'] else 'disabled'
        description = (
            f'{settings.get("currency_name", "Economy")} is active for this server. '
            'Players and their Central Bank/Treasury roles control the economy; prices are simulated.'
            if settings['enabled']
            else 'The server economy is disabled. Its accounts and settings remain saved.'
        )
        await self.reply(
            interaction,
            f'Economy {state}',
            description,
            'success',
        )

    @app_commands.command(name='economy-roles', description='Set the Central Bank and Treasury roles.')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def economy_roles(
        self,
        interaction: discord.Interaction,
        central_bank: discord.Role,
        treasury: discord.Role,
    ):
        await self.defer(interaction)
        try:
            await self.economy.set_roles(interaction.guild_id, central_bank.id, treasury.id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(
            interaction,
            'Economy roles configured',
            f'Central Bank: {central_bank.mention}\nTreasury: {treasury.mention}',
            'success',
        )

    @app_commands.command(name='economy-currency', description='Set the server currency name, code, and symbol.')
    @app_commands.guild_only()
    async def economy_currency(self, interaction: discord.Interaction, name: str, code: str, symbol: str):
        await self.defer(interaction)
        try:
            await self.require_role(interaction.guild_id, interaction.user, 'central_bank')
            name, code, symbol = await self.economy.set_currency(
                interaction.guild_id, name, code, symbol, interaction.user.id,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Currency configured', f'**{name}** (`{code}`) uses **{symbol}**.', 'success')

    @app_commands.command(name='economy-policy', description='Set a Central Bank or Treasury economy policy.')
    @app_commands.guild_only()
    async def economy_policy(self, interaction: discord.Interaction, key: str, value: str):
        await self.defer(interaction)
        try:
            authority = 'treasury' if key.lower() == 'trading_fee_bps' else 'central_bank'
            await self.require_role(interaction.guild_id, interaction.user, authority)
            stored_value = await self.economy.set_policy(
                interaction.guild_id, key, value, interaction.user.id,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        if key.lower() in POLICY_MONEY:
            settings = await self.economy.settings(interaction.guild_id)
            currency_symbol = str(settings.get('currency_symbol') or '') if settings else ''
            value_text = format_money(stored_value, currency_symbol)
        else:
            value_text = str(stored_value)
        await self.reply(interaction, 'Economy policy updated', f'`{key.lower()}` is now **{value_text}**.', 'success')

    @app_commands.command(name='balance', description='View a member’s wallet and bank balance.')
    @app_commands.guild_only()
    async def balance(self, interaction: discord.Interaction, member: discord.Member | None = None):
        await self.defer(interaction)
        target = member or interaction.user
        try:
            account, settings = await self.economy.balance(interaction.guild_id, target.id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        card = embed(f'{target.display_name}’s account')
        card.add_field(name='Wallet', value=format_money(account['wallet'], settings['currency_symbol']), inline=True)
        card.add_field(name='Bank', value=format_money(account['bank'], settings['currency_symbol']), inline=True)
        card.add_field(name='Total', value=format_money(account['wallet'] + account['bank'], settings['currency_symbol']), inline=True)
        card.set_thumbnail(url=target.display_avatar.url)
        await interaction.followup.send(embed=card)

    @app_commands.command(name='portfolio', description='View your cash and investment holdings.')
    @app_commands.guild_only()
    async def portfolio(self, interaction: discord.Interaction):
        await self.defer(interaction)
        try:
            holdings, account = await self.economy.portfolio(interaction.guild_id, interaction.user.id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        currency_symbol = await self.currency_symbol(interaction.guild_id)
        lines = []
        market_value = 0
        cost_basis = 0
        for holding in holdings:
            value = holding['price'] * holding['quantity_milli'] // 1000
            basis = holding['average_cost'] * holding['quantity_milli'] // 1000
            market_value += value
            cost_basis += basis
            lines.append(
                f'`{holding["symbol"]}` {format_quantity(holding["quantity_milli"])} '
                f'{holding["name"]} — value **{format_money(value, currency_symbol)}** · '
                f'P/L **{format_money(value - basis, currency_symbol)}**'
            )
        lines.extend((
            f'Cash: **{format_money(account["wallet"] + account["bank"], currency_symbol)}**',
            f'Portfolio value: **{format_money(market_value, currency_symbol)}** · cost basis **{format_money(cost_basis, currency_symbol)}**',
        ))
        await self.reply(interaction, f'{interaction.user.display_name}’s portfolio', '\n'.join(lines)[:4000])

    @app_commands.command(name='work', description='Earn a simulated paycheck.')
    @app_commands.guild_only()
    async def work(self, interaction: discord.Interaction):
        await self.defer(interaction)
        try:
            reward = await self.economy.claim_income(interaction.guild_id, interaction.user.id, 'work')
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Shift complete', f'You earned **{format_money(reward, await self.currency_symbol(interaction.guild_id))}**.', 'success')

    @app_commands.command(name='daily', description='Claim your once-per-day allowance.')
    @app_commands.guild_only()
    async def daily(self, interaction: discord.Interaction):
        await self.defer(interaction)
        try:
            reward = await self.economy.claim_income(interaction.guild_id, interaction.user.id, 'daily')
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Daily allowance claimed', f'You received **{format_money(reward, await self.currency_symbol(interaction.guild_id))}**.', 'success')

    @app_commands.command(name='pay', description='Send money to another member.')
    @app_commands.guild_only()
    async def pay(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[float, 0.01, 1000000000.0], note: str = ''):
        await self.defer(interaction)
        try:
            paid = await self.economy.transfer(interaction.guild_id, interaction.user.id, member.id, amount, note)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Payment sent', f'{interaction.user.mention} paid {member.mention} **{format_money(paid, await self.currency_symbol(interaction.guild_id))}**.', 'success')

    @bank.command(name='deposit', description='Move wallet funds into the bank.')
    @app_commands.guild_only()
    async def bank_deposit(self, interaction: discord.Interaction, amount: app_commands.Range[float, 0.01, 1000000000.0]):
        await self._bank_move(interaction, amount, 'deposit')

    @bank.command(name='withdraw', description='Move bank funds into your wallet.')
    @app_commands.guild_only()
    async def bank_withdraw(self, interaction: discord.Interaction, amount: app_commands.Range[float, 0.01, 1000000000.0]):
        await self._bank_move(interaction, amount, 'withdraw')

    @bank.command(name='balance', description='View your bank balance and accrued interest.')
    @app_commands.guild_only()
    async def bank_balance(self, interaction: discord.Interaction):
        await self.defer(interaction)
        try:
            account, settings = await self.economy.balance(interaction.guild_id, interaction.user.id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(
            interaction,
            'Bank account',
            f'Bank: **{format_money(account["bank"], settings["currency_symbol"])}**\n'
            f'Savings earn {settings["savings_interest_bps"] / 100:.2f}% annual interest.',
        )

    async def _bank_move(self, interaction, amount, direction):
        await self.defer(interaction)
        try:
            moved = await self.economy.bank_move(interaction.guild_id, interaction.user.id, amount, direction)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, f'Funds {direction}ed', f'Moved **{format_money(moved, await self.currency_symbol(interaction.guild_id))}** {direction} your bank.', 'success')

    @market.command(name='list', description='List simulated stock and forex prices.')
    @app_commands.guild_only()
    async def market_list(self, interaction: discord.Interaction):
        await self.defer(interaction)
        try:
            assets = await self.economy.market(interaction.guild_id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        settings = await self.economy.settings(interaction.guild_id)
        symbol = settings.get('currency_symbol', '') if settings else ''
        lines = [
            f'`{row["symbol"]}` **{row["name"]}** — {format_money(row["price"], symbol)} per unit · {row["kind"]}'
            for row in assets
        ]
        card = embed('Simulated markets', ('\n'.join(lines) or 'No assets are listed.')[:4000])
        card.set_footer(text='Fictional, server-local prices · not financial advice or real-world quotes')
        await interaction.followup.send(embed=card)

    @market.command(name='create', description='List a Central Bank-approved forex currency.')
    @app_commands.guild_only()
    async def market_create(
        self,
        interaction: discord.Interaction,
        symbol: str,
        name: str,
        price: app_commands.Range[float, 0.01, 1000000000.0],
        volatility_bps: app_commands.Range[int, 0, 5000],
    ):
        await self.defer(interaction)
        try:
            await self.require_role(interaction.guild_id, interaction.user, 'central_bank')
            created = await self.economy.create_forex_asset(
                interaction.guild_id, symbol, name, price, volatility_bps,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(
            interaction,
            'Forex currency listed',
            f'`{created[0]}` **{created[1]}** — {format_money(created[2], await self.currency_symbol(interaction.guild_id))} per unit.',
            'success',
        )

    @market.command(name='history', description='View recent simulated daily prices.')
    @app_commands.guild_only()
    async def market_history(self, interaction: discord.Interaction, symbol: str):
        await self.defer(interaction)
        try:
            rows = await self.economy.history(interaction.guild_id, symbol)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        if not rows:
            return await self.reply(interaction, 'No price history', f'No quotes are recorded for `{symbol.upper()}`.', 'warning')
        settings = await self.economy.settings(interaction.guild_id)
        currency_symbol = settings.get('currency_symbol', '') if settings else ''
        lines = [f'`{row["quote_day"]}` — **{format_money(row["price"], currency_symbol)}**' for row in rows]
        await self.reply(interaction, f'{symbol.upper()} price history', '\n'.join(lines))

    @blicense.command(name='issue', description='Issue a business license to a member.')
    @app_commands.guild_only()
    async def blicense_issue(self, interaction: discord.Interaction, member: discord.Member):
        await self.defer(interaction)
        try:
            await self.require_role(interaction.guild_id, interaction.user, 'central_bank')
            fee = await self.economy.issue_license(interaction.guild_id, member.id, interaction.user.id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Business license issued', f'{member.mention} may now found companies. License fee: **{format_money(fee, await self.currency_symbol(interaction.guild_id))}**.', 'success')

    @blicense.command(name='revoke', description='Revoke an active business license.')
    @app_commands.guild_only()
    async def blicense_revoke(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = 'No reason supplied',
    ):
        await self.defer(interaction)
        try:
            await self.require_role(interaction.guild_id, interaction.user, 'central_bank')
            await self.economy.revoke_license(
                interaction.guild_id, member.id, interaction.user.id, reason,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Business license revoked', f'{member.mention} — {reason}', 'warning')

    @blicense.command(name='status', description='Check a member’s business license.')
    @app_commands.guild_only()
    async def blicense_status(self, interaction: discord.Interaction, member: discord.Member | None = None):
        await self.defer(interaction)
        target = member or interaction.user
        license_row = await self.economy.license_status(interaction.guild_id, target.id)
        status = license_row['status'] if license_row else 'not issued'
        await self.reply(interaction, 'Business license status', f'{target.mention}: **{status}**.')

    @trade.command(name='buy', description='Buy listed stock shares.')
    @app_commands.guild_only()
    async def trade_buy(self, interaction: discord.Interaction, symbol: str, quantity: app_commands.Range[float, 0.001, 1000000.0]):
        await self._trade(interaction, symbol, quantity, 'buy')

    @trade.command(name='sell', description='Sell shares you own.')
    @app_commands.guild_only()
    async def trade_sell(self, interaction: discord.Interaction, symbol: str, quantity: app_commands.Range[float, 0.001, 1000000.0]):
        await self._trade(interaction, symbol, quantity, 'sell')

    async def _trade(self, interaction, symbol, quantity, direction):
        await self.defer(interaction)
        try:
            asset, gross, fee, wallet_delta = await self.economy.trade(
                interaction.guild_id, interaction.user.id, symbol, quantity, direction,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        noun = 'bought' if direction == 'buy' else 'sold'
        amount = gross + fee if direction == 'buy' else wallet_delta
        currency_symbol = await self.currency_symbol(interaction.guild_id)
        await self.reply(
            interaction,
            f'Shares {noun}',
            f'{format_quantity(parse_quantity(quantity))} `{asset["symbol"]}` at **{format_money(asset["price"], currency_symbol)}** each.\n'
            f'Trade value: **{format_money(gross, currency_symbol)}** · fee: **{format_money(fee, currency_symbol)}** · '
            f'{"paid" if direction == "buy" else "received"}: **{format_money(amount, currency_symbol)}**.',
            'success',
        )

    @forex.command(name='exchange', description='Exchange BASE credits or a listed currency.')
    @app_commands.guild_only()
    async def forex_exchange(
        self,
        interaction: discord.Interaction,
        from_currency: str,
        to_currency: str,
        amount: app_commands.Range[float, 0.001, 1000000000.0],
    ):
        await self.defer(interaction)
        try:
            output, fee, base_currency = await self.economy.exchange(
                interaction.guild_id, interaction.user.id,
                from_currency, to_currency, amount,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        currency_symbol = await self.currency_symbol(interaction.guild_id)
        source_amount = (
            format_money(parse_money(amount), currency_symbol)
            if from_currency.upper() == base_currency else format_quantity(parse_quantity(amount))
        )
        destination_amount = (
            format_money(output, currency_symbol)
            if to_currency.upper() == base_currency else format_quantity(output)
        )
        await self.reply(
            interaction,
            'Currency exchanged',
            f'Exchanged **{source_amount} '
            f'{from_currency.upper()}** for **{destination_amount} '
            f'{to_currency.upper()}**.\n'
            f'Fee: **{format_money(fee, currency_symbol)}**. Use `{base_currency}` for this server’s main currency.',
            'success',
        )

    @bond.command(name='issue', description='Create a government bond offering from the Treasury.')
    @app_commands.guild_only()
    async def bond_issue(
        self,
        interaction: discord.Interaction,
        name: str,
        face_value: app_commands.Range[float, 0.01, 1000000000.0],
        yield_bps: app_commands.Range[int, 0, 100000],
        term_days: app_commands.Range[int, 1, 3650],
        units: app_commands.Range[int, 1, 1000000000],
    ):
        await self.defer(interaction)
        try:
            await self.require_role(interaction.guild_id, interaction.user, 'treasury')
            offering_id = await self.economy.issue_bond(
                interaction.guild_id, interaction.user.id, name, face_value,
                yield_bps, term_days, units,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(
            interaction,
            f'Bond offering #{offering_id} created',
            f'**{name}** is open for {units:,} units at '
            f'{format_money(parse_money(face_value), await self.currency_symbol(interaction.guild_id))} per unit, '
            f'{yield_bps / 100:.2f}% APR, {term_days} days.',
            'success',
        )

    @bond.command(name='list', description='List open government bond offerings.')
    @app_commands.guild_only()
    async def bond_list(self, interaction: discord.Interaction):
        await self.defer(interaction)
        try:
            offerings = await self.economy.bond_offerings(interaction.guild_id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        if not offerings:
            return await self.reply(interaction, 'Bond offerings', 'There are no open offerings.')
        currency_symbol = await self.currency_symbol(interaction.guild_id)
        lines = [
            f'`#{row["id"]}` **{row["name"]}** · {format_money(row["face_value"], currency_symbol)}/unit · '
            f'{row["yield_bps"] / 100:.2f}% APR · {row["term_days"]} days · '
            f'{row["units_total"] - row["units_sold"]:,} units available'
            for row in offerings
        ]
        await self.reply(interaction, 'Open bond offerings', '\n'.join(lines)[:4000])

    @bond.command(name='buy', description='Purchase units from an open government bond offering.')
    @app_commands.guild_only()
    async def bond_buy(
        self,
        interaction: discord.Interaction,
        offering_id: app_commands.Range[int, 1, 2147483647],
        units: app_commands.Range[int, 1, 1000000000],
    ):
        await self.defer(interaction)
        try:
            bond_id, principal, yield_bps, matures = await self.economy.buy_bond(
                interaction.guild_id, interaction.user.id, offering_id, units,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        maturity_date = datetime.fromtimestamp(matures, timezone.utc).date().isoformat()
        await self.reply(
            interaction,
            f'Bond #{bond_id} purchased',
            f'Principal: **{format_money(principal, await self.currency_symbol(interaction.guild_id))}** · '
            f'annual yield: **{yield_bps / 100:.2f}%** · maturity: **{maturity_date}**.\n'
            'Redeem with `/bond redeem` after maturity.',
            'success',
        )

    @bond.command(name='portfolio', description='View your government bonds.')
    @app_commands.guild_only()
    async def bond_portfolio(self, interaction: discord.Interaction):
        await self.defer(interaction)
        try:
            rows = await self.economy.bonds(interaction.guild_id, interaction.user.id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        if not rows:
            return await self.reply(interaction, 'Bond portfolio', 'You do not hold any bonds.')
        lines = []
        currency_symbol = await self.currency_symbol(interaction.guild_id)
        for row in rows:
            interest = row['principal'] * row['yield_bps'] * row['term_days'] // (10000 * 365)
            maturity = datetime.fromtimestamp(row['matures_at'], timezone.utc).date().isoformat()
            lines.append(
                f'`#{row["id"]}` {format_money(row["principal"], currency_symbol)} · {row["yield_bps"] / 100:.2f}% APR · '
                f'{row["term_days"]} days · matures {maturity} · {row["status"]}'
                + (f' · payout {format_money(row["principal"] + interest, currency_symbol)}' if row['status'] == 'active' else '')
            )
        await self.reply(interaction, 'Bond portfolio', '\n'.join(lines)[:4000])

    @bond.command(name='redeem', description='Redeem all matured government bonds.')
    @app_commands.guild_only()
    async def bond_redeem(self, interaction: discord.Interaction):
        await self.defer(interaction)
        try:
            amount, count = await self.economy.redeem_bonds(interaction.guild_id, interaction.user.id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Bonds redeemed', f'Redeemed **{count}** bond(s) for **{format_money(amount, await self.currency_symbol(interaction.guild_id))}**, paid into your bank.', 'success')

    @company.command(name='create', description='Found a company using your active business license.')
    @app_commands.guild_only()
    async def company_create(
        self,
        interaction: discord.Interaction,
        name: str,
        symbol: str,
        share_price: app_commands.Range[float, 0.01, 1000000000.0],
        shares: app_commands.Range[int, 1, 1000000000],
        volatility_bps: app_commands.Range[int, 0, 5000],
    ):
        await self.defer(interaction)
        try:
            _, issued_shares = await self.economy.create_company(
                interaction.guild_id, interaction.user.id, name, symbol,
                share_price, shares, volatility_bps,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(
            interaction,
            f'{name} founded',
            f'**{symbol.upper()}** is listed with {issued_shares:,} founder shares at '
            f'{format_money(parse_money(share_price), await self.currency_symbol(interaction.guild_id))} per share.\n'
            'Founder shares can be traded through `/trade`.',
            'success',
        )

    @company.command(name='view', description='View company finances and stock quote.')
    @app_commands.guild_only()
    async def company_view(self, interaction: discord.Interaction, name_or_symbol: str):
        await self.defer(interaction)
        try:
            await self.economy.market(interaction.guild_id)
            company = await self.economy.company(interaction.guild_id, name_or_symbol)
            if company is None:
                raise EconomyError('Company not found.')
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        card = embed(company['name'], f'Listed company · `{company["symbol"]}`')
        currency_symbol = await self.currency_symbol(interaction.guild_id)
        card.add_field(name='Share price', value=format_money(company['price'], currency_symbol), inline=True)
        card.add_field(name='Company treasury', value=format_money(company['treasury'], currency_symbol), inline=True)
        card.add_field(name='Shares outstanding', value=f'{company["shares_total"]:,}', inline=True)
        card.add_field(name='Founder', value=f'<@{company["owner_id"]}>', inline=True)
        await interaction.followup.send(embed=card)

    @company.command(name='deposit', description='Move your money into your company treasury.')
    @app_commands.guild_only()
    async def company_deposit(self, interaction: discord.Interaction, name_or_symbol: str, amount: app_commands.Range[float, 0.01, 1000000000.0]):
        await self._company_cash(interaction, name_or_symbol, amount, 'deposit')

    @company.command(name='withdraw', description='Withdraw money from your company treasury.')
    @app_commands.guild_only()
    async def company_withdraw(self, interaction: discord.Interaction, name_or_symbol: str, amount: app_commands.Range[float, 0.01, 1000000000.0]):
        await self._company_cash(interaction, name_or_symbol, amount, 'withdraw')

    async def _company_cash(self, interaction, name_or_symbol, amount, direction):
        await self.defer(interaction)
        try:
            moved = await self.economy.company_cash(
                interaction.guild_id, interaction.user.id, name_or_symbol, amount, direction,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, f'Company funds {direction}ed', f'Moved **{format_money(moved, await self.currency_symbol(interaction.guild_id))}** {direction} the company treasury.', 'success')

    @company.command(name='hire', description='Hire a member with a daily salary.')
    @app_commands.guild_only()
    async def company_hire(
        self,
        interaction: discord.Interaction,
        name_or_symbol: str,
        member: discord.Member,
        daily_salary: app_commands.Range[float, 0.01, 1000000000.0],
    ):
        await self.defer(interaction)
        try:
            salary = await self.economy.company_hire(
                interaction.guild_id, interaction.user.id, name_or_symbol, member.id, daily_salary,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Employee hired', f'{member.mention} earns **{format_money(salary, await self.currency_symbol(interaction.guild_id))}** per daily payroll.', 'success')

    @company.command(name='fire', description='Remove an employee from your company.')
    @app_commands.guild_only()
    async def company_fire(self, interaction: discord.Interaction, name_or_symbol: str, member: discord.Member):
        await self.defer(interaction)
        try:
            await self.economy.company_fire(
                interaction.guild_id, interaction.user.id, name_or_symbol, member.id,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Employee removed', f'{member.mention} is no longer on the company payroll.', 'success')

    @company.command(name='payroll', description='Pay all employees once per day from company funds.')
    @app_commands.guild_only()
    async def company_payroll(self, interaction: discord.Interaction, name_or_symbol: str):
        await self.defer(interaction)
        try:
            total, count = await self.economy.company_payroll(
                interaction.guild_id, interaction.user.id, name_or_symbol,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Payroll complete', f'Paid **{count}** employee(s), **{format_money(total, await self.currency_symbol(interaction.guild_id))}** total.', 'success')

    @company.command(name='dividend', description='Pay a total dividend to current shareholders.')
    @app_commands.guild_only()
    async def company_dividend(
        self,
        interaction: discord.Interaction,
        name_or_symbol: str,
        total_amount: app_commands.Range[float, 0.01, 1000000000.0],
    ):
        await self.defer(interaction)
        try:
            paid, shareholders = await self.economy.company_dividend(
                interaction.guild_id, interaction.user.id, name_or_symbol, total_amount,
            )
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(
            interaction,
            'Dividend paid',
            f'Distributed **{format_money(paid, await self.currency_symbol(interaction.guild_id))}** to **{shareholders}** shareholder(s) by current ownership.',
            'success',
        )

    @treasury.command(name='balance', description='View the server treasury and configured trading fee.')
    @app_commands.guild_only()
    async def treasury_balance(self, interaction: discord.Interaction):
        await self.defer(interaction)
        try:
            treasury, settings = await self.economy.treasury(interaction.guild_id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(
            interaction,
            'Server treasury',
            f'Balance: **{format_money(treasury["balance"], settings["currency_symbol"])}**\n'
            f'Trading fee: **{settings["trading_fee_bps"] / 100:.2f}%**\n'
            'A negative balance represents public debt.',
        )

    @treasury.command(name='fee', description='Set the trading fee in basis points.')
    @app_commands.guild_only()
    async def treasury_tax(self, interaction: discord.Interaction, basis_points: app_commands.Range[int, 0, 10000]):
        await self.defer(interaction)
        try:
            await self.require_role(interaction.guild_id, interaction.user, 'treasury')
            await self.economy.set_tax(interaction.guild_id, basis_points, interaction.user.id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Trading fee updated', f'New rate: **{basis_points / 100:.2f}%**.', 'success')

    @treasury.command(name='grant', description='Grant a member money from available treasury funds.')
    @app_commands.guild_only()
    async def treasury_grant(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[float, 0.01, 1000000000.0],
    ):
        await self.defer(interaction)
        try:
            await self.require_role(interaction.guild_id, interaction.user, 'treasury')
            granted = await self.economy.grant_treasury(interaction.guild_id, member.id, amount)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, 'Treasury grant issued', f'{member.mention} received **{format_money(granted, await self.currency_symbol(interaction.guild_id))}**.', 'success')

    @loan.command(name='take', description='Take a loan subject to the configured lending policy.')
    @app_commands.guild_only()
    async def loan_take(self, interaction: discord.Interaction, amount: app_commands.Range[float, 0.01, 1000000000.0]):
        await self.defer(interaction)
        try:
            loan_id, principal = await self.economy.take_loan(interaction.guild_id, interaction.user.id, amount)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        await self.reply(interaction, f'Loan #{loan_id} issued', f'**{format_money(principal, await self.currency_symbol(interaction.guild_id))}** at configured interest and term. Repay with `/loan repay`.', 'success')

    @loan.command(name='repay', description='Make a payment against your active loan.')
    @app_commands.guild_only()
    async def loan_repay(self, interaction: discord.Interaction, amount: app_commands.Range[float, 0.01, 1000000000.0]):
        await self.defer(interaction)
        try:
            paid, remaining = await self.economy.repay_loan(interaction.guild_id, interaction.user.id, amount)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        currency_symbol = await self.currency_symbol(interaction.guild_id)
        message = 'Loan fully repaid.' if remaining == 0 else f'Remaining balance: **{format_money(remaining, currency_symbol)}**.'
        await self.reply(interaction, 'Loan payment received', f'Paid **{format_money(paid, currency_symbol)}**. {message}', 'success')

    @loan.command(name='status', description='View your active loan balance.')
    @app_commands.guild_only()
    async def loan_status(self, interaction: discord.Interaction):
        await self.defer(interaction)
        try:
            loan = await self.economy.loan_status(interaction.guild_id, interaction.user.id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        if loan is None:
            return await self.reply(interaction, 'Loan status', 'You have no active loan.')
        elapsed = max(0, int(time.time()) - loan['issued_at'])
        numerator = loan['balance'] * loan['rate_bps'] * elapsed + loan['interest_remainder']
        interest = numerator // (10000 * YEAR_SECONDS)
        due_date = datetime.fromtimestamp(loan['due_at'], timezone.utc).date().isoformat()
        await self.reply(
            interaction,
            f'Loan #{loan["id"]}',
            f'Current payoff: **{format_money(loan["balance"] + interest, await self.currency_symbol(interaction.guild_id))}** · '
            f'rate: **{loan["rate_bps"] / 100:.2f}% APR** · due: **{due_date}**.',
        )

    @app_commands.command(name='economy-history', description='View your recent economy transactions.')
    @app_commands.guild_only()
    async def economy_history(self, interaction: discord.Interaction):
        await self.defer(interaction)
        try:
            rows = await self.economy.ledger(interaction.guild_id, interaction.user.id)
        except EconomyError as error:
            return await self.handle_error(interaction, error)
        if not rows:
            return await self.reply(interaction, 'Transaction history', 'No transactions yet.')
        currency_symbol = await self.currency_symbol(interaction.guild_id)
        lines = [
            f'`{row["created_at"]}` **{row["entry_type"]}** · '
            f'wallet {format_money(row["wallet_delta"], currency_symbol)} · bank {format_money(row["bank_delta"], currency_symbol)}'
            + (f' — {row["details"]}' if row['details'] else '')
            for row in rows
        ]
        await self.reply(interaction, 'Recent transactions', '\n'.join(lines)[:4000])


async def setup(bot):
    await bot.add_cog(Economy(bot))
