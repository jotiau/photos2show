import base64
import io
import json
import re

from openai import BadRequestError, OpenAI
from PIL import Image, ImageOps

from imgsort.config import Settings

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
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def make_client(settings: Settings) -> OpenAI:
    return OpenAI(base_url=settings.base_url, api_key=settings.api_key)


def analyse_image(client: OpenAI, settings: Settings, img: Image.Image) -> dict:
    return complete_json(client, settings, [{
        "role": "user",
        "content": [
            {"type": "text", "text": ANALYSE_PROMPT},
            {"type": "image_url", "image_url": {"url": encode_image(img)}},
        ],
    }])


def complete_json(client: OpenAI, settings: Settings, messages: list) -> dict:
    try:
        resp = client.chat.completions.create(
            model=settings.model, messages=messages, response_format={"type": "json_object"}
        )
    except BadRequestError:
        # Server may not support JSON mode; rely on the prompt instead.
        resp = client.chat.completions.create(model=settings.model, messages=messages)
    return parse_json(resp.choices[0].message.content)
