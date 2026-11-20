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
