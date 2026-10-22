"""Find media files, read capture dates, extract video frames."""
import re
from datetime import datetime
from pathlib import Path

import cv2
from PIL import Image, ImageOps

IMAGE_EXT = {".jpg", ".jpeg"}
VIDEO_EXT = {".mp4"}
VIDEO_POSITIONS = (0.1, 0.5, 0.9)  # sampled frames, as fraction of the duration
LOAD_EDGE = 2048  # decode JPEGs at reduced size; analysis and thumbnails need less


def find_media(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXT | VIDEO_EXT and not p.name.startswith(".")
    )


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


def read_image(path: Path) -> tuple[Image.Image, str | None, tuple[int, int]]:
    """Return (oriented RGB image, capture time as ISO string or None, original size)."""
    img = Image.open(path)
    size = img.size
    exif = img.getexif()
    raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)  # DateTimeOriginal, DateTime
    taken = None
    if raw:
        try:
            taken = datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S").isoformat()
        except ValueError:
            pass
    img.draft("RGB", (LOAD_EDGE, LOAD_EDGE))
    img = ImageOps.exif_transpose(img).convert("RGB")
    if exif.get(274, 1) in (5, 6, 7, 8):  # rotated by 90°
        size = size[::-1]
    return img, taken, size


def read_video_frames(path: Path) -> tuple[list[Image.Image], tuple[int, int]]:
    cap = cv2.VideoCapture(str(path))
    try:
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        frames = []
        for pos in VIDEO_POSITIONS:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(pos * max(count - 1, 0)))
            ok, bgr = cap.read()
            if ok:
                img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                img.thumbnail((LOAD_EDGE, LOAD_EDGE))
                frames.append(img)
    finally:
        cap.release()
    if not frames:
        raise ValueError(f"no readable frames in {path}")
    return frames, size


NAME_DATE = re.compile(r"(20\d{6})[_-](\d{6})")  # phone naming, e.g. 20260725_130945.mp4


def file_time(path: Path) -> str:
    """Capture time for videos: from the file name if it has one, else file mtime.

    mtime can be off by hours after copying (seen: exactly 1 h, time zone).
    shortcut: MP4 creation_time is not read; add a metadata parser if names lack dates.
    """
    if m := NAME_DATE.search(path.stem):
        try:
            return datetime.strptime(m[1] + m[2], "%Y%m%d%H%M%S").isoformat()
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat()
