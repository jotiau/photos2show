"""SQLite cache per library folder, stored outside the photo folder."""
import hashlib
import json
import sqlite3
from pathlib import Path

from imgsort.config import cache_root

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,              -- 'image' | 'video'
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    taken_at TEXT,                   -- ISO timestamp
    width INTEGER,
    height INTEGER,
    thumb TEXT,                      -- file name in thumbs/
    frames INTEGER DEFAULT 0,        -- number of saved video frames
    metrics TEXT,                    -- JSON from quality.metrics()
    score REAL,
    phash TEXT,
    flags TEXT DEFAULT '[]',         -- JSON list
    duplicate_of INTEGER,            -- id of the best image of the same scene
    ai TEXT                          -- JSON from ai.analyse_image(), NULL until described
);
"""


def library_dir(root: Path) -> Path:
    key = hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:12]
    d = cache_root() / f"{root.resolve().name}-{key}"
    (d / "thumbs").mkdir(parents=True, exist_ok=True)
    return d


def connect(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(library_dir(root) / "imgsort.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    try:  # caches created before M2 lack the ai column
        conn.execute("ALTER TABLE media ADD COLUMN ai TEXT")
    except sqlite3.OperationalError:
        pass
    return conn


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["metrics"] = json.loads(d["metrics"] or "{}")
    d["flags"] = json.loads(d["flags"] or "[]")
    d["ai"] = json.loads(d["ai"]) if d["ai"] else None
    return d


def all_media(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM media ORDER BY taken_at IS NULL, taken_at, path")
    return [row_to_dict(r) for r in rows]


def get_media(conn: sqlite3.Connection, media_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
    return row_to_dict(row) if row else None
