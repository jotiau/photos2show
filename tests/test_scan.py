import os
from datetime import datetime

from imgsort.scan import file_time


def test_file_time_prefers_name_over_mtime(tmp_path):
    named = tmp_path / "20260715_131305.mp4"
    other = tmp_path / "clip.mp4"
    bad = tmp_path / "20261399_999999.mp4"
    for f in (named, other, bad):
        f.write_bytes(b"")
        os.utime(f, (0, datetime(2026, 7, 15, 12, 13, 18).timestamp()))  # 1 h off, like a copied file
    assert file_time(named) == "2026-07-15T13:13:05"
    assert file_time(other) == file_time(bad) == "2026-07-15T12:13:18"
