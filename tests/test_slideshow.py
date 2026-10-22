from pathlib import Path

import pytest

from imgsort import db, slideshow, testdata
from imgsort.pipeline import analyse_folder, describe_folder


@pytest.fixture
def described(tmp_path, monkeypatch, fake_ai):
    monkeypatch.setenv("IMGSORT_CACHE", str(tmp_path / "cache"))
    folder = testdata.generate(tmp_path / "photos")
    analyse_folder(folder)
    describe_folder(folder)
    return folder, {Path(it["path"]).name: it["id"] for it in db.all_media(db.connect(folder))}


def test_select_keeps_only_valid_picks(described, monkeypatch):
    folder, ids = described
    answer = {"picks": [{"id": ids["scene_2.jpg"], "reason": "nice"}, {"id": str(ids["scene_1.jpg"])},
                        {"id": ids["scene_2.jpg"]}, {"id": 9999}, {"id": ids["burst_2.jpg"]}, {"no": "id"}, "junk"]}
    prompts = []
    monkeypatch.setattr("imgsort.ai.complete_json", lambda c, s, messages: prompts.append(messages) or answer)
    picks = slideshow.select(db.connect(folder), "short show")
    assert picks == [{"id": ids["scene_2.jpg"], "reason": "nice"}, {"id": ids["scene_1.jpg"], "reason": ""}]
    prompt = prompts[0][0]["content"]
    assert "short show" in prompt and "fake caption" in prompt and f"\n{ids['burst_2.jpg']} |" not in prompt


def test_edit():
    picks = [{"id": 1, "reason": ""}, {"id": 2, "reason": ""}]
    ids = lambda ps: [p["id"] for p in ps]  # noqa: E731
    assert ids(slideshow.edit(picks, "up", 2)) == [2, 1]
    assert ids(slideshow.edit(picks, "up", 1)) == [1, 2]
    assert ids(slideshow.edit(picks, "down", 1)) == [2, 1]
    assert ids(slideshow.edit(picks, "remove", 1)) == [2]
    assert ids(slideshow.edit(picks, "add", 3)) == [1, 2, 3]
    assert ids(slideshow.edit(picks, "add", 1)) == [1, 2]
    assert ids(picks) == [1, 2]  # input untouched


def test_export(described, tmp_path):
    folder, ids = described
    slideshow.save(folder, {"description": "d", "picks": [{"id": ids["video.mp4"], "reason": ""},
                                                           {"id": ids["scene_1.jpg"], "reason": ""}]})
    with pytest.raises(ValueError, match="outside"):
        slideshow.export(folder, folder / "show")
    assert slideshow.export(folder, tmp_path / "show") == 2
    names = sorted(p.name for p in (tmp_path / "show").iterdir())
    assert names[0].startswith("001_") and names[0].endswith("_beach-sunset.mp4")
    assert names[1] == "002_2026-08-11_beach-sunset.jpg" and names[2] == "selection.json"
    with pytest.raises(ValueError, match="not empty"):
        slideshow.export(folder, tmp_path / "show")
