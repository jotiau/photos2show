# Configuration

There are two places: the `.env` file (or environment variables) for the AI endpoint and system
paths, and the *Settings* tab for choices you change while using the app.

## Environment variables (`.env`)

Copy `.env.example` to `.env` in the project folder. `.env` is git-ignored.

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_BASE_URL` | – (required) | OpenAI-compatible endpoint, e.g. `http://localhost:11434/v1`. Must start with `http://` or `https://`. |
| `OPENAI_API_KEY` | empty | API key; leave empty for servers without auth |
| `MODEL` | `glm-5.3-flash` | model id exactly as the server lists it |
| `AI_CONCURRENCY` | `2` | parallel AI requests (whole number ≥ 1); default for *Parallel AI requests* and `photos2show-scan --parallel` |
| `PHOTOS2SHOW_CACHE` | `~/.local/share/photos2show` (`$XDG_DATA_HOME/photos2show`) | data folder for projects, AI results and thumbnails |
| `PHOTOS2SHOW_PORT` | `8000` | port of the web UI |

Values may reference other environment variables: `OPENAI_BASE_URL=${MY_BASE_URL}` (and `$MY_BASE_URL`).

**Old name:** the tool used to be called imgsort. `IMGSORT_CACHE` and `IMGSORT_PORT` still work, and an
existing `~/.local/share/imgsort` folder keeps being used if there is no `photos2show` folder yet.

## Settings page

Saved in `settings.json` in the data folder and valid for all projects.

| Setting | Choices | Default | Effect |
|---|---|---|---|
| Theme | auto, light, dark | auto | colour theme; auto follows your system |
| Parallel AI requests | 1, 2, 4, 8 (plus the `AI_CONCURRENCY` value) | `AI_CONCURRENCY` | how many photos go to the AI at once. More is faster if the server keeps up; a local model usually works best with 1. |
| Review duplicates before AI | on, off | on | on: Analyse pauses after scanning so you can pick the best shot of each new same-scene group, or let the AI compare them. Off: the technically best shot is used without asking. |
| Photo details for choosing | full, short, minimal, auto | full | how much the slideshow AI reads per photo (see below). Also offered next to *Select photos*. |

*Photo details for choosing*:

| Choice | Per photo | Approx. tokens |
|---|---|---|
| full | id, date, kind, scene, all tags, caption, aesthetic, interest, flags | ~70 |
| short | caption cut to 8 words, 5 tags | ~35 |
| minimal | no caption, 3 tags | ~20 |
| auto | the most detailed of the above whose list fills at most half the model's context | – |

*auto* needs the server to report its context size under `/models` (e.g. vLLM's `max_model_len`).
llama.cpp, Ollama and LM Studio do not, so auto then uses full.

## Choosing a model

Any server that speaks the OpenAI chat API and accepts images works.

| Where the model runs | `OPENAI_BASE_URL` | `OPENAI_API_KEY` | `MODEL` |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | your key | a vision model of your choice |
| Ollama (local) | `http://localhost:11434/v1` | empty | e.g. `qwen2.5vl:7b` |
| LM Studio (local) | `http://localhost:1234/v1` | empty | the model id LM Studio shows |
| Self-hosted (vLLM, SGLang, …) | your server's `/v1` URL | your key, if any | as the server lists it |

`uv run photos2show-smoke` prints the model ids the server offers and sends one test picture.
Development used a self-hosted GLM-5.3 Flash; other models should work but may describe photos differently.
If you try one, please share how it went in an issue (*Model experience* template).

The app asks for JSON answers (`response_format: json_object`); servers that reject JSON mode get the
same request without it, and the answer is parsed leniently.

### Local models

Untested starting points that run on one consumer GPU or a recent Mac:

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

**LM Studio**: download a vision model (e.g. *Qwen2.5-VL-7B-Instruct*, marked with an eye icon), load it
with a context length of 32k or more, and start the server in the *Developer* tab.

```ini
OPENAI_BASE_URL=http://localhost:1234/v1
OPENAI_API_KEY=
MODEL=qwen2.5-vl-7b-instruct    # the id LM Studio shows; photos2show-smoke lists it too
AI_CONCURRENCY=1
```

Tips:
- **Context length matters for the slideshow.** The selection step sends one line per photo: roughly
  20–40k tokens for 1000 files. With the small default context of Ollama or LM Studio the start of the
  list is cut off silently. Use 32k or more (as in the examples above), or choose short or minimal
  *Photo details*.
- **Start with 1 parallel request.** A single local GPU rarely gets faster with parallel requests
  (raise it only if the server has parallel slots, e.g. `OLLAMA_NUM_PARALLEL`).
- Expect a few seconds per photo for a 7–8B model on a mid-range GPU; CPU-only can take a minute or more.

### Timings

For ~1000 photos and videos (laptop + self-hosted GLM endpoint):

| Step | Time |
|---|---|
| Local scan | ~2–3 min first run, seconds afterwards |
| AI descriptions, 1 / 2 / 4 parallel | ~80 / ~50 / ~33 min |
| Slideshow selection | ~5–6 min |

AI descriptions are made once per file.

## Timeouts

Each photo description may take up to 90 s, an AI side-by-side comparison 120 s, and the `/models`
lookup for *auto* 10 s. A connection or server error is retried once (the `/models` lookup not at
all); a request that still fails is reported, and the next *Analyse again* retries the photo.
