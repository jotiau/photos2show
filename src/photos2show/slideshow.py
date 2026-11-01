"""Slideshow: let the LLM pick and order photos from a description, edit the picks, export a numbered folder."""
import json
import math
import re
import shutil
import threading
from pathlib import Path

from photos2show import ai, db, scan
from photos2show.config import load_settings

SECONDS_PER_PHOTO = 5  # default; the user can choose 2-15 per slideshow

SELECT_PROMPT = """You are choosing photos for a holiday slideshow. The user describes it like this:
---
{description}
---
Candidates, one per line: id | date | kind | scene | tags | caption | aesthetic/10 | interest/10 | quality flags
{lines}

Pick the photos that best fit the description, in the order they should be shown (chronological unless
the description says otherwise). Respect a requested number of photos or duration (about {seconds} seconds
per photo; a video runs its full length, shown as e.g. "video 12s"). Prefer high aesthetic and interest, avoid quality flags and repetitive shots.
"""

REFINE_PROMPT = """
The slideshow already has these photos, in this order (id | reason):
{current}

Change it as follows and keep everything else as it is (the order and photos the user added by hand):
---
{instruction}
---
Answer with the complete new slideshow, not only the changes.
"""

ANSWER = """Answer ONLY with JSON: {"picks": [{"id": 12, "reason": "short reason"}]}"""


def _line(it: dict) -> str:
    a = it["ai"] or {}  # a photo added by hand may not be described
    kind = f"video {on_screen(it, 0):.0f}s" if it["kind"] == "video" else it["kind"]
    return (f"{it['id']} | {(it['taken_at'] or '')[:16]} | {kind} | {a.get('scene')} | "
            f"{', '.join(a.get('tags') or [])} | {a.get('caption')} | {a.get('aesthetic')} | "
            f"{a.get('interest')} | {','.join(it['flags']) or '-'}")


def per_photo(selection: dict) -> int:
    return selection.get("seconds", SECONDS_PER_PHOTO)


def on_screen(it: dict, seconds: float) -> float:
    """Seconds an item is shown: a photo the chosen time, a video its length (the chosen time if unreadable)."""
    return (scan.video_seconds(it["path"], it["mtime"]) or seconds) if it["kind"] == "video" else seconds


def select(conn, description: str, current: list[dict] | None = None, instruction: str = "",
           seconds: int = SECONDS_PER_PHOTO) -> list[dict]:
    """Ask the LLM for picks; returns [{"id", "reason"}] restricted to described, non-duplicate items.

    With an instruction, the current picks are refined instead of chosen anew; they stay valid even if
    added by hand (a duplicate or a photo without AI description).
    """
    current = current or []
    kept = {p["id"] for p in current}
    items = [it for it in db.all_media(conn) if (it["ai"] and not it["duplicate_of"]) or it["id"] in kept]
    if not any(it["ai"] for it in items):
        raise ValueError("No photos described by AI yet – analyse the project first.")
    settings = load_settings()
    prompt = SELECT_PROMPT.format(description=description, lines="\n".join(map(_line, items)),
                                  seconds=seconds)
    if instruction:
        lines = "\n".join(f"{p['id']} | {p['reason'] or '-'}" for p in current)
        prompt += REFINE_PROMPT.format(current=lines or "(empty)", instruction=instruction)
    answer = ai.complete_json(ai.make_client(settings), settings, [{"role": "user", "content": prompt + ANSWER}])
    valid, old_reason, picks = {it["id"] for it in items}, {p["id"]: p["reason"] for p in current}, []
    for p in answer.get("picks") or []:  # LLM output: drop unknown, repeated or malformed ids
        try:
            pid = int(p["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if pid in valid and pid not in {q["id"] for q in picks}:
            picks.append({"id": pid, "reason": str(p.get("reason") or old_reason.get(pid, ""))})
    return picks


def edit(picks: list[dict], action: str, media_id: int) -> list[dict]:
    """add / remove one item (reordering is move()); returns a new list."""
    if action == "add":
        added = any(p["id"] == media_id for p in picks)
        return picks if added else picks + [{"id": media_id, "reason": "added by hand"}]
    return [p for p in picks if p["id"] != media_id]


def swap(picks: list[dict], media_id: int, other: int) -> list[dict]:
    """Put other in media_id's place, keeping position and reason; unchanged if other is already a pick."""
    if any(p["id"] == other for p in picks):
        return picks
    return [{**p, "id": other} if p["id"] == media_id else p for p in picks]


def move(picks: list[dict], media_id: int, index: int) -> list[dict]:
    """Move one item to position index (clamped, as in the list without it); returns a new list."""
    moved = [p for p in picks if p["id"] == media_id]
    if not moved:
        return picks
    rest = [p for p in picks if p["id"] != media_id]
    index = max(0, min(index, len(rest)))
    return rest[:index] + moved + rest[index:]


def number(v) -> float | None:
    try:  # AI scores are raw LLM output: may be missing or a string
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None  # "nan" breaks sorting, "inf" breaks rounding


# name -> (label, key on the media item, best/newest first)
SORTS = {
    "date": ("Date (oldest first)", lambda it: it["taken_at"], False),
    "date_desc": ("Date (newest first)", lambda it: it["taken_at"], True),
    "aesthetic": ("Aesthetic (best first)", lambda it: number((it["ai"] or {}).get("aesthetic")), True),
    "interest": ("Interest (best first)", lambda it: number((it["ai"] or {}).get("interest")), True),
    "quality": ("Technical quality (best first)", lambda it: it["score"], True),
    "name": ("File name", lambda it: Path(it["path"]).name.lower(), False),
    "reverse": ("Reverse current order", None, False),
}


def sort(picks: list[dict], items: dict[int, dict], by: str) -> list[dict]:
    """Reorder picks by one of SORTS; picks without a value keep their order at the end."""
    _, key, desc = SORTS[by]
    if key is None:
        return picks[::-1]
    known = [p for p in picks if p["id"] in items and key(items[p["id"]]) is not None]
    rest = [p for p in picks if p not in known]
    return sorted(known, key=lambda p: key(items[p["id"]]), reverse=desc) + rest


def load(root: Path) -> dict:
    f = db.library_dir(root) / "selection.json"
    return json.loads(f.read_text()) if f.exists() else {"description": "", "picks": []}


def save(root: Path, selection: dict) -> None:
    db.write_json(db.library_dir(root) / "selection.json", selection)


def _previous(root: Path) -> Path:
    return db.library_dir(root) / "selection.prev.json"


# FastAPI runs routes in parallel threads: two quick edits must not both start from the same old file.
lock = threading.Lock()


def update(root: Path, fn) -> None:
    """Apply fn(selection) -> new selection under the lock, keeping one step of undo."""
    with lock:
        change(root, fn(load(root)))


def change(root: Path, selection: dict) -> None:
    """Save an edit of the picks and keep the list before it for one step of undo."""
    old = load(root)
    if old["picks"] != selection["picks"]:
        db.write_json(_previous(root), old)
    save(root, selection)


def can_undo(root: Path) -> bool:
    return _previous(root).exists()


def undo(root: Path) -> None:
    """Restore the picks before the last change (one step); the export folder stays remembered."""
    f = _previous(root)
    with lock:
        if not f.exists():
            return
        current = load(root)
        save(root, {**json.loads(f.read_text()), **({"exported_to": current["exported_to"]}
                                                    if "exported_to" in current else {})})
        f.unlink()


def forget(root: Path, gone: set[int]) -> None:
    """Drop deleted files from the picks and the undo copy: SQLite gives a deleted top id to the next new file."""
    with lock:
        for f in (db.library_dir(root) / "selection.json", _previous(root)):
            if f.exists():
                selection = json.loads(f.read_text())
                if any(p["id"] in gone for p in selection["picks"]):
                    db.write_json(f, {**selection, "picks": [p for p in selection["picks"] if p["id"] not in gone]})


EXPORTED = re.compile(r"\d{3}_.+")  # file names written by export()


def export(root: Path, target: Path, replace: bool = False) -> int:
    """Copy the picks as 001_<date>_<tags>.<ext> into target; originals are never touched.

    replace: target may hold an earlier export (selection.json + numbered files only); it is emptied first.
    """
    root, target = root.resolve(), target.expanduser().resolve()
    if target.is_relative_to(root):
        raise ValueError("Export folder must be outside the photo folder, otherwise it gets scanned again.")
    selection, conn = load(root), db.connect(root)
    items = [it for p in selection["picks"] if (it := db.get_media(conn, p["id"]))]
    if not items:
        raise ValueError("Nothing selected.")
    old = list(target.iterdir()) if target.is_dir() else []
    if old and not replace:
        raise ValueError(f"{target} is not empty – choose a new folder or tick 'replace previous export'.")
    if old:
        ours = lambda f: f.is_file() and (f.name == "selection.json" or EXPORTED.fullmatch(f.name))  # noqa: E731
        if not (target / "selection.json").is_file() or not all(map(ours, old)):
            raise ValueError(f"{target} holds files that are not from an export – choose another folder.")
        for f in old:
            f.unlink()
    target.mkdir(parents=True, exist_ok=True)
    for n, it in enumerate(items, 1):
        src = Path(it["path"])
        slug = re.sub(r"[^a-z0-9]+", "-", " ".join(((it["ai"] or {}).get("tags") or [])[:2]).lower()).strip("-")
        shutil.copy2(src, target / f"{n:03d}_{(it['taken_at'] or '')[:10]}_{slug or 'photo'}{src.suffix.lower()}")
    db.write_json(target / "selection.json", {k: v for k, v in selection.items() if k != "exported_to"})
    with lock:  # re-read: picks may have been edited while copying
        save(root, {**load(root), "exported_to": str(target)})  # suggested again for the next export
    return len(items)
