"""Analyse a folder: quality metrics, thumbnails, same-scene clusters and flags, cached in the library DB."""
import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import imagehash
from PIL import Image

from imgsort import ai, db, dedup, quality, scan, slideshow
from imgsort.config import default_concurrency, load_settings

THUMB_EDGE = 400
COLUMNS = ("path", "kind", "size", "mtime", "taken_at", "width", "height",
           "thumb", "frames", "metrics", "score", "phash", "ai")
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
        "ai": None,  # a changed file needs a new description
    }


def analyse_folder(root: Path, progress: Callable[[int, int], None] | None = None) -> dict:
    """Analyse new/changed files under root (unchanged files are cached). Returns a summary."""
    root = root.resolve()  # stored paths must not depend on how the folder was typed
    if not root.is_dir():  # e.g. unplugged drive: would otherwise delete every row as "gone"
        raise FileNotFoundError(f"Folder not found: {root}")
    conn = db.connect(root)
    thumbs = db.library_dir(root) / "thumbs"
    paths = scan.find_media(root)
    known = {r["path"]: (r["size"], r["mtime"]) for r in conn.execute("SELECT path, size, mtime FROM media")}
    errors, analysed = [], 0
    for i, path in enumerate(paths):
        try:
            st = path.stat()  # inside: the file may vanish during the scan (phone sync)
            row = analyse_file(path, thumbs) if known.get(str(path)) != (st.st_size, st.st_mtime) else None
        except Exception as e:  # one broken file must not stop the whole scan
            errors.append(f"{path.name}: {e}")
        else:
            if row:
                conn.execute(UPSERT, [row[c] for c in COLUMNS])
                conn.commit()
                analysed += 1
        if progress:
            progress(i + 1, len(paths))
    present = {str(p) for p in paths}
    gone = {r["id"] for r in conn.execute("SELECT id, path FROM media") if r["path"] not in present}
    # SQLite gives a deleted top id to the next new file, so the slideshow must forget gone ids now.
    selection = slideshow.load(root)
    if any(p["id"] in gone for p in selection["picks"]):
        slideshow.save(root, {**selection, "picks": [p for p in selection["picks"] if p["id"] not in gone]})
    conn.executemany("DELETE FROM media WHERE id = ?", [(i,) for i in gone])
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


def describe_folder(root: Path, progress: Callable[[int, int], None] | None = None,
                    concurrency: int | None = None) -> dict:
    """Pass 2: send each image still worth describing to the LLM; every result is saved right away."""
    settings = load_settings()
    client = ai.make_client(settings)
    conn = db.connect(root)
    todo = [it for it in db.all_media(conn) if it["ai"] is None and skip_ai_reason(it) is None]

    def describe(it: dict) -> dict:
        path = Path(it["path"])
        if it["kind"] == "video":
            frames, _ = scan.read_video_frames(path)
            img = frames[len(frames) // 2]
        else:
            img = scan.read_image(path)[0]
        try:
            return ai.analyse_image(client, settings, img)
        except Exception as e:
            # The traceback's frames hold the decoded image (~30 MB); failed futures waiting in
            # as_completed would keep every one of them alive (20 GB with 857 failures).
            del img
            raise e.with_traceback(None)

    errors = []
    with ThreadPoolExecutor(concurrency or default_concurrency()) as pool:
        futures = {pool.submit(describe, it): it for it in todo}
        for i, fut in enumerate(as_completed(futures)):
            # pop: don't keep every finished future (and its result or error) until the run ends
            it = futures.pop(fut)
            try:
                conn.execute("UPDATE media SET ai = ? WHERE id = ?", (json.dumps(fut.result()), it["id"]))
                conn.commit()
            except Exception as e:  # one failed request must not stop the run; it is retried next time
                errors.append(f"{Path(it['path']).name}: {e}")
            if progress:
                progress(i + 1, len(todo))
    conn.close()
    return {"described": len(todo) - len(errors), "errors": errors}
