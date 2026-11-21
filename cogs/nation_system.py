import discord
from discord import app_commands
from discord.ext import commands

from common import embed


def citizen_id(guild_id, user_id):
    return f'C-{guild_id % 100000}-{user_id % 100000:05d}'


class NationSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    nation = app_commands.Group(name='nation', description='Nation profile and statistics')
    citizen = app_commands.Group(name='citizen', description='Citizenship registry')
    government = app_commands.Group(name='government', description='Government administration')
    court = app_commands.Group(name='court', description='Court and legal system')
    territory = app_commands.Group(name='territory', description='Territory administration')
    profile = app_commands.Group(name='profile', description='Citizen profiles')
    achievement = app_commands.Group(name='achievement', description='Citizen achievements')

    @nation.command(name='set', description='Create or update nation information.')
    @app_commands.checks.has_permissions(administrator=True)
    async def nation_set(self, interaction, name: str, description: str | None = None,
                         capital: str | None = None, population: int | None = None,
                         government_type: str | None = None, language: str | None = None,
                         currency: str | None = None, flag_url: str | None = None):
        await interaction.response.defer(thinking=True)
        await self.bot.db.execute(
            '''INSERT INTO nations(guild_id,name,flag_url,description,capital,population,government_type,official_language,currency)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(guild_id) DO UPDATE SET name=excluded.name,flag_url=COALESCE(excluded.flag_url,nations.flag_url),
               description=COALESCE(excluded.description,nations.description),capital=COALESCE(excluded.capital,nations.capital),
               population=COALESCE(excluded.population,nations.population),government_type=COALESCE(excluded.government_type,nations.government_type),
               official_language=COALESCE(excluded.official_language,nations.official_language),currency=COALESCE(excluded.currency,nations.currency),
               updated_at=CURRENT_TIMESTAMP''',
            (interaction.guild_id, name, flag_url, description, capital, population or 0,
             government_type, language, currency),
        )
        await interaction.followup.send(embed=embed('Nation profile saved', f'**{name}** has been saved.', 'success'))

    @nation.command(name='view', description='View the public nation profile.')
    async def nation_view(self, interaction):
        row = await self.bot.db.fetchone('SELECT * FROM nations WHERE guild_id=?', (interaction.guild_id,))
        if not row:
            return await interaction.response.send_message('No nation profile has been configured.')
        citizens = await self.bot.db.fetchone(
            "SELECT COUNT(*) AS count FROM citizens WHERE guild_id=? AND status='active'",
            (interaction.guild_id,),
        )
        card = embed(row['name'], row['description'] or 'No national description configured.')
        card.add_field(name='Capital', value=row['capital'] or 'Not set', inline=True)
        card.add_field(name='Population', value=f"{row['population']:,}", inline=True)
        card.add_field(name='Citizens registered', value=str(citizens['count']), inline=True)
        card.add_field(name='Government', value=row['government_type'] or 'Not set', inline=True)
        card.add_field(name='Language', value=row['official_language'] or 'Not set', inline=True)
        card.add_field(name='Currency', value=row['currency'] or 'Not set', inline=True)
        if row['flag_url']:
            card.set_thumbnail(url=row['flag_url'])
        await interaction.response.send_message(embed=card)

    @citizen.command(name='register', description='Register or update a citizen.')
    @app_commands.checks.has_permissions(administrator=True)
    async def citizen_register(self, interaction, member: discord.Member, character_name: str,
                               nationality: str = 'Unspecified'):
        await interaction.response.defer(thinking=True)
        cid = citizen_id(interaction.guild_id, member.id)
        await self.bot.db.execute(
            '''INSERT INTO citizens(guild_id,user_id,citizen_id,character_name,nationality,status)
               VALUES(?,?,?,?,?, 'active')
               ON CONFLICT(guild_id,user_id) DO UPDATE SET character_name=excluded.character_name,
               nationality=excluded.nationality,status='active',registered_at=COALESCE(citizens.registered_at,CURRENT_TIMESTAMP)''',
            (interaction.guild_id, member.id, cid, character_name, nationality),
        )
        await self.bot.db.execute(
            'INSERT INTO citizen_history(guild_id,user_id,action,actor_id,details) VALUES(?,?,?,?,?)',
            (interaction.guild_id, member.id, 'registered', interaction.user.id, cid),
        )
        await interaction.followup.send(embed=embed('Citizen registered', f'{member.mention} — `{cid}` — {character_name}', 'success'))

    @citizen.command(name='view', description='View a citizen record.')
    async def citizen_view(self, interaction, member: discord.Member):
        row = await self.bot.db.fetchone(
            'SELECT * FROM citizens WHERE guild_id=? AND user_id=?',
            (interaction.guild_id, member.id),
        )
        if not row:
            return await interaction.response.send_message('This member is not in the citizenship registry.')
        card = embed(f'Citizen: {member}', f'Character: **{row["character_name"] or "Not set"}**')
        card.set_thumbnail(url=member.display_avatar.url)
        card.add_field(name='Citizen ID', value=row['citizen_id'], inline=True)
        card.add_field(name='Nationality', value=row['nationality'] or 'Unspecified', inline=True)
        card.add_field(name='Status', value=row['status'], inline=True)
        card.add_field(name='Registered', value=row['registered_at'], inline=False)
        await interaction.response.send_message(embed=card)

    @citizen.command(name='remove', description='Remove citizenship while preserving history.')
    @app_commands.checks.has_permissions(administrator=True)
    async def citizen_remove(self, interaction, member: discord.Member, reason: str = 'Citizenship removed'):
        await self.bot.db.execute(
            "UPDATE citizens SET status='removed' WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, member.id),
        )
        await self.bot.db.execute(
            'INSERT INTO citizen_history(guild_id,user_id,action,actor_id,details) VALUES(?,?,?,?,?)',
            (interaction.guild_id, member.id, 'removed', interaction.user.id, reason),
        )
        await interaction.response.send_message(embed=embed('Citizenship removed', f'{member.mention}: {reason}', 'warning'))

    @citizen.command(name='search', description='Search citizenship records.')
    async def citizen_search(self, interaction, query: str):
        term = f'%{query}%'
        rows = await self.bot.db.fetchall(
            '''SELECT * FROM citizens WHERE guild_id=? AND status='active'
               AND (character_name LIKE ? OR nationality LIKE ? OR citizen_id LIKE ?) LIMIT 20''',
            (interaction.guild_id, term, term, term),
        )
        if not rows:
            return await interaction.response.send_message('No matching citizen records.')
        await interaction.response.send_message(embed=embed(
            'Citizen search',
            '\n'.join(f'`{r["citizen_id"]}` — {r["character_name"] or "Unknown"} ({r["nationality"] or "Unspecified"})' for r in rows),
        ))

    @government.command(name='position', description='Create a government position.')
    @app_commands.checks.has_permissions(administrator=True)
    async def government_position(self, interaction, name: str, description: str = 'No description'):
        await self.bot.db.execute(
            'INSERT INTO government_positions(guild_id,name,description) VALUES(?,?,?) '
            'ON CONFLICT(guild_id,name) DO UPDATE SET description=excluded.description',
            (interaction.guild_id, name, description),
        )
        await interaction.response.send_message(embed=embed('Government position saved', f'**{name}**', 'success'))

    @government.command(name='appoint', description='Appoint an official.')
    @app_commands.checks.has_permissions(administrator=True)
    async def government_appoint(self, interaction, member: discord.Member, position: str):
        await self.bot.db.execute(
            'INSERT INTO government_appointments(guild_id,user_id,position_name,appointed_by) '
            'VALUES(?,?,?,?) ON CONFLICT(guild_id,user_id,position_name) DO UPDATE SET appointed_by=excluded.appointed_by',
            (interaction.guild_id, member.id, position, interaction.user.id),
        )
        await interaction.response.send_message(embed=embed('Official appointed', f'{member.mention} — **{position}**', 'success'))

    @government.command(name='view', description='View government structure.')
    async def government_view(self, interaction):
        positions = await self.bot.db.fetchall(
            'SELECT name,description FROM government_positions WHERE guild_id=? ORDER BY name',
            (interaction.guild_id,),
        )
        appointments = await self.bot.db.fetchall(
            'SELECT user_id,position_name FROM government_appointments WHERE guild_id=?',
            (interaction.guild_id,),
        )
        lines = [f'**{r["name"]}** — {r["description"]}' for r in positions]
        officials = [f'<@{r["user_id"]}> — **{r["position_name"]}**' for r in appointments]
        card = embed('Government structure')
        card.add_field(name='Positions', value='\n'.join(lines)[:1024] or 'None configured', inline=False)
        card.add_field(name='Officials', value='\n'.join(officials)[:1024] or 'No appointments', inline=False)
        await interaction.response.send_message(embed=card)

    @government.command(name='department', description='Create or update a department.')
    @app_commands.checks.has_permissions(administrator=True)
    async def government_department(self, interaction, name: str, description: str = 'No description'):
        await self.bot.db.execute(
            'INSERT INTO departments(guild_id,name,description) VALUES(?,?,?) '
            'ON CONFLICT(guild_id,name) DO UPDATE SET description=excluded.description',
            (interaction.guild_id, name, description),
        )
        await interaction.response.send_message(embed=embed('Department saved', f'**{name}**', 'success'))

    @court.command(name='law-create', description='Create a national law.')
    @app_commands.checks.has_permissions(administrator=True)
    async def law_create(self, interaction, title: str, description: str):
        cur = await self.bot.db.execute(
            'INSERT INTO laws(guild_id,title,description,author_id) VALUES(?,?,?,?)',
            (interaction.guild_id, title, description, interaction.user.id),
        )
        await interaction.response.send_message(embed=embed('Law created', f'**{title}** — Law #{cur.lastrowid}', 'success'))

    @court.command(name='law-view', description='View a national law.')
    async def law_view(self, interaction, law_id: int):
        row = await self.bot.db.fetchone(
            'SELECT * FROM laws WHERE guild_id=? AND id=?',
            (interaction.guild_id, law_id),
        )
        if not row:
            return await interaction.response.send_message('Law not found.')
        await interaction.response.send_message(embed=embed(row['title'], row['description'], 'info').add_field(name='Status', value=row['status']))

    @court.command(name='case-open', description='Open a court case.')
    @app_commands.checks.has_permissions(administrator=True)
    async def case_open(self, interaction, title: str, case_type: str, defendant: discord.Member | None = None):
        cur = await self.bot.db.execute(
            'INSERT INTO court_cases(guild_id,title,case_type,defendant_id,created_by) VALUES(?,?,?,?,?)',
            (interaction.guild_id, title, case_type, defendant.id if defendant else None, interaction.user.id),
        )
        await self.bot.db.execute(
            'INSERT INTO case_history(guild_id,case_id,actor_id,action,details) VALUES(?,?,?,?,?)',
            (interaction.guild_id, cur.lastrowid, interaction.user.id, 'opened', title),
        )
        await interaction.response.send_message(embed=embed('Court case opened', f'**{title}** — Case #{cur.lastrowid}', 'success'))

    @court.command(name='case-record', description='Record a verdict or sentencing decision.')
    @app_commands.checks.has_permissions(administrator=True)
    async def case_record(self, interaction, case_id: int, verdict: str, sentence: str = 'Not specified'):
        await self.bot.db.execute(
            "UPDATE court_cases SET verdict=?,sentence=?,status='closed',updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?",
            (verdict, sentence, interaction.guild_id, case_id),
        )
        await self.bot.db.execute(
            'INSERT INTO case_history(guild_id,case_id,actor_id,action,details) VALUES(?,?,?,?,?)',
            (interaction.guild_id, case_id, interaction.user.id, 'verdict recorded', f'{verdict} — {sentence}'),
        )
        await interaction.response.send_message(embed=embed('Court record updated', f'Case #{case_id}: **{verdict}**', 'success'))

    @court.command(name='case-view', description='View a court case.')
    async def case_view(self, interaction, case_id: int):
        row = await self.bot.db.fetchone(
            'SELECT * FROM court_cases WHERE guild_id=? AND id=?',
            (interaction.guild_id, case_id),
        )
        if not row:
            return await interaction.response.send_message('Case not found.')
        card = embed(row['title'], f'Type: **{row["case_type"]}**')
        card.add_field(name='Status', value=row['status'], inline=True)
        card.add_field(name='Verdict', value=row['verdict'] or 'Pending', inline=True)
        card.add_field(name='Sentence', value=row['sentence'] or 'Pending', inline=False)
        await interaction.response.send_message(embed=card)

    @territory.command(name='register', description='Register a territory.')
    @app_commands.checks.has_permissions(administrator=True)
    async def territory_register(self, interaction, name: str, territory_type: str = 'Region',
                                 capital: str | None = None, description: str | None = None, population: int = 0):
        await self.bot.db.execute(
            'INSERT INTO territories(guild_id,name,territory_type,capital,description,population) '
            'VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET territory_type=excluded.territory_type,'
            'capital=excluded.capital,description=excluded.description,population=excluded.population,updated_at=CURRENT_TIMESTAMP',
            (interaction.guild_id, name, territory_type, capital, description, population),
        )
        await interaction.response.send_message(embed=embed('Territory registered', f'**{name}** ({territory_type})', 'success'))

    @territory.command(name='view', description='View a territory.')
    async def territory_view(self, interaction, name: str):
        row = await self.bot.db.fetchone(
            'SELECT * FROM territories WHERE guild_id=? AND name=?',
            (interaction.guild_id, name),
        )
        if not row:
            return await interaction.response.send_message('Territory not found.')
        card = embed(row['name'], row['description'] or 'No description.')
        card.add_field(name='Type', value=row['territory_type'], inline=True)
        card.add_field(name='Capital', value=row['capital'] or 'Not set', inline=True)
        card.add_field(name='Population', value=f"{row['population']:,}", inline=True)
        await interaction.response.send_message(embed=card)

    @profile.command(name='view', description='View or create a public citizen profile.')
    async def profile_view(self, interaction, member: discord.Member | None = None):
        member = member or interaction.user
        row = await self.bot.db.fetchone(
            'SELECT * FROM profiles WHERE guild_id=? AND user_id=?',
            (interaction.guild_id, member.id),
        )
        xp = await self.bot.db.fetchone(
            'SELECT xp,level FROM xp WHERE guild_id=? AND user_id=?',
            (interaction.guild_id, member.id),
        )
        card = embed(f'{member.display_name} • Citizen Profile', row['bio'] if row and row['bio'] else 'No profile customization yet.')
        card.set_thumbnail(url=member.display_avatar.url)
        card.add_field(name='Character', value=row['character_name'] if row and row['character_name'] else 'Not set', inline=True)
        card.add_field(name='Nationality', value=row['nationality'] if row and row['nationality'] else 'Unspecified', inline=True)
        card.add_field(name='Level', value=str(xp['level'] if xp else 0), inline=True)
        card.add_field(name='XP', value=str(xp['xp'] if xp else 0), inline=True)
        await interaction.response.send_message(embed=card)

    @profile.command(name='edit', description='Edit your citizen profile.')
    async def profile_edit(self, interaction, character_name: str | None = None, nationality: str | None = None, bio: str | None = None):
        existing = await self.bot.db.fetchone(
            'SELECT character_name,nationality,bio FROM profiles WHERE guild_id=? AND user_id=?',
            (interaction.guild_id, interaction.user.id),
        )
        character_name = character_name or (existing['character_name'] if existing else None)
        nationality = nationality or (existing['nationality'] if existing else None)
        bio = bio or (existing['bio'] if existing else None)
        await self.bot.db.execute(
            '''INSERT INTO profiles(guild_id,user_id,character_name,nationality,bio,avatar_url)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(guild_id,user_id) DO UPDATE SET character_name=excluded.character_name,
               nationality=excluded.nationality,bio=excluded.bio,avatar_url=excluded.avatar_url''',
            (interaction.guild_id, interaction.user.id, character_name, nationality, bio, interaction.user.display_avatar.url),
        )
        await interaction.response.send_message(embed=embed('Profile updated', 'Your citizen profile has been saved.', 'success'))

    @achievement.command(name='award', description='Award a citizen achievement.')
    @app_commands.checks.has_permissions(administrator=True)
    async def achievement_award(self, interaction, member: discord.Member, name: str, description: str = 'Awarded by TowelBot'):
        await self.bot.db.execute(
            'INSERT INTO achievements(guild_id,user_id,name,description) VALUES(?,?,?,?) '
            'ON CONFLICT(guild_id,user_id,name) DO UPDATE SET description=excluded.description',
            (interaction.guild_id, member.id, name, description),
        )
        await interaction.response.send_message(embed=embed('Achievement awarded', f'{member.mention} earned **{name}**', 'success'))


async def setup(bot):
    await bot.add_cog(NationSystem(bot))
