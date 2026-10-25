"""M0 smoke test: send one image to the configured model and print the analysis.

Usage: photos2show-smoke [IMAGE]   (without IMAGE a synthetic test picture is used)
"""
import json
import sys
import time

from PIL import Image, ImageDraw

from photos2show.ai import analyse_image, make_client
from photos2show.config import load_settings


def synthetic_image() -> Image.Image:
    """Blue sky, green ground, yellow sun, red house – easy to verify the model really sees it."""
    img = Image.new("RGB", (800, 600), (110, 170, 235))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 400, 800, 600], fill=(60, 150, 60))
    d.ellipse([600, 60, 720, 180], fill=(250, 220, 40))
    d.rectangle([200, 280, 380, 420], fill=(200, 40, 40))
    d.polygon([(180, 280), (290, 190), (400, 280)], fill=(120, 60, 30))
    return img


def main() -> None:
    settings = load_settings()
    client = make_client(settings)
    print(f"Endpoint: {settings.base_url}  model: {settings.model}")

    try:
        models = [m.id for m in client.models.list()]
        print(f"Models on server: {', '.join(models)}")
    except Exception as e:  # listing is optional on some servers
        print(f"Could not list models ({e.__class__.__name__}); continuing.")

    if len(sys.argv) > 1:
        img = Image.open(sys.argv[1])
        print(f"Image: {sys.argv[1]}")
    else:
        img = synthetic_image()
        print("Image: synthetic (sky, sun, red house) – the caption should mention those.")

    start = time.perf_counter()
    result = analyse_image(client, settings, img)
    print(f"Took {time.perf_counter() - start:.1f}s")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
