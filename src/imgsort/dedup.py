"""Group images of the same scene (bursts, near-duplicates) and pick the best of each group."""
from datetime import datetime

import imagehash

# Calibrated on 848 real photos with user spot-check: only exact repeats of a scene count.
# Seconds-apart repeats reach distance ~18; at 20-22 reframed shots of the same place appear,
# which the user wants to keep apart. A missed repeat only costs one extra LLM call.
SAME_SCENE = ((14, 60), (18, 10))  # (max phash Hamming distance of 64 bits, max seconds apart)
IDENTICAL_DIST = 2                  # practically identical images, regardless of capture time


def same_scene(a: dict, b: dict) -> bool:
    if (a["width"] > a["height"]) != (b["width"] > b["height"]):  # portrait vs landscape
        return False
    dist = imagehash.hex_to_hash(a["phash"]) - imagehash.hex_to_hash(b["phash"])
    if dist <= IDENTICAL_DIST:
        return True
    if not (a["taken_at"] and b["taken_at"]):
        return False
    gap = abs(datetime.fromisoformat(a["taken_at"]) - datetime.fromisoformat(b["taken_at"])).total_seconds()
    return any(dist <= max_dist and gap <= max_gap for max_dist, max_gap in SAME_SCENE)


def best_of_clusters(items: list[dict]) -> dict[int, int]:
    """items: dicts with id, phash, taken_at, width, height, score. Returns {id: best id of its cluster}."""
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
