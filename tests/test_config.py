import pytest

from photos2show.config import cache_root, default_concurrency, load_settings


def test_env_references_are_expanded_and_bad_urls_refused(monkeypatch):
    monkeypatch.setenv("BLUM_BASE_URL", "https://glm.example/v1")
    monkeypatch.setenv("BLUM_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "$BLUM_BASE_URL")  # plain $VAR, not expanded by python-dotenv
    monkeypatch.setenv("OPENAI_API_KEY", "$BLUM_API_KEY")
    s = load_settings()
    assert (s.base_url, s.api_key) == ("https://glm.example/v1", "secret")

    monkeypatch.delenv("BLUM_BASE_URL")  # e.g. started from PyCharm without ~/.bashrc
    with pytest.raises(SystemExit, match="not a URL"):
        load_settings()


def test_data_folder_and_settings_from_before_the_rename_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr("photos2show.config.load_dotenv", lambda: None)  # ignore a developer's .env
    for name in ("PHOTOS2SHOW_CACHE", "IMGSORT_CACHE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert cache_root() == tmp_path / "photos2show"
    (tmp_path / "imgsort").mkdir()
    assert cache_root() == tmp_path / "imgsort"
    (tmp_path / "photos2show").mkdir()
    assert cache_root() == tmp_path / "photos2show"
    monkeypatch.setenv("IMGSORT_CACHE", str(tmp_path / "old"))
    assert cache_root() == tmp_path / "old"
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "new"))
    assert cache_root() == tmp_path / "new"


@pytest.mark.parametrize("value", ["0", "abc", "-1", ""])
def test_bad_ai_concurrency_is_refused_clearly(monkeypatch, value):
    monkeypatch.setattr("photos2show.config.load_dotenv", lambda: None)
    monkeypatch.setenv("AI_CONCURRENCY", value)
    with pytest.raises(SystemExit, match="AI_CONCURRENCY"):
        default_concurrency()
    monkeypatch.setenv("AI_CONCURRENCY", " 4 ")
    assert default_concurrency() == 4
