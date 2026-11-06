import os
import struct
from datetime import datetime, timezone

import pytest

from photos2show.scan import file_time, find_media, mp4_created


def test_file_time_prefers_name_over_mtime(tmp_path):
    named = tmp_path / "20260715_131305.mp4"
    other = tmp_path / "clip.mp4"
    bad = tmp_path / "20261399_999999.mp4"
    for f in (named, other, bad):
        f.write_bytes(b"")
        os.utime(f, (0, datetime(2026, 7, 15, 12, 13, 18).timestamp()))  # 1 h off, like a copied file
    assert file_time(named) == "2026-07-15T13:13:05"
    assert file_time(other) == file_time(bad) == "2026-07-15T12:13:18"


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mvhd(secs: int, version: int = 0) -> bytes:
    time = struct.pack(">Q" if version else ">I", secs)
    return box(b"mvhd", bytes([version, 0, 0, 0]) + time + time + bytes(80))


def local(utc: datetime) -> str:
    return utc.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None).isoformat()


def test_mp4_creation_time_from_movie_header(tmp_path):
    secs = int((datetime(2026, 7, 15, 11, 13, 5) - datetime(1904, 1, 1)).total_seconds())
    ftyp, moov = box(b"ftyp", b"isom" + bytes(4)), box(b"moov", mvhd(secs))
    big_mdat = struct.pack(">I4sQ", 1, b"mdat", 16 + 5) + bytes(5)  # 64-bit size, moov after the media
    cases = {
        "start.mp4": ftyp + moov + box(b"mdat", bytes(10)),
        "end.mp4": ftyp + big_mdat + moov,
        "v1.mp4": ftyp + box(b"moov", box(b"trak", b"") + mvhd(secs, version=1)),
    }
    for name, data in cases.items():
        (tmp_path / name).write_bytes(data)
        assert mp4_created(tmp_path / name) == local(datetime(2026, 7, 15, 11, 13, 5)), name

    unset, junk, empty = tmp_path / "unset.mp4", tmp_path / "junk.mp4", tmp_path / "empty.mp4"
    unset.write_bytes(ftyp + box(b"moov", mvhd(0)))
    junk.write_bytes(b"\x00\x00\x00\x04oops" + bytes(3))
    empty.write_bytes(b"")
    assert mp4_created(unset) is None and mp4_created(junk) is None and mp4_created(empty) is None
    os.utime(unset, (0, datetime(2026, 7, 15, 12, 13, 18).timestamp()))
    assert file_time(unset) == "2026-07-15T12:13:18"  # no header time: mtime

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(cases["end.mp4"])
    assert file_time(clip) == local(datetime(2026, 7, 15, 11, 13, 5))  # header beats mtime


def test_hidden_files_and_folders_are_skipped(tmp_path):
    for name in ("a.jpg", ".b.jpg", ".Trash-1000/files/c.jpg", "sub/d.JPG", "e.png", "f.txt", "g.MOV", "h.webp"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"")
    found = [p.relative_to(tmp_path).as_posix() for p in find_media(tmp_path)]
    assert found == ["a.jpg", "e.png", "g.MOV", "h.webp", "sub/d.JPG"]


def test_common_image_formats_are_read(tmp_path):
    import numpy as np
    from PIL import Image

    from photos2show.scan import read_image
    cases = {"a.webp": Image.new("RGBA", (60, 40), (0, 0, 255, 100)), "b.avif": Image.new("RGB", (60, 40)),
             "c.tiff": Image.fromarray(np.full((40, 60), 40000, np.uint16)), "d.gif": Image.new("P", (60, 40)),
             "e.bmp": Image.new("RGB", (60, 40))}
    for name, img in cases.items():
        img.save(tmp_path / name)
    assert len(find_media(tmp_path)) == len(cases)
    for name in cases:
        img, _, size = read_image(tmp_path / name)
        assert img.mode == "RGB" and img.size == size == (60, 40), name


def test_heic_is_found_and_read_with_date_and_rotation(tmp_path):
    from PIL import Image

    from photos2show.scan import read_image
    exif = Image.Exif()
    exif[306], exif[274] = "2026:07:14 09:30:00", 6  # DateTime; rotated 90°
    Image.new("RGB", (60, 40), "red").save(tmp_path / "IMG_0001.HEIC", exif=exif.tobytes())
    assert find_media(tmp_path) == [tmp_path / "IMG_0001.HEIC"]
    img, taken, size = read_image(tmp_path / "IMG_0001.HEIC")
    assert taken == "2026-07-14T09:30:00" and img.size == size == (40, 60)


def test_raw_is_found_and_developed_with_date_and_rotation(tmp_path):
    from PIL import Image

    from photos2show import testdata
    from photos2show.scan import read_image
    testdata.save_dng(Image.new("RGB", (60, 40), "red"), tmp_path / "IMG_0001.DNG", datetime(2026, 7, 14, 9, 30), 6)
    assert find_media(tmp_path) == [tmp_path / "IMG_0001.DNG"]
    img, taken, size = read_image(tmp_path / "IMG_0001.DNG")
    assert taken == "2026-07-14T09:30:00" and img.mode == "RGB" and img.size == size == (40, 60)


def test_file_time_takes_the_creation_time_when_it_is_earlier(tmp_path):
    from photos2show.scan import birth_time
    edited = tmp_path / "edited.png"  # created now, modified "later" (an edit)
    edited.write_bytes(b"")
    os.utime(edited, (0, datetime(2030, 1, 1).timestamp()))
    if birth_time(edited, edited.stat()) is None:
        pytest.skip("file system keeps no creation time")
    assert file_time(edited)[:4] == str(datetime.now().year)


def test_iphone_video_keeps_the_local_time_where_it_was_filmed(tmp_path):
    utc = int((datetime(2026, 8, 11, 14, 0, 0) - datetime(1904, 1, 1)).total_seconds())  # 10:00 in New York
    key = lambda name: struct.pack(">I", 8 + len(name)) + b"mdta" + name  # noqa: E731
    keys = box(b"keys", bytes(4) + struct.pack(">I", 2) + key(b"com.apple.quicktime.make")
               + key(b"com.apple.quicktime.creationdate"))
    item = lambda n, text: box(struct.pack(">I", n), box(b"data", struct.pack(">II", 1, 0) + text))  # noqa: E731
    ilst = box(b"ilst", item(1, b"Apple") + item(2, b"2026-08-11T10:00:00-0400"))
    ftyp = box(b"ftyp", b"qt  " + bytes(4))
    cases = {
        "IMG_1.MOV": (box(b"meta", box(b"hdlr", bytes(25)) + keys + ilst), "2026-08-11T10:00:00"),  # QuickTime meta
        "IMG_2.MOV": (box(b"meta", bytes(4) + keys + ilst), "2026-08-11T10:00:00"),  # MP4 meta: version/flags
        "IMG_3.MOV": (box(b"meta", keys + box(b"ilst", item(2, b"not a date"))), local(datetime(2026, 8, 11, 14))),
    }
    for name, (meta, expected) in cases.items():
        (tmp_path / name).write_bytes(ftyp + box(b"moov", mvhd(utc) + meta))  # meta after mvhd, as iPhones write it
        assert mp4_created(tmp_path / name) == expected, name
