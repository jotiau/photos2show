"""Local web UI: holiday projects, analysis, browsing, slideshow selection and export."""
import argparse
import io
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections import Counter, defaultdict
from html import escape
from pathlib import Path
from urllib.parse import urlencode

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from photos2show import db, scan, slideshow
from photos2show.config import env
from photos2show.pipeline import (
    analyse_folder,
    choose_best,
    describe_folder,
    skip_ai_reason,
)

HERE = Path(__file__).parent
app = FastAPI(title="photos2show")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["basename"] = lambda p: Path(p).name
templates.env.globals["settings"] = db.load_settings
templates.env.globals["setting_fields"] = db.SETTINGS

FILTERS = ("all", "ok", "skips_ai", "very_blurry", "blurry", "too_dark", "too_bright",
           "low_contrast", "monotone", "duplicate", "video", "in_slideshow")
REFRESH = {"HX-Refresh": "true"}
UNSTACKED = ("duplicate", "skips_ai")  # these filters are about the shots a stack hides
SORTS = {k: v[0] for k, v in slideshow.SORTS.items() if k != "reverse"}  # Library: name -> label
TOP_TAGS = 30
REVIEW = "Review duplicates"

# Single local user, one analysis at a time.
job = {"project": "", "folder": "", "running": False, "phase": "", "done": 0, "total": 0, "started": 0.0,
       "stopping": False, "summary": None, "error": None, "flag": "all", "query": "", "stacks": True, "sort": "date",
       "grid_phase": "", "grid_done": 0}
lock = threading.Lock()
stop = threading.Event()
reviewed = threading.Event()  # "Continue with AI" clicked in the duplicate review


def time_left() -> str:
    """Remaining time of the running phase, from its speed so far; empty until there is enough to go by."""
    done, total = job["done"], job["total"]
    if not job["running"] or job["stopping"] or done < 3 or done >= total:
        return ""
    minutes = (time.monotonic() - job["started"]) / done * (total - done) / 60
    return "less than a minute left" if minutes < 1 else f"about {round(minutes)} min left"


templates.env.globals["time_left"] = time_left


def stars(ai: dict | None) -> str:
    """1-5 stars from the AI's aesthetic and interest scores (1-10); '' if there are none."""
    scores = [n for k in ("aesthetic", "interest") if (n := slideshow.number((ai or {}).get(k))) is not None]
    return "★" * min(5, max(1, round(sum(scores) / len(scores) / 2))) if scores else ""


templates.env.globals["stars"] = stars


@app.middleware("http")
async def htmx_only(request: Request, call_next):
    # Other websites can post forms to localhost (CSRF) but cannot add this header.
    if request.method != "GET" and "hx-request" not in request.headers:
        return HTMLResponse("Forbidden", status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        # Without it, browsers may keep an old style.css after an update; the ETag keeps the check cheap.
        response.headers["Cache-Control"] = "no-cache"
    return response


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
        if db.load_settings()["review_duplicates"] == "on" and not stop.is_set() and _review_scenes():
            job.update(phase=REVIEW)
            while not (reviewed.wait(0.5) or stop.is_set()):
                pass
        described = {"described": 0, "errors": []}
        if not stop.is_set():
            job.update(phase="Describing with AI", done=0, total=0, started=time.monotonic())
            described = describe_folder(root, progress, int(db.load_settings()["parallel"]), stop)
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
            reviewed.clear()
            job.update(running=True, stopping=False, done=0, total=0, summary=None, error=None)
            threading.Thread(target=_run, args=(Path(job["folder"]),), daemon=True).start()


def _review_scenes() -> list[list[dict]]:
    """Scenes of several shots none of which is described yet: their best shot is still to be sent to the AI."""
    scenes = defaultdict(list)
    for it in _items():
        scenes[_scene(it)].append(it)
    return [s for s in scenes.values() if len(s) > 1 and not any(it["ai"] for it in s)]


templates.env.globals["review_scenes"] = _review_scenes


describing: dict[int, str | None] = {}  # media id -> None while its description runs, else the error
templates.env.globals["describing"] = describing


def _describe_now(root: Path, media_id: int) -> None:
    try:
        errors = describe_folder(root, concurrency=1, only={media_id})["errors"]
    except (Exception, SystemExit) as e:  # e.g. AI not configured
        errors = [str(e)]
    if errors:  # shown in the detail panel; the next Analyse retries
        describing[media_id] = errors[0]
    else:
        describing.pop(media_id, None)


def _items() -> list[dict]:
    if not job["folder"] or not Path(job["folder"]).is_dir():
        return []
    items, picks = db.all_media(db.connect(_root())), _pick_numbers()
    for it in items:
        it["skip_ai"], it["pick"] = skip_ai_reason(it), picks.get(it["id"])
    return items


def _pick_numbers() -> dict[int, int]:
    """media id -> its position in the slideshow (1 = first)."""
    return {p["id"]: n for n, p in enumerate(slideshow.load(_root())["picks"], 1)}


def _matches(it: dict, flag: str) -> bool:
    if flag == "all":
        return True
    if flag == "ok":
        return not it["flags"]
    if flag == "skips_ai":
        return it["skip_ai"] is not None
    if flag == "video":
        return it["kind"] == "video"
    if flag == "in_slideshow":
        return it["pick"] is not None
    if flag.startswith("tag:"):
        return flag[4:] in ((it["ai"] or {}).get("tags") or [])
    return flag in it["flags"]


def _found(it: dict, query: str) -> bool:
    """Every word of the search occurs in the caption, tags, scene, issues or file name (any case)."""
    a = it["ai"] or {}
    text = " ".join([str(a.get("caption") or ""), str(a.get("scene") or ""), *a.get("tags", []),
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
def index(request: Request, flag: str | None = None, q: str | None = None, sort: str | None = None,
          photo: int | None = None):
    """The view comes from the URL (library_url): reload, back/forward and bookmarks keep it.
    A bare / (typed, or a bookmark of the start page) keeps the last view."""
    if not job["project"] and (projects := db.load_projects()):
        _open(list(projects)[-1])
    if flag:
        job.update(flag=flag, query=(q or "").strip(), sort=sort if sort in SORTS else "date")
    return _page(request, "library.html", page="library", photo=photo)


def library_url(**changes) -> str:
    """The Library URL of the current view (flag, search, sort), with changes; always has flag, so a bare /
    means "keep the last view"."""
    state = {"flag": job["flag"], "q": job["query"], "sort": job["sort"], **changes}
    return "/?" + urlencode({k: v for k, v in state.items() if k == "flag" or v not in (None, "", "date")})


templates.env.globals["library_url"] = library_url


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


@app.get("/projects/cover")
def project_cover(name: str):
    folder = db.load_projects().get(name)
    cover = folder and db.project_stats(Path(folder))["cover"]
    if not cover or not cover.is_file():
        raise HTTPException(404)
    return FileResponse(cover)


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
def analyse(request: Request):
    _start()
    return templates.TemplateResponse(request, "_progress.html", {"job": job})


@app.post("/analyse/stop", response_class=HTMLResponse)
def stop_analysis(request: Request):
    if job["running"]:
        stop.set()
        job["stopping"] = True
    return templates.TemplateResponse(request, "_progress.html", {"job": job})


@app.post("/analyse/continue", response_class=HTMLResponse)
def continue_analysis(request: Request):
    if job["phase"] == REVIEW:
        job["phase"] = "Starting AI"  # right away: the reply must not show the review again
        reviewed.set()
    return templates.TemplateResponse(request, "_progress.html", {"job": job})


@app.get("/progress", response_class=HTMLResponse)
def progress(request: Request):
    resp = templates.TemplateResponse(request, "_progress.html", {"job": job})
    # Reload the grid when a phase starts and once at the end; in between a "Show N new results" button,
    # so browsing during a long AI run doesn't jump.
    if not job["running"] or job["phase"] != job["grid_phase"]:
        resp.headers["HX-Trigger"] = "analysed"
    return resp


@app.get("/grid", response_class=HTMLResponse)
def grid(request: Request, flag: str | None = None, q: str | None = None, stacks: bool | None = None,
         sort: str | None = None):
    flag = job["flag"] = flag or job["flag"]  # remembered so live reloads keep the filter
    query = job["query"] = (job["query"] if q is None else q).strip()  # q="" clears the search
    job.update(grid_phase=job["phase"], grid_done=job["done"])  # what the grid shows: newer = "new results"
    stacked = job["stacks"] = job["stacks"] if stacks is None else stacks
    by = job["sort"] = sort if sort in SORTS else job["sort"]
    items = [it for it in _items() if _found(it, query)]
    # Stacks: only the best shot of a scene is shown, with "+N" for its other shots (unless the search hides it).
    ids = {it["id"] for it in items}
    stacked_away = {it["id"] for it in items if stacked and it["duplicate_of"] in ids}
    more = Counter(it["duplicate_of"] for it in items if it["id"] in stacked_away)
    visible = [it for it in items if it["id"] not in stacked_away]
    counts = {f: sum(_matches(it, f) for it in (items if f in UNSTACKED else visible)) for f in FILTERS}
    if flag.startswith("tag:"):
        counts[flag] = sum(_matches(it, flag) for it in visible)
    shown = [it for it in (items if flag in UNSTACKED else visible) if _matches(it, flag)]
    _, key, desc = slideshow.SORTS[by]
    known = [it for it in shown if key(it) is not None]  # without a value (no date, no AI score): at the end
    shown = sorted(known, key=key, reverse=desc) + [it for it in shown if key(it) is None]
    tags = Counter(t for it in visible for t in set((it["ai"] or {}).get("tags", [])))
    resp = templates.TemplateResponse(request, "_grid.html", {"items": shown, "counts": counts, "flag": flag,
                                                              "query": query, "stacked": stacked, "more": more,
                                                              "tags": tags.most_common(TOP_TAGS),
                                                              "sorts": SORTS, "sort": by})
    if q is not None or sort is not None:  # typing or sorting replaces the URL; filters push their own
        resp.headers["HX-Replace-Url"] = library_url()
    return resp


@app.get("/media/{media_id}", response_class=HTMLResponse)
def detail(request: Request, media_id: int):
    item = _item(media_id)
    item["skip_ai"], item["pick"] = skip_ai_reason(item), _pick_numbers().get(media_id)
    scene = [it for it in _items() if _scene(it) == _scene(item) and it["id"] != item["id"]]
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


PREVIEW_EDGE = 1024


@app.get("/preview/{media_id}")
def preview(media_id: int):
    """The photo at ~1024 px for the detail panel (the original is often several MB), made on first request."""
    item = _item(media_id)
    dest = db.library_dir(_root()) / "thumbs" / item["thumb"].replace(".jpg", "_preview.jpg")
    if not dest.exists():
        if item["kind"] != "image" or not Path(item["path"]).is_file():
            raise HTTPException(404)
        img = scan.read_image(Path(item["path"]))[0]  # EXIF-rotated
        img.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE))
        tmp = dest.with_suffix(f".{threading.get_ident()}.tmp")  # two requests at once must not mix their bytes
        img.save(tmp, "JPEG", quality=85)
        os.replace(tmp, dest)
    return FileResponse(dest)


@app.get("/original/{media_id}")
def original(media_id: int):
    path = _item(media_id)["path"]
    if not Path(path).is_file():  # deleted or drive unplugged since the scan
        raise HTTPException(404)
    if Path(path).suffix.lower() in scan.HEIF_EXT:  # only Safari shows HEIC: send a JPEG
        buf = io.BytesIO()
        scan.read_image(Path(path))[0].save(buf, "JPEG", quality=90)
        return Response(buf.getvalue(), media_type="image/jpeg")
    return FileResponse(path)


def _scene(it: dict) -> int:
    """Shots of the same scene share this id: the best shot's."""
    return it["duplicate_of"] or it["id"]


@app.post("/best/{media_id}", response_class=HTMLResponse)
def best_shot(request: Request, media_id: int):
    """Make a shot the best of its scene: only the best is sent to the AI and offered for the slideshow."""
    item = _item(media_id)
    if item["kind"] != "image" or "very_blurry" in item["flags"]:  # never part of a scene
        raise HTTPException(400)
    choose_best(db.connect(_root()), media_id)
    if request.headers.get("hx-target") == "detail":  # Library detail panel
        if not item["ai"]:  # skipped so far as a duplicate
            describing[media_id] = None
            threading.Thread(target=_describe_now, args=(_root(), media_id), daemon=True).start()
        resp = detail(request, media_id)
        resp.headers["HX-Trigger"] = "analysed"  # the stacks in the grid changed
        return resp
    scene = [it for it in _items() if _scene(it) == media_id]  # duplicate review during Analyse
    return templates.TemplateResponse(request, "_scene.html", {"scene": scene})


@app.get("/describing/{media_id}", response_class=HTMLResponse)
def describing_status(request: Request, media_id: int):
    """Polled by the detail panel while a shot is described right away; when done, the panel reloads."""
    if media_id in describing and describing[media_id] is None:
        return HTMLResponse(status_code=204)  # still running: htmx keeps the "Describing" line
    resp = detail(request, media_id)
    resp.headers.update({"HX-Retarget": "#detail", "HX-Reswap": "innerHTML"})
    return resp


@app.post("/slideshow/swap", response_class=HTMLResponse)
def swap(request: Request, id: int = Form(...), to: int = Form(...)):
    """Replace a pick by another shot of its scene, in the same place and with the same reason."""
    conn = db.connect(_root())
    a, b = db.get_media(conn, id), db.get_media(conn, to)
    if not a or not b or _scene(a) != _scene(b):
        raise HTTPException(400)
    slideshow.update(_root(), lambda s: {**s, "picks": slideshow.swap(s["picks"], id, to)})
    return _picks(request)


def _picks(request: Request, error: str | None = None) -> HTMLResponse:
    conn, selection = db.connect(_root()), slideshow.load(_root())
    seconds = slideshow.per_photo(selection)
    items = {it["id"]: it for it in db.all_media(conn)}
    scenes = defaultdict(list)  # best shot id -> every shot of that scene
    for it in items.values():
        scenes[_scene(it)].append(it["id"])
    picked = {p["id"] for p in selection["picks"]}
    picks = [{"item": it, "reason": p["reason"], "alts": [i for i in scenes[_scene(it)] if i not in picked]}
             for p in selection["picks"] if (it := items.get(p["id"]))]
    return templates.TemplateResponse(request, "_picks.html", {
        "picks": picks, "error": error, "seconds": seconds,
        "minutes": sum(slideshow.on_screen(p["item"], seconds) for p in picks) / 60,
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
def select(request: Request, description: str = Form(...), desc_size: str | None = Form(None)):
    root = _root()
    if desc_size is not None:  # the same choice as on the settings page, offered next to the description
        if desc_size not in db.SETTINGS["desc_size"][1]:
            raise HTTPException(400)
        db.save_setting("desc_size", desc_size)
    try:
        picks = slideshow.select(db.connect(root), description, seconds=slideshow.per_photo(slideshow.load(root)))
    except (Exception, SystemExit) as e:
        return _picks(request, error=str(e))
    slideshow.update(root, lambda s: {**s, "description": description, "picks": picks, "refinements": []})
    return _picks(request)


@app.post("/slideshow/refine", response_class=HTMLResponse)
def refine(request: Request, instruction: str = Form(...)):
    root = _root()
    selection = slideshow.load(root)
    try:
        picks = slideshow.select(db.connect(root), selection["description"], selection["picks"], instruction,
                                 slideshow.per_photo(selection))
    except (Exception, SystemExit) as e:
        return _picks(request, error=str(e))
    # refinements: kept for selection.json in the export
    slideshow.update(root, lambda s: {**s, "picks": picks, "refinements": [*s.get("refinements", []), instruction]})
    return _picks(request)


@app.post("/slideshow/seconds", response_class=HTMLResponse)
def seconds_per_photo(request: Request, seconds: int = Form(ge=2, le=15)):
    slideshow.update(_root(), lambda s: {**s, "seconds": seconds})
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
    if request.headers.get("hx-target") == "picks":  # × on the Slideshow page
        return _picks(request)
    # Library: the new toggle plus every card badge, as numbers shift after a removal
    return templates.TemplateResponse(request, "_toggle.html", {"id": media_id, "picks": _pick_numbers()})


@app.post("/slideshow/bulk", response_class=HTMLResponse)
def bulk(action: str = Form(...), ids: str = Form(...)):
    """Add or remove the cards selected in the Library (ids: comma-separated); the grid reloads for the badges."""
    try:
        wanted = [int(i) for i in ids.split(",")]
    except ValueError:
        raise HTTPException(400) from None
    if action not in ("add", "remove"):
        raise HTTPException(400)
    conn = db.connect(_root())
    known = [i for i in wanted if db.get_media(conn, i)]

    def change(s):
        for i in known:
            s = {**s, "picks": slideshow.edit(s["picks"], action, i)}
        return s

    slideshow.update(_root(), change)
    return HTMLResponse("", headers={"HX-Trigger": "analysed"})


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
