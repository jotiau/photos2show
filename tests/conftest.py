import pytest


@pytest.fixture
def fake_ai(monkeypatch):
    """Replace the LLM call; returns the list of image sizes it was called with."""
    monkeypatch.setenv("OPENAI_BASE_URL", "http://fake.invalid/v1")
    calls = []

    def analyse_image(client, settings, img):
        calls.append(img.size)
        return {"caption": "fake caption", "tags": ["beach", "sunset"], "scene": "landscape",
                "people_count": 0, "aesthetic": 7, "interest": 6, "issues": []}

    monkeypatch.setattr("imgsort.ai.analyse_image", analyse_image)
    return calls
