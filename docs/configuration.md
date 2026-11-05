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
| `ANALYSIS_MODEL` | `MODEL` | model for the photo descriptions and side-by-side comparisons (needs vision); default for *Analysis model* |
| `SLIDESHOW_MODEL` | `MODEL` | model for choosing and refining the slideshow (text only); default for *Slideshow model* |
| `AI_CONCURRENCY` | `2` | parallel AI requests (whole number ≥ 1); default for *Parallel AI requests* and `photos2show-scan --parallel` |
| `AI_THINKING` | `on` | `off` asks thinking models to answer without reasoning first (see [Thinking models](#thinking-models)); `on` or `off` |
| `PHOTOS2SHOW_CACHE` | `~/.local/share/photos2show` (`$XDG_DATA_HOME/photos2show`) | data folder for projects, AI results and thumbnails |
| `PHOTOS2SHOW_PORT` | `8000` | port of the web UI |

Values may reference other environment variables: `OPENAI_BASE_URL=${MY_BASE_URL}` (and `$MY_BASE_URL`).

**Old name:** the tool used to be called imgsort. `IMGSORT_CACHE` and `IMGSORT_PORT` still work, and an
existing `~/.local/share/imgsort` folder keeps being used if there is no `photos2show` folder yet.

## Settings page

Saved in `settings.json` in the data folder and valid for all projects.

| Setting | Choices | Default | Effect |
|---|---|---|---|
| Theme | auto, light, dark, grey | auto | colour theme; auto follows your system. Grey is a neutral mid-grey that does not tint how photos look. |
| Accent colour | blue, indigo, purple, magenta, pink, red, orange, amber, olive, green, teal, cyan, slate | blue | colour of the top bar, buttons, selections, links and highlights. Picked by clicking a colour swatch. |
| Analysis model | the models the server lists | `ANALYSIS_MODEL`, else `MODEL` | the model that describes each photo and compares shots of one scene. Needs vision and runs once per photo, so a small, fast model pays off. |
| Slideshow model | the models the server lists | `SLIDESHOW_MODEL`, else `MODEL` | the model that chooses and refines the slideshow in one request over all photo descriptions. Needs no vision but a large context and good reasoning. *auto* photo details use its context size. |
| Parallel AI requests | 1, 2, 4, 8 (plus the `AI_CONCURRENCY` value) | `AI_CONCURRENCY` | how many photos go to the AI at once. More is faster if the server keeps up; a local model usually works best with 1. |
| Review duplicates before AI | on, off | on | on: Analyse pauses after scanning so you can pick the best shot of each new same-scene group, or let the AI compare them. Off: the technically best shot is used without asking. |
| Photo details for choosing | full, short, minimal, auto | full | how much the slideshow AI reads per photo (see below). Also offered next to *Select photos*. |
| Captions in Play | off, on | off | on: *▶ Play* shows the date and the AI caption under every photo. Also switched with *Captions* in the player. |
| Slow zoom in Play | off, on | off | on: *▶ Play* slowly zooms into each photo (Ken Burns effect); videos play as they are. Also switched with *Zoom* in the player. |

*Analysis model* and *Slideshow model* list the models from the server's `/models`; the first entry,
*default (…)*, keeps the model from `.env`. If the server does not answer within 3 s, a text field is
shown instead: type the model id, or leave it empty for the default. Both use the same server
(`OPENAI_BASE_URL`). A new analysis model only describes photos that have no description yet; existing
descriptions are kept.

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
| Ollama (local) | `http://localhost:11434/v1` | empty | e.g. `gemma4:e4b` |
| LM Studio (local) | `http://localhost:1234/v1` | empty | the model id LM Studio shows |
| Self-hosted (vLLM, SGLang, …) | your server's `/v1` URL | your key, if any | as the server lists it |

`uv run photos2show-smoke` prints the model ids the server offers and sends one test picture to the
analysis model.

The analysis and the slideshow step can use different models (Settings → *Analysis model* /
*Slideshow model*, or `ANALYSIS_MODEL` / `SLIDESHOW_MODEL`): a small vision model for the many photo
descriptions, a larger text model with a big context for the one slideshow request.
Development used a self-hosted GLM-5.3 Flash; other models should work but may describe photos differently.
If you try one, please share how it went in an issue (*Model experience* template).

The app asks for JSON answers (`response_format: json_object`); servers that reject JSON mode get the
same request without it, and the answer is parsed leniently.

### Thinking models

Some models reason before they answer (Qwen3.5, Qwen3.8, Qwen3-VL without `-instruct`, GLM). For a photo
caption that only costs time: several times more seconds and output tokens per photo, and the 90 s limit
can be reached. With `AI_THINKING=off` every request asks the model to skip it: `reasoning_effort: none`
(Ollama) and `chat_template_kwargs: {enable_thinking: false}` (vLLM, SGLang, llama.cpp). A server that
rejects these fields gets the request again without them, so the model then thinks as before. The
default `on` sends neither field. `photos2show-smoke` shows which one is active.

### Local models

Untested starting points for one consumer GPU or a recent Mac. Ollama tags, download sizes and context
sizes as listed on ollama.com in November 2026; add 1–2 GB for images and context.

*Role* says which step a model suits (see [Choosing a model](#choosing-a-model)): **analysis** needs
vision and runs once per photo, so small and fast wins; **slideshow** is one text request over all
photo descriptions, so a large context and good reasoning win; **both** are vision models good enough
for either.

| Model | Ollama | Size | Context | Role | Notes |
|---|---|---|---|---|---|
| Gemma 4 E2B | `gemma4:e2b` | 4.6 GB | 128K | analysis | smaller and faster, for small GPUs and CPU-only |
| Gemma 4 E4B | `gemma4:e4b` | 6.6 GB | 128K | analysis | good default for 6–8 GB GPUs: reliable JSON, ~2 s per photo; weaker on non-English tags |
| Qwen3-VL 4B / 2B Instruct | `qwen3-vl:4b-instruct`, `qwen3-vl:2b-instruct` | 3.3 / 1.9 GB | 256K | analysis | small GPUs; JSON less reliable, tends to give too many tags |
| Qwen3-VL 8B Instruct | `qwen3-vl:8b-instruct` | 6.2 GB | 256K | analysis | best at naming what is in a photo; take an `-instruct` tag (the others think) |
| Qwen3.5 4B / 9B | `qwen3.5:4b`, `qwen3.5:9b` | 4 / 7.6 GB | 256K | analysis | fast captions; thinking model, set `AI_THINKING=off` |
| Gemma 4 12B | `gemma4:12b` | 8 GB | 256K | both | better captions on a 12 GB GPU |
| Gemma 4 26B (MoE) | `gemma4:26b` | 16–19 GB | 256K | both | 24 GB GPU: large-model quality at about the speed of a 4B model |
| Qwen3-VL 30B-A3B Instruct | `qwen3-vl:30b-a3b-instruct` | 20 GB | 256K | both | 24 GB GPU, fast mixture-of-experts |
| Qwen3.8 27B | `qwen3.8` | 18 GB | 256K | both | newest Qwen, 24 GB GPU; thinking model, set `AI_THINKING=off` |
| Qwen3 4B Instruct | `qwen3:4b-instruct` | 2.5 GB | 256K | slideshow | text only; for CPU-only and small GPUs |
| Qwen3 14B | `qwen3:14b` | 9.3 GB | 40K | slideshow | text only, 12 GB GPU; 40K context: use short *Photo details* above ~1000 photos; thinking model |
| gpt-oss 20B | `gpt-oss:20b` | 14 GB | 128K | slideshow | text only, 16 GB GPU, careful reasoning; a thinking model that may keep reasoning even with `AI_THINKING=off` (one request, so it only costs a little time) |
| Qwen3 30B-A3B Instruct | `qwen3:30b-instruct` | 19 GB | 256K | slideshow | text only, 24 GB GPU, fast mixture-of-experts, very large context |

Older models such as LLaVA, Llama 3.2 Vision, Qwen2.5-VL and Gemma 3 still work but are superseded by the
ones above. Without a GPU, `gemma4:e2b` or `qwen3-vl:2b-instruct` take about a minute per photo.

**Recommended pairs** (Settings → *Analysis model* / *Slideshow model*, or `ANALYSIS_MODEL` /
`SLIDESHOW_MODEL`):

| Hardware | Analysis model | Slideshow model |
|---|---|---|
| CPU only, 16 GB RAM | `gemma4:e2b` | `qwen3:4b-instruct` |
| 8 GB GPU | `gemma4:e4b` | `gemma4:e4b` (one model, nothing to swap) |
| 12 GB GPU | `qwen3-vl:8b-instruct` | `gemma4:12b`, or `qwen3:14b` for up to ~1000 photos |
| 16 GB GPU | `qwen3-vl:8b-instruct` | `gpt-oss:20b` |
| 24 GB GPU | `qwen3-vl:30b-a3b-instruct` or `gemma4:26b` | `qwen3:30b-instruct` |
| Apple Silicon 16 GB | as 8 GB GPU | as 8 GB GPU |
| Apple Silicon 24–32 GB | as 16 GB GPU | as 16 GB GPU |
| Apple Silicon 36 GB or more | as 24 GB GPU | as 24 GB GPU |

`setup.sh` / `setup.ps1` (see [Install and start](user-guide.md#install-and-start)) picks the row that
matches your GPU memory, downloads both models and creates `photos2show-slideshow`: the slideshow model
with a 32k context (`num_ctx 32768`), so the setting below is not needed. It shares the downloaded
files. Models chosen in Settings still override `.env`.

The two models do not need to fit at the same time: the analysis runs first, the slideshow afterwards,
and Ollama swaps them (a few seconds). Each one alone has to fit, the slideshow model together with
its 32k+ context; if it does not, Ollama runs part of it on the CPU, which is slower but fine for one
request. A Mac's GPU can use about two thirds to three quarters of its memory, hence the shifted tiers.
`AI_THINKING` applies to both models, so instruct (non-thinking) models avoid the trade-off.

**Ollama**

```bash
ollama pull gemma4:e4b
OLLAMA_CONTEXT_LENGTH=32768 ollama serve   # larger context for the slideshow step, see below
```

```ini
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=
MODEL=gemma4:e4b
AI_CONCURRENCY=1
AI_THINKING=off    # only matters for thinking models, harmless otherwise
```

Two models, e.g. for a 16 GB GPU: `ollama pull qwen3-vl:8b-instruct` and `ollama pull gpt-oss:20b`, then

```ini
ANALYSIS_MODEL=qwen3-vl:8b-instruct
SLIDESHOW_MODEL=gpt-oss:20b
```

**LM Studio**: download a vision model (e.g. *Gemma 4 E4B* or *Qwen3-VL 8B Instruct*, marked with an eye
icon), load it with a context length of 32k or more, and start the server in the *Developer* tab.

```ini
OPENAI_BASE_URL=http://localhost:1234/v1
OPENAI_API_KEY=
MODEL=    # the id LM Studio shows; photos2show-smoke lists it too
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
lookup for *auto* 10 s (3 s for the model list on the Settings page). A connection or server error is retried once (the `/models` lookup not at
all); a request that still fails is reported, and the next *Analyse again* retries the photo.
