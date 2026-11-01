import base64
import io
import json

import httpx
import openai
import pytest
from PIL import Image

from photos2show import db
from photos2show.ai import NO_THINKING, complete_json, encode_image, parse_json
from photos2show.config import Settings


def test_parse_json_plain_and_fenced():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('Sure:\n```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    with pytest.raises(ValueError, match="got list"):
        parse_json('[{"a": 1}]')


@pytest.mark.parametrize("raw, tags", [("beach, Sunset ,", ["beach", "Sunset"]), ([1, None, " sea "], ["1", "sea"]),
                                       (None, []), ({"a": 1}, [])])
def test_ai_tags_are_read_back_as_strings(raw, tags):
    row = {"metrics": None, "flags": None, "ai": json.dumps({"tags": raw})}
    assert db.row_to_dict(row)["ai"]["tags"] == tags


def test_encode_image_downscales():
    url = encode_image(Image.new("RGB", (4000, 2000)))
    assert url.startswith("data:image/jpeg;base64,")
    img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert img.size == (1024, 512)


class FakeClient:
    """Rejects the first `reject` requests with 400, like a server without JSON mode or no-thinking fields."""

    def __init__(self, reject):
        self.reject, self.calls = reject, []
        self.chat = self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.reject:
            raise openai.BadRequestError("rejected", body=None,
                                         response=httpx.Response(400, request=httpx.Request("POST", "http://x")))
        message = type("M", (), {"content": '{"a": 1}'})
        return type("R", (), {"choices": [type("C", (), {"message": message})]})


@pytest.mark.parametrize("thinking, reject, sent", [
    (True, 0, [(True, {})]),
    (True, 1, [(True, {}), (False, {})]),
    (False, 0, [(True, NO_THINKING)]),
    (False, 2, [(True, NO_THINKING), (False, NO_THINKING), (False, None)]),  # fields rejected: plain request
])
def test_complete_json_falls_back_step_by_step(thinking, reject, sent):
    client = FakeClient(reject)
    assert complete_json(client, Settings("http://x", "k", "m", thinking), []) == {"a": 1}
    assert [("response_format" in c, c.get("extra_body")) for c in client.calls] == sent


def test_complete_json_gives_up_after_the_plain_request():
    with pytest.raises(openai.BadRequestError):
        complete_json(FakeClient(9), Settings("http://x", "k", "m", False), [])
