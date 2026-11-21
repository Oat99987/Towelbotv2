import sqlite3
from pathlib import Path

def test_schema_can_initialize(tmp_path):
    # Import schema text without requiring a Discord connection.
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from database import SCHEMA
    db=sqlite3.connect(tmp_path/"test.sqlite")
    db.executescript(SCHEMA)
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"audit","cases","applications","elections","election_votes","tickets","reminders","police_records"} <= names
    db.close()


def test_general_utility_commands_and_input_validation():
    import asyncio
    import discord
    from discord.ext import commands

    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.core import Core, parse_choices, parse_dice_notation

    assert parse_dice_notation("d20") == (1, 20)
    assert parse_dice_notation("2D6") == (2, 6)
    assert parse_choices("red | blue | green") == ["red", "blue", "green"]
    for notation in ("d0", "101d6", "d1001", "2d"):
        try:
            parse_dice_notation(notation)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid dice notation was accepted: {notation}")
    for options in ("one", "a|b|" + "x" * 1001):
        try:
            parse_choices(options)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid choice list was accepted")

    async def scenario():
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
        await bot.add_cog(Core(bot))
        slash_names = {command.name for command in bot.tree.get_commands()}
        assert {"userinfo", "roleinfo", "choose", "roll", "coinflip"} <= slash_names
        assert {"userinfo", "roleinfo", "choose", "roll", "coinflip"} <= {
            command.name for command in bot.commands
        }
        for command in bot.tree.get_commands():
            command.to_dict(bot.tree)
        await bot.close()

    asyncio.run(scenario())


def test_police_record_schema_and_hybrid_command_registration():
    import asyncio
    import discord
    from discord.ext import commands

    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.moderation import Moderation
    from cogs.police import Police
    from database import SCHEMA

    db = sqlite3.connect(':memory:')
    db.executescript(SCHEMA)
    db.execute(
        "INSERT INTO police_records(guild_id,subject_id,officer_id,action,status,details) "
        "VALUES(1,2,3,'warrant','active','Roleplay warrant')"
    )
    try:
        db.execute(
            "INSERT INTO police_records(guild_id,subject_id,officer_id,action,status,details) "
            "VALUES(1,2,3,'unknown','active','Invalid action')"
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError('The police record schema accepted an unsupported action')
    db.close()

    async def scenario():
        bot = commands.Bot(command_prefix='!', intents=discord.Intents.none(), help_command=None)
        await bot.add_cog(Police(bot))
        await bot.add_cog(Moderation(bot))
        police = next(command for command in bot.tree.get_commands() if command.name == 'police')
        assert isinstance(police, discord.app_commands.Group)
        subcommands = {command.name for command in police.commands}
        assert {'set-role', 'report', 'citation', 'arrest', 'release', 'records', 'warrant'} <= subcommands
        warrant = next(command for command in police.commands if command.name == 'warrant')
        assert isinstance(warrant, discord.app_commands.Group)
        assert {'issue', 'clear', 'list'} <= {command.name for command in warrant.commands}
        police.to_dict(bot.tree)
        assert {'warnings', 'note'} <= {command.name for command in bot.tree.get_commands()}
        await bot.close()

    asyncio.run(scenario())


def test_bot_replies_do_not_use_ephemeral_responses():
    cogs_dir = Path(__file__).resolve().parents[1] / "cogs"
    python_sources = "\n".join(path.read_text(encoding="utf-8") for path in cogs_dir.glob("*.py"))
    assert "ephemeral=" not in python_sources

def test_all_cog_modules_import():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import pkgutil
    import cogs

    imported = [
        module.name
        for module in pkgutil.iter_modules(cogs.__path__, cogs.__name__ + ".")
    ]
    assert len(imported) >= 15
    for module_name in imported:
        __import__(module_name)

def test_setup_dashboard_views_fit_discord_component_limit():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.setup import AdditionalChannelSettingsView, ChannelSettingsView, RoleSettingsView, SetupDashboard

    for view in (
        SetupDashboard(object()),
        ChannelSettingsView(object()),
        AdditionalChannelSettingsView(object()),
        RoleSettingsView(object()),
    ):
        assert len(view.children) <= 5
        view.stop()

def test_setup_channel_selection_accepts_discord_resolved_channel():
    import sys
    from types import SimpleNamespace

    import discord

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.setup import is_supported_channel_selection

    text_channel = SimpleNamespace(guild_id=123, type=discord.ChannelType.text)
    news_channel = SimpleNamespace(guild_id=123, type=discord.ChannelType.news)
    forum = SimpleNamespace(guild_id=123, type=discord.ChannelType.forum)
    category = SimpleNamespace(guild_id=123, type=discord.ChannelType.category)
    voice_channel = SimpleNamespace(guild_id=123, type=discord.ChannelType.voice)
    stage_channel = SimpleNamespace(guild_id=123, type=discord.ChannelType.stage_voice)
    other_guild_channel = SimpleNamespace(guild_id=456, type=discord.ChannelType.text)

    assert is_supported_channel_selection(text_channel, 123, 'welcome')
    assert is_supported_channel_selection(news_channel, 123, 'announcement')
    assert is_supported_channel_selection(forum, 123, 'announcement')
    assert is_supported_channel_selection(category, 123, 'ticket_category')
    assert is_supported_channel_selection(voice_channel, 123, 'voice_lobby')
    assert is_supported_channel_selection(stage_channel, 123, 'voice_lobby')
    assert not is_supported_channel_selection(voice_channel, 123, 'welcome')
    assert not is_supported_channel_selection(other_guild_channel, 123, 'welcome')

def test_configured_role_uses_server_specific_database_setting():
    import asyncio
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from common import configured_role

    expected_role = object()

    class FakeDatabase:
        async def get_config(self, guild_id, section, key):
            assert (guild_id, section, key) == (123, 'roles', 'jailed')
            return '456'

    class FakeGuild:
        id = 123

        def get_role(self, role_id):
            return expected_role if role_id == 456 else None

    class FakeBot:
        db = FakeDatabase()
        config = {'roles': {'jailed': 789}}

    assert asyncio.run(configured_role(FakeGuild(), FakeBot(), 'jailed')) is expected_role

def test_border_review_channel_uses_per_server_setting_without_setup():
    import asyncio
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    import discord

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.border import Border

    expected_channel = SimpleNamespace(id=456, type=discord.ChannelType.news)

    class FakeDatabase:
        async def get_config(self, guild_id, section, key):
            assert (guild_id, section, key) == (123, 'channels', 'application_review')
            return '456'

    class FakeGuild:
        id = 123
        text_channels = []

        def get_channel(self, channel_id):
            return expected_channel if channel_id == 456 else None

    class FakeBot:
        db = FakeDatabase()
        config = {}

    border = Border(FakeBot())
    assert asyncio.run(border.get_application_review_channel(FakeGuild())) is expected_channel


def test_border_get_role_uses_database_role_setting():
    import asyncio
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.border import Border

    expected_role = object()

    class FakeDatabase:
        async def get_config(self, guild_id, section, key):
            assert (guild_id, section, key) == (123, 'roles', 'citizen')
            return '456'

    class FakeGuild:
        id = 123

        def get_role(self, role_id):
            return expected_role if role_id == 456 else None

    class FakeBot:
        db = FakeDatabase()
        config = {}

    assert asyncio.run(Border(FakeBot()).get_role(FakeGuild(), 'citizen')) is expected_role


def test_border_member_lookup_fetches_when_not_cached():
    import asyncio
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.border import Border

    expected_member = object()

    class FakeGuild:
        def get_member(self, user_id):
            return None

        async def fetch_member(self, user_id):
            assert user_id == 789
            return expected_member

    class FakeBot:
        pass

    assert asyncio.run(Border(FakeBot()).get_member(FakeGuild(), 789)) is expected_member


def test_border_review_channel_error_points_to_border_command():
    import asyncio
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.border import Border

    class FakeDatabase:
        async def get_config(self, guild_id, section, key):
            return None

    class FakeGuild:
        id = 123
        text_channels = []

    class FakeBot:
        db = FakeDatabase()
        config = {}

    border = Border(FakeBot())
    try:
        asyncio.run(border.get_application_review_channel(FakeGuild()))
    except ValueError as exc:
        assert '/border application-channel' in str(exc)
    else:
        raise AssertionError('Expected an error when no review channel is configured')

def test_ticket_category_uses_server_setting_and_persistent_close_buttons():
    import asyncio
    import sys
    from pathlib import Path
    from unittest.mock import Mock

    import discord

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cogs.tickets import TicketCloseView, get_ticket_category

    category = Mock(spec=discord.CategoryChannel)
    category.id = 456

    class FakeDatabase:
        async def get_config(self, guild_id, section, key):
            return '456'

        async def set_config(self, guild_id, section, key, value):
            raise AssertionError('Configured categories should not be rewritten')

    class FakeGuild:
        id = 123
        categories = []

        def get_channel(self, channel_id):
            return category if channel_id == 456 else None

    class FakeBot:
        db = FakeDatabase()
        config = {}

    assert asyncio.run(get_ticket_category(FakeBot(), FakeGuild())) is category
    view_a = TicketCloseView(FakeBot(), 100)
    view_b = TicketCloseView(FakeBot(), 101)
    assert view_a.is_persistent()
    assert view_b.is_persistent()
    button_a, button_b = view_a.children[0], view_b.children[0]
    assert isinstance(button_a, discord.ui.Button)
    assert isinstance(button_b, discord.ui.Button)
    assert button_a.custom_id != button_b.custom_id
    view_a.stop()
    view_b.stop()
