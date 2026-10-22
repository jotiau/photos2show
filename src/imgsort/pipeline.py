"""Analyse a folder: quality metrics, thumbnails, same-scene clusters and flags, cached in the library DB."""
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import imagehash
from PIL import Image

from imgsort import db, dedup, quality, scan

THUMB_EDGE = 400
COLUMNS = ("path", "kind", "size", "mtime", "taken_at", "width", "height",
           "thumb", "frames", "metrics", "score", "phash")
UPSERT = (
    f"INSERT INTO media ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))}) "
    f"ON CONFLICT(path) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in COLUMNS)}"
)


def _save_thumb(img: Image.Image, dest: Path) -> None:
    t = img.copy()
    t.thumbnail((THUMB_EDGE, THUMB_EDGE))
    t.save(dest, "JPEG", quality=80)


def analyse_file(path: Path, thumbs: Path) -> dict:
    name = hashlib.sha1(str(path).encode()).hexdigest()[:16]
    if scan.is_video(path):
        frames, (w, h) = scan.read_video_frames(path)
        for i, f in enumerate(frames):
            _save_thumb(f, thumbs / f"{name}_f{i}.jpg")
        # The sharpest sampled frame represents the video.
        m, rep = max(((quality.metrics(quality.prepare(f)), f) for f in frames), key=lambda x: x[0]["sharpness"])
        kind, taken = "video", scan.file_time(path)
    else:
        rep, taken, (w, h) = scan.read_image(path)
        m, kind, frames = quality.metrics(quality.prepare(rep)), "image", []
    _save_thumb(rep, thumbs / f"{name}.jpg")
    st = path.stat()
    return {
        "path": str(path), "kind": kind, "size": st.st_size, "mtime": st.st_mtime,
        "taken_at": taken, "width": w, "height": h, "thumb": f"{name}.jpg", "frames": len(frames),
        "metrics": json.dumps(m), "score": quality.score(m), "phash": str(imagehash.phash(rep)),
    }


def analyse_folder(root: Path, progress: Callable[[int, int], None] | None = None) -> dict:
    """Analyse new/changed files under root (unchanged files are cached). Returns a summary."""
    conn = db.connect(root)
    thumbs = db.library_dir(root) / "thumbs"
    paths = scan.find_media(root)
    known = {r["path"]: (r["size"], r["mtime"]) for r in conn.execute("SELECT path, size, mtime FROM media")}
    errors, analysed = [], 0
    for i, path in enumerate(paths):
        st = path.stat()
        if known.get(str(path)) != (st.st_size, st.st_mtime):
            try:
                row = analyse_file(path, thumbs)
            except Exception as e:  # one broken file must not stop the whole scan
                errors.append(f"{path.name}: {e}")
            else:
                conn.execute(UPSERT, [row[c] for c in COLUMNS])
                conn.commit()
                analysed += 1
        if progress:
            progress(i + 1, len(paths))
    present = {str(p) for p in paths}
    conn.executemany("DELETE FROM media WHERE path = ?", [(p,) for p in known if p not in present])
    update_flags(conn)
    conn.commit()
    conn.close()
    return {"total": len(paths), "analysed": analysed, "errors": errors}


def update_flags(conn) -> None:
    items = db.all_media(conn)
    # Very blurry images and videos never compete for "best of scene".
    candidates = [it for it in items if it["kind"] == "image" and quality.VERY_BLURRY <= it["metrics"]["sharpness"]]
    best = dedup.best_of_clusters(candidates)
    for it in items:
        flags = quality.flags(it["metrics"])
        dup = best.get(it["id"])
        dup = dup if dup != it["id"] else None
        if dup:
            flags.append("duplicate")
        conn.execute("UPDATE media SET flags = ?, duplicate_of = ? WHERE id = ?", (json.dumps(flags), dup, it["id"]))


def skip_ai_reason(item: dict) -> str | None:
    """Why an item is not sent to the LLM (saves tokens), or None if it should be analysed."""
    if "very_blurry" in item["flags"]:
        return "very blurry"
    if item["duplicate_of"]:
        return f"same scene as #{item['duplicate_of']}"
    return None
