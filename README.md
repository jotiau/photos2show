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
   - *Describing with AI*: each remaining photo goes to the model one after another (~4 s each);
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

## Command line

```bash
uv run imgsort-scan ~/Pictures/holiday     # both analysis passes, printed as a table
uv run imgsort-smoke [photo.jpg]           # test the AI endpoint with one image
uv run imgsort-testdata demo_images        # synthetic test photos with known defects
uv run pytest
```

Settings (`.env` or environment): `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL`,
`AI_CONCURRENCY` (default 1), `IMGSORT_CACHE` (default `~/.cache/imgsort`), `IMGSORT_PORT` (default 8000).
Projects, results and thumbnails live in the cache folder, never in your photo folder.

See [PLAN.md](PLAN.md) for the roadmap.
