import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    ai_concurrency: int


def load_settings() -> Settings:
    load_dotenv()
    base_url = os.environ.get("OPENAI_BASE_URL")
    if not base_url:
        raise SystemExit("OPENAI_BASE_URL is not set – copy .env.example to .env and fill it in.")
    return Settings(
        base_url=base_url,
        api_key=os.environ.get("OPENAI_API_KEY") or "none",  # empty is fine for servers without auth
        model=os.environ.get("MODEL", "glm-5.3-flash"),
        ai_concurrency=default_concurrency(),
    )


def default_concurrency() -> int:
    """Parallel LLM requests unless chosen in the UI/CLI."""
    load_dotenv()
    return int(os.environ.get("AI_CONCURRENCY", "2"))


def cache_root() -> Path:
    """Where projects, per-library databases and thumbnails live (never inside the photo folder).

    A data dir, not ~/.cache: the AI results cost ~30-70 min per holiday to recreate.
    """
    load_dotenv()
    default = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "imgsort"
    return Path(os.environ.get("IMGSORT_CACHE", default))
