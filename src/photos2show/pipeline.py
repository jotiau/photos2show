"""Analyse a folder: quality metrics, thumbnails, same-scene clusters and flags, cached in the library DB."""
import hashlib
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import imagehash
from PIL import Image

from photos2show import ai, db, dedup, quality, scan, slideshow
from photos2show.config import default_concurrency, load_settings

THUMB_EDGE = 400
COLUMNS = ("path", "kind", "size", "mtime", "taken_at", "width", "height",
           "thumb", "frames", "metrics", "score", "phash", "ai", "flags")
UPSERT = (
    f"INSERT INTO media ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))}) "
    f"ON CONFLICT(path) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in COLUMNS)}"
)


def _save_thumb(img: Image.Image, dest: Path) -> None:
    t = img.copy()
    t.thumbnail((THUMB_EDGE, THUMB_EDGE))
    t.save(dest, "JPEG", quality=80)


def _sharpest(frames: list[Image.Image]) -> tuple[dict, Image.Image]:
    """(metrics, frame) of the sharpest sampled frame: it represents a video in the UI and for the AI."""
    return max(((quality.metrics(quality.prepare(f)), f) for f in frames), key=lambda x: x[0]["sharpness"])


def analyse_file(path: Path, thumbs: Path) -> dict:
    name = hashlib.sha1(str(path).encode()).hexdigest()[:16]
    for old in thumbs.glob(f"{name}_*.jpg"):  # a changed file: old preview, a video may now give fewer frames
        old.unlink()
    if scan.is_video(path):
        frames, (w, h) = scan.read_video_frames(path)
        for i, f in enumerate(frames):
            _save_thumb(f, thumbs / f"{name}_f{i}.jpg")
        m, rep = _sharpest(frames)
        kind, taken = "video", scan.file_time(path)
    else:
        rep, taken, (w, h) = scan.read_image(path)
        taken = taken or scan.file_time(path)  # screenshots, PNGs, edited copies: no EXIF date
        m, kind, frames = quality.metrics(quality.prepare(rep)), "image", []
    _save_thumb(rep, thumbs / f"{name}.jpg")
    st = path.stat()
    return {
        "path": str(path), "kind": kind, "size": st.st_size, "mtime": st.st_mtime,
        "taken_at": taken, "width": w, "height": h, "thumb": f"{name}.jpg", "frames": len(frames),
        "metrics": json.dumps(m), "score": quality.score(m), "phash": str(imagehash.phash(rep)),
        "ai": None,  # a changed file needs a new description
        "flags": None,  # not computed yet: update_flags() runs while any row is NULL, even after a crash
    }


def analyse_folder(root: Path, progress: Callable[[int, int], None] | None = None,
                   stop: threading.Event | None = None) -> dict:
    """Analyse new/changed files under root (unchanged files are cached). Returns a summary.

    stop: set it to end early; what is scanned so far is kept, the rest follows on the next run.
    """
    root = root.resolve()  # stored paths must not depend on how the folder was typed
    if not root.is_dir():  # e.g. unplugged drive: would otherwise delete every row as "gone"
        raise FileNotFoundError(f"Folder not found: {root}")
    conn = db.connect(root)
    thumbs = db.library_dir(root) / "thumbs"
    paths = scan.find_media(root)
    known = {r["path"]: r for r in conn.execute("SELECT path, size, mtime, kind, taken_at FROM media")}
    errors, analysed = [], 0
    for i, path in enumerate(paths):
        if stop and stop.is_set():
            break
        try:
            st = path.stat()  # inside: the file may vanish during the scan (phone sync)
            old = known.get(str(path))
            cached = old is not None and (old["size"], old["mtime"]) == (st.st_size, st.st_mtime)
            row = None if cached else analyse_file(path, thumbs)
            # Video dates got better (MP4 header before mtime), undated images now get the file date:
            # fix cached rows without a full re-scan.
            if cached and (old["kind"] == "video" or not old["taken_at"]) and (taken := scan.file_time(path)) != old["taken_at"]:
                conn.execute("UPDATE media SET taken_at = ? WHERE path = ?", (taken, str(path)))
                conn.commit()
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
    gone_rows = [r for r in conn.execute("SELECT id, path, thumb, frames FROM media") if r["path"] not in present]
    gone = {r["id"] for r in gone_rows}
    slideshow.forget(root, gone)  # before the ids are deleted and handed to new files
    conn.executemany("DELETE FROM media WHERE id = ?", [(i,) for i in gone])
    # Grouping compares every photo with every other (~2 s per 1500); skip it when nothing changed.
    # shortcut: new flag thresholds in the code apply only once a scan finds a new, changed or deleted file;
    # when quality.py or dedup.py thresholds change, run UPDATE media SET flags = NULL so the next scan regroups.
    if gone or conn.execute("SELECT 1 FROM media WHERE flags IS NULL LIMIT 1").fetchone():
        update_flags(conn)
    conn.commit()
    conn.close()
    if not (stop and stop.is_set()):  # a stopped scan is not "analysed"
        db.mark_analysed(root)
    for r in gone_rows:  # after the commit: no row may point at a deleted thumbnail
        for f in [thumbs / r["thumb"], *thumbs.glob(r["thumb"].replace(".jpg", "_*.jpg"))]:  # frames, preview
            f.unlink(missing_ok=True)
    return {"total": len(paths), "analysed": analysed, "errors": errors}


def update_flags(conn, scenes: set[int] | None = None) -> None:
    """Set the quality flags and regroup same-scene shots; scenes: only the shots of these (best shot ids).

    A scene is a connected group: taking shots out of it or choosing another best never affects other scenes,
    so regrouping just those avoids comparing every photo with every other (seconds per click on big trips).
    """
    items = db.all_media(conn)
    if scenes is not None:
        items = [it for it in items if (it["duplicate_of"] or it["id"]) in scenes]
    # Very blurry images (the flag: a dark low-contrast shot is not one) and videos never compete for "best of scene".
    # Shots the user kept on their own (SEPARATE) are never grouped again.
    candidates = [it for it in items if it["kind"] == "image" and "very_blurry" not in quality.flags(it["metrics"])
                  and it["chosen"] != SEPARATE]
    best = dedup.best_of_clusters(candidates)
    for it in items:
        flags = quality.flags(it["metrics"])
        dup = best.get(it["id"])
        dup = dup if dup != it["id"] else None
        if dup:
            flags.append("duplicate")
        conn.execute("UPDATE media SET flags = ?, duplicate_of = ? WHERE id = ?", (json.dumps(flags), dup, it["id"]))


USER_PICK, AI_PICK, SEPARATE = 1, 2, 3  # values of media.chosen; SEPARATE: "not similar", kept on its own


def choose_best(conn, media_id: int, by: int = USER_PICK) -> None:
    """Make media_id the best shot of its scene: it is kept when the scene is regrouped (later scans too)."""
    scene = conn.execute("SELECT COALESCE(duplicate_of, id) FROM media WHERE id = ?", (media_id,)).fetchone()[0]
    conn.execute("UPDATE media SET chosen = (id = ?) * ? WHERE id = ? OR duplicate_of = ?",
                 (media_id, by, scene, scene))
    update_flags(conn, {scene})
    conn.commit()


def keep_separate(conn, ids: list[int]) -> None:
    """Take these shots out of their scene: each is its own picture, described by the AI (later scans too)."""
    marks = ",".join("?" * len(ids))
    scenes = {r[0] for r in conn.execute(f"SELECT COALESCE(duplicate_of, id) FROM media WHERE id IN ({marks})", ids)}
    conn.executemany("UPDATE media SET chosen = ? WHERE id = ?", [(SEPARATE, i) for i in ids])
    update_flags(conn, scenes)
    conn.commit()


BEST_OF = 6  # shots of one scene the AI compares side by side; the rest rank too low on the technical score


def ai_choose_best(conn, scene: list[dict]) -> None:
    """Let the AI compare the shots of one scene side by side and make its pick the best (chosen = AI_PICK).

    Raises on a failed request or bad answer; the best shot stays as it was.
    """
    shots = sorted(scene, key=lambda it: -it["score"])[:BEST_OF]
    settings = load_settings()
    imgs = [scan.read_image(Path(it["path"]))[0] for it in shots]
    choose_best(conn, shots[ai.pick_best(ai.make_client(settings), settings, imgs)]["id"], AI_PICK)


def skip_ai_reason(item: dict) -> str | None:
    """Why an item is not sent to the LLM (saves tokens), or None if it should be analysed."""
    if "very_blurry" in item["flags"]:
        return "very blurry"
    if item["duplicate_of"]:
        return f"same scene as #{item['duplicate_of']}"
    return None


class Stopped(Exception):
    pass


def describe_folder(root: Path, progress: Callable[[int, int], None] | None = None,
                    concurrency: int | None = None, stop: threading.Event | None = None,
                    only: set[int] | None = None) -> dict:
    """Pass 2: send each image still worth describing to the LLM; every result is saved right away.

    stop: set it to end early; requests already sent finish and are saved, queued ones are skipped.
    only: describe exactly these ids, also if described already or normally skipped (very blurry, not the best shot).
    """
    settings = load_settings()
    client = ai.make_client(settings)
    conn = db.connect(root)
    items = db.all_media(conn)
    todo = ([it for it in items if it["id"] in only] if only is not None
            else [it for it in items if it["ai"] is None and skip_ai_reason(it) is None])

    def describe(it: dict) -> dict:
        if stop and stop.is_set():
            raise Stopped
        path = Path(it["path"])
        if it["kind"] == "video":
            frames, _ = scan.read_video_frames(path)
            img = _sharpest(frames)[1]  # the frame the user sees on the card
        else:
            img = scan.read_image(path)[0]
        try:
            return ai.analyse_image(client, settings, img)
        except Exception as e:
            # The traceback's frames hold the decoded image (~30 MB); failed futures waiting in
            # as_completed would keep every one of them alive (20 GB with 857 failures).
            del img
            raise e.with_traceback(None)

    errors, described = [], 0
    with ThreadPoolExecutor(concurrency or default_concurrency()) as pool:
        futures = {pool.submit(describe, it): it for it in todo}
        for i, fut in enumerate(as_completed(futures)):
            # pop: don't keep every finished future (and its result or error) until the run ends
            it = futures.pop(fut)
            try:
                conn.execute("UPDATE media SET ai = ? WHERE id = ?", (json.dumps(fut.result()), it["id"]))
                conn.commit()
                described += 1
            except Stopped:
                pass
            except Exception as e:  # one failed request must not stop the run; it is retried next time
                errors.append(f"{Path(it['path']).name}: {e}")
            if progress:
                progress(i + 1, len(todo))
    conn.close()
    return {"described": described, "errors": errors}
