import time
import weakref
from pathlib import Path

import pytest

from imgsort import db, testdata
from imgsort.pipeline import analyse_folder, describe_folder, skip_ai_reason


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    folder = testdata.generate(tmp_path_factory.mktemp("photos"))
    mp = pytest.MonkeyPatch()
    mp.setenv("IMGSORT_CACHE", str(tmp_path_factory.mktemp("cache")))
    summary = analyse_folder(folder)
    yield folder, summary
    mp.undo()


def items_by_name(folder):
    return {Path(it["path"]).name: it for it in db.all_media(db.connect(folder))}


def test_all_files_analysed(library):
    folder, summary = library
    assert summary == {"total": len(testdata.EXPECTED), "analysed": len(testdata.EXPECTED), "errors": []}


@pytest.mark.parametrize("name", testdata.EXPECTED)
def test_expected_flags(library, name):
    item = items_by_name(library[0])[name]
    assert sorted(item["flags"]) == sorted(testdata.EXPECTED[name]), item["metrics"]


def test_burst_keeps_sharpest_and_skips_ai_for_rest(library):
    items = items_by_name(library[0])
    best = items["burst_1.jpg"]
    assert best["duplicate_of"] is None and skip_ai_reason(best) is None
    for name in ("burst_2.jpg", "burst_3.jpg"):
        assert items[name]["duplicate_of"] == best["id"]
        assert skip_ai_reason(items[name]) == f"same scene as #{best['id']}"


def test_very_blurry_skips_ai(library):
    assert skip_ai_reason(items_by_name(library[0])["very_blurry.jpg"]) == "very blurry"


def test_different_scenes_are_not_duplicates(library):
    items = items_by_name(library[0])
    assert all(items[f"scene_{i}.jpg"]["duplicate_of"] is None for i in (1, 2, 3))


def test_video_frames_and_exif_dates(library):
    folder = library[0]
    items = items_by_name(folder)
    video = items["video.mp4"]
    assert video["kind"] == "video" and video["frames"] == 3 and (video["width"], video["height"]) == (640, 480)
    assert items["scene_1.jpg"]["taken_at"] == "2026-08-11T10:00:00"
    thumbs = db.library_dir(folder) / "thumbs"
    assert all((thumbs / it["thumb"]).exists() for it in items.values())


def test_describe_sends_only_images_worth_it(library, fake_ai):
    folder = library[0]
    expected = sorted(n for n, it in items_by_name(folder).items() if skip_ai_reason(it) is None)
    assert "burst_2.jpg" not in expected and "very_blurry.jpg" not in expected and "video.mp4" in expected
    assert describe_folder(folder) == {"described": len(expected), "errors": []}
    assert len(fake_ai) == len(expected)
    assert sorted(n for n, it in items_by_name(folder).items() if it["ai"]) == expected
    assert describe_folder(folder)["described"] == 0  # cached, nothing re-sent


def test_describe_failure_is_reported_and_retried(library, fake_ai, monkeypatch):
    folder = library[0]
    conn = db.connect(folder)
    conn.execute("UPDATE media SET ai = NULL WHERE path LIKE '%scene_1.jpg'")
    conn.commit()
    monkeypatch.setattr("imgsort.ai.analyse_image", lambda *a: 1 / 0)
    assert describe_folder(folder) == {"described": 0, "errors": ["scene_1.jpg: division by zero"]}
    assert items_by_name(folder)["scene_1.jpg"]["ai"] is None
    monkeypatch.setattr("imgsort.ai.analyse_image", lambda *a: {"caption": "retry"})
    assert describe_folder(folder)["described"] == 1
    assert items_by_name(folder)["scene_1.jpg"]["ai"] == {"caption": "retry"}


def test_failed_requests_do_not_keep_images_in_memory(library, fake_ai, monkeypatch):
    folder = library[0]
    conn = db.connect(folder)
    conn.execute("UPDATE media SET ai = NULL")
    conn.commit()
    refs = []

    def failing(client, settings, img):
        refs.append(weakref.ref(img))
        raise ConnectionError("server down")

    monkeypatch.setattr("imgsort.ai.analyse_image", failing)
    alive = []

    def progress(done, total):
        alive.append(sum(r() is not None for r in refs))
        time.sleep(0.05)  # slow main loop (as on the Windows CI runner): failed futures queue up meanwhile

    result = describe_folder(folder, progress, 2)
    assert len(result["errors"]) == len(refs) > 4
    assert max(alive) <= 2  # only images still in flight, not every failed one (was: all of them, 20 GB OOM)
    describe_folder(folder)  # restore descriptions for the following tests


def test_relative_path_keeps_results_and_missing_folder_is_refused(library, monkeypatch):
    folder = library[0]
    monkeypatch.chdir(folder.parent)
    assert analyse_folder(Path(folder.name))["analysed"] == 0  # same rows as the absolute path
    with pytest.raises(FileNotFoundError):
        analyse_folder(folder / "unplugged")
    assert items_by_name(folder)  # nothing deleted


def test_rescan_uses_cache_and_drops_removed_files(library):
    folder = library[0]
    (folder / "grey_sky.jpg").unlink()
    summary = analyse_folder(folder)
    assert summary["analysed"] == 0 and summary["total"] == len(testdata.EXPECTED) - 1
    assert "grey_sky.jpg" not in items_by_name(folder)
