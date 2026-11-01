# AI Image Sorting – Plan

Goal: drop a folder of holiday photos and videos, get automatic quality checks and content
tags, then describe the slideshow you want in plain words and get a sorted folder with the
selected files.

## Working agreement
Whenever there is doubt about how to continue (unclear requirement, design trade-off, unexpected
result, deviation from this plan), stop and come back to ask before proceeding – don't guess.

## Decisions

| Topic | Decision |
|---|---|
| AI model | Self-hosted **GLM-5.3 Flash** via OpenAI-compatible API (natively multimodal: image input, ~1M context) |
| Input | Several hundred photos + some videos in the common formats incl. camera RAW (videos: a few sampled frames only) |
| Output | Sorted folder with copied, numbered files (originals untouched) |
| UI | Simple local web UI from the start |
| Faces / people | Not in scope – possible future feature |
| Local ML (CLIP, pyiqa, torch) | Not in MVP – GLM covers semantics/aesthetics; classic CV covers technical quality |

## Pipeline

```
folder ─► 1 Scan ─► 2 Quality ─► 3 Dedup ─► 4 AI analysis ─► 5 Select ─► 6 Review ─► 7 Export
          EXIF,      OpenCV        pHash +     GLM: caption,     GLM picks     UI: toggle    numbered
          thumbs,    metrics       time gap    tags, scores      from prompt   in/out        copies
          video
          frames
                 └──────────── all results cached in SQLite (re-runs only process new files) ─┘
```

### 1. Scan
- Walk folder recursively (hidden folders skipped) for photos: JPEG, PNG, WebP, AVIF, GIF (first
  frame), BMP, TIFF, HEIC/HEIF (via `pillow-heif`) and camera RAW (DNG, CR2, CR3, NEF, NRW, ARW, ORF,
  RW2, RAF, PEF, SRW via `rawpy`/LibRaw); videos: MP4, MOV, M4V, 3GP, AVI, MKV, WebM, MTS/M2TS, WMV,
  MPG. A folder without any of them shows "No photos or videos found" instead of an empty result.
- Browsers can't show HEIC, TIFF or RAW, so `/original` sends those as a JPEG. Browsers play only
  some video formats (MP4/WebM everywhere, MOV/MKV mostly); the others still get analysed, the
  detail panel shows their sampled frames and the slideshow preview skips them.
- RAW: developed by LibRaw at half size (camera white balance, rotation applied; ~0.25 s per photo)
  for analysis, previews and the AI; date from its EXIF via LibRaw.
- Read EXIF `DateTimeOriginal` with Pillow; apply EXIF orientation (camera/GPS not read yet). Photos
  without an EXIF date (screenshots, most PNGs) are dated like videos (below); already scanned
  undated photos get that date on the next scan without a re-analysis.
- Thumbnails: ~400 px for the grid, saved in the cache. The ~1024 px image for the LLM is only
  encoded in memory; a ~1024 px preview for the detail panel is made on first view and cached.
  A changed or deleted file loses its thumbnail, video frames and preview.
- Videos: OpenCV `VideoCapture` grabs frames at 10 % / 50 % / 90 % of duration; the sharpest
  frame represents the video. Date: from the phone-style file name (`YYYYMMDD_HHMMSS`), else
  the MP4 movie header `creation_time` (MP4, MOV, M4V, 3GP; UTC, shown in local time), else the
  earlier of file creation and modification time: copying resets creation (all Norway photos and
  screenshots showed the copy day) while editing resets mtime; mtime was 1 h off for copied Norway
  videos. Linux: creation time via GNU `stat -c %W` (Python < 3.15 can't read it there). The name goes first: it is the phone's local time like the photo
  EXIF, while the header is absolute and differed by 1 h for 2 Norway clips. Cached videos are
  re-dated on every scan without a full re-analysis. Video length (for the slideshow) is read
  when needed and cached by path + mtime.
- Cache key: path + size + mtime.

### 2. Classic quality (OpenCV + NumPy, no AI)
| Metric | Method | Flags |
|---|---|---|
| Sharpness | Variance of Laplacian on a 3×3 tile grid of a fixed-size grayscale image; use max tile (bokeh-safe) and mean | `blurry` |
| Exposure | Mean brightness, % clipped pixels at 0 / 255 | `too_dark`, `too_bright` |
| Contrast | Std. dev. of luminance | `low_contrast` |
| Colourfulness | Hasler–Süsstrunk metric | – |
| Monotony | Edge density (Canny) + low contrast (entropy only shown) | `monotone` |

Two blur levels: `blurry` (shown as warning) and `very_blurry` (excluded, never sent to the LLM).
Blur is only judged with enough contrast (dark/washed-out images get exposure flags instead).
Absolute thresholds, calibrated in M4 on 848 real photos (`BLURRY` 200, `VERY_BLURRY` 20).

### 3. Duplicates / bursts
- Perceptual hash (`imagehash.phash`) + capture time: same orientation and Hamming distance ≤14
  within 60 s or ≤18 within 10 s (or ≤2 at any time) form a cluster. Only exact repeats count;
  reframed shots of the same place stay separate (user spot-check in M4).
- Best of cluster = highest technical score (sharpness, exposure); others flagged `duplicate_of`.
- AI compare, only when the user asks for it in the duplicate review (*Let the AI compare* per group, or
  *… all*, run one at a time): the top 6 shots by technical score go to GLM in one request (768 px each,
  "which is best?"). The winner gets `chosen = 2` (user picks are 1) and is labelled *AI pick* in the review
  and the detail panel; clicking another shot overrules it, and a later run never compares again on its own.
  If the request fails, the error shows under the group and the best stays as it was.
- The user can override it (`chosen` column, kept across regrouping): *Make this the best shot* in the
  detail panel describes the new best right away; with *Review duplicates before AI* on (default), Analyse pauses
  after the scan and shows every group without a description so the best can be picked before the AI pass.

### 4. AI analysis (GLM-5.3 Flash, per image)
- Send the 1024px image (base64 `image_url`) with a fixed prompt; request JSON:
  ```json
  {"caption": "...", "tags": ["beach", "sunset", "people"], "scene": "landscape|people|food|city|...",
   "people_count": 2, "aesthetic": 1-10, "interest": 1-10, "issues": ["closed eyes", "tilted horizon"]}
  ```
- Use `response_format={"type": "json_object"}`; if the server rejects JSON mode, the request is
  sent again without it and the answer is parsed leniently. An unusable answer (not a JSON object)
  counts as failed and is retried on the next run.
- **Two passes:** pass 1 (steps 1–3, local, fast) runs over all files first, because the best
  image of a scene is only known once its neighbours are scanned. Pass 2 then sends the remaining
  images to the LLM in small parallel batches (Settings → *Parallel AI requests*, default from
  `AI_CONCURRENCY` = 2; CLI `--parallel`; 4 was 2.4× faster than 1 in the test). Each result is
  saved immediately and an interrupted run resumes where it stopped.
  *Stop* lets running requests finish (saved) and skips the queued ones; a stopped scan ends
  early and does not count as "last analysed". Time left = speed of the current phase so far.
- **Save tokens – only send what is worth analysing:**
  - Very blurry images (`very_blurry` flag) are never sent to the LLM.
  - From several images of the same scene (duplicate/burst cluster) only the best one is sent.

### 5. Slideshow selection
1. User enters a description, e.g. *"10-minute show for my parents, ~80 photos, chronological,
   mostly landscapes and the two of us, little food"*.
2. Candidates: every described, non-duplicate item. Flagged ones stay in, with their flags in
   the prompt; GLM is told to avoid them (no separate hard filter).
3. Send GLM one prompt: the description + a compact line per candidate
   (`id | date | kind | scene | tags | caption | aesthetic | interest | flags`, videos as
   `video 12s`), ~70 tokens each (110 candidates ≈ 7.8k tokens, ~850 ≈ 60k). *Photo details* (Settings
   and next to *Select photos*): full; short = caption cut to 8 words and 5 tags (~35); minimal = no caption,
   3 tags (~20); auto = the most detailed list that fills at most half the model's context
   (from `/models`: vLLM `max_model_len`, `context_length`; unknown → full. Not llama.cpp's
   `meta.n_ctx_train`: the training size, often far above what the server runs with).
4. GLM returns JSON: ordered list of ids with a short reason per pick (count from the description,
   at the chosen seconds per photo, 2–15 s, default 5; videos run their full length).
5. Validate: unknown, repeated or malformed ids are dropped; only described, non-duplicate items.
   No score-based fallback – on a bad answer the UI shows the error and you press *Select* again.
6. Default order chronological unless the description says otherwise.
7. One selection per project (`selection.json` in the project cache: description, picks,
   refinements, seconds per photo, last export folder); editing by hand in the UI. All edits run
   under one lock, so parallel requests, a rescan or an export never drop a change.
8. *Refine*: an instruction plus the current picks in order (id | reason) is appended to the same
   prompt; the LLM returns the complete new list. Current picks stay valid even if added by hand
   (duplicates, undescribed); a pick without a new reason keeps its old one. Instructions are kept in
   `refinements`. Every change of the picks (select, refine, sort, move, add, remove, bulk) saves
   the list before it to `selection.prev.json` for one step of *Undo*.

### 6. Review UI
Local web app (FastAPI + Jinja2 templates + HTMX, no JS build step). One primary (blue) button
per page; destructive actions red.
- **Projects**: one per holiday (name → photo folder, stored in `projects.json` in the cache);
  dropdown in the header to switch; the *Projects* page has "New project" to create and analyse
  (names must be unique) and shows each as a card: cover (the photo the AI liked best), name,
  state (files, described, picks, last analysed, folder missing) and *Open*; rename and change
  folder and *Remove project…* behind *Edit*. Remove asks in a dialog; it keeps the cached AI results (they are keyed
  by folder, not by project name) unless "also delete saved AI results" is ticked there and no
  other project uses that folder.
- **Library**: progress bar with *Stop* and time left; during the AI pass the grid stays still and
  a "Show N new results" button reloads it (automatic reload only when a phase starts and ends).
  Grid cards: date, 1–5 stars from aesthetic + interest, caption, flags, "✓ 3" pick number, video
  icon. Same-scene shots are stacked (best shot with "+N"; *Show all shots* toggle). Filters by
  flag, "in slideshow", tag list (30 most common) and search (every word in caption, tags, scene,
  issues or file name); sort by date, scores, quality or name. Filter, search, sort and open photo
  are in the URL (reload, back/forward, bookmarks; typing replaces the URL instead of adding history,
  a bare `/` reopens the last view). Detail panel: 1024 px preview (click → original), caption,
  metrics, frames for videos, "Same scene" strip, *Add* / *Remove from slideshow*. Keys: Tab to a card, Enter/Space opens it, ← → step
  through the grid, A adds/removes, Esc closes. Ctrl/⌘-click and Shift-click select cards for a
  bulk add/remove bar.
- **Slideshow**: two step cards. ① description → *Select photos* (spinner, elapsed time, picks
  locked meanwhile); collapses to one line with *Edit* once there are picks. ② ordered tiles with
  reasons under a sticky toolbar (count and length, seconds per photo, sort, *Undo*, *Export…*
  dialog with *Open folder*, *▶ Play*) and *Refine picks*; drag & drop or ‹ › to reorder, × to
  remove, "⇄ N other shots" to swap a pick for another shot of its scene (same place and reason);
  lightbox with ← → through the picks; *▶ Play* fills the window, with the reason and a control
  panel (back, hold, forward, close) on mouse move or tap; keys ← → Space Esc.
- **Settings**: theme (auto / light / dark / grey), accent colour, parallel AI requests, review duplicates before AI, photo details for choosing
  (each with a help line); delete cached data of folders
  no project uses; *Open documentation* opens `docs/` rendered as pages in a new tab (`/docs/<page>`).

### 7. Export
- Copy to a new, empty folder outside the photo folder (default: the last export folder, else
  `<photo folder>_slideshow`) as
  `001_2026-07-14_beach-sunset.jpg` (other files copied as they are, e.g. `.mp4`, `.heic`, in their
  slot). RAW photos are developed to a full-size JPEG (quality 92), since TVs and slideshow apps
  can't show RAW. Never move or modify originals.
- Write `selection.json` (prompt, ids, reasons) next to it for reproducibility.
- *Replace previous export*: a non-empty folder is emptied first, but only if it holds a
  `selection.json` and otherwise just `NNN_…` files; anything else is refused.
- *Open folder* opens the last export folder (taken from the selection, never from the request)
  with `xdg-open` / `open` / `os.startfile`.

## Tech stack
- Python ≥ 3.12 managed with `uv` (no torch needed, so 3.14 works)
- `pillow`, `pillow-heif` (HEIC), `rawpy` (camera RAW via LibRaw), `opencv-python-headless`, `numpy`, `imagehash`
- `openai` (client with custom `base_url`, thread pool for concurrency)
- `fastapi`, `uvicorn`, `jinja2`, HTMX (vendored static file), `markdown-it-py` (docs viewer)
- SQLite via stdlib `sqlite3`
- `pytest` for tests
- Config via `.env`: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL` (`prod` on the BLUM server),
  `AI_CONCURRENCY`, `PHOTOS2SHOW_CACHE` (data folder, default `~/.local/share/photos2show`)

## Project layout
```
pyproject.toml
src/photos2show/
  config.py     # settings from .env
  db.py         # SQLite schema + cache, projects, app settings
  scan.py       # file walk, EXIF, RAW developing, video frames, dates and length
  quality.py    # classic metrics + flags
  dedup.py      # phash clustering
  pipeline.py   # pass 1 (scan, thumbnails, flags) and pass 2 (LLM descriptions)
  ai.py         # GLM client: analyse_image(), complete_json()
  slideshow.py  # LLM selection, validation, editing, export
  cli.py, smoke.py, testdata.py
  web/
    app.py      # FastAPI routes, background analysis job
    templates/  # projects, library, slideshow, settings pages + partials
    static/     # htmx.min.js, style.css
tests/
docs/           # documentation (user guide, configuration, internals); kept in sync with every change
CLAUDE.md       # notes for Claude: update docs/ with every change
```

## Future features ideas
- Face detection / clustering ("everyone appears at least once")
- Local CLIP embeddings for fast semantic search and better duplicate grouping
- Learned IQA models (pyiqa: MUSIQ/TOPIQ/NIMA) if GLM aesthetic scores prove noisy
- Deeper video handling (scene detection, picking clips)
- Map / location grouping from GPS
- Several saved slideshows per project; projects spanning several folders

## Risks / open points
Reviewed with the user on 2026-10-23; resolved ones removed 2026-11-01.

| Risk | Status / decision |
|---|---|
| AI scores compressed (mostly 6–8) | Accepted – selection works via captions/tags; prompt unchanged |
| Very large folders (5,000+): pairwise dedup is O(n²) | Accepted – minutes at worst, fine for holidays |
| "Photos of us" without face recognition | Accepted – people count + captions; faces are a future feature |
| Motion blur with one sharp tile not detected | Accepted – GLM usually lists it under issues |
