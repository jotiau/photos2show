import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from photos2show import db, slideshow, testdata
from photos2show.pipeline import analyse_folder, describe_folder


@pytest.fixture
def described(tmp_path, monkeypatch, fake_ai):
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "cache"))
    folder = testdata.generate(tmp_path / "photos")
    analyse_folder(folder)
    describe_folder(folder)
    return folder, {Path(it["path"]).name: it["id"] for it in db.all_media(db.connect(folder))}


def test_select_keeps_only_valid_picks(described, monkeypatch):
    folder, ids = described
    answer = {"picks": [{"id": ids["scene_2.jpg"], "reason": "nice"}, {"id": str(ids["scene_1.jpg"])},
                        {"id": ids["scene_2.jpg"]}, {"id": 9999}, {"id": ids["burst_2.jpg"]}, {"no": "id"}, "junk"]}
    prompts = []
    monkeypatch.setattr("photos2show.ai.complete_json", lambda c, s, messages: prompts.append(messages) or answer)
    picks = slideshow.select(db.connect(folder), "short show")
    assert picks == [{"id": ids["scene_2.jpg"], "reason": "nice"}, {"id": ids["scene_1.jpg"], "reason": ""}]
    prompt = prompts[0][0]["content"]
    assert "short show" in prompt and "fake caption" in prompt and f"\n{ids['burst_2.jpg']} |" not in prompt
    answer = {"picks": [ids["scene_1.jpg"], ids["scene_2.jpg"]]}  # bare ids, as small models answer
    assert [p["id"] for p in slideshow.select(db.connect(folder), "x")] == [ids["scene_1.jpg"], ids["scene_2.jpg"]]
    answer = {"photos": [1, 2]}  # nothing usable: an error, not an empty slideshow
    with pytest.raises(ValueError, match="no usable picks"):
        slideshow.select(db.connect(folder), "x")


def test_description_size_shortens_the_candidate_lines(described, monkeypatch):
    folder, _ = described
    prompts = []
    monkeypatch.setattr("photos2show.ai.complete_json", lambda c, s, m: prompts.append(m[0]["content"]) or {"picks": []})
    monkeypatch.setattr("photos2show.ai.context_length", lambda c, s: None)
    for size in ("full", "short", "minimal", "auto"):
        db.save_setting("desc_size", size)
        with pytest.raises(ValueError):  # no picks: only the prompt matters here
            slideshow.select(db.connect(folder), "x")
    full, short, minimal, auto = prompts
    assert "fake caption" in full and "fake caption" in short and "fake caption" not in minimal
    assert "| caption |" not in minimal and len(minimal) < len(short) <= len(full)
    assert auto == full  # the server does not report its context size


def test_slideshow_uses_the_slideshow_model(described, monkeypatch):
    folder, _ = described
    used = []
    monkeypatch.setattr("photos2show.ai.complete_json", lambda c, s, m: used.append(s.model) or {"picks": []})
    db.save_setting("slideshow_model", "big-text-model")
    with pytest.raises(ValueError):
        slideshow.select(db.connect(folder), "x")
    assert used == ["big-text-model"]


def test_auto_size_picks_the_most_that_fits():
    item = {"id": 1, "taken_at": "2026-07-14T10:00", "kind": "image", "flags": [],
            "ai": {"scene": "landscape", "tags": [f"tag{i}" for i in range(10)], "aesthetic": 7, "interest": 6,
                   "caption": "A long caption about a sunny beach with people walking along the waves at dusk"}}
    items = [item] * 100
    chars = {s: len(slideshow._candidates(items, s)["lines"]) for s in slideshow.SIZES}
    tokens = lambda s: chars[s] / slideshow.CHARS_PER_TOKEN * 2  # context that just fits
    assert slideshow.fitting_size(items, None) == "full"
    assert slideshow.fitting_size(items, int(tokens("full")) + 1) == "full"
    assert slideshow.fitting_size(items, int(tokens("short")) + 1) == "short"
    assert slideshow.fitting_size(items, int(tokens("minimal")) + 1) == "minimal"
    assert slideshow.fitting_size(items, 10) == "minimal"


def test_rescan_forgets_picks_of_deleted_files(described):
    folder, ids = described
    top = max(ids.values())
    slideshow.save(folder, {"description": "d", "picks": [{"id": ids["scene_1.jpg"], "reason": ""},
                                                           {"id": top, "reason": ""}]})
    conn = db.connect(folder)
    Path(db.get_media(conn, top)["path"]).unlink()
    analyse_folder(folder)
    (folder / "zz_new.jpg").write_bytes(Path(db.get_media(conn, ids["scene_2.jpg"])["path"]).read_bytes())
    analyse_folder(folder)
    assert {Path(it["path"]).name: it["id"] for it in db.all_media(conn)}["zz_new.jpg"] == top  # id reused
    assert slideshow.load(folder)["picks"] == [{"id": ids["scene_1.jpg"], "reason": ""}]


def test_edit():
    picks = [{"id": 1, "reason": ""}, {"id": 2, "reason": ""}]
    ids = lambda ps: [p["id"] for p in ps]  # noqa: E731
    assert ids(slideshow.edit(picks, "remove", 1)) == [2]
    assert ids(slideshow.edit(picks, "remove", 9)) == [1, 2]
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


def test_swap():
    picks = [{"id": 1, "reason": "a"}, {"id": 2, "reason": "b"}, {"id": 3, "reason": "c"}]
    assert slideshow.swap(picks, 2, 9) == [{"id": 1, "reason": "a"}, {"id": 9, "reason": "b"}, {"id": 3, "reason": "c"}]
    assert slideshow.swap(picks, 2, 3) == picks  # already a pick: no double
    assert picks[1]["id"] == 2  # input untouched


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


def test_export_develops_raw_to_jpeg(tmp_path, monkeypatch):
    from datetime import datetime

    from PIL import Image
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "cache"))
    folder = tmp_path / "photos"
    folder.mkdir()
    testdata.save_dng(testdata.scene(1), folder / "DSC_0001.NEF", datetime(2026, 7, 14, 9, 30))
    analyse_folder(folder)
    [item] = db.all_media(db.connect(folder))
    slideshow.save(folder, {"description": "d", "picks": [{"id": item["id"], "reason": ""}]})
    assert slideshow.export(folder, tmp_path / "show") == 1
    with Image.open(tmp_path / "show" / "001_2026-07-14_photo.jpg") as img:
        assert img.format == "JPEG" and img.size == testdata.scene(1).size


def test_refine_sends_current_picks_and_keeps_hand_added_ones(described, monkeypatch):
    folder, ids = described
    current = [{"id": ids["scene_1.jpg"], "reason": "great view"}, {"id": ids["burst_2.jpg"], "reason": "added by hand"}]
    answer = {"picks": [{"id": ids["burst_2.jpg"]}, {"id": ids["scene_2.jpg"], "reason": "more beach"}]}
    prompts = []
    monkeypatch.setattr("photos2show.ai.complete_json", lambda c, s, messages: prompts.append(messages) or answer)
    picks = slideshow.select(db.connect(folder), "short show", current, "more beach, start with the burst")
    # burst_2 is a duplicate, valid only because it is already in the show; its old reason is kept
    assert picks == [{"id": ids["burst_2.jpg"], "reason": "added by hand"},
                     {"id": ids["scene_2.jpg"], "reason": "more beach"}]
    prompt = prompts[0][0]["content"]
    assert f"\n{ids['scene_1.jpg']} | great view" in prompt and "more beach, start with the burst" in prompt
    assert f"\n{ids['burst_2.jpg']} |" in prompt and prompt.endswith('"short reason"}]}')


def test_undo_is_one_step_and_forgets_deleted_files(described):
    folder, ids = described
    a, b = {"id": ids["scene_1.jpg"], "reason": ""}, {"id": ids["scene_2.jpg"], "reason": ""}
    assert not slideshow.can_undo(folder)
    slideshow.change(folder, {"description": "d", "picks": [a, b]})
    slideshow.change(folder, {"description": "d", "picks": [b]})
    slideshow.change(folder, {"description": "d", "picks": [b]})  # no change: keeps the undo step
    slideshow.save(folder, {**slideshow.load(folder), "exported_to": "/stick/show"})
    slideshow.undo(folder)
    assert slideshow.load(folder) == {"name": "Slideshow", "description": "d", "picks": [a, b],
                                      "exported_to": "/stick/show"}
    assert not slideshow.can_undo(folder)
    slideshow.undo(folder)  # nothing to undo: no error, no change
    assert slideshow.load(folder)["picks"] == [a, b]

    slideshow.change(folder, {"description": "d", "picks": [b]})
    slideshow.forget(folder, {a["id"]})
    slideshow.undo(folder)
    assert slideshow.load(folder)["picks"] == [b]  # the undo copy forgot the deleted file too


def test_export_again_replaces_only_an_earlier_export(described, tmp_path):
    folder, ids = described
    show = tmp_path / "show"
    slideshow.save(folder, {"description": "d", "picks": [{"id": ids["scene_1.jpg"], "reason": ""},
                                                           {"id": ids["scene_2.jpg"], "reason": ""}]})
    assert slideshow.export(folder, show) == 2
    assert slideshow.load(folder)["exported_to"] == str(show)
    assert "exported_to" not in (show / "selection.json").read_text()
    slideshow.save(folder, {**slideshow.load(folder), "picks": [{"id": ids["scene_2.jpg"], "reason": ""}]})
    with pytest.raises(ValueError, match="replace previous export"):
        slideshow.export(folder, show)
    assert slideshow.export(folder, show, replace=True) == 1
    assert sorted(p.name for p in show.iterdir()) == ["001_2026-08-11_beach-sunset.jpg", "selection.json"]

    (show / "my notes.txt").write_text("keep me")
    with pytest.raises(ValueError, match="not from an export"):
        slideshow.export(folder, show, replace=True)
    other = tmp_path / "holiday"
    other.mkdir()
    (other / "001_mine.jpg").write_bytes(b"x")  # numbered, but no selection.json: not ours
    with pytest.raises(ValueError, match="not from an export"):
        slideshow.export(folder, other, replace=True)
    assert (show / "my notes.txt").exists() and (other / "001_mine.jpg").exists()


def test_export_that_failed_halfway_can_be_replaced(described, tmp_path, monkeypatch):
    folder, ids = described
    show = tmp_path / "show"
    slideshow.save(folder, {"description": "d", "picks": [{"id": ids["scene_1.jpg"], "reason": ""},
                                                           {"id": ids["scene_2.jpg"], "reason": ""}]})
    copy = slideshow.shutil.copy2
    calls = []

    def full_stick(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("No space left on device")
        return copy(src, dst)

    monkeypatch.setattr(slideshow.shutil, "copy2", full_stick)
    with pytest.raises(OSError):
        slideshow.export(folder, show)
    monkeypatch.setattr(slideshow.shutil, "copy2", copy)
    assert slideshow.export(folder, show, replace=True) == 2


def test_videos_count_with_their_length(described):
    folder, ids = described
    conn = db.connect(folder)
    video, photo = db.get_media(conn, ids["video.mp4"]), db.get_media(conn, ids["scene_1.jpg"])
    assert slideshow.on_screen(video, 5) == pytest.approx(3.0) and slideshow.on_screen(photo, 8) == 8  # 3 s clip
    assert " | video 3s | " in slideshow._line(video)  # the AI sees the length too
    assert slideshow.on_screen({**video, "path": str(folder / "gone.mp4")}, 7) == 7  # unreadable: like a photo


def test_parallel_edits_are_not_lost(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "cache"))
    add = lambda i: slideshow.update(tmp_path, lambda s: {**s, "picks": slideshow.edit(s["picks"], "add", i)})  # noqa: E731
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(add, range(40)))
    assert sorted(p["id"] for p in slideshow.load(tmp_path)["picks"]) == list(range(40))


def test_several_shows_per_project(described):
    folder, ids = described
    a, b = {"id": ids["scene_1.jpg"], "reason": ""}, {"id": ids["scene_2.jpg"], "reason": ""}
    lib = db.library_dir(folder)
    (lib / "selection.json").write_text(json.dumps({"description": "old", "picks": [a], "exported_to": "/x"}))
    assert slideshow.shows(folder) == [(1, "Slideshow")] and slideshow.load(folder)["picks"] == [a]  # moved in
    assert not (lib / "selection.json").exists()
    slideshow.create(folder, "Parents", copy=True)
    assert slideshow.current(folder) == 2 and slideshow.load(folder) == {"name": "Parents", "description": "old",
                                                                         "picks": [a]}  # export folder not copied
    slideshow.change(folder, {**slideshow.load(folder), "picks": [a, b]})
    slideshow.create(folder, "Friends")
    assert slideshow.load(folder)["picks"] == [] and not slideshow.can_undo(folder)  # undo is per show
    slideshow.switch(folder, 2)
    slideshow.switch(folder, 99)  # unknown: stays
    slideshow.rename(folder, "Mum and Dad")
    slideshow.undo(folder)  # the picks go back, the new name stays
    assert slideshow.load(folder) == {"name": "Mum and Dad", "description": "old", "picks": [a]}
    assert slideshow.folder_name("Mum & Dad / 2026") == "mum-dad-2026" and slideshow.folder_name("!") == "slideshow"

    slideshow.forget(folder, {a["id"]})  # also what Never pick does: out of every show and undo copy
    assert [slideshow.load(folder, n)["picks"] for n in slideshow.ids(folder)] == [[], [], []]
    slideshow.update(folder, lambda s: {**s, "picks": [b]}, 1)  # a slow request that started on show 1
    assert slideshow.load(folder, 1)["picks"] == [b] and slideshow.load(folder)["picks"] == []  # 2 is open
    slideshow.delete(folder, 3)  # not the open one: 2 stays open
    assert slideshow.shows(folder) == [(1, "Slideshow"), (2, "Mum and Dad")] and slideshow.current(folder) == 2
    assert db.project_stats(folder)["shows"] == 2
    slideshow.delete(folder, 2)
    assert slideshow.current(folder) == 1
    with pytest.raises(ValueError, match="already gone"):
        slideshow.delete(folder, 2)
    with pytest.raises(ValueError, match="last slideshow"):
        slideshow.delete(folder, 1)
    (lib / "selection.json").write_text(json.dumps({"description": "older version", "picks": []}))
    assert slideshow.load(folder)["picks"] == [b]  # an older version run again does not replace show 1
    assert slideshow.shows(folder) == [(1, "Slideshow")]
    assert db.project_stats(folder)["shows"] == 1 and db.project_stats(folder)["picks"] == 1


def test_export_records_its_folder_in_the_show_it_started_on(described, tmp_path, monkeypatch):
    folder, ids = described
    slideshow.save(folder, {"description": "d", "picks": [{"id": ids["scene_1.jpg"], "reason": ""}]})
    slideshow.create(folder, "Other")
    slideshow.switch(folder, 1)
    copy = slideshow.shutil.copy2
    monkeypatch.setattr(slideshow.shutil, "copy2", lambda *a: (slideshow.switch(folder, 2), copy(*a)))  # meanwhile
    assert slideshow.export(folder, tmp_path / "show") == 1
    assert slideshow.load(folder, 1)["exported_to"] == str(tmp_path / "show")
    assert "exported_to" not in slideshow.load(folder, 2)
