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

See [PLAN.md](PLAN.md) for the roadmap.
