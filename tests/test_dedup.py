from photos2show.dedup import same_scene

BASE = "0" * 16


def item(bits_flipped: int, seconds: int, portrait: bool = False) -> dict:
    """phash with the lowest `bits_flipped` bits set = Hamming distance to BASE."""
    w, h = (3, 4) if portrait else (4, 3)
    return {"phash": f"{(1 << bits_flipped) - 1:016x}", "taken_at": f"2026-07-19T10:00:{seconds:02d}",
            "width": w, "height": h}


def test_same_scene_tiers():
    a = item(0, 0)
    assert same_scene(a, item(14, 59))        # close hash, within a minute
    assert not same_scene(a, item(16, 30))    # medium hash needs a short gap
    assert same_scene(a, item(18, 10))        # medium hash, seconds apart
    assert not same_scene(a, item(20, 2))     # reframed shot of the same place: keep both
    assert not same_scene(a, item(14, 2, portrait=True))  # orientation differs
    assert same_scene(a, {**item(2, 0), "taken_at": None})  # identical regardless of time
