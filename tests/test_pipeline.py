from pathlib import Path

import pytest

from imgsort import db, testdata
from imgsort.pipeline import analyse_folder, skip_ai_reason


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


def test_rescan_uses_cache_and_drops_removed_files(library):
    folder = library[0]
    (folder / "grey_sky.jpg").unlink()
    summary = analyse_folder(folder)
    assert summary["analysed"] == 0 and summary["total"] == len(testdata.EXPECTED) - 1
    assert "grey_sky.jpg" not in items_by_name(folder)
