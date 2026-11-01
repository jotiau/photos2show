import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    thinking: bool = True


def load_settings() -> Settings:
    load_dotenv()
    # expandvars: python-dotenv only expands ${VAR}; a plain $VAR stayed literal ("Connection error" x 857)
    base_url = os.path.expandvars(os.environ.get("OPENAI_BASE_URL", ""))
    if not base_url.startswith(("http://", "https://")):
        raise SystemExit(f"OPENAI_BASE_URL is not a URL ({base_url!r}) – check .env and the variables it "
                         "references (e.g. ${BLUM_BASE_URL}; PyCharm does not load ~/.bashrc).")
    return Settings(
        base_url=base_url,
        api_key=os.path.expandvars(os.environ.get("OPENAI_API_KEY", "")) or "none",  # empty: server without auth
        model=os.environ.get("MODEL", "glm-5.3-flash"),
        thinking=thinking(),
    )


def thinking() -> bool:
    """AI_THINKING: off asks thinking models (Qwen3.5, GLM, ...) to answer without reasoning first."""
    value = os.environ.get("AI_THINKING", "on").strip().lower()
    if value not in ("on", "off"):  # a typo would otherwise silently keep the slow reasoning
        raise SystemExit(f"AI_THINKING must be on or off, not {value!r} – check .env.")
    return value == "on"


def default_concurrency() -> int:
    """Parallel LLM requests unless chosen in the UI/CLI."""
    load_dotenv()
    value = os.environ.get("AI_CONCURRENCY", "2").strip()
    if not value.isdigit() or int(value) < 1:  # 0 or "abc" would break every AI run or the start
        raise SystemExit(f"AI_CONCURRENCY must be a whole number of 1 or more, not {value!r} – check .env.")
    return int(value)


def cache_root() -> Path:
    """Where projects, per-library databases and thumbnails live (never inside the photo folder).

    A data dir, not ~/.cache: the AI results cost ~30-70 min per holiday to recreate.
    """
    load_dotenv()
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    default = data / "photos2show"
    if not default.exists() and (data / "imgsort").exists():
        default = data / "imgsort"  # data from before the rename to photos2show stays in use
    return Path(env("CACHE", default))


def env(name: str, default):
    """PHOTOS2SHOW_<name>, or IMGSORT_<name> as set up before the rename."""
    return os.environ.get(f"PHOTOS2SHOW_{name}", os.environ.get(f"IMGSORT_{name}", default))
