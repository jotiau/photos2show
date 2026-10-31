import time
from pathlib import Path

from fastapi.testclient import TestClient

from photos2show import testdata
from photos2show.web import app as web

LOCAL = "http://127.0.0.1"


def test_project_analyse_browse_and_slideshow(tmp_path, monkeypatch, fake_ai):
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "cache"))
    folder = testdata.generate(tmp_path / "photos")
    client = TestClient(web.app, base_url=LOCAL, headers={"HX-Request": "true"})
    assert TestClient(web.app, base_url=LOCAL).post("/analyse", data={"parallel": "1"}).status_code == 403  # CSRF guard
    assert TestClient(web.app, base_url="http://rebound.example").get("/").status_code == 400  # DNS rebinding

    assert "Create a project" in client.get("/").text
    assert "Not a folder" in client.post("/projects", data={"name": "x", "folder": str(tmp_path / "nope")}).text

    def wait():
        for _ in range(100):
            resp = client.get("/progress")
            if 'hx-get="/progress"' not in resp.text:  # stopped polling = finished
                return resp
            time.sleep(0.1)

    assert "Create &amp; analyse" in client.get("/projects").text
    resp = client.post("/projects", data={"name": "Norway 2026", "folder": str(folder)})
    assert resp.headers["HX-Redirect"] == "/"  # to the library, where the progress shows
    resp = wait()
    assert f"{len(testdata.EXPECTED)} files" in resp.text and "9 described by AI" in resp.text
    assert "Norway 2026" in client.get("/").text
    assert "already exists" in client.post("/projects", data={"name": "Norway 2026", "folder": str(tmp_path)}).text
    assert web.db.load_projects()["Norway 2026"] == str(folder.resolve())
    assert client.post("/analyse", data={"parallel": "99"}).status_code == 422
    client.post("/analyse", data={"parallel": "1"})
    assert web.job["parallel"] == 1 and "0 described by AI" in wait().text  # all cached

    grid = client.get("/grid").text
    assert "burst_1.jpg" in grid and "▶ video" in grid
    skipped = client.get("/grid?flag=skips_ai").text
    assert "very_blurry.jpg" in skipped and "burst_2.jpg" in skipped and "scene_1.jpg" not in skipped

    items = {Path(it["path"]).name: it["id"] for it in web._items()}
    assert "fake caption" in client.get(f"/media/{items['video.mp4']}").text
    tagged = client.get("/grid?flag=tag:beach").text
    assert "burst_1.jpg" in tagged and "burst_2.jpg" not in tagged
    found = client.get("/grid?flag=all&q=FAKE scene_1").text  # all words, any case, caption + file name
    assert "scene_1.jpg" in found and "scene_2.jpg" not in found
    assert "scene_1.jpg" in client.get("/grid").text and "scene_2.jpg" not in client.get("/grid").text  # kept
    assert "No photos match" in client.get("/grid?q=penguin").text
    assert "scene_2.jpg" in client.get("/grid?q=").text  # cleared
    assert client.get(f"/thumb/{items['video.mp4']}?frame=2").headers["content-type"] == "image/jpeg"
    assert client.get(f"/original/{items['video.mp4']}").status_code == 200
    assert client.get("/media/9999").status_code == 404

    # Slideshow: GLM picks, edit by hand, export
    answer = {"picks": [{"id": items["scene_1.jpg"], "reason": "great view"}, {"id": items["scene_2.jpg"]}]}
    monkeypatch.setattr("photos2show.ai.complete_json", lambda c, s, m: answer)
    assert "Describe the slideshow" in client.get("/slideshow").text
    picks = client.post("/slideshow/select", data={"description": "short"}).text
    assert "great view" in picks and "2 photos" in picks
    assert "✓" in client.post(f"/slideshow/add/{items['video.mp4']}").text
    picks = client.post("/slideshow/move", data={"id": items["scene_2.jpg"], "to": 0}).text  # ← button
    assert picks.index("scene_2.jpg") < picks.index("scene_1.jpg") and "3 photos" in picks
    assert '"to": 2}' in picks  # → button of the 2nd tile
    picks = client.post("/slideshow/move", data={"id": items["video.mp4"], "to": 0}).text
    assert picks.index("video.mp4") < picks.index("scene_2.jpg") < picks.index("scene_1.jpg")
    assert "2 photos" in client.post(f"/slideshow/remove/{items['video.mp4']}").text
    picks = client.post("/slideshow/sort", data={"by": "name"}).text
    assert picks.index("scene_1.jpg") < picks.index("scene_2.jpg") and "Sort by" in picks
    assert client.post("/slideshow/sort", data={"by": "nope"}).status_code == 400
    picks = client.post("/slideshow/undo").text  # back to the order before sorting
    assert picks.index("scene_2.jpg") < picks.index("scene_1.jpg") and "Undo" not in picks

    refined = {"picks": [{"id": items["scene_1.jpg"]}, {"id": items["video.mp4"], "reason": "add the video"}]}
    monkeypatch.setattr("photos2show.ai.complete_json", lambda c, s, m: refined)
    picks = client.post("/slideshow/refine", data={"instruction": "add the video"}).text
    assert "add the video" in picks and "great view" in picks and "Undo" in picks  # old reason kept
    assert web.slideshow.load(folder)["refinements"] == ["add the video"]
    assert "Refine picks" in client.post("/slideshow/undo").text
    assert "Describe the slideshow" in client.get("/slideshow").text

    def disk_full(src, dst):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as m:
        m.setattr(web.slideshow.shutil, "copy2", disk_full)
        resp = client.post("/slideshow/export", data={"target": str(tmp_path / "stick")})
    assert resp.status_code == 200 and "No space left" in resp.text  # shown, not a silent 500
    assert "Exported 2 files" in client.post("/slideshow/export", data={"target": str(tmp_path / "show")}).text
    assert sorted(p.name for p in (tmp_path / "show").iterdir())[0].startswith("001_2026-08-11")
    assert str(tmp_path / "show") in client.get("/slideshow").text  # the last export is suggested again
    assert "replace previous export" in client.post("/slideshow/export", data={"target": str(tmp_path / "show")}).text
    resp = client.post("/slideshow/export", data={"target": str(tmp_path / "show"), "replace": "true"})
    assert "Exported 2 files" in resp.text and "Open folder" in resp.text
    opened = []
    monkeypatch.setattr(web.subprocess, "Popen", lambda cmd: opened.append(cmd))
    monkeypatch.setattr(web.sys, "platform", "linux")
    client.post("/slideshow/open-export")
    assert opened == [["xdg-open", str((tmp_path / "show").resolve())]]

    def no_xdg_open(cmd):
        raise FileNotFoundError(2, "No such file or directory", "xdg-open")

    monkeypatch.setattr(web.subprocess, "Popen", no_xdg_open)
    assert "Could not open the folder" in client.post("/slideshow/open-export").text


def test_stop_and_time_left(tmp_path, monkeypatch, fake_ai):
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "cache"))
    for key, value in {"running": False, "project": "", "folder": "", "summary": None}.items():
        monkeypatch.setitem(web.job, key, value)  # restored afterwards: job is shared by all tests
    client = TestClient(web.app, base_url=LOCAL, headers={"HX-Request": "true"})

    monkeypatch.setitem(web.job, "running", True)
    monkeypatch.setitem(web.job, "stopping", False)
    monkeypatch.setitem(web.job, "started", time.monotonic() - 120)  # 4 of 10 done in 2 min: 3 min left
    monkeypatch.setitem(web.job, "done", 4)
    monkeypatch.setitem(web.job, "total", 10)
    page = client.get("/progress").text
    assert "about 3 min left" in page and "Stop" in page
    monkeypatch.setitem(web.job, "done", 2)
    assert web.time_left() == ""  # too early to tell
    assert "Stopping" in client.post("/analyse/stop").text and web.stop.is_set()
    monkeypatch.setitem(web.job, "running", False)

    folder = testdata.generate(tmp_path / "photos")
    monkeypatch.setattr(web, "describe_folder", lambda *a: (_ for _ in ()).throw(AssertionError("AI after stop")))
    web.job.update(folder=str(folder), running=True)
    web._run(folder)  # stop is still set: the scan ends at once and the AI pass is skipped
    assert web.job["summary"]["stopped"] and "Stopped" in client.get("/progress").text
    web.stop.clear()


def test_pick_folder(tmp_path, monkeypatch):
    client = TestClient(web.app, base_url=LOCAL, headers={"HX-Request": "true"})
    answer, calls = str(tmp_path / "picked"), []

    def fake_run(cmd, **kwargs):
        calls.append(cmd[-1])
        return web.subprocess.CompletedProcess(cmd, 0, stdout=answer + "\n", stderr="")

    monkeypatch.setattr(web.shutil, "which", lambda name: None)  # Tk dialog, whatever is installed
    monkeypatch.setattr(web.subprocess, "run", fake_run)
    assert client.post("/pick-folder", data={"path": str(tmp_path / "new" / "deeper")}).text == answer
    assert calls == [str(tmp_path.resolve())]  # dialog starts at the nearest existing folder
    resp = client.post("/pick-folder", data={"path": str(tmp_path / "show"), "keep_name": "true"})
    assert resp.text == str(tmp_path / "picked" / "show")
    answer = ""  # cancelled
    assert client.post("/pick-folder", data={"path": ""}).text == ""
    assert TestClient(web.app, base_url=LOCAL).post("/pick-folder").status_code == 403  # CSRF guard

    def no_display(cmd, **kwargs):
        return web.subprocess.CompletedProcess(cmd, 1, stdout="", stderr="_tkinter.TclError: no display name")

    monkeypatch.setattr(web.subprocess, "run", no_display)
    resp = client.post("/pick-folder", data={"path": ""})
    assert resp.status_code == 500 and "no display name" in resp.text


def test_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "cache"))
    client = TestClient(web.app, base_url=LOCAL, headers={"HX-Request": "true"})
    assert 'data-theme="auto"' in client.get("/settings").text
    assert client.post("/settings", data={"key": "theme", "value": "pink"}).status_code == 400
    assert client.post("/settings", data={"key": "theme", "value": "dark"}).headers["HX-Refresh"] == "true"
    assert 'data-theme="dark"' in client.get("/slideshow").text


def test_cleanup_unused_data(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "cache"))
    monkeypatch.setitem(web.job, "running", False)
    client = TestClient(web.app, base_url=LOCAL, headers={"HX-Request": "true"})
    used, old = tmp_path / "used", tmp_path / "old"
    assert "None – all saved data" in client.get("/settings").text
    web.db.save_projects({"Used": str(used)})
    web.db.library_dir(used), web.db.library_dir(old)
    (web.db.library_dir(old) / "photos2show.db").write_bytes(b"x" * 2_000_000)
    other = tmp_path / "cache" / "notes-0123456789ab"  # looks like a cache name, but no thumbs/: not ours
    other.mkdir()

    page = client.get("/settings").text
    assert "1 folder of saved AI results and thumbnails (2.0 MB)" in page
    assert client.post("/settings/cleanup").headers["HX-Refresh"] == "true"
    assert not web.db.library_path(old).exists()
    assert web.db.library_path(used).exists() and other.exists() and (tmp_path / "cache" / "projects.json").exists()


def test_pick_folder_linux_uses_desktop_dialog(tmp_path, monkeypatch):
    client = TestClient(web.app, base_url=LOCAL, headers={"HX-Request": "true"})
    monkeypatch.setattr(web.sys, "platform", "linux")
    monkeypatch.setattr(web.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "zenity" else None)
    calls, code = [], 0

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return web.subprocess.CompletedProcess(cmd, code, stdout="" if code else f"{tmp_path}\n", stderr="")

    monkeypatch.setattr(web.subprocess, "run", fake_run)
    assert client.post("/pick-folder", data={"path": str(tmp_path)}).text == str(tmp_path)
    assert calls[0][0] == "zenity" and calls[0][-1] == f"--filename={tmp_path.resolve()}{web.os.sep}"
    code = 1  # zenity's cancel, not an error
    resp = client.post("/pick-folder", data={"path": str(tmp_path)})
    assert resp.status_code == 200 and resp.text == ""

    monkeypatch.setattr(web.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "kdialog" else None)
    client.post("/pick-folder", data={"path": str(tmp_path)})
    assert calls[-1] == ["kdialog", "--getexistingdirectory", str(tmp_path.resolve())]


def test_projects_page(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "cache"))
    for key, value in {"running": False, "project": "", "folder": ""}.items():
        monkeypatch.setitem(web.job, key, value)  # restored afterwards: job is shared by all tests
    client = TestClient(web.app, base_url=LOCAL, headers={"HX-Request": "true"})
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    web.db.save_projects({"Alps": str(a), "Coast": str(tmp_path / "gone")})
    web._open("Alps")

    page = client.get("/projects").text
    assert "Folder not found" in page and "not analysed yet" in page
    assert not web.db.library_path(tmp_path / "gone").exists()  # looking creates no cache folder

    assert "already exists" in client.post("/projects/rename", data={"name": "Alps", "new_name": "Coast"}).text
    assert client.post("/projects/rename", data={"name": "Alps", "new_name": " Alps 2026 "}).headers["HX-Refresh"]
    assert list(web.db.load_projects()) == ["Alps 2026", "Coast"] and web.job["project"] == "Alps 2026"
    assert client.post("/projects/rename", data={"name": "nope", "new_name": "x"}).status_code == 404

    assert "Not a folder" in client.post("/projects/folder", data={"name": "Alps 2026", "folder": str(tmp_path / "x")}).text
    client.post("/projects/folder", data={"name": "Alps 2026", "folder": str(b)})
    web.db.mark_analysed(b)  # as if analysed
    assert "0 files" in client.get("/projects").text and "last analysed" in client.get("/projects").text
    assert web.db.load_projects()["Alps 2026"] == str(b.resolve()) and web.job["folder"] == str(b.resolve())

    monkeypatch.setitem(web.job, "running", True)
    assert "Wait until" in client.post("/projects/remove", data={"name": "Coast"}).text
    monkeypatch.setitem(web.job, "running", False)
    client.post("/projects/remove", data={"name": "Alps 2026"})
    assert list(web.db.load_projects()) == ["Coast"] and web.job["project"] == ""
    assert "Coast" in client.get("/").text and web.job["project"] == "Coast"  # the library opens the one left
    assert web.db.library_path(b).exists()  # kept unless asked to delete

    web.db.save_projects({"One": str(b), "Two": str(b), "Coast": str(tmp_path / "gone")})
    client.post("/projects/remove", data={"name": "One", "delete_results": "true"})
    assert web.db.library_path(b).exists()  # still used by Two
    client.post("/projects/remove", data={"name": "Two", "delete_results": "true"})
    assert not web.db.library_path(b).exists() and b.exists()  # results gone, photos untouched
