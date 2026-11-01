# Development

Plain Python ≥ 3.12, managed with uv. FastAPI + Jinja2 templates + HTMX, no JavaScript build step.
SQLite via the standard library. No torch or other local ML.

```bash
uv sync
uv run pytest                              # runs without an AI endpoint
uv run photos2show-testdata demo_images    # synthetic photos with known defects
uv run photos2show-scan demo_images
```

CI (`.github/workflows/tests.yml`) runs `uv run pytest` on Linux, macOS and Windows.

## Command line tools

| Command | What it does |
|---|---|
| `photos2show-web [--no-browser]` | the web UI on `127.0.0.1:$PHOTOS2SHOW_PORT` |
| `photos2show-scan FOLDER [--parallel N]` | both analysis passes (N = 1–16), then a table of metrics, flags and tags per file |
| `photos2show-smoke [IMAGE]` | lists the server's models and describes one image (a synthetic one without IMAGE) |
| `photos2show-testdata [FOLDER]` | writes synthetic test photos and a video with known flags (default `demo_images`) |

## Code layout

```
src/photos2show/
  config.py     # .env settings, AI_CONCURRENCY, data folder location
  db.py         # SQLite schema, projects.json, settings.json (SETTINGS), project stats, unused data
  scan.py       # file walk, supported formats, EXIF, RAW, video frames, dates, video length
  quality.py    # OpenCV metrics, thresholds, flags, technical score
  dedup.py      # pHash same-scene grouping, best of each group
  pipeline.py   # pass 1 analyse_folder(), pass 2 describe_folder(), choose_best(), ai_choose_best()
  ai.py         # OpenAI client, prompts, analyse_image(), pick_best(), complete_json(), context_length()
  slideshow.py  # selection prompt, validation, edits, sorting, undo, export
  cli.py        # photos2show-scan
  smoke.py      # photos2show-smoke
  testdata.py   # photos2show-testdata
  web/
    app.py      # FastAPI routes, background analysis job
    templates/  # pages (projects, library, slideshow, settings, docs) and _partials for HTMX swaps
    static/     # htmx.min.js (vendored), style.css
tests/          # test_<module>.py per area, test_web.py for the routes; conftest.py has the fixtures
docs/           # this documentation
```

## Web app

- **Single local user.** One analysis runs at a time in a background thread; its state is the `job`
  dict in `web/app.py` (current project and folder, phase, progress, last filter/search/sort). The
  current project lives in the server, not in the browser.
- **Pages** are full templates; everything else returns an HTMX partial (`_grid.html`, `_detail.html`,
  `_picks.html`, `_progress.html`, …). `HX-Refresh`, `HX-Redirect` and `HX-Trigger: analysed` tell the
  browser to reload, navigate or refresh the grid.
- **Security**: every non-GET request must carry the `HX-Request` header (blocks cross-site form posts),
  and only the hosts `127.0.0.1` and `localhost` are accepted (blocks DNS rebinding). *Open folder* opens
  only the stored export folder, never a path from the request, and *replace previous export* empties a
  folder only if it holds an earlier export.
- **Folder picker**: `/pick-folder` opens the system dialog on the server's computer (zenity or kdialog
  on Linux, else Tk in a subprocess), because a browser never reveals real paths.
- **Settings** are declared in `db.SETTINGS` (`key: (label, choices, default, help)`); a new line there
  appears on the Settings page automatically.
- **Documentation viewer**: `/docs/<page>` renders `docs/<page>.md` with markdown-it-py (CommonMark +
  tables, raw HTML shown as text). Links like `configuration.md#local-models` become
  `/docs/configuration#local-models`, and headings get GitHub-style ids so those anchors work. The page
  list on the left follows the links in the table of `docs/index.md`. `docs/` is read from the source
  checkout. FastAPI's own Swagger pages (`/docs`, `/redoc`) are turned off.

### Routes

| Route | Purpose |
|---|---|
| `GET /` | Library page; `flag`, `q`, `sort`, `photo` in the URL restore the view |
| `GET /grid` | grid partial with filters, search, stacks, sort |
| `GET /media/{id}` | detail panel |
| `GET /thumb/{id}[?frame=N]`, `/preview/{id}`, `/original/{id}` | thumbnail / video frame, 1024 px preview, original (HEIC/TIFF/RAW as JPEG); ids restart at 1 in every project, so the pages add `?p=<project key>` (ignored by the server) to keep browsers from showing another project's cached image |
| `GET /projects`, `POST /projects` | projects page; create a project and start analysing |
| `POST /projects/open`, `/rename`, `/folder`, `/remove` | switch, rename, change folder, remove |
| `GET /projects/cover?name=` | cover photo of a project |
| `POST /analyse`, `/analyse/stop`, `/analyse/continue`; `GET /progress` | start, stop, continue after the duplicate review; progress partial |
| `POST /best/{id}`, `/best/ai/{scene id}`; `GET /describing/{id}` | make a shot the best; AI compare during the review; poll a description started right away |
| `POST /separate/{id}`, `/separate/{id}?all=true` | review: keep a shot (or every shot of its scene) as its own picture |
| `GET /slideshow`, `/slideshow/picks` | slideshow page; picks partial |
| `POST /slideshow/select`, `/refine`, `/seconds`, `/sort`, `/move`, `/swap`, `/undo` | change the picks |
| `POST /slideshow/{add\|remove}/{id}`, `/slideshow/bulk` | add or remove one or several |
| `POST /slideshow/export`, `/slideshow/open-export` | export; open the export folder |
| `GET /settings`, `POST /settings`, `POST /settings/cleanup` | settings page; save one setting; delete unused data |
| `GET /docs`, `/docs/{page}` | the documentation (opened from Settings in a new tab) |
| `POST /pick-folder` | system folder dialog |

## Conventions

- **Never modify the user's photo folder.** Everything goes into the data folder or the export folder.
- JSON files are written atomically (`db.write_json`: temp file, then rename).
- LLM output is untrusted: tags, scores and ids are cleaned or validated where they are read
  (`db.row_to_dict`, `slideshow.number`, `slideshow.select`).
- One broken file or failed request never stops a run; errors are collected and shown.
- Keep it simple: no new dependency for a few lines, no JS build step.
- Add or update a test in `tests/` for behaviour changes.
- Changing quality or grouping thresholds only takes effect after a scan finds a new, changed or deleted
  file; to regroup an existing library run `UPDATE media SET flags = NULL` on its database.
- One primary (blue) button per page; destructive actions are red.

## Keeping these docs current

Every change to behaviour, UI labels, settings, file formats, routes or data files must update the
matching page in `docs/` in the same commit (and the README where it repeats the same information).
The app shows them at `/docs/<page>`, so they link only to each other with relative links like
`[Configuration](configuration.md)`, and a new page goes into the table in `docs/index.md`.

The design notes and the reasoning behind decisions live in `PLAN.md`.
