"""Local web UI: analyse a folder and browse the results."""
import os
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from imgsort import db
from imgsort.pipeline import analyse_folder, skip_ai_reason

HERE = Path(__file__).parent
app = FastAPI(title="imgsort")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["basename"] = lambda p: Path(p).name

FILTERS = ("all", "ok", "skips_ai", "very_blurry", "blurry", "too_dark", "too_bright",
           "low_contrast", "monotone", "duplicate", "video")

# Single local user, one analysis at a time.
job = {"folder": os.environ.get("IMGSORT_LIBRARY", ""), "running": False, "done": 0, "total": 0,
       "summary": None, "error": None}
lock = threading.Lock()


def _run(root: Path) -> None:
    try:
        job["summary"] = analyse_folder(root, lambda done, total: job.update(done=done, total=total))
    except Exception as e:
        job["error"] = str(e)
    finally:
        job["running"] = False


def _items() -> list[dict]:
    if not job["folder"] or not Path(job["folder"]).is_dir():
        return []
    items = db.all_media(db.connect(Path(job["folder"])))
    for it in items:
        it["skip_ai"] = skip_ai_reason(it)
    return items


def _matches(it: dict, flag: str) -> bool:
    if flag == "all":
        return True
    if flag == "ok":
        return not it["flags"]
    if flag == "skips_ai":
        return it["skip_ai"] is not None
    if flag == "video":
        return it["kind"] == "video"
    return flag in it["flags"]


def _item(media_id: int) -> dict:
    item = db.get_media(db.connect(Path(job["folder"])), media_id) if job["folder"] else None
    if not item:
        raise HTTPException(404)
    return item


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "library.html", {"job": job})


@app.post("/analyse", response_class=HTMLResponse)
def analyse(request: Request, folder: str = Form(...)):
    root = Path(folder).expanduser()
    if not root.is_dir():
        return HTMLResponse(f'<div id="progress" class="error">Not a folder: {folder}</div>')
    with lock:
        if not job["running"]:
            job.update(folder=str(root.resolve()), running=True, done=0, total=0, summary=None, error=None)
            threading.Thread(target=_run, args=(root,), daemon=True).start()
    return templates.TemplateResponse(request, "_progress.html", {"job": job})


@app.get("/progress", response_class=HTMLResponse)
def progress(request: Request):
    resp = templates.TemplateResponse(request, "_progress.html", {"job": job})
    if not job["running"]:
        resp.headers["HX-Trigger"] = "analysed"  # tells the grid to reload
    return resp


@app.get("/grid", response_class=HTMLResponse)
def grid(request: Request, flag: str = "all"):
    items = _items()
    counts = {f: sum(_matches(it, f) for it in items) for f in FILTERS}
    shown = [it for it in items if _matches(it, flag)]
    return templates.TemplateResponse(request, "_grid.html", {"items": shown, "counts": counts, "flag": flag})


@app.get("/media/{media_id}", response_class=HTMLResponse)
def detail(request: Request, media_id: int):
    item = _item(media_id)
    item["skip_ai"] = skip_ai_reason(item)
    best = item["duplicate_of"] or item["id"]
    scene = [it for it in _items() if (it["duplicate_of"] or it["id"]) == best and it["id"] != item["id"]]
    return templates.TemplateResponse(request, "_detail.html", {"it": item, "scene": scene})


@app.get("/thumb/{media_id}")
def thumb(media_id: int, frame: int | None = None):
    item = _item(media_id)
    name = item["thumb"] if frame is None else item["thumb"].replace(".jpg", f"_f{frame}.jpg")
    return FileResponse(db.library_dir(Path(job["folder"])) / "thumbs" / name)


@app.get("/original/{media_id}")
def original(media_id: int):
    return FileResponse(_item(media_id)["path"])


def main() -> None:
    port = int(os.environ.get("IMGSORT_PORT", "8000"))
    print(f"imgsort UI on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
