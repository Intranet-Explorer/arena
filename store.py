"""SQLite event log for the arena. Everything that happens goes here."""

import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    ts REAL, turn INTEGER, agent TEXT, role TEXT, content TEXT, dur REAL
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY,
    ts REAL, turn INTEGER, agent TEXT, tool TEXT,
    args TEXT, result TEXT, duration REAL, ok INTEGER
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    ts REAL, turn INTEGER, root TEXT, n_files INTEGER, manifest TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL, turn INTEGER, kind TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_turn ON messages(turn);
CREATE INDEX IF NOT EXISTS idx_tool_turn ON tool_calls(turn);
"""


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """Add columns to databases created by older versions."""
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(messages)")]
        if "dur" not in cols:
            self.db.execute("ALTER TABLE messages ADD COLUMN dur REAL")

    def message(self, turn, agent, role, content, dur=None):
        self.db.execute(
            "INSERT INTO messages (ts,turn,agent,role,content,dur) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), turn, agent, role, content, dur),
        )

    def tool_call(self, turn, agent, tool, args, result, duration, ok):
        self.db.execute(
            "INSERT INTO tool_calls (ts,turn,agent,tool,args,result,duration,ok) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                time.time(), turn, agent, tool,
                json.dumps(args)[:20000], str(result)[:100000],
                duration, int(ok),
            ),
        )

    def snapshot(self, turn, root, manifest):
        self.db.execute(
            "INSERT INTO snapshots (ts,turn,root,n_files,manifest) VALUES (?,?,?,?,?)",
            (time.time(), turn, root, len(manifest), json.dumps(manifest)),
        )

    def event(self, turn, kind, detail=""):
        self.db.execute(
            "INSERT INTO events (ts,turn,kind,detail) VALUES (?,?,?,?)",
            (time.time(), turn, kind, str(detail)[:20000]),
        )

    def last_turn(self):
        row = self.db.execute("SELECT MAX(turn) FROM messages").fetchone()
        return row[0] or 0

    def transcript(self, agent=None, limit=200):
        q = "SELECT turn,agent,role,content FROM messages"
        args = []
        if agent:
            q += " WHERE agent=?"
            args.append(agent)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return list(reversed(self.db.execute(q, args).fetchall()))
