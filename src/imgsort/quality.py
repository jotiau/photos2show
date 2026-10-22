"""Classic (non-AI) image quality metrics and flags."""
import math

import cv2
import numpy as np
from PIL import Image

# Metrics are computed at a fixed size so values are comparable across cameras.
ANALYSIS_EDGE = 1024

# Calibrated on 848 real phone photos (Norway 2026): median sharpness ≈ 2850; below 200 only
# visibly blurry shots; sharp but low-detail scenes (lake, sunset, sky) sit at 200–300.
# shortcut: motion blur with one sharp tile (e.g. from a car) is not caught; needs a directional measure.
BLURRY = 200.0            # max-tile Laplacian variance
VERY_BLURRY = 20.0        # below this the image is excluded and never sent to the LLM
TOO_DARK = 0.15           # mean brightness 0..1
TOO_BRIGHT = 0.88
CLIPPED = 0.4             # fraction of (almost) pure black / white pixels
LOW_CONTRAST = 20.0       # luminance std, 0..255; below this blur cannot be judged reliably
MONOTONE_EDGES = 0.005    # fraction of edge pixels (a real landscape ≈ 0.04, plain sky ≈ 0.003)
MONOTONE_CONTRAST = 25.0

QUALITY_FLAGS = ("very_blurry", "blurry", "too_dark", "too_bright", "low_contrast", "monotone")


def prepare(img: Image.Image) -> np.ndarray:
    img = img.convert("RGB")
    img.thumbnail((ANALYSIS_EDGE, ANALYSIS_EDGE))
    return np.asarray(img)


def metrics(rgb: np.ndarray) -> dict:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    # Sharpness per 3x3 tile: a sharp subject in front of a blurred background still counts as sharp.
    tiles = [
        cv2.Laplacian(gray[y * h // 3:(y + 1) * h // 3, x * w // 3:(x + 1) * w // 3], cv2.CV_64F).var()
        for y in range(3) for x in range(3)
    ]
    hist = np.bincount(gray.ravel(), minlength=256) / gray.size
    p = hist[hist > 0]
    r, g, b = (rgb[..., i].astype(np.float64) for i in range(3))
    rg, yb = r - g, 0.5 * (r + g) - b
    return {
        "sharpness": float(max(tiles)),
        "sharpness_mean": float(np.mean(tiles)),
        "brightness": float(gray.mean() / 255),
        "clip_dark": float(hist[:6].sum()),
        "clip_bright": float(hist[250:].sum()),
        "contrast": float(gray.std()),
        # Hasler & Süsstrunk colourfulness
        "colorfulness": float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean())),
        "entropy": float(-(p * np.log2(p)).sum()),
        "edge_density": float((cv2.Canny(gray, 100, 200) > 0).mean()),
    }


def flags(m: dict) -> list[str]:
    out = []
    if m["contrast"] >= LOW_CONTRAST:
        if m["sharpness"] < VERY_BLURRY:
            out.append("very_blurry")
        elif m["sharpness"] < BLURRY:
            out.append("blurry")
    if m["brightness"] < TOO_DARK or m["clip_dark"] > CLIPPED:
        out.append("too_dark")
    if m["brightness"] > TOO_BRIGHT or m["clip_bright"] > CLIPPED:
        out.append("too_bright")
    if m["contrast"] < LOW_CONTRAST:
        out.append("low_contrast")
    if m["edge_density"] < MONOTONE_EDGES and m["contrast"] < MONOTONE_CONTRAST:
        out.append("monotone")
    return out


def score(m: dict) -> float:
    """Technical quality, used to pick the best image of a duplicate cluster."""
    exposure_penalty = 3 * abs(m["brightness"] - 0.5) + 3 * (m["clip_dark"] + m["clip_bright"])
    return math.log1p(m["sharpness"]) - exposure_penalty
