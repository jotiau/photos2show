"""Group images of the same scene (bursts, near-duplicates) and pick the best of each group."""
from datetime import datetime

import imagehash

SAME_SCENE_DIST = 10   # phash Hamming distance (of 64 bits) for "same scene"
SAME_SCENE_GAP = 60    # max seconds between shots of the same scene
IDENTICAL_DIST = 2     # practically identical images, regardless of capture time


def same_scene(a: dict, b: dict) -> bool:
    dist = imagehash.hex_to_hash(a["phash"]) - imagehash.hex_to_hash(b["phash"])
    if dist <= IDENTICAL_DIST:
        return True
    if dist > SAME_SCENE_DIST or not (a["taken_at"] and b["taken_at"]):
        return False
    gap = abs(datetime.fromisoformat(a["taken_at"]) - datetime.fromisoformat(b["taken_at"]))
    return gap.total_seconds() <= SAME_SCENE_GAP


def best_of_clusters(items: list[dict]) -> dict[int, int]:
    """items: dicts with id, phash, taken_at, score. Returns {id: best id of its cluster}."""
    parent = {it["id"]: it["id"] for it in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if same_scene(a, b):
                parent[find(a["id"])] = find(b["id"])

    clusters: dict[int, list[dict]] = {}
    for it in items:
        clusters.setdefault(find(it["id"]), []).append(it)
    best = {}
    for members in clusters.values():
        winner = max(members, key=lambda it: it["score"])["id"]
        for it in members:
            best[it["id"]] = winner
    return best
