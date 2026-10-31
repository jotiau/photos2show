"""Local web UI: holiday projects, analysis, browsing, slideshow selection and export."""
import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from html import escape
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from photos2show import db, slideshow
from photos2show.config import default_concurrency, env
from photos2show.pipeline import analyse_folder, describe_folder, skip_ai_reason

HERE = Path(__file__).parent
app = FastAPI(title="photos2show")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["basename"] = lambda p: Path(p).name
templates.env.globals["settings"] = db.load_settings

FILTERS = ("all", "ok", "skips_ai", "very_blurry", "blurry", "too_dark", "too_bright",
           "low_contrast", "monotone", "duplicate", "video")
REFRESH = {"HX-Refresh": "true"}

# Single local user, one analysis at a time.
job = {"project": "", "folder": "", "running": False, "phase": "", "done": 0, "total": 0, "started": 0.0,
       "stopping": False, "summary": None, "error": None, "flag": "all", "query": "",
       "parallel": default_concurrency()}
lock = threading.Lock()
stop = threading.Event()


def time_left() -> str:
    """Remaining time of the running phase, from its speed so far; empty until there is enough to go by."""
    done, total = job["done"], job["total"]
    if not job["running"] or job["stopping"] or done < 3 or done >= total:
        return ""
    minutes = (time.monotonic() - job["started"]) / done * (total - done) / 60
    return "less than a minute left" if minutes < 1 else f"about {round(minutes)} min left"


templates.env.globals["time_left"] = time_left


@app.middleware("http")
async def htmx_only(request: Request, call_next):
    # Other websites can post forms to localhost (CSRF) but cannot add this header.
    if request.method != "GET" and "hx-request" not in request.headers:
        return HTMLResponse("Forbidden", status_code=403)
    return await call_next(request)


# DNS rebinding: a website can point its own domain at 127.0.0.1 and then pass the check above.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


def _open(name: str, projects: dict[str, str] | None = None) -> None:
    folder = (projects or db.load_projects())[name]
    job.update(project=name, folder=folder, flag="all", query="", summary=None, error=None)


def _run(root: Path) -> None:
    progress = lambda done, total: job.update(done=done, total=total)  # noqa: E731
    try:
        job.update(phase="Scanning", started=time.monotonic())
        summary = analyse_folder(root, progress, stop)
        described = {"described": 0, "errors": []}
        if not stop.is_set():
            job.update(phase="Describing with AI", done=0, total=0, started=time.monotonic())
            described = describe_folder(root, progress, job["parallel"], stop)
        job["summary"] = {**summary, **described, "errors": summary["errors"] + described["errors"],
                          "stopped": stop.is_set()}
    except (Exception, SystemExit) as e:  # SystemExit: AI endpoint not configured
        job["error"] = str(e)
    finally:
        job["running"] = False


def _start() -> None:
    with lock:
        if job["folder"] and not job["running"]:
            stop.clear()
            job.update(running=True, stopping=False, done=0, total=0, summary=None, error=None)
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


def _found(it: dict, query: str) -> bool:
    """Every word of the search occurs in the caption, tags, scene, issues or file name (any case)."""
    a = it["ai"] or {}
    text = " ".join([str(a.get("caption") or ""), str(a.get("scene") or ""), *map(str, a.get("tags") or []),
                     *map(str, a.get("issues") or []), Path(it["path"]).name]).lower()
    return all(word in text for word in query.lower().split())


def _root() -> Path:
    if not job["folder"]:  # no project open: Path("") would be the server's working directory
        raise HTTPException(404)
    return Path(job["folder"])


def _item(media_id: int) -> dict:
    item = db.get_media(db.connect(_root()), media_id)
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
    if name.strip() in db.load_projects():  # would silently point the old project at the new folder
        return HTMLResponse(f'<span class="error">A project called {escape(name.strip())} already exists.</span>')
    if job["running"]:
        return HTMLResponse('<span class="error">Wait until the running analysis is finished.</span>')
    db.save_project(name.strip(), root.resolve())
    _open(name.strip())
    _start()
    return HTMLResponse("", headers={"HX-Redirect": "/"})  # the library shows the analysis progress


@app.post("/projects/open", response_class=HTMLResponse)
def open_project(name: str = Form(...)):
    if job["running"]:
        return HTMLResponse('<span class="error">Wait until the running analysis is finished.</span>')
    if name not in db.load_projects():
        raise HTTPException(404)
    _open(name)
    return HTMLResponse("", headers=REFRESH)


@app.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request):
    stats = {name: db.project_stats(Path(folder)) for name, folder in db.load_projects().items()}
    return _page(request, "projects.html", page="projects", stats=stats)


def _edit_projects(name: str, change) -> HTMLResponse:
    """Apply change(projects) -> error or None to the saved projects; the page reloads on success."""
    if job["running"]:
        return HTMLResponse('<span class="error">Wait until the running analysis is finished.</span>')
    projects = db.load_projects()
    if name not in projects:
        raise HTTPException(404)
    if error := change(projects):
        return HTMLResponse(f'<span class="error">{escape(error)}</span>')
    db.save_projects(projects)
    return HTMLResponse("", headers=REFRESH)


@app.post("/projects/rename", response_class=HTMLResponse)
def rename_project(name: str = Form(...), new_name: str = Form(...)):
    new_name = new_name.strip()

    def change(projects):
        if not new_name:
            return "Name missing"
        if new_name != name and new_name in projects:
            return f"A project called {new_name} already exists."
        renamed = {new_name if k == name else k: v for k, v in projects.items()}  # keeps the order
        projects.clear()
        projects.update(renamed)
        if job["project"] == name:
            job["project"] = new_name

    return _edit_projects(name, change)


@app.post("/projects/folder", response_class=HTMLResponse)
def change_folder(name: str = Form(...), folder: str = Form(...)):
    root = Path(folder).expanduser()

    def change(projects):
        if not root.is_dir():
            return f"Not a folder: {folder}"
        projects[name] = str(root.resolve())
        if job["project"] == name:
            _open(name, projects)

    return _edit_projects(name, change)


@app.post("/projects/remove", response_class=HTMLResponse)
def remove_project(name: str = Form(...), delete_results: bool = Form(False)):
    def change(projects):
        folder = projects.pop(name)
        if delete_results and folder not in projects.values():  # another project on the same folder keeps them
            shutil.rmtree(db.library_path(Path(folder)), ignore_errors=True)
        if job["project"] == name:
            job.update(project="", folder="", summary=None, error=None)  # the library opens the newest one left

    return _edit_projects(name, change)


@app.post("/analyse", response_class=HTMLResponse)
def analyse(request: Request, parallel: int = Form(ge=1, le=16)):
    if not job["running"]:
        job["parallel"] = parallel
    _start()
    return templates.TemplateResponse(request, "_progress.html", {"job": job})


@app.post("/analyse/stop", response_class=HTMLResponse)
def stop_analysis(request: Request):
    if job["running"]:
        stop.set()
        job["stopping"] = True
    return templates.TemplateResponse(request, "_progress.html", {"job": job})


@app.get("/progress", response_class=HTMLResponse)
def progress(request: Request):
    resp = templates.TemplateResponse(request, "_progress.html", {"job": job})
    if not job["running"] or job["phase"] != "Scanning":
        resp.headers["HX-Trigger"] = "analysed"  # tells the grid to reload, live while AI results come in
    return resp


@app.get("/grid", response_class=HTMLResponse)
def grid(request: Request, flag: str | None = None, q: str | None = None):
    flag = job["flag"] = flag or job["flag"]  # remembered so live reloads keep the filter
    query = job["query"] = (job["query"] if q is None else q).strip()  # q="" clears the search
    items = [it for it in _items() if _found(it, query)]
    counts = {f: sum(_matches(it, f) for it in items) for f in FILTERS}
    if flag.startswith("tag:"):
        counts[flag] = sum(_matches(it, flag) for it in items)
    shown = [it for it in items if _matches(it, flag)]
    return templates.TemplateResponse(request, "_grid.html", {"items": shown, "counts": counts, "flag": flag,
                                                              "query": query})


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


def _folder_dialog(start: Path) -> list[str]:
    """On Linux Tk draws its own Motif-era dialog, so prefer the desktop's (GTK via zenity, KDE via kdialog).
    Windows and macOS get their native dialog through Tk."""
    if sys.platform.startswith("linux"):
        if shutil.which("zenity"):
            return ["zenity", "--file-selection", "--directory", f"--filename={start}{os.sep}"]
        if shutil.which("kdialog"):
            return ["kdialog", "--getexistingdirectory", str(start)]
    return [sys.executable, "-c", PICK_FOLDER, str(start)]


@app.post("/pick-folder", response_class=PlainTextResponse)
def pick_folder(path: str = Form(""), keep_name: bool = Form(False)):
    """Open the system's folder dialog on this computer – the browser's own dialog never reveals real paths.
    A subprocess, because Tk must own the main thread (macOS). keep_name appends the typed folder name."""
    start = (Path(path).expanduser() if path.strip() else Path.home()).resolve()
    while not start.is_dir():
        start = start.parent
    cmd = _folder_dialog(start)
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    cancelled = done.returncode == 1 and cmd[0] != sys.executable  # zenity/kdialog exit 1 on cancel
    if done.returncode and not cancelled:
        reason = (done.stderr.strip().splitlines() or ["unknown error"])[-1]
        return PlainTextResponse(f"Could not open the folder dialog ({reason}). Type the path instead.", status_code=500)
    picked = "" if cancelled else done.stdout.strip()
    if picked and keep_name and Path(path).name:
        picked = str(Path(picked) / Path(path).name)
    return str(Path(picked)) if picked else ""


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    unused = db.unused_libraries()
    size = sum(f.stat().st_size for d in unused for f in d.rglob("*") if f.is_file())
    return _page(request, "settings.html", page="settings", fields=db.SETTINGS, unused=len(unused), unused_mb=size / 1e6)


@app.post("/settings/cleanup", response_class=HTMLResponse)
def cleanup():
    if job["running"]:
        return HTMLResponse('<span class="error">Wait until the running analysis is finished.</span>')
    for d in db.unused_libraries():
        shutil.rmtree(d, ignore_errors=True)
    return HTMLResponse("", headers=REFRESH)


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
    return FileResponse(db.library_dir(_root()) / "thumbs" / name)


@app.get("/original/{media_id}")
def original(media_id: int):
    path = _item(media_id)["path"]
    if not Path(path).is_file():  # deleted or drive unplugged since the scan
        raise HTTPException(404)
    return FileResponse(path)


def _picks(request: Request, error: str | None = None) -> HTMLResponse:
    conn = db.connect(_root())
    picks = [{"item": it, "reason": p["reason"]}
             for p in slideshow.load(_root())["picks"] if (it := db.get_media(conn, p["id"]))]
    return templates.TemplateResponse(request, "_picks.html", {
        "picks": picks, "error": error, "minutes": len(picks) * slideshow.SECONDS_PER_PHOTO / 60,
        "seconds": slideshow.SECONDS_PER_PHOTO,
        "sorts": {k: v[0] for k, v in slideshow.SORTS.items()}, "can_undo": slideshow.can_undo(_root())})


@app.get("/slideshow", response_class=HTMLResponse)
def slideshow_page(request: Request):
    if not job["folder"]:
        return _page(request, "slideshow.html", page="slideshow", selection=None)
    selection = slideshow.load(_root())
    return _page(request, "slideshow.html", page="slideshow", selection=selection,
                 default_target=selection.get("exported_to") or f"{job['folder']}_slideshow")


@app.get("/slideshow/picks", response_class=HTMLResponse)
def picks(request: Request):
    return _picks(request)


@app.post("/slideshow/select", response_class=HTMLResponse)
def select(request: Request, description: str = Form(...)):
    root = _root()
    try:
        picks = slideshow.select(db.connect(root), description)
    except (Exception, SystemExit) as e:
        return _picks(request, error=str(e))
    slideshow.update(root, lambda s: {**s, "description": description, "picks": picks, "refinements": []})
    return _picks(request)


@app.post("/slideshow/refine", response_class=HTMLResponse)
def refine(request: Request, instruction: str = Form(...)):
    root = _root()
    selection = slideshow.load(root)
    try:
        picks = slideshow.select(db.connect(root), selection["description"], selection["picks"], instruction)
    except (Exception, SystemExit) as e:
        return _picks(request, error=str(e))
    # refinements: kept for selection.json in the export
    slideshow.update(root, lambda s: {**s, "picks": picks, "refinements": [*s.get("refinements", []), instruction]})
    return _picks(request)


@app.post("/slideshow/undo", response_class=HTMLResponse)
def undo(request: Request):
    slideshow.undo(_root())
    return _picks(request)


@app.post("/slideshow/sort", response_class=HTMLResponse)
def sort(request: Request, by: str = Form(...)):
    if by not in slideshow.SORTS:
        raise HTTPException(400)
    root = _root()
    items = {it["id"]: it for it in db.all_media(db.connect(root))}
    slideshow.update(root, lambda s: {**s, "picks": slideshow.sort(s["picks"], items, by)})
    return _picks(request)


@app.post("/slideshow/move", response_class=HTMLResponse)
def move(request: Request, id: int = Form(...), to: int = Form(...)):
    slideshow.update(_root(), lambda s: {**s, "picks": slideshow.move(s["picks"], id, to)})
    return _picks(request)


@app.post("/slideshow/export", response_class=HTMLResponse)
def export(target: str = Form(...), replace: bool = Form(False)):
    try:
        n = slideshow.export(_root(), Path(target), replace)
    except (ValueError, OSError) as e:  # OSError: full USB stick, no write rights – htmx shows nothing for a 500
        return HTMLResponse(f'<span class="error">{escape(str(e))}</span>')
    return HTMLResponse(f"Exported {n} files to {escape(str(Path(target).expanduser()))} "
                        '<button type="button" hx-post="/slideshow/open-export" hx-swap="afterend">Open folder</button>')


@app.post("/slideshow/open-export", response_class=HTMLResponse)
def open_export():
    """Show the last export in the system file manager; only that folder, never a path from the request."""
    folder = slideshow.load(_root()).get("exported_to")
    try:
        if not folder or not Path(folder).is_dir():
            raise FileNotFoundError("The export folder is gone.")
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
    except OSError as e:  # e.g. no xdg-open on a minimal Linux: say so instead of a silent 500
        return HTMLResponse(f' <span class="error">Could not open the folder: {escape(str(e))}</span>')
    return HTMLResponse("")


@app.post("/slideshow/{action}/{media_id}", response_class=HTMLResponse)
def edit(request: Request, action: str, media_id: int):
    if action not in ("add", "remove"):
        raise HTTPException(404)
    media_id = _item(media_id)["id"]  # 404 for an unknown id
    slideshow.update(_root(), lambda s: {**s, "picks": slideshow.edit(s["picks"], action, media_id)})
    return HTMLResponse("In slideshow ✓") if action == "add" else _picks(request)


def main() -> None:
    parser = argparse.ArgumentParser(prog="photos2show-web", description="Start the local photos2show web UI.")
    parser.add_argument("--no-browser", action="store_true", help="don't open the UI in the browser on start")
    args = parser.parse_args()
    port = int(env("PORT", "8000"))
    url = f"http://127.0.0.1:{port}"
    print(f"photos2show UI on {url}")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()  # give uvicorn a moment to bind the port
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
