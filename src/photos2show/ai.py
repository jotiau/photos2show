import base64
import io
import json
import re

from openai import BadRequestError, OpenAI
from PIL import Image, ImageOps

from photos2show.config import Settings

LLM_EDGE = 1024

ANALYSE_PROMPT = """You are helping to sort holiday photos for a slideshow.
Describe this image and answer ONLY with a JSON object with these keys:
- "caption": one sentence describing the content
- "tags": list of 3-10 short lowercase content tags (e.g. "beach", "sunset", "people", "food")
- "scene": one of "landscape", "people", "portrait", "group", "food", "city", "architecture",
  "nature", "animal", "activity", "interior", "document", "other"
- "people_count": number of clearly visible people
- "aesthetic": 1-10, how visually pleasing the photo is (composition, light, colour)
- "interest": 1-10, how interesting/memorable it would be in a holiday slideshow
- "issues": list of visible problems (e.g. "closed eyes", "tilted horizon", "motion blur"), may be empty
"""


def encode_image(img: Image.Image, edge: int = LLM_EDGE) -> str:
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((edge, edge))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def parse_json(text: str) -> dict:
    """Parse a JSON object, tolerating markdown fences or surrounding prose."""
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        result = json.loads(match.group(0))
    if not isinstance(result, dict):  # e.g. [{...}]: would be cached and break every later .get()
        raise ValueError(f"expected a JSON object, got {type(result).__name__}")
    return result


def make_client(settings: Settings) -> OpenAI:
    return OpenAI(base_url=settings.base_url, api_key=settings.api_key, max_retries=1)


def analyse_image(client: OpenAI, settings: Settings, img: Image.Image) -> dict:
    # Image calls take 2-12 s; a hung one must not block a worker for the 10 min default.
    return complete_json(client.with_options(timeout=90), settings, [{
        "role": "user",
        "content": [
            {"type": "text", "text": ANALYSE_PROMPT},
            {"type": "image_url", "image_url": {"url": encode_image(img)}},
        ],
    }])  # tags are cleaned when read back (db.row_to_dict)


def complete_json(client: OpenAI, settings: Settings, messages: list) -> dict:
    try:
        resp = client.chat.completions.create(
            model=settings.model, messages=messages, response_format={"type": "json_object"}
        )
    except BadRequestError:
        # Server may not support JSON mode; rely on the prompt instead.
        resp = client.chat.completions.create(model=settings.model, messages=messages)
    return parse_json(resp.choices[0].message.content)


BEST_PROMPT = """These {n} photos show the same scene (a burst or near-duplicates). Which one is best for a holiday
slideshow: sharp, well exposed, eyes open, good expression and composition?
Answer ONLY with JSON: {{"best": <photo number 1-{n}>}}"""
BEST_EDGE = 768  # several images per request: smaller than LLM_EDGE keeps it fast


def pick_best(client: OpenAI, settings: Settings, imgs: list[Image.Image]) -> int:
    """Index of the best of several shots of one scene, judged side by side."""
    content = [{"type": "text", "text": BEST_PROMPT.format(n=len(imgs))}]
    for i, img in enumerate(imgs, 1):
        content += [{"type": "text", "text": f"Photo {i}:"},
                    {"type": "image_url", "image_url": {"url": encode_image(img, BEST_EDGE)}}]
    best = complete_json(client.with_options(timeout=120), settings, [{"role": "user", "content": content}])
    n = int(best.get("best"))  # raw LLM output: a bad answer raises and the technical best stays
    if not 1 <= n <= len(imgs):
        raise ValueError(f"best photo {n} is not one of 1-{len(imgs)}")
    return n - 1


def context_length(client: OpenAI, settings: Settings) -> int | None:
    """The model's context size in tokens if the server lists it under /models (e.g. vLLM), else None.

    Not llama.cpp's meta.n_ctx_train: that is the training size, often far above the size the server runs with.
    """
    try:
        models = client.models.list().data
    except Exception:  # not offered by every server; unknown is fine
        return None
    for m in models:
        if m.id == settings.model:
            d = m.model_dump()
            for v in (d.get("max_model_len"), d.get("context_length"), d.get("max_context_length")):
                if isinstance(v, int) and v > 0:
                    return v
    return None
