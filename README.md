# ai-image-sorting
quick tool to presort images with the help of ai

Drop your holiday photos and videos into a folder, let the tool check their quality and describe
them with an AI model, then describe the slideshow you want and get a sorted folder.

## Setup

```bash
uv sync
cp .env.example .env   # fill in your OpenAI-compatible endpoint, key and model
```

`.env` may reference existing environment variables, e.g. `OPENAI_BASE_URL=${BLUM_BASE_URL}`.

## How to use

Start the UI and open http://127.0.0.1:8000:

```bash
uv run imgsort-web
```

1. **Create a project** – one per holiday. Click *New project*, enter a name (e.g. "Norway 2026")
   and the folder with the photos/videos (subfolders are included), press *Create & analyse*.
   Switch between holidays with the project dropdown in the header.
2. **Analysis runs in two passes** (progress in the *Library* tab):
   - *Scanning*: local checks for blur, exposure, contrast, monotony, same-scene shots; fast.
   - *Describing with AI*: each remaining photo goes to the model 2 at a time by default (choose 1–8 next to *Analyse again*);
     captions and tags appear live. Very blurry photos and the non-best shots of a scene are
     never sent (marked *no AI*). Interrupted? Press *Analyse again* – it continues where it stopped.
3. **Browse the Library**: filter by quality flag or click a card to see details, all metrics,
   other shots of the same scene and the AI description. Click a tag to filter by it.
4. **Slideshow tab**: describe the show in your own words, e.g.
   *"10-minute show for my parents, chronological, mostly landscapes and the two of us, little food"*,
   and press *Select photos*. The model picks and orders photos and says why.
   Reorder with ↑ ↓, remove with ×, add photos via *Add to slideshow* in the Library detail panel.
5. **Export**: choose a new folder outside the photo folder and press *Export*. You get
   `001_2026-08-11_church-fog.jpg`, `002_…` in show order plus `selection.json`.
   Originals are never moved or modified.

Each project keeps one slideshow selection; to make a second show from the same holiday,
export the first one, then select again.

## Runtime estimates and findings

Measured on 2026-10-23 on a laptop (Ryzen 8040 / Radeon 780M iGPU, 16 cores, 29 GB RAM) against the
self-hosted GLM endpoint (`MODEL=prod`), with the Norway 2026 holiday (848 JPG + 29 MP4, 2 GB):
local scan of all 877 files, plus the full AI pass on a random subset of 100 JPG + 10 MP4.

| Step | Measured | Per file | Estimate for 877 files |
|---|---|---|---|
| Pass 1 – local scan (first run) | 877 files in 146 s | ~0.17 s | **~2.5 min** |
| Pass 1 – re-run (cached) | 877 files in 4 s | – | seconds |
| Pass 2 – AI, `AI_CONCURRENCY=1` | 110 files in 518 s | 4.7 s (median 4.3, 2.5–11.4) | **~65–70 min** (~850 sent) |
| Pass 2 – AI, `AI_CONCURRENCY=4` | 20 files in 39 s, no errors | 1.9 s | **~27 min** |
| Slideshow selection | 110 candidates → 60 picks in 176 s | ~3 s per pick | 110 candidates: ~3 min; ~850: not tested, expect several minutes |

Rules of thumb:
- **Total for one holiday of ~900 files: ~1 h 10 min one-by-one, or ~30 min with
  `AI_CONCURRENCY=4`** – once. Afterwards everything is cached; re-runs only process new/changed files.
- Selection time grows mainly with the number of picks (the model writes a reason for each);
  the prompt is ~70 tokens per candidate (110 candidates ≈ 7.8k tokens, ~850 ≈ 60k tokens).
- Videos cost about the same as photos (one middle frame is sent).

Findings:
- The endpoint supports JSON mode, so each photo is exactly one request.
- Same-scene detection on the full set: 21 photos are exact repeats and are not sent to AI
  (~2.5 % saved). A random sample has almost no repeats, so all 110 were sent.
- Only 2 of 848 photos are flagged blurry; holiday phone photos are mostly sharp and well exposed.
  Motion blur where one part of the picture stays sharp is not detected.
- AI scores are compressed: aesthetic mostly 6–8, interest mostly 6–7 (on 1–10). They separate
  the very weak (1–3) and the highlights (9) well, but not the middle – the selection relies on
  captions and tags as much as on scores.
- Tags fit the trip (mountains, clouds, tundra, lake, hiking, snow); 80 of 110 photos are "landscape".
- Selection quality: "5-minute show … chronological, best landscapes, some photos of us, no blurry
  or boring shots" gave exactly 60 picks (= 5 min at 5 s each), in date order, with a video,
  several people shots and sensible reasons per pick.

## Command line

```bash
uv run imgsort-scan ~/Pictures/holiday     # both analysis passes, printed as a table
uv run imgsort-smoke [photo.jpg]           # test the AI endpoint with one image
uv run imgsort-testdata demo_images        # synthetic test photos with known defects
uv run pytest
```

Settings (`.env` or environment): `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL`,
`AI_CONCURRENCY` (default 2; 1 = strictly one by one; selectable in the UI and via `imgsort-scan --parallel N`), `IMGSORT_CACHE` (data folder, default `~/.local/share/imgsort`), `IMGSORT_PORT` (default 8000).
Projects, AI results and thumbnails live in that data folder, never in your photo folder.

See [PLAN.md](PLAN.md) for the roadmap.
