# ai-image-sorting

**Turn a folder of 1000 holiday photos into a slideshow by describing the show you want.**

[![Tests](https://github.com/jotiau/ai-image-sorting/actions/workflows/tests.yml/badge.svg)](https://github.com/jotiau/ai-image-sorting/actions/workflows/tests.yml)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

You come back from a trip with hundreds of photos and videos: blurry ones, dark ones, ten shots
of the same fjord. This tool sorts out the technical rejects on your own machine, has an AI model
describe the rest, and then builds a slideshow from a plain sentence like:

> *"10-minute show for my parents, chronological, mostly landscapes and the two of us, little food"*

You get a new folder with numbered copies (`001_2026-08-11_landscape.jpg`, `002_…`) ready for any
slideshow app, TV or photo frame. **Your originals are never moved or changed.**

It works with any OpenAI-compatible vision model: a hosted API, or a local model via Ollama or
LM Studio if you'd rather keep your photos on your own computer.

## Features

**Quality checks, offline and fast**
- Finds blurry, too dark, too bright, low-contrast and monotone shots (plain sky, a wall) with
  classic image analysis; no AI, no upload. Sharp subjects in front of a blurred background count as sharp.
- Groups same-scene shots (bursts, "one more just in case") and keeps the best of each group.
- Very blurry photos and the weaker shots of a scene are never sent to the AI, which saves time and money.

**AI descriptions**
- A caption, content tags, scene type, people count, scores for aesthetic and interest, and visible
  issues (closed eyes, tilted horizon) for every photo worth keeping.
- Videos are covered too: frames at 10 %, 50 % and 90 % of the clip, the sharpest one represents it.
- Runs 1–8 requests in parallel, shows results live, and resumes where it stopped after an interruption.
- Everything is cached: re-runs only process new or changed files.

**Slideshow from a description**
- Describe length, audience, order and content in your own words; the model picks and orders the
  photos and says why it chose each one.
- Fine-tune by hand: reorder, remove, add photos from the library, or sort by date, aesthetic,
  interest, technical quality or file name.
- Export numbered copies plus a `selection.json` with your description and the reason for every pick.

**Library browser**
- Thumbnail grid with filters by quality flag and tag; a detail panel with all metrics, the other
  shots of the same scene and the AI description.
- One project per trip; switch between them in the header.

**Your photos stay yours**
- Runs locally in your browser; projects, AI results and thumbnails live in a data folder, never in your photo folder.
- Only a downscaled copy (1024 px) of each photo goes to the AI endpoint you configure.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (installs Python for you)
and access to a vision-capable AI model (see [Choosing a model](#choosing-a-model)).

```bash
git clone https://github.com/jotiau/ai-image-sorting.git
cd ai-image-sorting
uv sync
cp .env.example .env      # then enter your endpoint, key and model
uv run imgsort-smoke      # sends one test picture to check that the model answers
uv run imgsort-web        # opens http://127.0.0.1:8000 in your browser
```

Everything is plain Python, so it should run on Linux, macOS and Windows (on Windows use `copy`
instead of `cp`). Day-to-day use has been tested on Linux so far; reports from other systems are very welcome.

## Choosing a model

Any server that speaks the OpenAI chat API and accepts images works. Set three values in `.env`:

| Where the model runs | `OPENAI_BASE_URL` | `OPENAI_API_KEY` | `MODEL` |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | your key | a vision model of your choice |
| [Ollama](https://ollama.com) (local) | `http://localhost:11434/v1` | leave empty | e.g. a Qwen-VL or Gemma 3 model you pulled |
| [LM Studio](https://lmstudio.ai) (local) | `http://localhost:1234/v1` | leave empty | the model id LM Studio shows |
| Self-hosted (vLLM, SGLang, …) | your server's `/v1` URL | your key, if any | as the server lists it |

`imgsort-smoke` lists the model ids your server offers, so you can copy the right one.
Development used a self-hosted GLM-5.3 Flash; other models should work but may describe photos
differently. If you try one, please share how it went in an issue.

`.env` may reference existing environment variables, e.g. `OPENAI_BASE_URL=${MY_BASE_URL}`.

### Local model examples

These are vision models that run on a single consumer GPU or a recent Mac. They haven't been
tested with this tool yet, so treat them as starting points:

| Model | Ollama | Rough memory | Notes |
|---|---|---|---|
| Qwen2.5-VL 7B | `qwen2.5vl:7b` | ~6 GB | good all-rounder, follows the JSON format well |
| Qwen3-VL 8B | `qwen3-vl:8b` | ~6 GB | newer Qwen, more detailed captions |
| Gemma 3 12B | `gemma3:12b` | ~9 GB | good descriptions; `gemma3:4b` for small GPUs |
| MiniCPM-V 8B | `minicpm-v:8b` | ~6 GB | fast, fine for tagging |
| Llama 3.2 Vision 11B | `llama3.2-vision:11b` | ~8 GB | solid, slower than the ones above |

**Ollama**

```bash
ollama pull qwen2.5vl:7b
OLLAMA_CONTEXT_LENGTH=32768 ollama serve   # larger context for the slideshow step, see below
```

```ini
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=
MODEL=qwen2.5vl:7b
AI_CONCURRENCY=1
```

**LM Studio**: download a vision model (e.g. *Qwen2.5-VL-7B-Instruct*, marked with an eye icon),
load it with a context length of 32k or more, and start the server in the *Developer* tab.

```ini
OPENAI_BASE_URL=http://localhost:1234/v1
OPENAI_API_KEY=
MODEL=qwen2.5-vl-7b-instruct    # the id LM Studio shows; imgsort-smoke lists it too
AI_CONCURRENCY=1
```

Tips for local models:
- **Context length matters for the slideshow.** The selection step sends one line per photo to the
  model, which is roughly 20–40k tokens for a trip of 1000 files. With the small default context of
  Ollama or LM Studio, the start of the list is cut off silently. Set 32k or more.
- **Start with `AI_CONCURRENCY=1`.** A single local GPU rarely gets faster with parallel requests;
  raise it only if your server is set up for parallel slots (`OLLAMA_NUM_PARALLEL` in Ollama).
- **Expect it to be slower than the timings below** on a laptop GPU; a 7–8B model on a mid-range
  GPU typically needs a few seconds per photo, CPU-only can take a minute or more.

## How to use

1. **Create a project**: one per trip. Click *New project*, enter a name (e.g. "Norway 2026") and
   the folder with the photos and videos (subfolders are included), then press *Create & analyse*.
2. **Let it analyse** (progress in the *Library* tab):
   - *Scanning*: local checks for blur, exposure, contrast, monotony and same-scene shots. Fast.
   - *Describing with AI*: each remaining photo goes to the model, 2 at a time by default
     (choose 1–8 next to *Analyse again*). Captions and tags appear live; skipped photos are marked
     *no AI*. Interrupted? Press *Analyse again* and it continues where it stopped.
3. **Browse the Library**: filter by quality flag, click a card for details, click a tag to filter by it.
4. **Build the slideshow** in the *Slideshow* tab: describe the show and press *Select photos*.
   The picks are shown as tiles: drag them to reorder (or use ← →), remove with ×, sort the whole
   set, or add photos via *Add to slideshow* in the Library detail panel.
5. **Export**: choose a new folder outside the photo folder and press *Export*.

Each project keeps one slideshow; to make a second show from the same trip, export the first one,
then select again.

### How long does it take?

For a trip of ~1000 photos and videos (laptop + self-hosted GLM endpoint):

| Step | Time |
|---|---|
| Local scan (blur, exposure, repeats) | ~2–3 min first run, seconds afterwards |
| AI descriptions, 2 parallel (default) | ~50 min (~3 s per file) |
| AI descriptions, 4 parallel | ~33 min (~2 s per file) |
| AI descriptions, 1 at a time | ~80 min (~5 s per file) |
| Slideshow selection | ~5–6 min |

AI descriptions are made once per file; later runs take seconds.

## Supported files

JPEG photos (`.jpg`, `.jpeg`) and MP4 videos. Dates come from the photo's EXIF data; for videos
from phone-style file names (`20260725_130945.mp4`), else the file date. HEIC and RAW are on the roadmap.

## Configuration

Set in `.env` or as environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_BASE_URL` | – (required) | OpenAI-compatible endpoint, e.g. `http://localhost:11434/v1` |
| `OPENAI_API_KEY` | empty | API key; leave empty for servers without auth |
| `MODEL` | `glm-5.3-flash` | model id as the server lists it |
| `AI_CONCURRENCY` | `2` | parallel AI requests (1 = strictly one by one); also selectable in the UI |
| `IMGSORT_CACHE` | `~/.local/share/imgsort` | data folder for projects, AI results and thumbnails |
| `IMGSORT_PORT` | `8000` | port of the web UI |

## Command line

```bash
uv run imgsort-web [--no-browser]              # the web UI
uv run imgsort-scan ~/Pictures/trip [--parallel N]  # both analysis passes, printed as a table
uv run imgsort-smoke [photo.jpg]               # test the AI endpoint with one image
uv run imgsort-testdata demo_images            # synthetic test photos with known defects
```

## Roadmap

Planned, roughly in this order of interest:

- HEIC and RAW support
- Several saved slideshows per project; projects spanning several folders
- Face detection and grouping ("everyone appears at least once")
- Map and location grouping from GPS
- Local semantic search (CLIP) and smarter duplicate grouping
- Deeper video handling (scene detection, picking clips)
- Fit the slideshow step to the model's context size: send less per photo (e.g. shorter captions,
  fewer tags) for small local models, more detail for large ones

[PLAN.md](PLAN.md) has the design notes and decisions behind the current version.

## Contributing

Bug reports, model experiences and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
The tests run without an AI endpoint: `uv run pytest`.

## License

[GPL-3.0](LICENSE). You may use, change and share this tool; if you distribute a modified
version, you must publish its source under the same license.
