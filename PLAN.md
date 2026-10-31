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
  the MP4 movie header `creation_time` (UTC, shown in local time), else file mtime (mtime was 1 h
  off for copied Norway videos). The name goes first: it is the phone's local time like the photo
  EXIF, while the header is absolute and differed by 1 h for 2 Norway clips. Cached videos are
  re-dated on every scan without a full re-analysis.
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
- Use `response_format={"type": "json_object"}`; if the server rejects JSON mode, the request is
  sent again without it and the answer is parsed leniently. An unusable answer (not a JSON object)
  counts as failed and is retried on the next run.
- **Two passes:** pass 1 (steps 1–3, local, fast) runs over all files first, because the best
  image of a scene is only known once its neighbours are scanned. Pass 2 then sends the remaining
  images to the LLM in small parallel batches (`AI_CONCURRENCY`, default 2, selectable in the UI and CLI; 4 was 2.4× faster than 1 in the test); each result is saved
  immediately, shows up live in the UI, and an interrupted run resumes where it stopped.
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
   (`id | date | kind | scene | tags | caption | aesthetic | interest | flags`), ~70 tokens each
   (110 candidates ≈ 7.8k tokens, ~850 ≈ 60k).
4. GLM returns JSON: ordered list of ids with a short reason per pick (count from the description,
   ~5 s per photo).
5. Validate: unknown, repeated or malformed ids are dropped; only described, non-duplicate items.
   No score-based fallback – on a bad answer the UI shows the error and you press *Select* again.
6. Default order chronological unless the description says otherwise.
7. One selection per project (`selection.json` in the project cache); editing by hand in the UI.
8. *Refine*: an instruction plus the current picks in order (id | reason) is appended to the same
   prompt; the LLM returns the complete new list. Current picks stay valid even if added by hand
   (duplicates, undescribed); a pick without a new reason keeps its old one. Instructions are kept in
   `refinements`. Every change of the picks (select, refine, sort, move, add, remove) saves the list
   before it to `selection.prev.json` for one step of *Undo*.

### 6. Review UI
Local web app (FastAPI + Jinja2 templates + HTMX, no JS build step):
- **Projects**: one per holiday (name → photo folder, stored in `projects.json` in the cache);
  dropdown in the header to switch; the *Projects* page has "New project" to create and analyse and lists
  them with their state (files, described, picks, last analysed, folder missing) to rename, change the folder or remove.
  Removing keeps the cached AI results (they are keyed by folder, not by project name) unless
  "also delete saved AI results" is ticked and no other project uses that folder.
- **Library view**: progress bar → thumbnail grid with badges (score, flags, tags, video icon);
  filter by flag/tag and search (every word in caption, tags, scene, issues or file name); click for
  detail (caption, metrics, frames for videos, "Add to slideshow"); keys ← → step through the grid
  order, A adds, Esc closes.
- **Slideshow view**: prompt box → "Select" → ordered tiles of picks with reasons; "Refine picks"
  with an instruction, "Undo"; drag & drop to reorder, × to remove; "Export", "Open folder".

### 7. Export
- Copy to a new, empty folder outside the photo folder (default: the last export folder, else
  `<photo folder>_slideshow`) as
  `001_2026-07-14_beach-sunset.jpg` (videos copied as `.mp4` in their slot). Never move or
  modify originals.
- Write `selection.json` (prompt, ids, reasons) next to it for reproducibility.
- *Replace previous export*: a non-empty folder is emptied first, but only if it holds a
  `selection.json` and otherwise just `NNN_…` files; anything else is refused.
- *Open folder* opens the last export folder (taken from the selection, never from the request)
  with `xdg-open` / `open` / `os.startfile`.

## Tech stack
- Python ≥ 3.12 managed with `uv` (no torch needed, so 3.14 works)
- `pillow`, `opencv-python-headless`, `numpy`, `imagehash`
- `openai` (client with custom `base_url`, thread pool for concurrency)
- `fastapi`, `uvicorn`, `jinja2`, HTMX (vendored static file)
- SQLite via stdlib `sqlite3`
- `pytest` for tests
- Config via `.env`: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL` (`prod` on the BLUM server),
  `AI_CONCURRENCY`, `PHOTOS2SHOW_CACHE` (data folder, default `~/.local/share/photos2show`)

## Project layout
```
pyproject.toml
src/photos2show/
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
    templates/  # library.html, slideshow.html, settings.html, partials
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
- Several saved slideshows per project; projects spanning several folders

### Usability (suggested 2026-10-31; stop/time left, export again, refine/undo, search, preview and keyboard done)
- Seconds per photo selectable; length estimate counts real video durations – **done 2026-11-01** (see 12)
- Swap a pick for another shot of the same scene, right on the slideshow tile

### UI improvements (suggested 2026-11-01, not yet prioritised)
From a review of all templates, `style.css` and the grid/detail routes. Ordered by expected value;
the first four are the suggested starting point. Items marked *(see Usability)* expand an entry above.
All of them stay within the current stack (Jinja2 + HTMX + a little inline JS, no build step).

#### Biggest wins
1. **Slideshow "Play" preview** *(see Usability)* – **done 2026-11-01**
   - Built: *▶ Play*, full screen, ← / → / Space / Esc, videos to their end, "12 / 104", next photo
     preloaded. Left out: the reason on hover, and the speed is still the fixed 5 s (see 12).
   - Today: the only way to see the show as a show is to export it and open it in another app.
   - Proposal: a *Play* button on the Slideshow page opens a fullscreen player that steps through
     the picks in order at the chosen seconds per photo (see 12). Videos play to their end (or a cap),
     then the next pick follows. Keys: ← / → previous/next, Space pause/resume, Esc closes.
     Shows "12 / 104" and the pick's reason on hover/tap.
   - How: reuse the existing `<dialog id="lightbox">` in `slideshow.html`; the picks list is
     already in the DOM (`.picks li[data-id]`, `img[data-full]`), so the player needs no new route.
     Use `requestFullscreen()` on the dialog; preload the next image.
   - Done when: the full show can be watched from the first to the last pick without leaving the app.

2. **Keyboard navigation in the Library** *(see Usability)* – **done 2026-11-01**
   - Built: ← / → through the current filter and search, `a` adds, Esc closes, active card
     highlighted and scrolled into view. Left out: cards are not Tab-focusable; `a` does not remove (see 3).
   - Today: every photo needs a mouse click; `.card` is an `<article>` with `hx-get` and no
     `tabindex`, so the grid can't be reached with the keyboard at all.
   - Proposal: ← / → load the previous/next card of the *current filter* into the detail panel,
     Esc closes it, `a` adds/removes the photo from the slideshow (see 3). The active card gets a
     visible focus ring and is scrolled into view.
   - How: make cards focusable (`tabindex="0"`, `role="button"`, Enter/Space open them) or render
     them as `<button>`s; a small document-level `keydown` handler finds the active card's
     sibling and triggers its `hx-get`. Ignore keys while focus is in an input/select.
   - Done when: a whole filtered set can be reviewed and curated with the keyboard only.

3. **Show what is already in the slideshow** – **done 2026-11-01**
   - Built: "✓ 3" badge (pick number) on cards, *Add* / *Remove from slideshow* toggle in the detail
     panel (also on `a`) that renumbers every badge, filter "in slideshow".
   - Today: Library cards don't show if a photo is a pick, and the detail panel always offers
     "Add to slideshow", even for photos already picked.
   - Proposal: a ✓ badge (with the pick number) on cards that are in the slideshow; in the detail
     panel an *Add* / *Remove from slideshow* toggle that updates the card badge too.
     Optional filter "in slideshow".
   - How: `/grid` and `/media/{id}` read the current selection ids once and pass a set to the
     template; the toggle posts to the existing `/slideshow/add|remove/{id}` and returns the new
     button plus an out-of-band swap (`hx-swap-oob`) for the card badge.
   - Done when: you can curate the show from the Library without switching to the Slideshow tab to check.

4. **Stop the grid jumping during AI analysis** – **done 2026-11-01 (option b)**
   - Built: the grid reloads when a phase starts and once at the end; in between the progress bar
     shows "Show N new results".
   - Today: `/progress` sends `HX-Trigger: analysed` every 2 s while the AI runs, and the whole
     `#grid` (filters + up to ~1000 cards) is re-rendered each time. That is heavy and can move
     the scroll position, change the hovered card and reset what you were looking at.
   - Proposal (pick one):
     a) incremental: `/grid?since=<last id or timestamp>` returns only cards whose AI result
        changed, swapped in by id (`hx-swap-oob` per card) plus updated filter counts; or
     b) opt-in: a small "23 new results – refresh" pill instead of the automatic swap; the grid
        reloads only when clicked and once when the run ends.
   - b) is simpler and enough; a) is nicer if live updates are wanted.
   - Done when: scrolling and browsing during a 50-minute run doesn't jump.

5. **Collapse same-scene shots into stacks** – **done 2026-11-01**
   - Built: stacked by default, "+N" badge on the best shot (open it for the "Same scene" strip),
     *Show all shots* toggle, remembered; the *duplicate* and *skips AI* filters always show every shot.
   - Today: every burst shot is its own card; duplicates are only marked with a grey flag. The
     scene grouping (`duplicate_of`) is already used in the detail panel's "Same scene".
   - Proposal: by default show only the best shot of each scene with a "+4" badge; clicking the
     badge expands the stack in place (or opens the detail panel on the "Same scene" strip).
     A toggle "show all shots" restores today's view. Filter counts follow the setting.
   - Done when: the grid of a ~850-photo holiday shrinks to the distinct scenes.

#### Library
6. **Sort and search** *(search: see Usability)* – **done 2026-11-01**
   - Built: search over caption, tags, scene, issues and file name, combined with the flag filter
     and remembered across live reloads; a sort select (the slideshow's sort keys) and a collapsible
     list of the 30 most common tags with counts, both following the search and stacks.
   - Today: no sort order other than the default; tags can only be filtered by clicking one inside
     the detail panel; no text search.
   - Proposal: a sort select (date, aesthetic, interest, sharpness/score, file name) and a search
     box matching caption, tags and file name (`hx-trigger="input changed delay:300ms"`), combined
     with the current flag filter. A tag list (top tags with counts, collapsible) next to the flag
     filters.
   - Remember sort/search the same way `job["flag"]` is remembered, so live reloads keep them.

7. **Easier-to-read card info** – **done 2026-11-01**
   - Built: date, 1–5 stars from the AI's aesthetic + interest (exact scores as tooltip) and the
     caption as second line; "sharp 412" and the card's tag list are gone (tags: see the tag list in 6).
   - Today: card meta shows "sharp 412" (raw Laplacian value) and "★ 7/6" (aesthetic/interest
     without labels) – both need explaining.
   - Proposal: one simple quality indicator (e.g. 1–5 stars or a coloured dot from the combined
     score), the date, and the caption as tooltip/second line. Raw metrics stay in the detail table.

8. **Smaller image in the detail panel** – **done 2026-11-01**
   - Built: `/preview/{id}` (1024 px, cached as `<thumb>_preview.jpg`, removed with the thumbnails
     when the file changes or goes); click opens the original. Videos still play the original.
   - Today: the 380 px panel loads `/original/{id}` – the full camera file, often several MB.
   - Proposal: a new `/preview/{id}` route serving a ~1024 px JPEG (EXIF-rotated, cached next to
     the thumbnails on first request – today the 1024 px AI image is only encoded in memory, never
     saved), and keep the link to the original on click. Makes keyboard stepping (2) fast.

9. **Bulk select** – **done 2026-11-01**
   - Built: Ctrl/⌘-click toggles a card, Shift-click adds a range; a bar *Add to / Remove from
     slideshow / Clear* posts the ids to `/slideshow/bulk` (one undo step), the grid reloads its badges.
   - Proposal: a "Select" mode (or Shift/Ctrl-click) with checkboxes on cards and an action bar:
     *Add N to slideshow*, *Remove from slideshow*. One new route taking a list of ids.

10. **State in the URL** – **done 2026-11-01**
    - Built: filter, tag and card clicks push `/?flag=…&photo=…`; back/forward reload that URL
      (htmx history cache off), `index` restores filter and detail panel. Search and sort stay server-side.
    - Today: filter and open photo live only in server state; reload and the back button lose them.
    - Proposal: `hx-push-url` on filter buttons and card clicks (`/?flag=tag:fjord&photo=123`);
      `index` reads the query to restore filter and detail panel. Also makes a view shareable as a bookmark.

#### Slideshow
11. **Next/previous in the lightbox** – **done 2026-11-01**
    - Built: ← / → and on-screen arrows, "3 / 104 · reason" line (as text: the reason is LLM output).
    - Today: the lightbox shows one photo; you have to close it and click the next tile.
    - Proposal: ← / → and on-screen arrows step through the picks; shows the pick number and
      reason. Shares code with the player (1).

12. **Adjustable seconds per photo** *(see Usability)* – **done 2026-11-01**
    - Built: 2–15 s input in the estimate line, saved in `selection.json`; used by the estimate, the
      player and the AI prompt; videos count with their length (OpenCV, cached by path + mtime) and
      are listed to the AI as "video 12s".
    - Today: "about X min at 5 s per photo" is hard-coded in `_picks.html`.
    - Proposal: a small number input (2–15 s) next to the estimate, saved with the selection, used
      by the estimate, the player (1) and passed to the AI as context ("the show should be ~10 min").
      Videos count with their real duration.

13. **Better feedback while the AI chooses** – **done 2026-11-01**
    - Built: spinner, elapsed "1:12", picks dimmed and not clickable during *Select photos* and *Refine picks*.
    - Today: only the text "The AI is choosing… this can take a minute" appears.
    - Proposal: a spinner, the picks area dimmed and disabled (no edits that would be overwritten),
      and the elapsed time ("1:12"). Same for *Refine picks*.

#### Projects, Settings, general
14. **Calmer Projects page**
    - Today: every project always shows a rename form, a change-folder form with Browse…, Open,
      Remove and the "also delete saved AI results" checkbox – a wall of inputs. The checkbox sits
      right next to Remove and is easy to tick by mistake.
    - Proposal: project cards with a cover thumbnail (best-scored photo), name, stats and *Open*
      as the main action; rename/change folder behind an *Edit* toggle (`<details>`); the
      "also delete AI results" choice moves into the remove confirmation (a small `<dialog>`
      instead of `hx-confirm`).

15. **Clear button hierarchy**
    - Today: `form button { background: var(--accent) }` makes nearly every button look like the
      main action (Rename, Change folder, Analyse again, Refine, Export…).
    - Proposal: a `.primary` class for the one main action per page (Create & analyse, Select photos,
      Export) and a neutral default style for the rest; destructive actions keep `.remove`.

16. **Move "Parallel AI requests" to Settings** – **done 2026-11-01** (default from `AI_CONCURRENCY`)
    - Today: a technical option shown in the Library bar on every visit.
    - Proposal: a setting (default 2) in the Settings page; the Library bar keeps only
      *Analyse again* and the progress.

17. **Fix outdated empty-state text** – **done 2026-11-01**
    - Today: `_grid.html` says "Nothing here yet – enter a folder and press Analyse", but folders
      are now set on the Projects page.
    - Proposal: "Nothing here yet – analyse the project to see its photos" (or the filter-specific
      "No photos match this filter" when items exist but the filter is empty).

18. **Short help text in Settings** – **done 2026-11-01**
    - Today: `db.SETTINGS` has only *Theme*; the page grows with 12 (seconds per photo) and
      16 (parallel AI requests), and *Unused data* already needs explaining.
    - Proposal: one muted line under each setting explaining what it changes, stored as a fourth
      element in the `db.SETTINGS` tuples next to label, choices and default.

## Risks / open points
Reviewed with the user on 2026-10-23.

| Risk | Status / decision |
|---|---|
| Endpoint: vision, JSON mode, image size | Resolved – works; JSON mode supported (1 request per image) |
| Throughput | Resolved – ~30–70 min per ~900-file holiday once, then cached; parallel requests selectable |
| Selection on a full holiday (~850 candidates, ~60k-token prompt) | Resolved – tested on all 857 Norway files: 104 good picks in 294 s, well under the 10-min client timeout |
| Video dates from mtime off by 1 h | Fixed – date from file name, then MP4 header, mtime last |
| AI scores compressed (mostly 6–8) | Accepted – selection works via captions/tags; prompt unchanged |
| AI results lost if a cache cleaner runs | Fixed – data moved from `~/.cache` to `~/.local/share/photos2show` |
| No backup of the code | Resolved – on GitHub (`jotiau/photos2show`) |
| Very large folders (5,000+): pairwise dedup is O(n²) | Accepted – minutes at worst, fine for holidays |
| "Photos of us" without face recognition | Accepted – people count + captions; faces are a future feature |
| Motion blur with one sharp tile not detected | Accepted – GLM usually lists it under issues |
