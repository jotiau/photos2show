"""Slideshow: let the LLM pick and order photos from a description, edit the picks, export a numbered folder."""
import json
import re
import shutil
from pathlib import Path

from imgsort import ai, db
from imgsort.config import load_settings

SECONDS_PER_PHOTO = 5

SELECT_PROMPT = """You are choosing photos for a holiday slideshow. The user describes it like this:
---
{description}
---
Candidates, one per line: id | date | kind | scene | tags | caption | aesthetic/10 | interest/10 | quality flags
{lines}

Pick the photos that best fit the description, in the order they should be shown (chronological unless
the description says otherwise). Respect a requested number of photos or duration (about {seconds} seconds
per photo). Prefer high aesthetic and interest, avoid quality flags and repetitive shots.
Answer ONLY with JSON: {{"picks": [{{"id": 12, "reason": "short reason"}}]}}"""


def _line(it: dict) -> str:
    a = it["ai"]
    return (f"{it['id']} | {(it['taken_at'] or '')[:16]} | {it['kind']} | {a.get('scene')} | "
            f"{', '.join(a.get('tags') or [])} | {a.get('caption')} | {a.get('aesthetic')} | "
            f"{a.get('interest')} | {','.join(it['flags']) or '-'}")


def select(conn, description: str) -> list[dict]:
    """Ask the LLM for picks; returns [{"id", "reason"}] restricted to described, non-duplicate items."""
    items = [it for it in db.all_media(conn) if it["ai"] and not it["duplicate_of"]]
    if not items:
        raise ValueError("No photos described by AI yet – analyse the project first.")
    settings = load_settings()
    prompt = SELECT_PROMPT.format(description=description, lines="\n".join(map(_line, items)),
                                  seconds=SECONDS_PER_PHOTO)
    answer = ai.complete_json(ai.make_client(settings), settings, [{"role": "user", "content": prompt}])
    valid, picks = {it["id"] for it in items}, []
    for p in answer.get("picks") or []:  # LLM output: drop unknown, repeated or malformed ids
        try:
            pid = int(p["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if pid in valid and pid not in {q["id"] for q in picks}:
            picks.append({"id": pid, "reason": str(p.get("reason") or "")})
    return picks


def edit(picks: list[dict], action: str, media_id: int) -> list[dict]:
    """add / remove one item (reordering is move()); returns a new list."""
    if action == "add":
        added = any(p["id"] == media_id for p in picks)
        return picks if added else picks + [{"id": media_id, "reason": "added by hand"}]
    return [p for p in picks if p["id"] != media_id]


def move(picks: list[dict], media_id: int, index: int) -> list[dict]:
    """Move one item to position index (clamped, as in the list without it); returns a new list."""
    moved = [p for p in picks if p["id"] == media_id]
    if not moved:
        return picks
    rest = [p for p in picks if p["id"] != media_id]
    index = max(0, min(index, len(rest)))
    return rest[:index] + moved + rest[index:]


def _number(v) -> float | None:
    try:  # AI scores are raw LLM output: may be missing or a string
        return float(v)
    except (TypeError, ValueError):
        return None


# name -> (label, key on the media item, best/newest first)
SORTS = {
    "date": ("Date (oldest first)", lambda it: it["taken_at"], False),
    "date_desc": ("Date (newest first)", lambda it: it["taken_at"], True),
    "aesthetic": ("Aesthetic (best first)", lambda it: _number((it["ai"] or {}).get("aesthetic")), True),
    "interest": ("Interest (best first)", lambda it: _number((it["ai"] or {}).get("interest")), True),
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
    (db.library_dir(root) / "selection.json").write_text(json.dumps(selection, indent=2, ensure_ascii=False))


def export(root: Path, target: Path) -> int:
    """Copy the picks as 001_<date>_<tags>.<ext> into target; originals are never touched."""
    root, target = root.resolve(), target.expanduser().resolve()
    if target.is_relative_to(root):
        raise ValueError("Export folder must be outside the photo folder, otherwise it gets scanned again.")
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"{target} is not empty – choose a new folder.")
    selection, conn = load(root), db.connect(root)
    items = [it for p in selection["picks"] if (it := db.get_media(conn, p["id"]))]
    if not items:
        raise ValueError("Nothing selected.")
    target.mkdir(parents=True, exist_ok=True)
    for n, it in enumerate(items, 1):
        src = Path(it["path"])
        slug = re.sub(r"[^a-z0-9]+", "-", " ".join(((it["ai"] or {}).get("tags") or [])[:2]).lower()).strip("-")
        shutil.copy2(src, target / f"{n:03d}_{(it['taken_at'] or '')[:10]}_{slug or 'photo'}{src.suffix.lower()}")
    (target / "selection.json").write_text(json.dumps(selection, indent=2, ensure_ascii=False))
    return len(items)
