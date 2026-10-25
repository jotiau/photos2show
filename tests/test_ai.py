import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from photos2show.ai import analyse_image, encode_image, parse_json


def test_parse_json_plain_and_fenced():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('Sure:\n```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    with pytest.raises(ValueError, match="got list"):
        parse_json('[{"a": 1}]')


def test_text_tags_become_a_list(monkeypatch):
    monkeypatch.setattr("photos2show.ai.complete_json", lambda c, s, m: {"tags": "beach, Sunset ,"})
    client = SimpleNamespace(with_options=lambda **kw: None)
    assert analyse_image(client, None, Image.new("RGB", (8, 8)))["tags"] == ["beach", "Sunset"]


def test_encode_image_downscales():
    url = encode_image(Image.new("RGB", (4000, 2000)))
    assert url.startswith("data:image/jpeg;base64,")
    img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert img.size == (1024, 512)
