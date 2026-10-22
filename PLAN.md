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
| Input | Several hundred JPGs + some MP4s (videos: a few sampled frames only) |
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
- Walk folder recursively for `.jpg/.jpeg` and `.mp4`.
- Read EXIF `DateTimeOriginal` with Pillow; apply EXIF orientation (camera/GPS not read yet).
- Create thumbnails: ~400px for the UI, ~1024px long edge for the LLM (keeps token use low).
- Videos: OpenCV `VideoCapture` grabs frames at 10 % / 50 % / 90 % of duration; the sharpest
  frame represents the video. Date: from the phone-style file name (`YYYYMMDD_HHMMSS`), else
  file mtime (mtime was 1 h off for copied Norway videos).
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

### 4. AI analysis (GLM-5.3 Flash, per image)
- Send the 1024px thumbnail (base64 `image_url`) with a fixed prompt; request JSON:
  ```json
  {"caption": "...", "tags": ["beach", "sunset", "people"], "scene": "landscape|people|food|city|...",
   "people_count": 2, "aesthetic": 1-10, "interest": 1-10, "issues": ["closed eyes", "tilted horizon"]}
  ```
- Use `response_format={"type": "json_object"}` if the server supports it; otherwise parse
  leniently and retry once.
- **Two passes:** pass 1 (steps 1–3, local, fast) runs over all files first, because the best
  image of a scene is only known once its neighbours are scanned. Pass 2 then sends the remaining
  images to the LLM in small parallel batches (`AI_CONCURRENCY`, default 2, selectable in the UI and CLI; 4 was 2.4× faster than 1 in the test); each result is saved
  immediately, shows up live in the UI, and an interrupted run resumes where it stopped.
- **Save tokens – only send what is worth analysing:**
  - Very blurry images (`very_blurry` flag) are never sent to the LLM.
  - From several images of the same scene (duplicate/burst cluster) only the best one is sent.

### 5. Slideshow selection
1. User enters a description, e.g. *"10-minute show for my parents, ~80 photos, chronological,
   mostly landscapes and the two of us, little food"*.
2. Candidates: every described, non-duplicate item. Flagged ones stay in, with their flags in
   the prompt; GLM is told to avoid them (no separate hard filter).
3. Send GLM one prompt: the description + a compact line per candidate
   (`id | date | kind | scene | tags | caption | aesthetic | interest | flags`), ~70 tokens each
   (110 candidates ≈ 7.8k tokens, ~850 ≈ 60k).
4. GLM returns JSON: ordered list of ids with a short reason per pick (count from the description,
   ~5 s per photo).
5. Validate: unknown, repeated or malformed ids are dropped; only described, non-duplicate items.
   No score-based fallback – on a bad answer the UI shows the error and you press *Select* again.
6. Default order chronological unless the description says otherwise.
7. One selection per project (`selection.json` in the project cache); editing by hand in the UI.

### 6. Review UI
Local web app (FastAPI + Jinja2 templates + HTMX, no JS build step):
- **Projects**: one per holiday (name → photo folder, stored in `projects.json` in the cache);
  dropdown in the header to switch, "New project" to create and analyse.
- **Library view**: progress bar → thumbnail grid with badges (score, flags, tags, video icon);
  filter by flag/tag; click for detail (caption, metrics, frames for videos, "Add to slideshow").
- **Slideshow view**: prompt box → "Select" → ordered list of picks with reasons; up/down to
  reorder, × to remove; "Export".

### 7. Export
- Copy to a new, empty folder outside the photo folder (default `<photo folder>_slideshow`) as
  `001_2026-07-14_beach-sunset.jpg` (videos copied as `.mp4` in their slot). Never move or
  modify originals.
- Write `selection.json` (prompt, ids, reasons) next to it for reproducibility.

## Tech stack
- Python ≥ 3.12 managed with `uv` (no torch needed, so 3.14 works)
- `pillow`, `opencv-python-headless`, `numpy`, `imagehash`
- `openai` (client with custom `base_url`, thread pool for concurrency)
- `fastapi`, `uvicorn`, `jinja2`, HTMX (vendored static file)
- SQLite via stdlib `sqlite3`
- `pytest` for tests
- Config via `.env`: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL` (`prod` on the BLUM server),
  `AI_CONCURRENCY`, `IMGSORT_CACHE` (data folder, default `~/.local/share/imgsort`)

## Project layout
```
pyproject.toml
src/imgsort/
  config.py     # settings from .env
  db.py         # SQLite schema + cache, projects
  scan.py       # file walk, EXIF, video frames
  quality.py    # classic metrics + flags
  dedup.py      # phash clustering
  pipeline.py   # pass 1 (scan, thumbnails, flags) and pass 2 (LLM descriptions)
  ai.py         # GLM client: analyse_image(), complete_json()
  slideshow.py  # LLM selection, validation, editing, export
  cli.py, smoke.py, testdata.py
  web/
    app.py      # FastAPI routes, background analysis job
    templates/  # library.html, slideshow.html, partials
    static/     # htmx.min.js, style.css
tests/
```

## Milestones
- **M0 – Skeleton & API smoke test**: `uv` project, config, a script that sends one image to
  the GLM instance and prints JSON. Confirms vision + JSON mode work on the self-hosted
  instance and measures seconds/image.
- **M1 – Scan, quality, dedup + Library UI**: grid with metric badges and flags, no AI yet.
- **M2 – AI analysis**: captions/tags/scores in DB and UI, filtering by tag.
- **M3 – Selection, review, export**: prompt → picks → toggle/reorder → sorted folder;
  holiday projects. *(M0–M3 done)*
- **M4 – Calibration**: rate ~100 own photos (keep/reject), tune thresholds and the combined
  score; small regression test set. *(done: 848 Norway photos reviewed via contact sheets plus
  user spot-check → `BLURRY` 200, same-scene = same orientation and phash ≤14 within 60 s or
  ≤18 within 10 s; motion blur with one sharp tile is a known gap)*

## Future features
- Face detection / clustering ("everyone appears at least once")
- Local CLIP embeddings for fast semantic search and better duplicate grouping
- Learned IQA models (pyiqa: MUSIQ/TOPIQ/NIMA) if GLM aesthetic scores prove noisy
- GLM pairwise "which is better" for burst clusters
- Deeper video handling (scene detection, picking clips)
- HEIC / RAW support
- Map / location grouping from GPS
- Several saved slideshows per project; projects spanning several folders; drag & drop reordering

## Risks / open points
Reviewed with the user on 2026-10-23.

| Risk | Status / decision |
|---|---|
| Endpoint: vision, JSON mode, image size | Resolved – works; JSON mode supported (1 request per image) |
| Throughput | Resolved – ~30–70 min per ~900-file holiday once, then cached; parallel requests selectable |
| Selection on a full holiday (~850 candidates, ~60k-token prompt) Resolved – tested on all 857 Norway files: 104 good picks in 294 s, well under the 10-min client timeout |
| Video dates from mtime off by 1 h | Fixed – date from file name, mtime fallback |
| AI scores compressed (mostly 6–8) | Accepted – selection works via captions/tags; prompt unchanged |
| AI results lost if a cache cleaner runs | Fixed – data moved from `~/.cache` to `~/.local/share/imgsort` |
| No backup of the code | **Open** – user pushes to GitHub (`git push origin main`) |
| Very large folders (5,000+): pairwise dedup is O(n²) | Accepted – minutes at worst, fine for holidays |
| "Photos of us" without face recognition | Accepted – people count + captions; faces are a future feature |
| Motion blur with one sharp tile not detected | Accepted – GLM usually lists it under issues |
