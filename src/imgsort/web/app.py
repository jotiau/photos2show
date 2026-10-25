"""Local web UI: holiday projects, analysis, browsing, slideshow selection and export."""
import argparse
import os
import subprocess
import sys
import threading
import webbrowser
from html import escape
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from imgsort import db, slideshow
from imgsort.config import default_concurrency
from imgsort.pipeline import analyse_folder, describe_folder, skip_ai_reason

HERE = Path(__file__).parent
app = FastAPI(title="imgsort")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["basename"] = lambda p: Path(p).name
templates.env.globals["settings"] = db.load_settings

FILTERS = ("all", "ok", "skips_ai", "very_blurry", "blurry", "too_dark", "too_bright",
           "low_contrast", "monotone", "duplicate", "video")
REFRESH = {"HX-Refresh": "true"}

# Single local user, one analysis at a time.
job = {"project": "", "folder": "", "running": False, "phase": "", "done": 0, "total": 0,
       "summary": None, "error": None, "flag": "all", "parallel": default_concurrency()}
lock = threading.Lock()


@app.middleware("http")
async def htmx_only(request: Request, call_next):
    # Other websites can post forms to localhost (CSRF) but cannot add this header.
    if request.method != "GET" and "hx-request" not in request.headers:
        return HTMLResponse("Forbidden", status_code=403)
    return await call_next(request)


def _open(name: str) -> None:
    job.update(project=name, folder=db.load_projects()[name], flag="all", summary=None, error=None)


def _run(root: Path) -> None:
    progress = lambda done, total: job.update(done=done, total=total)  # noqa: E731
    try:
        job["phase"] = "Scanning"
        summary = analyse_folder(root, progress)
        job.update(phase="Describing with AI", done=0, total=0)
        described = describe_folder(root, progress, job["parallel"])
        job["summary"] = {**summary, **described, "errors": summary["errors"] + described["errors"]}
    except (Exception, SystemExit) as e:  # SystemExit: AI endpoint not configured
        job["error"] = str(e)
    finally:
        job["running"] = False


def _start() -> None:
    with lock:
        if job["folder"] and not job["running"]:
            job.update(running=True, done=0, total=0, summary=None, error=None)
            threading.Thread(target=_run, args=(Path(job["folder"]),), daemon=True).start()


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
    if flag.startswith("tag:"):
        return flag[4:] in ((it["ai"] or {}).get("tags") or [])
    return flag in it["flags"]


def _item(media_id: int) -> dict:
    item = db.get_media(db.connect(Path(job["folder"])), media_id) if job["folder"] else None
    if not item:
        raise HTTPException(404)
    return item


def _page(request: Request, template: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, template, {"job": job, "projects": db.load_projects(), **ctx})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if not job["project"] and (projects := db.load_projects()):
        _open(list(projects)[-1])
    return _page(request, "library.html", page="library")


@app.post("/projects", response_class=HTMLResponse)
def create_project(name: str = Form(...), folder: str = Form(...)):
    root = Path(folder).expanduser()
    if not name.strip() or not root.is_dir():
        return HTMLResponse(f'<span class="error">Not a folder: {escape(folder)}</span>' if name.strip() else "Name missing")
    if job["running"]:
        return HTMLResponse('<span class="error">Wait until the running analysis is finished.</span>')
    db.save_project(name.strip(), root.resolve())
    _open(name.strip())
    _start()
    return HTMLResponse("", headers=REFRESH)


@app.post("/projects/open", response_class=HTMLResponse)
def open_project(name: str = Form(...)):
    if job["running"]:
        return HTMLResponse('<span class="error">Wait until the running analysis is finished.</span>')
    if name not in db.load_projects():
        raise HTTPException(404)
    _open(name)
    return HTMLResponse("", headers=REFRESH)


@app.post("/analyse", response_class=HTMLResponse)
def analyse(request: Request, parallel: int = Form(ge=1, le=16)):
    if not job["running"]:
        job["parallel"] = parallel
    _start()
    return templates.TemplateResponse(request, "_progress.html", {"job": job})


@app.get("/progress", response_class=HTMLResponse)
def progress(request: Request):
    resp = templates.TemplateResponse(request, "_progress.html", {"job": job})
    if not job["running"] or job["phase"] != "Scanning":
        resp.headers["HX-Trigger"] = "analysed"  # tells the grid to reload, live while AI results come in
    return resp


@app.get("/grid", response_class=HTMLResponse)
def grid(request: Request, flag: str | None = None):
    flag = job["flag"] = flag or job["flag"]  # remembered so live reloads keep the filter
    items = _items()
    counts = {f: sum(_matches(it, f) for it in items) for f in FILTERS}
    if flag.startswith("tag:"):
        counts[flag] = sum(_matches(it, flag) for it in items)
    shown = [it for it in items if _matches(it, flag)]
    return templates.TemplateResponse(request, "_grid.html", {"items": shown, "counts": counts, "flag": flag})


@app.get("/media/{media_id}", response_class=HTMLResponse)
def detail(request: Request, media_id: int):
    item = _item(media_id)
    item["skip_ai"] = skip_ai_reason(item)
    best = item["duplicate_of"] or item["id"]
    scene = [it for it in _items() if (it["duplicate_of"] or it["id"]) == best and it["id"] != item["id"]]
    return templates.TemplateResponse(request, "_detail.html", {"it": item, "scene": scene})


PICK_FOLDER = """import sys, tkinter, tkinter.filedialog
root = tkinter.Tk(); root.withdraw(); root.attributes("-topmost", True)
print(tkinter.filedialog.askdirectory(initialdir=sys.argv[1], parent=root) or "")"""


@app.post("/pick-folder", response_class=PlainTextResponse)
def pick_folder(path: str = Form(""), keep_name: bool = Form(False)):
    """Open the system's folder dialog on this computer – the browser's own dialog never reveals real paths.
    A subprocess, because Tk must own the main thread (macOS). keep_name appends the typed folder name."""
    start = (Path(path).expanduser() if path.strip() else Path.home()).resolve()
    while not start.is_dir():
        start = start.parent
    try:
        picked = subprocess.run([sys.executable, "-c", PICK_FOLDER, str(start)],
                                capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError as e:
        reason = (e.stderr.strip().splitlines() or ["unknown error"])[-1]
        return PlainTextResponse(f"Could not open the folder dialog ({reason}). Type the path instead.", status_code=500)
    if picked and keep_name and Path(path).name:
        picked = str(Path(picked) / Path(path).name)
    return str(Path(picked)) if picked else ""


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return _page(request, "settings.html", page="settings", fields=db.SETTINGS)


@app.post("/settings", response_class=HTMLResponse)
def save_setting(key: str = Form(...), value: str = Form(...)):
    if key not in db.SETTINGS or value not in db.SETTINGS[key][1]:
        raise HTTPException(400)
    db.save_setting(key, value)
    return HTMLResponse("", headers=REFRESH)


@app.get("/thumb/{media_id}")
def thumb(media_id: int, frame: int | None = None):
    item = _item(media_id)
    name = item["thumb"] if frame is None else item["thumb"].replace(".jpg", f"_f{frame}.jpg")
    return FileResponse(db.library_dir(Path(job["folder"])) / "thumbs" / name)


@app.get("/original/{media_id}")
def original(media_id: int):
    return FileResponse(_item(media_id)["path"])


def _picks(request: Request, error: str | None = None) -> HTMLResponse:
    conn = db.connect(Path(job["folder"]))
    picks = [{"item": it, "reason": p["reason"]}
             for p in slideshow.load(Path(job["folder"]))["picks"] if (it := db.get_media(conn, p["id"]))]
    return templates.TemplateResponse(request, "_picks.html", {
        "picks": picks, "error": error, "minutes": len(picks) * slideshow.SECONDS_PER_PHOTO / 60,
        "sorts": {k: v[0] for k, v in slideshow.SORTS.items()}})


@app.get("/slideshow", response_class=HTMLResponse)
def slideshow_page(request: Request):
    if not job["folder"]:
        return _page(request, "slideshow.html", page="slideshow", selection=None)
    return _page(request, "slideshow.html", page="slideshow", selection=slideshow.load(Path(job["folder"])),
                 default_target=f"{job['folder']}_slideshow")


@app.get("/slideshow/picks", response_class=HTMLResponse)
def picks(request: Request):
    return _picks(request)


@app.post("/slideshow/select", response_class=HTMLResponse)
def select(request: Request, description: str = Form(...)):
    root = Path(job["folder"])
    try:
        picks = slideshow.select(db.connect(root), description)
    except (Exception, SystemExit) as e:
        return _picks(request, error=str(e))
    slideshow.save(root, {"description": description, "picks": picks})
    return _picks(request)


@app.post("/slideshow/sort", response_class=HTMLResponse)
def sort(request: Request, by: str = Form(...)):
    if by not in slideshow.SORTS:
        raise HTTPException(400)
    root = Path(job["folder"])
    conn, selection = db.connect(root), slideshow.load(root)
    items = {p["id"]: it for p in selection["picks"] if (it := db.get_media(conn, p["id"]))}
    selection["picks"] = slideshow.sort(selection["picks"], items, by)
    slideshow.save(root, selection)
    return _picks(request)


@app.post("/slideshow/export", response_class=HTMLResponse)
def export(target: str = Form(...)):
    try:
        n = slideshow.export(Path(job["folder"]), Path(target))
    except ValueError as e:
        return HTMLResponse(f'<span class="error">{escape(str(e))}</span>')
    return HTMLResponse(f"Exported {n} files to {escape(str(Path(target).expanduser()))}")


@app.post("/slideshow/{action}/{media_id}", response_class=HTMLResponse)
def edit(request: Request, action: str, media_id: int):
    if action not in ("add", "remove", "up", "down"):
        raise HTTPException(404)
    root = Path(job["folder"])
    selection = slideshow.load(root)
    selection["picks"] = slideshow.edit(selection["picks"], action, _item(media_id)["id"])
    slideshow.save(root, selection)
    return HTMLResponse("In slideshow ✓") if action == "add" else _picks(request)


def main() -> None:
    parser = argparse.ArgumentParser(prog="imgsort-web", description="Start the local imgsort web UI.")
    parser.add_argument("--no-browser", action="store_true", help="don't open the UI in the browser on start")
    args = parser.parse_args()
    port = int(os.environ.get("IMGSORT_PORT", "8000"))
    url = f"http://127.0.0.1:{port}"
    print(f"imgsort UI on {url}")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()  # give uvicorn a moment to bind the port
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
