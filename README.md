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

Start the UI; it opens http://127.0.0.1:8000 in your browser (add `--no-browser` to skip that):

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

## Runtime

For a holiday of ~900 photos and videos (laptop + self-hosted GLM endpoint):

| Step | Time |
|---|---|
| Local scan (blur, exposure, repeats) | ~2–3 min first run, seconds afterwards |
| AI descriptions, 2 parallel (default) | ~45 min (~3 s per file) |
| AI descriptions, 4 parallel | ~30 min (~2 s per file) |
| AI descriptions, 1 at a time | ~70 min (~5 s per file) |
| Slideshow selection | ~5 min |

AI descriptions are made once per file and cached; re-runs only process new or changed files.

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
