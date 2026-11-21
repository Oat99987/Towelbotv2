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
    assert {"audit","cases","applications","elections","election_votes","tickets","reminders"} <= names
    db.close()

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
