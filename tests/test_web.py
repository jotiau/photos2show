import time

from fastapi.testclient import TestClient

from imgsort import testdata
from imgsort.web import app as web


def test_project_analyse_browse_and_slideshow(tmp_path, monkeypatch, fake_ai):
    monkeypatch.setenv("IMGSORT_CACHE", str(tmp_path / "cache"))
    folder = testdata.generate(tmp_path / "photos")
    client = TestClient(web.app, headers={"HX-Request": "true"})
    assert TestClient(web.app).post("/analyse", data={"parallel": "1"}).status_code == 403  # CSRF guard

    assert "Create a project" in client.get("/").text
    assert "Not a folder" in client.post("/projects", data={"name": "x", "folder": str(tmp_path / "nope")}).text

    def wait():
        for _ in range(100):
            resp = client.get("/progress")
            if 'hx-get="/progress"' not in resp.text:  # stopped polling = finished
                return resp
            time.sleep(0.1)

    resp = client.post("/projects", data={"name": "Norway 2026", "folder": str(folder)})
    assert resp.headers["HX-Refresh"] == "true"
    resp = wait()
    assert f"{len(testdata.EXPECTED)} files" in resp.text and "9 described by AI" in resp.text
    assert "Norway 2026" in client.get("/").text
    assert client.post("/analyse", data={"parallel": "99"}).status_code == 422
    client.post("/analyse", data={"parallel": "1"})
    assert web.job["parallel"] == 1 and "0 described by AI" in wait().text  # all cached

    grid = client.get("/grid").text
    assert "burst_1.jpg" in grid and "▶ video" in grid
    skipped = client.get("/grid?flag=skips_ai").text
    assert "very_blurry.jpg" in skipped and "burst_2.jpg" in skipped and "scene_1.jpg" not in skipped

    items = {it["path"].rsplit("/", 1)[1]: it["id"] for it in web._items()}
    assert "fake caption" in client.get(f"/media/{items['video.mp4']}").text
    tagged = client.get("/grid?flag=tag:beach").text
    assert "burst_1.jpg" in tagged and "burst_2.jpg" not in tagged
    assert client.get(f"/thumb/{items['video.mp4']}?frame=2").headers["content-type"] == "image/jpeg"
    assert client.get(f"/original/{items['video.mp4']}").status_code == 200
    assert client.get("/media/9999").status_code == 404

    # Slideshow: GLM picks, edit by hand, export
    answer = {"picks": [{"id": items["scene_1.jpg"], "reason": "great view"}, {"id": items["scene_2.jpg"]}]}
    monkeypatch.setattr("imgsort.ai.complete_json", lambda c, s, m: answer)
    assert "Describe the slideshow" in client.get("/slideshow").text
    picks = client.post("/slideshow/select", data={"description": "short"}).text
    assert "great view" in picks and "2 photos" in picks
    assert "✓" in client.post(f"/slideshow/add/{items['video.mp4']}").text
    picks = client.post(f"/slideshow/up/{items['scene_2.jpg']}").text
    assert picks.index("scene_2.jpg") < picks.index("scene_1.jpg") and "3 photos" in picks
    assert "2 photos" in client.post(f"/slideshow/remove/{items['video.mp4']}").text
    assert "Exported 2 files" in client.post("/slideshow/export", data={"target": str(tmp_path / "show")}).text
    assert sorted(p.name for p in (tmp_path / "show").iterdir())[0].startswith("001_2026-08-11")
