import time

from fastapi.testclient import TestClient

from imgsort import testdata
from imgsort.web import app as web


def test_analyse_and_browse(tmp_path, monkeypatch):
    monkeypatch.setenv("IMGSORT_CACHE", str(tmp_path / "cache"))
    folder = testdata.generate(tmp_path / "photos")
    client = TestClient(web.app)

    assert client.get("/").status_code == 200
    assert "Not a folder" in client.post("/analyse", data={"folder": str(tmp_path / "nope")}).text

    assert client.post("/analyse", data={"folder": str(folder)}).status_code == 200
    for _ in range(100):
        resp = client.get("/progress")
        if resp.headers.get("HX-Trigger") == "analysed":
            break
        time.sleep(0.1)
    assert f"{len(testdata.EXPECTED)} files" in resp.text

    grid = client.get("/grid").text
    assert "burst_1.jpg" in grid and "▶ video" in grid
    skipped = client.get("/grid?flag=skips_ai").text
    assert "very_blurry.jpg" in skipped and "burst_2.jpg" in skipped and "scene_1.jpg" not in skipped

    item = next(it for it in web._items() if it["path"].endswith("video.mp4"))
    assert "Will be sent to AI" in client.get(f"/media/{item['id']}").text
    assert client.get(f"/thumb/{item['id']}?frame=2").headers["content-type"] == "image/jpeg"
    assert client.get(f"/original/{item['id']}").status_code == 200
    assert client.get("/media/9999").status_code == 404
