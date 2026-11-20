import aiosqlite
from pathlib import Path

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, actor_id INTEGER, target_id INTEGER, action TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS cases (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, moderator_id INTEGER NOT NULL, action TEXT NOT NULL, reason TEXT, expires_at INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
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
'''


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.conn = None

    async def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute('PRAGMA journal_mode=WAL')
        await self.conn.execute('PRAGMA busy_timeout=5000')
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def execute(self, sql, params=()):
        async with self.conn.execute(sql, params) as cur:
            await self.conn.commit()
            return cur

    async def fetchone(self, sql, params=()):
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql, params=()):
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchall()

    async def audit(self, guild_id, actor_id, action, target_id=None, details=''):
        await self.execute('INSERT INTO audit(guild_id,actor_id,target_id,action,details) VALUES(?,?,?,?,?)', (guild_id, actor_id, target_id, action, details))
