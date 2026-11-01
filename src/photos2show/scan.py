"""Find media files, read capture dates, extract video frames."""
import functools
import re
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()  # Image.open() reads iPhone HEIC photos like JPEGs, EXIF included

HEIF_EXT = {".heic", ".heif"}
IMAGE_EXT = {".jpg", ".jpeg"} | HEIF_EXT
VIDEO_EXT = {".mp4"}
VIDEO_POSITIONS = (0.1, 0.5, 0.9)  # sampled frames, as fraction of the duration
LOAD_EDGE = 2048  # decode JPEGs at reduced size; analysis and thumbnails need less


def find_media(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXT | VIDEO_EXT
        # hidden folders too: .Trash-1000 on a USB stick holds deleted photos
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
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


@functools.lru_cache(maxsize=4096)
def video_seconds(path: str, mtime: float) -> float:
    """Length of a video, 0 if unreadable. mtime is only part of the cache key: a changed file is read again."""
    cap = cv2.VideoCapture(path)
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        return cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps > 0 else 0.0
    finally:
        cap.release()


NAME_DATE = re.compile(r"(20\d{6})[_-](\d{6})")  # phone naming, e.g. 20260725_130945.mp4


MP4_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)


def _boxes(f, end: int):
    """Yield (type, payload start, payload end) of the MP4 boxes from the current position up to end."""
    while (pos := f.tell()) + 8 <= end:
        size, kind = struct.unpack(">I4s", f.read(8))
        header = 8
        if size == 1:  # 64-bit size follows
            size, header = struct.unpack(">Q", f.read(8))[0], 16
        elif size == 0:  # box runs to the end of the file
            size = end - pos
        if size < header:
            return
        yield kind, pos + header, pos + size
        f.seek(pos + size)


def mp4_created(path: Path) -> str | None:
    """creation_time of the MP4 movie header (moov/mvhd) as local ISO time, or None if missing or unset.

    Phones store it in UTC; converted with this computer's time zone, like the photo dates.
    """
    try:
        with open(path, "rb") as f:
            end = f.seek(0, 2)
            f.seek(0)
            for kind, start, stop in _boxes(f, end):
                if kind != b"moov":
                    continue
                f.seek(start)
                for kind, start, _ in _boxes(f, stop):
                    if kind == b"mvhd":
                        f.seek(start)
                        version = f.read(1)[0]
                        f.read(3)  # flags
                        secs = struct.unpack(">Q" if version == 1 else ">I", f.read(8 if version == 1 else 4))[0]
                        if not secs:  # 0 = not set (common in edited or exported clips)
                            return None
                        t = (MP4_EPOCH + timedelta(seconds=secs)).astimezone().replace(tzinfo=None)
                        return t.isoformat()
                return None
    except (OSError, struct.error, IndexError, OverflowError):
        return None
    return None


def file_time(path: Path) -> str:
    """Capture time for videos: from the file name if it has one, else the MP4 header, else file mtime.

    mtime can be off by hours after copying (seen: exactly 1 h, time zone).
    """
    if m := NAME_DATE.search(path.stem):
        try:
            return datetime.strptime(m[1] + m[2], "%Y%m%d%H%M%S").isoformat()
        except ValueError:
            pass
    return mp4_created(path) or datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat()
