import io
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from photos2show import testdata
from photos2show.web import app as web

LOCAL = "http://127.0.0.1"


def test_project_analyse_browse_and_slideshow(tmp_path, monkeypatch, fake_ai):
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "cache"))
    folder = testdata.generate(tmp_path / "photos")
    client = TestClient(web.app, base_url=LOCAL, headers={"HX-Request": "true"})
    assert TestClient(web.app, base_url=LOCAL).post("/analyse").status_code == 403  # CSRF guard
    assert TestClient(web.app, base_url="http://rebound.example").get("/").status_code == 400  # DNS rebinding
    assert web.db.load_settings()["review_duplicates"] == "on"  # default: Analyse pauses for the duplicate review
    client.post("/settings", data={"key": "review_duplicates", "value": "off"})  # this test runs straight through

    assert "Create a project" in client.get("/").text
    assert client.post("/slideshow/undo").status_code == 404  # no project: never the server's working dir
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
    assert 'src="/projects/cover?name=Norway%202026"' in client.get("/projects").text
    assert client.get("/projects/cover?name=Norway 2026").headers["content-type"] == "image/jpeg"
    assert client.get("/projects/cover?name=nope").status_code == 404
    assert "already exists" in client.post("/projects", data={"name": "Norway 2026", "folder": str(tmp_path)}).text
    assert web.db.load_projects()["Norway 2026"] == str(folder.resolve())
    used, describe = [], web.describe_folder
    monkeypatch.setattr(web, "describe_folder", lambda *a: used.append(a[2:3]) or describe(*a))
    client.post("/settings", data={"key": "parallel", "value": "1"})
    client.post("/analyse")
    assert "0 described by AI" in wait().text and used == [(1,)]  # all cached; the setting is used
    assert client.post("/settings", data={"key": "parallel", "value": "99"}).status_code == 400

    grid = client.get("/grid").text
    assert "burst_1.jpg" in grid and "▶ video" in grid
    assert "burst_2.jpg" not in grid and ">+2</span>" in grid and "Show all shots" in grid  # stacked by default
    assert "burst_2.jpg" in client.get("/grid?q=burst_2").text  # its best shot doesn't match: shown alone
    everything = client.get("/grid?stacks=false&q=").text
    assert "burst_2.jpg" in everything and "+2" not in everything
    assert "burst_2.jpg" in client.get("/grid").text  # remembered
    client.get("/grid?stacks=true")
    by_name = client.get("/grid?sort=name").text
    assert by_name.index("blurry.jpg") < by_name.index("bright.jpg") < by_name.index("video.mp4")
    assert by_name.index("video.mp4") > by_name.index("scene_3.jpg")
    assert client.get("/grid?sort=nope").text == by_name and web.job["sort"] == "name"  # unknown: kept
    assert "#beach <span>" in by_name  # tag list with counts
    assert ">★★★</span>" in by_name and "fake caption" in by_name  # aesthetic 7, interest 6
    assert web.stars({"aesthetic": "10", "interest": 10}) == "★★★★★" and web.stars({"aesthetic": 0}) == "★"
    assert web.stars({"aesthetic": "great"}) == web.stars({"aesthetic": "inf"}) == web.stars(None) == ""  # raw LLM output
    assert '<option value="name" selected>' in client.get("/grid").text
    items = {Path(it["path"]).name: it["id"] for it in web._items()}
    page = client.get(f"/?flag=video&photo={items['video.mp4']}").text  # a pushed URL, reloaded
    assert f'hx-get="/media/{items["video.mp4"]}" hx-trigger="load"' in page and web.job["flag"] == "video"
    assert f'hx-push-url="/?flag=video&amp;photo={items["video.mp4"]}"' in client.get("/grid").text
    resp = client.get("/grid?q=fake")  # typing replaces the URL, filters push one with the search in it
    assert resp.headers["HX-Replace-Url"] == "/?flag=video&q=fake" and 'hx-push-url="/?flag=all&amp;q=fake"' in resp.text
    assert client.get("/grid?sort=name").headers["HX-Replace-Url"] == "/?flag=video&q=fake&sort=name"
    assert "HX-Replace-Url" not in client.get("/grid").headers  # live reloads leave the URL alone
    client.get("/?flag=all&q=beach")  # back/forward or a bookmark: the URL is the view
    assert (web.job["flag"], web.job["query"], web.job["sort"]) == ("all", "beach", "date")
    assert 'href="/?flag=all&amp;q=beach"' in client.get("/").text and web.job["query"] == "beach"  # bare /: kept
    client.get("/?flag=all")
    client.get("/grid?sort=date")
    skipped = client.get("/grid?flag=skips_ai").text
    assert "very_blurry.jpg" in skipped and "burst_2.jpg" in skipped and "scene_1.jpg" not in skipped

    assert "fake caption" in client.get(f"/media/{items['video.mp4']}").text
    tagged = client.get("/grid?flag=tag:beach").text
    assert "burst_1.jpg" in tagged and "burst_2.jpg" not in tagged
    assert 'hx-get="/grid?flag=tag%3Afish%20%26%20chips"' in client.get("/grid?flag=tag:fish %26 chips").text
    found = client.get("/grid?flag=all&q=FAKE scene_1").text  # all words, any case, caption + file name
    assert "scene_1.jpg" in found and "scene_2.jpg" not in found
    assert "scene_1.jpg" in client.get("/grid").text and "scene_2.jpg" not in client.get("/grid").text  # kept
    assert "No photos match" in client.get("/grid?q=penguin").text
    assert "No photos match this filter" in client.get("/grid?flag=tag:penguin&q=").text
    client.get("/grid?flag=all")
    assert "scene_2.jpg" in client.get("/grid?q=").text  # cleared
    assert client.get(f"/thumb/{items['video.mp4']}?frame=2").headers["content-type"] == "image/jpeg"
    assert client.get(f"/original/{items['video.mp4']}").status_code == 200
    assert client.get("/media/9999").status_code == 404
    resp = client.get(f"/preview/{items['scene_1.jpg']}")
    assert resp.headers["content-type"] == "image/jpeg" and max(Image.open(io.BytesIO(resp.content)).size) <= 1024
    cached = next((web.db.library_dir(folder) / "thumbs").glob("*_preview.jpg"))
    assert client.get(f"/preview/{items['video.mp4']}").status_code == 404  # videos play the original
    os.utime(folder / "scene_1.jpg", (1, 1))  # counts as changed
    web.analyse_folder(folder)
    assert not cached.exists()  # the next request makes a new one
    web.describe_folder(folder)
    (folder / "burst_3.jpg").rename(tmp_path / "away.jpg")  # deleted after the scan
    assert client.get(f"/original/{items['burst_3.jpg']}").status_code == 404
    (tmp_path / "away.jpg").rename(folder / "burst_3.jpg")

    # Slideshow: GLM picks, edit by hand, export
    answer = {"picks": [{"id": items["scene_1.jpg"], "reason": "great view"}, {"id": items["scene_2.jpg"]}]}
    monkeypatch.setattr("photos2show.ai.complete_json", lambda c, s, m: answer)
    assert "Describe the slideshow" in client.get("/slideshow").text
    assert client.post("/slideshow/select", data={"description": "x", "desc_size": "huge"}).status_code == 400
    picks = client.post("/slideshow/select", data={"description": "short", "desc_size": "short"}).text
    assert "great view" in picks and "2 photos" in picks
    assert web.db.load_settings()["desc_size"] == "short"  # kept, as on the settings page
    assert "<option selected>short</option>" in client.get("/slideshow").text
    toggle = client.post(f"/slideshow/add/{items['video.mp4']}").text  # from the Library detail panel
    assert "Remove from slideshow (#3)" in toggle and f'id="pick-{items["scene_1.jpg"]}"' in toggle  # all badges
    assert "✓ 3" in client.get(f"/media/{items['video.mp4']}").text
    shown = client.get("/grid?flag=in_slideshow").text
    assert "video.mp4" in shown and "scene_1.jpg" in shown and "burst_1.jpg" not in shown
    client.get("/grid?flag=all")
    picks = client.post("/slideshow/move", data={"id": items["scene_2.jpg"], "to": 0}).text  # ← button
    assert picks.index("scene_2.jpg") < picks.index("scene_1.jpg") and "3 photos" in picks
    assert "about 0.2 min" in picks  # 5 + 5 s photos + the 3 s video
    picks = client.post("/slideshow/seconds", data={"seconds": "10"}).text
    assert "about 0.4 min" in picks and 'value="10"' in picks and 'data-seconds="10"' in picks
    assert client.post("/slideshow/seconds", data={"seconds": "99"}).status_code == 422
    assert '"to": 2}' in picks  # → button of the 2nd tile
    picks = client.post("/slideshow/move", data={"id": items["video.mp4"], "to": 0}).text
    assert picks.index("video.mp4") < picks.index("scene_2.jpg") < picks.index("scene_1.jpg")
    assert "Add to slideshow" in client.post(f"/slideshow/remove/{items['burst_1.jpg']}").text  # not a pick: no-op
    some = f"{items['burst_1.jpg']},{items['scene_3.jpg']},9999"  # 9999: unknown, skipped
    resp = client.post("/slideshow/bulk", data={"action": "add", "ids": some})
    assert resp.headers["HX-Trigger"] == "analysed"  # the grid reloads its badges
    assert [p["id"] for p in web.slideshow.load(folder)["picks"]][-2:] == [items["burst_1.jpg"], items["scene_3.jpg"]]
    client.post("/slideshow/bulk", data={"action": "remove", "ids": some})
    assert len(web.slideshow.load(folder)["picks"]) == 3
    assert client.post("/slideshow/bulk", data={"action": "add", "ids": "1,x"}).status_code == 400
    assert client.post("/slideshow/bulk", data={"action": "move", "ids": "1"}).status_code == 400
    assert "2 photos" in client.post(f"/slideshow/remove/{items['video.mp4']}", headers={"HX-Target": "picks"}).text
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

    # Swap a pick for another shot of its scene
    client.post(f"/slideshow/add/{items['burst_1.jpg']}")
    picks = client.get("/slideshow/picks").text
    assert "⇄ 2 other shots" in picks and f'"to": {items["burst_2.jpg"]}' in picks
    before = [p["id"] for p in web.slideshow.load(folder)["picks"]]
    picks = client.post("/slideshow/swap", data={"id": items["burst_1.jpg"], "to": items["burst_2.jpg"]}).text
    after = [p["id"] for p in web.slideshow.load(folder)["picks"]]
    assert after == [items["burst_2.jpg"] if i == items["burst_1.jpg"] else i for i in before]  # same place
    assert web.slideshow.load(folder)["picks"][-1]["reason"] == "added by hand"  # reason kept
    bad = client.post("/slideshow/swap", data={"id": items["burst_2.jpg"], "to": items["scene_1.jpg"]})
    assert bad.status_code == 400  # another scene
    client.post("/slideshow/undo")
    assert [p["id"] for p in web.slideshow.load(folder)["picks"]] == before


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
    monkeypatch.setitem(web.job, "phase", "Describing with AI")
    monkeypatch.setitem(web.job, "grid_phase", "Scanning")
    resp = client.get("/progress")
    assert "about 3 min left" in resp.text and "Stop" in resp.text
    assert resp.headers["HX-Trigger"] == "analysed"  # a new phase: the grid reloads once
    monkeypatch.setitem(web.job, "grid_phase", "Describing with AI")
    monkeypatch.setitem(web.job, "grid_done", 1)
    resp = client.get("/progress")
    assert "HX-Trigger" not in resp.headers and "Show 3 new results" in resp.text  # no jumping, a button instead
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
    assert client.post("/settings", data={"key": "accent", "value": "teal"}).headers["HX-Refresh"] == "true"
    assert 'data-accent="teal"' in client.get("/").text
    assert client.get("/static/style.css").headers["Cache-Control"] == "no-cache"  # updates show without a hard reload


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
    assert client.get("/projects/cover?name=Alps 2026").status_code == 404  # no photos, no cover
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


def test_review_duplicates_then_change_best_later(tmp_path, monkeypatch, fake_ai):
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "cache"))
    folder = testdata.generate(tmp_path / "photos")
    client = TestClient(web.app, base_url=LOCAL, headers={"HX-Request": "true"})
    client.post("/settings", data={"key": "review_duplicates", "value": "on"})
    client.post("/projects", data={"name": "Review", "folder": str(folder)})
    for _ in range(100):  # the scan pauses before the AI
        if "Pick the best shot" in (resp := client.get("/progress")).text:
            break
        time.sleep(0.1)
    assert 'hx-get="/progress"' not in resp.text and not fake_ai  # no polling, no AI call yet
    items = {Path(it["path"]).name: it["id"] for it in web._items()}
    bursts = sorted((it for it in web._items() if Path(it["path"]).name.startswith("burst_")), key=lambda it: -it["score"])

    # Only when asked: the AI compares the shots side by side; its pick is marked and can be overruled.
    monkeypatch.setattr("photos2show.ai.pick_best", lambda c, s, imgs: 1 / 0)
    failed = client.post(f"/best/ai/{bursts[0]['id']}").text
    assert "AI comparison failed: division by zero" in failed and "AI pick" not in failed
    monkeypatch.setattr("photos2show.ai.pick_best", lambda c, s, imgs: len(imgs) - 1)  # the technically weakest
    scene = client.post(f"/best/ai/{bursts[0]['id']}").text
    assert f'class="shot best" hx-post="/best/{bursts[-1]["id"]}"' in scene and "<span>AI pick</span>" in scene
    assert client.post(f"/best/ai/{items['video.mp4']}").status_code == 400  # not a scene of several shots
    scene = client.post(f"/best/{items['burst_2.jpg']}").text  # the user overrules; the review row comes back
    assert f'class="shot best" hx-post="/best/{items["burst_2.jpg"]}"' in scene and "AI pick" not in scene
    assert client.post(f"/best/{items['video.mp4']}").status_code == 400
    client.post("/analyse/continue")
    assert client.post(f"/best/ai/{items['burst_2.jpg']}").status_code == 400  # outside the review: never
    for _ in range(100):
        if not web.job["running"]:
            break
        time.sleep(0.1)
    described = {Path(it["path"]).name for it in web._items() if it["ai"]}
    assert "burst_2.jpg" in described and "burst_1.jpg" not in described

    # Later, in the detail panel: burst_1 becomes the best again and is described right away.
    def poll(media_id):
        for _ in range(100):
            if (resp := client.get(f"/describing/{media_id}")).status_code == 200:
                return resp
            time.sleep(0.1)
        raise AssertionError("still describing")

    detail = client.post(f"/best/{items['burst_1.jpg']}", headers={"HX-Target": "detail"})
    assert detail.headers["HX-Trigger"] == "analysed" and "Make this the best shot" not in detail.text
    assert "Describing with AI" in detail.text
    done = poll(items["burst_1.jpg"])
    assert done.headers["HX-Retarget"] == "#detail" and "fake caption" in done.text
    assert "Make this the best shot" in client.get(f"/media/{items['burst_2.jpg']}").text

    def broken(*a):
        raise RuntimeError("model offline")

    monkeypatch.setattr("photos2show.ai.analyse_image", broken)
    client.post(f"/best/{items['burst_3.jpg']}", headers={"HX-Target": "detail"})
    failed = poll(items["burst_3.jpg"]).text
    assert 'class="error">AI description failed: burst_3.jpg: model offline' in failed


def test_heic_original_is_sent_as_jpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOS2SHOW_CACHE", str(tmp_path / "cache"))
    folder = tmp_path / "photos"
    folder.mkdir()
    Image.new("RGB", (60, 40), "red").save(folder / "IMG_0001.heic")  # only Safari shows HEIC
    Image.new("RGB", (60, 40), "blue").save(folder / "plain.jpg")
    web.analyse_folder(folder)
    monkeypatch.setitem(web.job, "folder", str(folder))
    client = TestClient(web.app, base_url=LOCAL, headers={"HX-Request": "true"})
    ids = {Path(it["path"]).name: it["id"] for it in web._items()}
    resp = client.get(f"/original/{ids['IMG_0001.heic']}")
    assert resp.headers["content-type"] == "image/jpeg" and Image.open(io.BytesIO(resp.content)).size == (60, 40)
    assert client.get(f"/original/{ids['plain.jpg']}").content == (folder / "plain.jpg").read_bytes()  # untouched
