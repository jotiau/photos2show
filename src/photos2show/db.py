"""SQLite cache per library folder, stored outside the photo folder."""
import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from photos2show.config import cache_root, default_concurrency, thinking

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
    ai TEXT,                         -- JSON from ai.analyse_image(), NULL until described
    chosen INTEGER DEFAULT 0,        -- best shot of its scene by choice: 1 the user, 2 the AI (side by side); 3 kept separately
    excluded INTEGER DEFAULT 0       -- 1: "Never pick" – never offered to the AI for the slideshow
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
    have = {r[1] for r in conn.execute("PRAGMA table_info(media)")}
    for column in {"chosen", "excluded"} - have:  # libraries from before the column
        conn.execute(f"ALTER TABLE media ADD COLUMN {column} INTEGER DEFAULT 0")
    return conn


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["metrics"] = json.loads(d["metrics"] or "{}")
    d["flags"] = json.loads(d["flags"] or "[]")
    d["ai"] = json.loads(d["ai"]) if d["ai"] else None
    if d["ai"]:  # raw LLM output: tags may be "beach, sunset", hold numbers or null; every reader expects strings
        tags = d["ai"].get("tags") or []
        tags = tags.split(",") if isinstance(tags, str) else tags if isinstance(tags, list) else []
        d["ai"]["tags"] = [s for t in tags if t is not None and (s := str(t).strip())]
    return d


def all_media(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM media ORDER BY taken_at IS NULL, taken_at, path")
    return [row_to_dict(r) for r in rows]


def get_media(conn: sqlite3.Connection, media_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
    return row_to_dict(row) if row else None


def write_json(path: Path, data, private: bool = False) -> None:
    """Write a temp file, then swap it in: a crash mid-write must not leave an empty file that breaks every page.

    private: only the owner may read it (settings.json can hold the API key).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    if private:  # before the content goes in, so the key is never readable by others
        tmp.touch(exist_ok=True)
        tmp.chmod(0o600)
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def load_projects() -> dict[str, str]:
    """Holiday projects: name -> photo folder."""
    f = cache_root() / "projects.json"
    return json.loads(f.read_text()) if f.exists() else {}


_PARALLEL = str(default_concurrency())  # AI_CONCURRENCY from .env, kept as a choice even if not 1/2/4/8
_THINKING = "on" if thinking() else "off"  # AI_THINKING from .env until chosen here

# App settings: key -> (label, choices, default, help). Add a line here and it shows up on the settings page.
# choices None: any text; "" keeps the .env value (the model names are offered from the server's list).
SETTINGS = {
    "theme": ("Theme", ("auto", "light", "dark", "grey"), "auto",
              "Light or dark colours; auto follows your system. Grey is a neutral mid-grey that doesn't tint how photos look."),
    "accent": ("Accent colour", ("blue", "indigo", "purple", "magenta", "pink", "red", "orange",
                                    "amber", "olive", "green", "teal", "cyan", "slate"), "blue",
               "Colour of the top bar, buttons, selections and highlights."),
    "base_url": ("Server address", None, "",
                 "OpenAI-compatible endpoint, e.g. http://localhost:11434/v1 (Ollama) or https://api.openai.com/v1. "
                 "Used by both models. Empty: OPENAI_BASE_URL from .env."),
    "api_key": ("API key", None, "",
                "Key for the server; servers without authentication (Ollama, LM Studio) need none. Saved in plain "
                "text in settings.json in the data folder, like .env. Empty: OPENAI_API_KEY from .env."),
    "analysis_model": ("Analysis model", None, "",
                       "The model that describes each photo and compares shots of one scene. It must see images "
                       "(vision) and runs once per photo, so a small, fast one pays off. Default: ANALYSIS_MODEL "
                       "or MODEL from .env."),
    "slideshow_model": ("Slideshow model", None, "",
                        "The model that chooses and refines the slideshow from all photo descriptions in one "
                        "request. It needs no vision but a large context and good reasoning, so a bigger model "
                        "helps. Default: SLIDESHOW_MODEL or MODEL from .env."),
    "thinking": ("Thinking", ("on", "off"), _THINKING,
                 "Off asks thinking models (Qwen3.5, GLM, ...) to answer without reasoning first – much faster per "
                 "photo; harmless for other models. Applies to both models. Default: AI_THINKING from .env."),
    "parallel": ("Parallel AI requests", tuple(sorted({"1", "2", "4", "8", _PARALLEL}, key=int)), _PARALLEL,
                 "How many photos are sent to the AI at once. More is faster if the server keeps up; "
                 "a local model on your own computer often works best with 1."),
    "review_duplicates": ("Review duplicates before AI", ("off", "on"), "on",
                          "On: after scanning, Analyse pauses and shows each new group of same-scene shots, so you can "
                          "pick the best one before it is sent to the AI – or let the AI compare them and overrule it if "
                          "you like. Off: the technically best shot is used."),
    "desc_size": ("Photo details for choosing", ("full", "short", "minimal", "auto"), "full",
                  "How much the AI reads about each photo when choosing a slideshow. Full: caption, tags, scores "
                  "(~70 tokens per photo). Short: caption cut to 8 words, 5 tags (~35). Minimal: scene, "
                  "3 tags, scores, no caption (~20) – for small models with little context. Auto: the most "
                  "that fits the model's context, if the server reports it (else Full)."),
    "play_captions": ("Captions in Play", ("off", "on"), "off",
                      "On: ▶ Play shows the date and the AI caption under every photo, e.g. when showing the "
                      "trip to others. Also switched with Captions in the player's control panel."),
    "ken_burns": ("Slow zoom in Play", ("off", "on"), "off",
                  "On: ▶ Play slowly zooms into each photo (Ken Burns effect), from a different corner each time; "
                  "videos play as they are. Also switched with Zoom in the player's control panel."),
}

# Blocks on the settings page, in this order; every setting is in exactly one.
GROUPS = {
    "Appearance": ("theme", "accent"),
    "AI server and models": ("base_url", "api_key", "analysis_model", "slideshow_model", "thinking", "parallel"),
    "Analysis": ("review_duplicates",),
    "Slideshow": ("desc_size", "play_captions", "ken_burns"),
}
assert sorted(k for keys in GROUPS.values() for k in keys) == sorted(SETTINGS)


def load_settings() -> dict[str, str]:
    f = cache_root() / "settings.json"
    saved = json.loads(f.read_text()) if f.exists() else {}
    return {k: saved[k] if valid_setting(k, saved.get(k)) else default for k, (_, _, default, _) in SETTINGS.items()}


def valid_setting(key: str, value) -> bool:
    choices = SETTINGS[key][1]
    if key == "base_url" and value and not str(value).startswith(("http://", "https://")):
        return False
    return isinstance(value, str) and len(value) <= 200 if choices is None else value in choices


def save_setting(key: str, value: str) -> None:
    write_json(cache_root() / "settings.json", load_settings() | {key: value}, private=True)


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
    stats = {"exists": folder.is_dir(), "files": 0, "described": 0, "picks": 0, "analysed_at": None, "cover": None}
    if (d / "analysed_at").exists():
        stats["analysed_at"] = datetime.fromisoformat((d / "analysed_at").read_text().strip())
    if (d / "photos2show.db").exists():
        conn = sqlite3.connect(d / "photos2show.db")
        stats["files"], stats["described"] = conn.execute("SELECT COUNT(*), COUNT(ai) FROM media").fetchone()
        # Cover: the photo the AI liked best (aesthetic + interest), else the technically best one.
        cover = conn.execute("SELECT thumb FROM media WHERE kind = 'image' ORDER BY "
                             "json_extract(ai, '$.aesthetic') + json_extract(ai, '$.interest') DESC NULLS LAST, "
                             "score DESC LIMIT 1").fetchone()
        stats["cover"] = cover and d / "thumbs" / cover[0]
        conn.close()
    if (d / "selection.json").exists():
        stats["picks"] = len(json.loads((d / "selection.json").read_text())["picks"])
    return stats
