"""Generate a folder of synthetic holiday photos with known quality problems (for tests and the demo)."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

SIZE = (1600, 1200)
START = datetime(2026, 8, 11, 9, 0, 0)

# file name -> flags the pipeline is expected to set
EXPECTED = {
    "scene_1.jpg": [],
    "scene_2.jpg": [],
    "scene_3.jpg": [],
    "blurry.jpg": ["blurry"],
    "very_blurry.jpg": ["very_blurry"],
    "dark.jpg": ["too_dark", "low_contrast", "monotone"],
    "bright.jpg": ["too_bright", "low_contrast", "monotone"],
    "grey_sky.jpg": ["low_contrast", "monotone"],
    "burst_1.jpg": [],
    "burst_2.jpg": ["duplicate"],
    "burst_3.jpg": ["blurry", "duplicate"],
    "video.mp4": [],
}


def scene(seed: int) -> Image.Image:
    """Textured landscape: sky gradient, hills, houses, trees and fine grain."""
    rng = np.random.default_rng(seed)
    w, h = SIZE
    y = np.linspace(0, 1, h)[:, None, None]
    sky = (np.array([90, 150, 230]) * (1 - y) + np.array([200, 220, 250]) * y) * np.ones((1, w, 1))
    img = Image.fromarray(sky.astype(np.uint8))
    d = ImageDraw.Draw(img)
    horizon = int(h * rng.uniform(0.45, 0.6))
    pts = [(0, h)] + [(x, horizon + int(60 * np.sin(x / rng.uniform(80, 200)))) for x in range(0, w + 40, 40)] + [(w, h)]
    d.polygon(pts, fill=tuple(int(v) for v in rng.integers(30, 120, 3)))
    for _ in range(200):
        x, yy, s = int(rng.integers(0, w)), int(rng.integers(horizon, h)), int(rng.integers(10, 80))
        color = tuple(int(v) for v in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            d.rectangle([x, yy - s, x + s, yy], fill=color, outline=(20, 20, 20), width=2)
        else:
            d.ellipse([x, yy - s, x + s // 2, yy], fill=color, outline=(20, 20, 20), width=2)
    grain = rng.normal(0, 3, (h, w, 1))
    return Image.fromarray(np.clip(np.asarray(img) + grain, 0, 255).astype(np.uint8))


def save_jpg(img: Image.Image, path: Path, taken: datetime) -> None:
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = taken.strftime("%Y:%m:%d %H:%M:%S")
    img.save(path, "JPEG", quality=90, exif=exif)


def save_video(path: Path, seed: int) -> None:
    base = np.asarray(scene(seed).resize((800, 600)))
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (640, 480))
    for i in range(30):  # 3 s pan
        out.write(cv2.cvtColor(np.ascontiguousarray(base[60:540, i * 5:i * 5 + 640]), cv2.COLOR_RGB2BGR))
    out.release()


def generate(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    t = lambda minutes: START + timedelta(minutes=minutes)  # noqa: E731
    for i in (1, 2, 3):
        save_jpg(scene(i), folder / f"scene_{i}.jpg", t(60 * i))
    # Each defect gets its own scene, otherwise they would (correctly) count as duplicates.
    save_jpg(scene(7).filter(ImageFilter.GaussianBlur(2)), folder / "blurry.jpg", t(300))
    save_jpg(scene(8).filter(ImageFilter.GaussianBlur(15)), folder / "very_blurry.jpg", t(310))
    save_jpg(ImageEnhance.Brightness(scene(9)).enhance(0.1), folder / "dark.jpg", t(320))
    save_jpg(Image.blend(scene(10), Image.new("RGB", SIZE, "white"), 0.85), folder / "bright.jpg", t(330))
    sky = np.linspace(150, 190, SIZE[1])[:, None, None] * np.ones((1, SIZE[0], 3))
    save_jpg(Image.fromarray(sky.astype(np.uint8)), folder / "grey_sky.jpg", t(340))
    # Burst: three shots of the same scene seconds apart; #1 is the sharpest.
    burst = scene(42)
    save_jpg(burst, folder / "burst_1.jpg", t(400))
    save_jpg(ImageEnhance.Brightness(burst.crop((8, 6, 1608, 1206))).enhance(0.93), folder / "burst_2.jpg",
             t(400) + timedelta(seconds=2))
    save_jpg(burst.filter(ImageFilter.GaussianBlur(2)), folder / "burst_3.jpg", t(400) + timedelta(seconds=4))
    save_video(folder / "video.mp4", 99)
    return folder


def main() -> None:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_images")
    print(f"Generated test images in {generate(folder)}")


if __name__ == "__main__":
    main()
