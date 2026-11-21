import aiosqlite
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, actor_id INTEGER, target_id INTEGER, action TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS cases (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, moderator_id INTEGER NOT NULL, action TEXT NOT NULL, reason TEXT, expires_at INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS police_records (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, subject_id INTEGER NOT NULL, officer_id INTEGER NOT NULL, action TEXT NOT NULL CHECK(action IN ('report','citation','arrest','release','warrant','warrant_clear')), status TEXT NOT NULL DEFAULT 'recorded' CHECK(status IN ('recorded','active','cleared')), details TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_police_records_subject ON police_records(guild_id,subject_id,id DESC);
CREATE TABLE IF NOT EXISTS border_questions (guild_id INTEGER NOT NULL, position INTEGER NOT NULL, label TEXT NOT NULL, required INTEGER NOT NULL, max_length INTEGER NOT NULL, PRIMARY KEY(guild_id,position));
CREATE TABLE IF NOT EXISTS applications (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', answers TEXT NOT NULL, reviewer_id INTEGER, reason TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, reviewed_at TEXT);
CREATE INDEX IF NOT EXISTS idx_applications_pending ON applications(guild_id,user_id,status);
CREATE TABLE IF NOT EXISTS elections (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', channel_id INTEGER, message_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, election_id INTEGER NOT NULL, name TEXT NOT NULL, FOREIGN KEY(election_id) REFERENCES elections(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS election_votes (election_id INTEGER NOT NULL, voter_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(election_id,voter_id), FOREIGN KEY(election_id) REFERENCES elections(id), FOREIGN KEY(candidate_id) REFERENCES candidates(id));
CREATE TABLE IF NOT EXISTS xp (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, xp INTEGER NOT NULL DEFAULT 0, level INTEGER NOT NULL DEFAULT 0, last_award INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS custom_commands (guild_id INTEGER NOT NULL, name TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(guild_id,name));
CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, content TEXT NOT NULL, due_at INTEGER NOT NULL, interval_seconds INTEGER NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS polls (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, message_id INTEGER, question TEXT NOT NULL, options TEXT NOT NULL, closes_at INTEGER, closed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, user_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, closed_at TEXT);
CREATE TABLE IF NOT EXISTS starboard_posts (guild_id INTEGER NOT NULL, message_id INTEGER NOT NULL, showcase_id INTEGER NOT NULL, PRIMARY KEY(guild_id,message_id));
CREATE TABLE IF NOT EXISTS invite_uses (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, invite_code TEXT, inviter_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS reminders_config (guild_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(guild_id,key));
CREATE TABLE IF NOT EXISTS temp_actions (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, action TEXT NOT NULL, expires_at INTEGER NOT NULL, payload TEXT);
CREATE TABLE IF NOT EXISTS reaction_roles (guild_id INTEGER NOT NULL, message_id INTEGER NOT NULL, role_id INTEGER NOT NULL, label TEXT NOT NULL, PRIMARY KEY(guild_id,message_id,role_id));
CREATE TABLE IF NOT EXISTS voice_rooms (guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, owner_id INTEGER NOT NULL, PRIMARY KEY(guild_id,channel_id));
CREATE TABLE IF NOT EXISTS stats (guild_id INTEGER NOT NULL, key TEXT NOT NULL, value INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(guild_id,key));
CREATE TABLE IF NOT EXISTS guild_config (guild_id INTEGER NOT NULL, section TEXT NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY(guild_id,section,key));
CREATE TABLE IF NOT EXISTS nations (guild_id INTEGER PRIMARY KEY, name TEXT NOT NULL, flag_url TEXT, description TEXT, capital TEXT, population INTEGER NOT NULL DEFAULT 0, government_type TEXT, official_language TEXT, currency TEXT, history TEXT, laws TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS citizens (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, citizen_id TEXT NOT NULL, character_name TEXT, nationality TEXT, status TEXT NOT NULL DEFAULT 'active', role_position TEXT, registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(guild_id,user_id), UNIQUE(guild_id,citizen_id));
CREATE TABLE IF NOT EXISTS citizen_history (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, action TEXT NOT NULL, actor_id INTEGER, details TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS government_positions (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, UNIQUE(guild_id,name));
CREATE TABLE IF NOT EXISTS departments (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, head_id INTEGER, UNIQUE(guild_id,name));
CREATE TABLE IF NOT EXISTS government_appointments (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, position_name TEXT NOT NULL, appointed_by INTEGER, appointed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(guild_id,user_id,position_name));
CREATE TABLE IF NOT EXISTS laws (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', author_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS court_cases (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, title TEXT NOT NULL, case_type TEXT NOT NULL, defendant_id INTEGER, prosecutor_id INTEGER, judge_id INTEGER, lawyer_id INTEGER, status TEXT NOT NULL DEFAULT 'open', verdict TEXT, sentence TEXT, evidence TEXT, notes TEXT, created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS case_history (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, case_id INTEGER NOT NULL, actor_id INTEGER, action TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS territories (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL, territory_type TEXT NOT NULL DEFAULT 'region', capital TEXT, owner_id INTEGER, description TEXT, boundaries TEXT, population INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(guild_id,name));
CREATE TABLE IF NOT EXISTS territory_history (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, territory_id INTEGER NOT NULL, actor_id INTEGER, action TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS territory_disputes (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, territory_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open', claimants TEXT, record TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS profiles (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, character_name TEXT, nationality TEXT, nation TEXT, bio TEXT, avatar_url TEXT, PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS achievements (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, awarded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(guild_id,user_id,name));
CREATE TABLE IF NOT EXISTS announcements (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL, channel_id INTEGER, role_id INTEGER, status TEXT NOT NULL DEFAULT 'draft', scheduled_at INTEGER, published_at TEXT, author_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS logging_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS warnings (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, moderator_id INTEGER NOT NULL, reason TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS economy_settings (
    guild_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1))
);
CREATE TABLE IF NOT EXISTS economy_policies (
    guild_id INTEGER NOT NULL,
    policy TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_by INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(guild_id,policy)
);
CREATE TABLE IF NOT EXISTS economy_roles (
    guild_id INTEGER PRIMARY KEY,
    central_bank_role_id INTEGER NOT NULL,
    treasury_role_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS economy_business_licenses (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','revoked')),
    issued_by INTEGER NOT NULL,
    issued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_by INTEGER,
    revoked_at TEXT,
    reason TEXT,
    PRIMARY KEY(guild_id,user_id)
);
CREATE TABLE IF NOT EXISTS economy_accounts (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    wallet INTEGER NOT NULL DEFAULT 0 CHECK(wallet >= 0),
    bank INTEGER NOT NULL DEFAULT 0 CHECK(bank >= 0),
    last_work INTEGER NOT NULL DEFAULT 0,
    last_daily INTEGER NOT NULL DEFAULT 0,
    bank_last_interest INTEGER NOT NULL DEFAULT 0,
    bank_interest_remainder INTEGER NOT NULL DEFAULT 0 CHECK(bank_interest_remainder >= 0),
    PRIMARY KEY(guild_id,user_id)
);
CREATE TABLE IF NOT EXISTS economy_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    entry_type TEXT NOT NULL,
    wallet_delta INTEGER NOT NULL DEFAULT 0,
    bank_delta INTEGER NOT NULL DEFAULT 0,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_economy_ledger_user ON economy_ledger(guild_id,user_id,id DESC);
CREATE TABLE IF NOT EXISTS economy_treasury (
    guild_id INTEGER PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS economy_assets (
    guild_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('stock','forex')),
    price INTEGER NOT NULL CHECK(price > 0),
    volatility_bps INTEGER NOT NULL DEFAULT 0 CHECK(volatility_bps BETWEEN 0 AND 5000),
    quote_day TEXT NOT NULL,
    company_id INTEGER,
    PRIMARY KEY(guild_id,symbol)
);
CREATE TABLE IF NOT EXISTS economy_price_history (
    guild_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    quote_day TEXT NOT NULL,
    price INTEGER NOT NULL CHECK(price > 0),
    PRIMARY KEY(guild_id,symbol,quote_day),
    FOREIGN KEY(guild_id,symbol) REFERENCES economy_assets(guild_id,symbol)
);
CREATE TABLE IF NOT EXISTS economy_holdings (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    quantity_milli INTEGER NOT NULL CHECK(quantity_milli > 0),
    average_cost INTEGER NOT NULL CHECK(average_cost >= 0),
    PRIMARY KEY(guild_id,user_id,symbol),
    FOREIGN KEY(guild_id,symbol) REFERENCES economy_assets(guild_id,symbol)
);
CREATE TABLE IF NOT EXISTS economy_bonds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    offering_id INTEGER,
    units INTEGER NOT NULL DEFAULT 1 CHECK(units > 0),
    principal INTEGER NOT NULL CHECK(principal > 0),
    yield_bps INTEGER NOT NULL CHECK(yield_bps >= 0),
    term_days INTEGER NOT NULL CHECK(term_days > 0),
    purchased_at INTEGER NOT NULL,
    matures_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','redeemed'))
);
CREATE INDEX IF NOT EXISTS idx_economy_bonds_due ON economy_bonds(guild_id,user_id,status,matures_at);
CREATE TABLE IF NOT EXISTS economy_bond_offerings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    face_value INTEGER NOT NULL CHECK(face_value > 0),
    yield_bps INTEGER NOT NULL CHECK(yield_bps >= 0),
    term_days INTEGER NOT NULL CHECK(term_days > 0),
    units_total INTEGER NOT NULL CHECK(units_total > 0),
    units_sold INTEGER NOT NULL DEFAULT 0 CHECK(units_sold >= 0),
    issuer_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id,name)
);
CREATE TABLE IF NOT EXISTS economy_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    treasury INTEGER NOT NULL DEFAULT 0 CHECK(treasury >= 0),
    shares_total INTEGER NOT NULL CHECK(shares_total > 0),
    last_payroll INTEGER NOT NULL DEFAULT 0,
    UNIQUE(guild_id,name),
    UNIQUE(guild_id,symbol),
    FOREIGN KEY(guild_id,symbol) REFERENCES economy_assets(guild_id,symbol)
);
CREATE TABLE IF NOT EXISTS economy_company_members (
    company_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    salary INTEGER NOT NULL DEFAULT 0 CHECK(salary >= 0),
    PRIMARY KEY(company_id,user_id),
    FOREIGN KEY(company_id) REFERENCES economy_companies(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS economy_loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    principal INTEGER NOT NULL CHECK(principal > 0),
    balance INTEGER NOT NULL CHECK(balance >= 0),
    rate_bps INTEGER NOT NULL CHECK(rate_bps >= 0),
    issued_at INTEGER NOT NULL,
    due_at INTEGER NOT NULL,
    interest_remainder INTEGER NOT NULL DEFAULT 0 CHECK(interest_remainder >= 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','repaid'))
);
CREATE INDEX IF NOT EXISTS idx_economy_loans_user ON economy_loans(guild_id,user_id,status);
'''


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self):
        async with self._lock:
            if self.conn is not None:
                raise RuntimeError('Database is already connected')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(self.path)
            try:
                conn.row_factory = aiosqlite.Row
                await conn.execute('PRAGMA journal_mode=WAL')
                await conn.execute('PRAGMA busy_timeout=5000')
                await conn.executescript(SCHEMA)
                await self._migrate_economy_schema(conn)
                await conn.commit()
            except BaseException:
                await conn.close()
                raise
            self.conn = conn

    async def _migrate_economy_schema(self, conn):
        async with conn.execute('PRAGMA table_info(economy_bonds)') as cursor:
            columns = {row['name'] for row in await cursor.fetchall()}
        if 'offering_id' not in columns:
            await conn.execute('ALTER TABLE economy_bonds ADD COLUMN offering_id INTEGER')
        if 'units' not in columns:
            await conn.execute(
                'ALTER TABLE economy_bonds ADD COLUMN units INTEGER NOT NULL DEFAULT 1'
            )

        legacy_symbols = ('TWL', 'ORB', 'SYN', 'USD', 'EUR', 'GBP', 'JPY')
        placeholders = ','.join('?' for _ in legacy_symbols)
        await conn.execute(
            f'''DELETE FROM economy_price_history
                WHERE symbol IN ({placeholders}) AND guild_id IN (
                    SELECT guild_id FROM economy_assets
                    WHERE symbol IN ({placeholders}) AND company_id IS NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM economy_holdings h
                        WHERE h.guild_id=economy_assets.guild_id
                        AND h.symbol=economy_assets.symbol
                    )
                )''',
            (*legacy_symbols, *legacy_symbols),
        )
        await conn.execute(
            f'''DELETE FROM economy_assets
                WHERE symbol IN ({placeholders}) AND company_id IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM economy_holdings h
                    WHERE h.guild_id=economy_assets.guild_id
                    AND h.symbol=economy_assets.symbol
                )''',
            legacy_symbols,
        )

    async def close(self):
        async with self._lock:
            conn = self.conn
            if conn is not None:
                await conn.close()
                self.conn = None

    async def execute(self, sql, params=()):
        async with self._lock:
            conn = self.conn
            if conn is None:
                raise RuntimeError('Database is not connected')
            async with conn.execute(sql, params) as cur:
                await conn.commit()
                return cur

    async def fetchone(self, sql, params=()):
        async with self._lock:
            conn = self.conn
            if conn is None:
                raise RuntimeError('Database is not connected')
            async with conn.execute(sql, params) as cur:
                return await cur.fetchone()

    async def fetchall(self, sql, params=()):
        async with self._lock:
            conn = self.conn
            if conn is None:
                raise RuntimeError('Database is not connected')
            async with conn.execute(sql, params) as cur:
                return await cur.fetchall()

    @asynccontextmanager
    async def transaction(self):
        async with self._lock:
            conn = self.conn
            if conn is None:
                raise RuntimeError('Database is not connected')
            await conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def audit(self, guild_id, actor_id, action, target_id=None, details=''):
        await self.execute('INSERT INTO audit(guild_id,actor_id,target_id,action,details) VALUES(?,?,?,?,?)', (guild_id, actor_id, target_id, action, details))

    async def get_config(self, guild_id, section, key, default=None):
        row = await self.fetchone(
            'SELECT value FROM guild_config WHERE guild_id=? AND section=? AND key=?',
            (guild_id, section, key),
        )
        return row['value'] if row else default

    async def set_config(self, guild_id, section, key, value):
        await self.execute(
            'INSERT INTO guild_config(guild_id,section,key,value) VALUES(?,?,?,?) '
            'ON CONFLICT(guild_id,section,key) DO UPDATE SET value=excluded.value',
            (guild_id, section, key, str(value)),
        )
