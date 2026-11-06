"""Find media files, read capture dates, extract video frames."""
import functools
import os
import re
import struct
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import rawpy
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()  # Image.open() reads iPhone HEIC photos like JPEGs, EXIF included

HEIF_EXT = {".heic", ".heif"}
BROWSER_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".bmp"}  # others are sent as JPEG
RAW_EXT = {".dng", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".orf", ".rw2", ".raf", ".pef", ".srw"}  # via LibRaw
IMAGE_EXT = BROWSER_IMAGE_EXT | HEIF_EXT | RAW_EXT | {".tif", ".tiff"}
MP4_EXT = {".mp4", ".mov", ".m4v", ".3gp"}  # ISO/QuickTime boxes: mp4_created() can read their date
VIDEO_EXT = MP4_EXT | {".avi", ".mkv", ".webm", ".mts", ".m2ts", ".wmv", ".mpg", ".mpeg"}
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
    if path.suffix.lower() in RAW_EXT:
        img, taken, size = read_raw(path)
        img.thumbnail((LOAD_EDGE, LOAD_EDGE))
        return img, taken, size
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
    img.thumbnail((LOAD_EDGE, LOAD_EDGE))  # draft() only shrinks JPEGs: a 24 MP HEIC would stay ~70 MB
    if exif.get(274, 1) in (5, 6, 7, 8):  # rotated by 90°
        size = size[::-1]
    return img, taken, size


def read_raw(path: Path, half_size: bool = True) -> tuple[Image.Image, str | None, tuple[int, int]]:
    """A camera RAW file developed by LibRaw (rotated, camera white balance), like read_image().
    half_size: 4x faster, plenty for analysis and previews; the export develops at full size."""
    with rawpy.imread(str(path)) as raw:
        s = raw.sizes  # before postprocess(): half_size halves them
        rgb = raw.postprocess(half_size=half_size, use_camera_wb=True)
        stamp = raw.other.timestamp  # EXIF capture time, read as local time like read_image()
    size = (s.height, s.width) if s.flip in (5, 6) else (s.width, s.height)  # 5, 6: rotated by 90°
    return Image.fromarray(rgb), stamp.isoformat() if stamp and stamp.year > 1970 else None, size


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
    """Capture time of an MP4/MOV as local ISO time, or None if missing or unset.

    iPhones store the local time of the trip with its offset (moov/meta, com.apple.quicktime.creationdate): kept as
    it is, like the photos' EXIF time. Else the movie header (moov/mvhd), which is in UTC: converted with this
    computer's time zone, so a clip filmed in another time zone is off by the difference.
    """
    try:
        with open(path, "rb") as f:
            end = f.seek(0, 2)
            f.seek(0)
            for kind, start, stop in _boxes(f, end):
                if kind != b"moov":
                    continue
                f.seek(start)
                header = None
                for kind, start, stop in _boxes(f, stop):
                    if kind == b"meta" and (local := _apple_created(f, start, stop)):
                        return local
                    if kind == b"mvhd":  # meta usually follows it: keep looking
                        f.seek(start)
                        version = f.read(1)[0]
                        f.read(3)  # flags
                        secs = struct.unpack(">Q" if version == 1 else ">I", f.read(8 if version == 1 else 4))[0]
                        if secs:  # 0 = not set (common in edited or exported clips)
                            t = (MP4_EPOCH + timedelta(seconds=secs)).astimezone().replace(tzinfo=None)
                            header = t.isoformat()
                return header
    except (OSError, struct.error, IndexError, OverflowError, ValueError):
        return None
    return None


def _apple_created(f, start: int, stop: int) -> str | None:
    """com.apple.quicktime.creationdate from a moov/meta box (keys name it, ilst holds the value by key number)."""
    f.seek(start)
    if f.read(4) != bytes(4):  # QuickTime's meta has no version/flags, MP4's has
        f.seek(start)
    keys, values = [], {}
    for kind, s, e in _boxes(f, stop):
        f.seek(s + 4)  # version/flags
        if kind == b"keys":
            for _ in range(struct.unpack(">I", f.read(4))[0]):
                size = struct.unpack(">I", f.read(4))[0]
                keys.append(f.read(size - 4)[4:])  # size counts itself; skip the namespace (mdta)
        elif kind == b"ilst":
            f.seek(s)
            for number, s2, e2 in _boxes(f, e):
                for data, s3, e3 in _boxes(f, e2):
                    if data == b"data":
                        f.seek(s3 + 8)  # type and locale
                        values[int.from_bytes(number)] = f.read(e3 - s3 - 8)
    if b"com.apple.quicktime.creationdate" in keys:
        value = values.get(keys.index(b"com.apple.quicktime.creationdate") + 1)
        try:  # e.g. 2026-08-11T10:00:00-0400: the clock time where it was filmed
            return datetime.fromisoformat(value.decode()).replace(tzinfo=None).isoformat()
        except (AttributeError, ValueError):  # missing or unreadable: the movie header is used
            pass
    return None


def file_time(path: Path) -> str:
    """Capture time of a video or an image without EXIF date: from the file name if it has one,
    else the MP4 header (videos), else the earlier of file creation and modification time.

    Copying sets the creation time to the copy date but usually keeps mtime; editing does the reverse.
    mtime can be off by hours after copying (seen: exactly 1 h, time zone).
    """
    if m := NAME_DATE.search(path.stem):
        try:
            return datetime.strptime(m[1] + m[2], "%Y%m%d%H%M%S").isoformat()
        except ValueError:
            pass
    if path.suffix.lower() in MP4_EXT and (header := mp4_created(path)):
        return header
    st = path.stat()
    t = min(filter(None, (birth_time(path, st), st.st_mtime)))
    return datetime.fromtimestamp(t).replace(microsecond=0).isoformat()


def birth_time(path: Path, st: os.stat_result) -> float | None:
    """File creation time, None if the system or file system doesn't keep it."""
    if hasattr(st, "st_birthtime"):  # macOS, Windows
        return st.st_birthtime or None
    try:  # Linux: Python < 3.15 can't read it, GNU stat can (0 or "-" when unknown)
        out = subprocess.run(["stat", "-c", "%W", str(path)], capture_output=True, text=True, check=True).stdout
        return float(out) or None
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
