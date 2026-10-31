"""SQLite cache per library folder, stored outside the photo folder."""
import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from photos2show.config import cache_root

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


def library_path(root: Path) -> Path:
    """The cache folder of a photo folder, without creating it."""
    key = hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:12]
    return cache_root() / f"{root.resolve().name}-{key}"


def library_dir(root: Path) -> Path:
    d = library_path(root)
    (d / "thumbs").mkdir(parents=True, exist_ok=True)
    return d


def connect(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(library_dir(root) / "photos2show.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
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


def write_json(path: Path, data) -> None:
    """Write a temp file, then swap it in: a crash mid-write must not leave an empty file that breaks every page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def load_projects() -> dict[str, str]:
    """Holiday projects: name -> photo folder."""
    f = cache_root() / "projects.json"
    return json.loads(f.read_text()) if f.exists() else {}


# App settings: key -> (label, choices, default). Add a line here and it shows up on the settings page.
SETTINGS = {
    "theme": ("Theme", ("auto", "light", "dark"), "auto"),
}


def load_settings() -> dict[str, str]:
    f = cache_root() / "settings.json"
    saved = json.loads(f.read_text()) if f.exists() else {}
    return {k: saved[k] if saved.get(k) in choices else default for k, (_, choices, default) in SETTINGS.items()}


def save_setting(key: str, value: str) -> None:
    write_json(cache_root() / "settings.json", load_settings() | {key: value})


def save_project(name: str, folder: Path) -> None:
    save_projects(load_projects() | {name: str(folder)})


def save_projects(projects: dict[str, str]) -> None:
    write_json(cache_root() / "projects.json", projects)


def unused_libraries() -> list[Path]:
    """Cache folders (<name>-<12 hex>/thumbs) that no project points to, e.g. left after removing a project."""
    used = {library_path(Path(f)) for f in load_projects().values()}
    root = cache_root()
    found = root.glob("*-" + "[0-9a-f]" * 12) if root.is_dir() else []
    return sorted(d for d in found if (d / "thumbs").is_dir() and d not in used)


def mark_analysed(root: Path) -> None:
    """Remember when the folder was last scanned (shown on the projects page)."""
    (library_dir(root) / "analysed_at").write_text(datetime.now().replace(microsecond=0).isoformat())


def project_stats(folder: Path) -> dict:
    """State for the projects page; reads only, so a missing or never analysed folder creates no cache."""
    d = library_path(folder)
    stats = {"exists": folder.is_dir(), "files": 0, "described": 0, "picks": 0, "analysed_at": None}
    if (d / "analysed_at").exists():
        stats["analysed_at"] = datetime.fromisoformat((d / "analysed_at").read_text().strip())
    if (d / "photos2show.db").exists():
        conn = sqlite3.connect(d / "photos2show.db")
        stats["files"], stats["described"] = conn.execute("SELECT COUNT(*), COUNT(ai) FROM media").fetchone()
        conn.close()
    if (d / "selection.json").exists():
        stats["picks"] = len(json.loads((d / "selection.json").read_text())["picks"])
    return stats
