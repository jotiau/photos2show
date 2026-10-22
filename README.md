# ai-image-sorting
quick tool to presort images with the help of ai

## Setup

```bash
uv sync
cp .env.example .env   # fill in your OpenAI-compatible endpoint, key and model
```

## M0 smoke test

```bash
uv run imgsort-smoke                 # synthetic test picture (sky, sun, red house)
uv run imgsort-smoke path/to/photo.jpg
```

## M1 quality scan

```bash
uv run imgsort-web                              # UI on http://127.0.0.1:8000 – enter a folder, press Analyse
uv run imgsort-scan ~/Pictures/holiday          # same analysis, printed as a table
uv run imgsort-testdata demo_images             # generate synthetic test photos with known defects
uv run pytest
```

Results and thumbnails are cached in `~/.cache/imgsort/` (override with `IMGSORT_CACHE`);
the photo folder itself is never modified.

See [PLAN.md](PLAN.md) for the roadmap.
