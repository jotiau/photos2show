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


def test_move():
    picks = [{"id": i, "reason": ""} for i in (1, 2, 3, 4)]
    ids = lambda ps: [p["id"] for p in ps]  # noqa: E731
    assert ids(slideshow.move(picks, 4, 0)) == [4, 1, 2, 3]
    assert ids(slideshow.move(picks, 1, 2)) == [2, 3, 1, 4]
    assert ids(slideshow.move(picks, 2, 99)) == [1, 3, 4, 2]
    assert ids(slideshow.move(picks, 3, -5)) == [3, 1, 2, 4]
    assert ids(slideshow.move(picks, 9, 0)) == [1, 2, 3, 4]
    assert ids(picks) == [1, 2, 3, 4]  # input untouched


def test_sort():
    items = {1: {"taken_at": "2026-08-12", "ai": {"aesthetic": 5, "interest": "9"}, "score": 0.2, "path": "/x/b.jpg"},
             2: {"taken_at": "2026-08-10", "ai": {"aesthetic": 8}, "score": 0.9, "path": "/x/C.jpg"},
             3: {"taken_at": None, "ai": None, "score": None, "path": "/x/a.jpg"}}
    picks = [{"id": i, "reason": ""} for i in (3, 1, 2)]
    ids = lambda by: [p["id"] for p in slideshow.sort(picks, items, by)]  # noqa: E731
    assert ids("date") == [2, 1, 3]  # missing values last
    assert ids("date_desc") == [1, 2, 3]
    assert ids("aesthetic") == [2, 1, 3]
    assert ids("interest") == [1, 3, 2]
    assert ids("quality") == [2, 1, 3]
    assert ids("name") == [3, 1, 2]
    assert ids("reverse") == [2, 1, 3]


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
