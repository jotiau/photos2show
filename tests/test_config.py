import pytest

from imgsort.config import load_settings


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
