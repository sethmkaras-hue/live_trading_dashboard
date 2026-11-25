import os
import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path

ROOT = Path(os.getenv("DATA_DIR", "data"))
DB = ROOT / "dashboard.sqlite3"

@contextmanager
def database(write=False):
    with closing(sqlite3.connect(DB, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn

def initialize():
    ROOT.mkdir(parents=True, exist_ok=True)
    with database() as conn:
        if conn.execute("PRAGMA user_version").fetchone()[0] > 2:
            raise RuntimeError("Database schema is newer than this application")
        conn.executescript("""
        PRAGMA journal_mode=WAL;
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS bars(symbol TEXT,t INTEGER,o REAL,h REAL,l REAL,c REAL,v INTEGER,
            PRIMARY KEY(symbol,t));
        CREATE TABLE IF NOT EXISTS levels(symbol TEXT,t INTEGER,s REAL,r REAL,evidence TEXT,
            PRIMARY KEY(symbol,t));
        CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY, t INTEGER, yaml TEXT);
        CREATE TABLE IF NOT EXISTS snapshots(symbol TEXT,t INTEGER, observed INTEGER, payload TEXT,
            PRIMARY KEY(symbol,t));
        CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY, symbol TEXT,t INTEGER,
            side TEXT, price REAL, qty REAL, fee REAL, stop REAL, target REAL, notes TEXT,
            checklist TEXT, context TEXT, version INTEGER,stage INTEGER);
        CREATE INDEX IF NOT EXISTS execution_order ON executions(symbol,t);
        CREATE TABLE IF NOT EXISTS flows(id INTEGER PRIMARY KEY,t INTEGER,amount REAL,note TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS quotes(symbol TEXT,t INTEGER,observed INTEGER,price REAL,
            PRIMARY KEY(symbol,t));
        CREATE TABLE IF NOT EXISTS screenshots(execution_id TEXT PRIMARY KEY, mime TEXT NOT NULL, image BLOB NOT NULL);
        PRAGMA user_version=2;
        COMMIT;
        """)
