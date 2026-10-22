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
- Read EXIF (`DateTimeOriginal`, camera, GPS if present) with Pillow; apply EXIF orientation.
- Create thumbnails: ~400px for the UI, ~1024px long edge for the LLM (keeps token use low).
- Videos: OpenCV `VideoCapture` grabs frames at 10 % / 50 % / 90 % of duration; the sharpest
  frame represents the video. Date: MP4 metadata if easily readable, else file mtime.
- Cache key: path + size + mtime.

### 2. Classic quality (OpenCV + NumPy, no AI)
| Metric | Method | Flags |
|---|---|---|
| Sharpness | Variance of Laplacian on a 3×3 tile grid of a fixed-size grayscale image; use max tile (bokeh-safe) and mean | `blurry` |
| Exposure | Mean brightness, % clipped pixels at 0 / 255 | `too_dark`, `too_bright` |
| Contrast | Std. dev. of luminance | `low_contrast` |
| Colourfulness | Hasler–Süsstrunk metric | – |
| Monotony | Shannon entropy of luminance + edge density (Canny) | `monotone` |

Two blur levels: `blurry` (shown as warning) and `very_blurry` (excluded, never sent to the LLM).
M1 uses absolute thresholds tuned on generated test images. Because sharpness/noise vary by
camera, M4 calibrates them on real photos (possibly folder-relative percentiles).

### 3. Duplicates / bursts
- Perceptual hash (`imagehash.phash`) + capture time: images within a few seconds and small
  Hamming distance form a cluster.
- Best of cluster = highest combined quality score; others flagged `duplicate_of`.

### 4. AI analysis (GLM-5.3 Flash, per image)
- Send the 1024px thumbnail (base64 `image_url`) with a fixed prompt; request JSON:
  ```json
  {"caption": "...", "tags": ["beach", "sunset", "people"], "scene": "landscape|people|food|city|...",
   "people_count": 2, "aesthetic": 1-10, "interest": 1-10, "issues": ["closed eyes", "tilted horizon"]}
  ```
- Use `response_format={"type": "json_object"}` if the server supports it; otherwise parse
  leniently and retry once.
- Async with a concurrency limit (configurable, default 4); results cached.
- **Save tokens – only send what is worth analysing:**
  - Very blurry images (`very_blurry` flag) are never sent to the LLM.
  - From several images of the same scene (duplicate/burst cluster) only the best one is sent;
    the others inherit its caption/tags.

### 5. Slideshow selection
1. User enters a description, e.g. *"10-minute show for my parents, ~80 photos, chronological,
   mostly landscapes and the two of us, little food"*.
2. Hard filter: drop `blurry`/`too_dark`/duplicate losers (toggle in UI to include anyway).
3. Send GLM one prompt: the description + a compact line per candidate
   (`id | date | scene | tags | caption | aesthetic | interest | quality`), ~60 tokens each –
   a few hundred images fit easily.
4. GLM returns JSON: target count, ordered list of ids, short reason per pick.
5. Validate (ids exist, no duplicates, max one per burst cluster, count within ±10 %);
   fallback: score-based ranking with per-day quota if the response is unusable.
6. Default order chronological unless the description says otherwise.

### 6. Review UI
Local web app (FastAPI + Jinja2 templates + HTMX, no JS build step):
- **Library view**: enter folder path → "Analyse" with progress bar → thumbnail grid with
  badges (score, flags, tags, video icon); filter by flag/tag; click for detail
  (caption, metrics, frames for videos).
- **Slideshow view**: prompt box → "Select" → ordered grid of picks with reasons; click to
  toggle in/out, drag or up/down to reorder; "Export".

### 7. Export
- Copy to `<output>/<slideshow-name>/` as `001_2026-07-14_beach-sunset.jpg` (videos copied as
  `.mp4` in their slot). Never move or modify originals.
- Write `selection.json` (prompt, ids, reasons) next to it for reproducibility.

## Tech stack
- Python ≥ 3.12 managed with `uv` (no torch needed, so 3.14 works)
- `pillow`, `opencv-python-headless`, `numpy`, `imagehash`
- `openai` (async client, custom `base_url`)
- `fastapi`, `uvicorn`, `jinja2`, HTMX (vendored static file)
- SQLite via stdlib `sqlite3`
- `pytest` for tests
- Config via `.env`: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL=glm-5.3-flash`, `AI_CONCURRENCY`

## Project layout
```
pyproject.toml
src/imgsort/
  config.py     # settings from .env
  db.py         # SQLite schema + cache
  scan.py       # file walk, EXIF, thumbnails, video frames
  quality.py    # classic metrics + flags
  dedup.py      # phash clustering
  ai.py         # GLM client: analyse_image(), select()
  select.py     # filtering, validation, fallback ranking
  export.py     # numbered copies + selection.json
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
- **M3 – Selection, review, export**: prompt → picks → toggle/reorder → sorted folder.
- **M4 – Calibration**: rate ~100 own photos (keep/reject), tune thresholds and the combined
  score; small regression test set.

## Future features
- Face detection / clustering ("everyone appears at least once")
- Local CLIP embeddings for fast semantic search and better duplicate grouping
- Learned IQA models (pyiqa: MUSIQ/TOPIQ/NIMA) if GLM aesthetic scores prove noisy
- GLM pairwise "which is better" for burst clusters
- Deeper video handling (scene detection, picking clips)
- HEIC / RAW support
- Map / location grouping from GPS

## Risks / open points
- Vision support, image size limits and JSON mode of the self-hosted GLM endpoint → verified in M0.
- Throughput: several hundred images × per-image latency; mitigated by caching and concurrency.
- LLM aesthetic scores may be poorly calibrated → combine with classic metrics, calibrate in M4.
