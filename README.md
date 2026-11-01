# photos2show

**Turn a folder of 1000 holiday photos into a slideshow by describing the show you want.**

[![Tests](https://github.com/jotiau/photos2show/actions/workflows/tests.yml/badge.svg)](https://github.com/jotiau/photos2show/actions/workflows/tests.yml)
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

- **Quality checks, offline:** finds blurry, too dark, too bright, low-contrast and monotone shots with
  classic image analysis – no AI, no upload. Groups same-scene shots (bursts) and keeps the best one,
  or lets you pick it before the AI runs.
- **AI descriptions:** a caption, tags, scene type, people count and scores for every photo worth
  keeping, videos included. Runs in parallel, can be stopped and resumed, and caches everything.
- **Slideshow from a description:** the model picks and orders the photos and says why. Refine it in
  words, reorder, swap or add by hand, undo, and play it full screen.
- **Library browser:** thumbnail grid with filters, tags, search over the captions, sorting, a detail
  panel and keyboard shortcuts. One project per trip.
- **Export:** numbered copies plus a `selection.json`; RAW photos become full-size JPEGs.
- **Your photos stay yours:** runs locally; only a downscaled 1024 px copy goes to the AI endpoint you
  choose, and nothing is ever written into your photo folder.

Supports photos (JPEG, PNG, HEIC, WebP, AVIF, TIFF, GIF, BMP), camera RAW (DNG, CR2/CR3, NEF, ARW, ORF,
RW2, RAF, …) and videos (MP4, MOV, MKV, WebM, AVI, MTS, …).

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (installs Python for you)
and access to a vision-capable AI model.

```bash
git clone https://github.com/jotiau/photos2show.git
cd photos2show
uv sync
cp .env.example .env          # then enter your endpoint, key and model
uv run photos2show-smoke      # sends one test picture to check that the model answers
uv run photos2show-web        # opens http://127.0.0.1:8000 in your browser
```

Then create a project in the *Projects* tab, let it analyse, and describe your show in the *Slideshow*
tab. It should run on Linux, macOS and Windows (use `copy` instead of `cp`); day-to-day use has been
tested on Linux so far, reports from other systems are very welcome.

Any server that speaks the OpenAI chat API and accepts images works:

| Where the model runs | `OPENAI_BASE_URL` | `MODEL` |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | a vision model of your choice |
| [Ollama](https://ollama.com) (local) | `http://localhost:11434/v1` | e.g. `qwen2.5vl:7b` |
| [LM Studio](https://lmstudio.ai) (local) | `http://localhost:1234/v1` | the model id LM Studio shows |

Local models need a context of 32k or more for the slideshow step and work best with 1 parallel
request; see [Configuration](docs/configuration.md#local-models).

## Documentation

| Page | Contents |
|---|---|
| [User guide](docs/user-guide.md) | the four tabs, duplicate review, slideshow editing, export, keyboard, troubleshooting |
| [Configuration](docs/configuration.md) | `.env` variables, the Settings page, model setup, timings |
| [How it works](docs/how-it-works.md) | quality checks and thresholds, same-scene grouping, AI prompts, selection, export rules |
| [Data and files](docs/data-and-files.md) | supported formats, dates, the data folder, privacy |
| [Development](docs/development.md) | code layout, routes, command line tools, tests, conventions |

[PLAN.md](PLAN.md) has the design notes and decisions behind the current version.

## Roadmap

Planned, roughly in this order of interest:

- Several saved slideshows per project; projects spanning several folders
- Face detection and grouping ("everyone appears at least once")
- Map and location grouping from GPS
- Local semantic search (CLIP) and smarter duplicate grouping
- Deeper video handling (scene detection, picking clips)

## Contributing

Bug reports, model experiences and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
The tests run without an AI endpoint: `uv run pytest`.

## License

[GPL-3.0](LICENSE). You may use, change and share this tool; if you distribute a modified
version, you must publish its source under the same license.
