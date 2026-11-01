# How it works

```
folder ─► Scan ─► Quality ─► Same-scene ─► (Review) ─► AI descriptions ─► Select ─► Edit ─► Export
          dates,   OpenCV     pHash +        pick the     caption, tags,     AI picks   by hand   numbered
          thumbs,  metrics    capture time   best shot    scores             from text  or words  copies
          frames
          └──────────── every result is cached; re-runs only process new or changed files ────────────┘
```

Analysis runs in two passes. **Pass 1** (scan, quality, grouping) is local and runs over all files first,
because the best shot of a scene is only known once its neighbours are scanned. **Pass 2** sends the
remaining photos to the AI.

## 1. Scan

- Walks the folder and all subfolders, skipping hidden folders (e.g. `.Trash-1000` on a USB stick).
- Photos are read with EXIF rotation applied and decoded at up to 2048 px. RAW files are developed by
  LibRaw at half size with the camera white balance.
- Videos: frames at 10 %, 50 % and 90 % of the length; the sharpest frame represents the video in the
  grid and for the AI.
- A ~400 px thumbnail is saved for the grid; a ~1024 px preview for the detail panel is made on first view.
- Cache key per file: path + size + modification time. A changed file is analysed again and loses its
  AI description; a deleted file is removed with its thumbnails and taken out of the slideshow.
- One unreadable file is reported as an error and does not stop the scan.

How capture dates are found: [Data and files – Dates](data-and-files.md#dates).

## 2. Quality checks

Computed on a 1024 px copy (mostly its grayscale version) so values are comparable across cameras. No AI.

| Metric | Method |
|---|---|
| Sharpness | variance of the Laplacian on a 3×3 tile grid; the sharpest tile counts, so a sharp subject in front of a blurred background is sharp |
| Brightness | mean luminance 0–1, plus the share of (almost) pure black and white pixels |
| Contrast | standard deviation of luminance |
| Colourfulness | Hasler–Süsstrunk metric (shown only) |
| Entropy | of the luminance histogram (shown only) |
| Edge density | share of Canny edge pixels |

| Flag | Rule |
|---|---|
| `very_blurry` | sharpness < 20 (with contrast ≥ 20) – never sent to the AI |
| `blurry` | sharpness < 200 (with contrast ≥ 20) |
| `too_dark` | brightness < 0.15, or > 40 % near-black pixels |
| `too_bright` | brightness > 0.88, or > 40 % near-white pixels |
| `low_contrast` | contrast < 20 (blur is not judged then) |
| `monotone` | edge density < 0.005 and contrast < 25 (plain sky, a wall) |
| `duplicate` | a weaker shot of a same-scene group |

Thresholds were calibrated on 848 real phone photos. Known limit: motion blur with one sharp tile is
not caught; the AI usually lists it under issues.

The **technical score** ranks shots within a group: `log(1 + sharpness)` minus a penalty for exposure
far from mid-grey and for clipped pixels.

## 3. Same-scene groups

Two photos belong to the same scene when they have the same orientation (portrait/landscape) and their
perceptual hashes (pHash, 64 bit) are close:

- Hamming distance ≤ 14 and taken within 60 s, or
- distance ≤ 18 and within 10 s, or
- distance ≤ 2 at any time (practically identical).

Groups are transitive (A~B and B~C puts all three together). Only exact repeats count; reframed shots of
the same place stay separate. Very blurry photos and videos are never grouped.

The **best shot** of a group is the one chosen by hand or by the AI, else the highest technical score.
The others get the `duplicate` flag and are not sent to the AI. Shots kept separately in the review
(*Keep separately*, *Keep all*) are never grouped again. Choices are kept when groups are recomputed on
later scans.

### Review and AI compare

With *Review duplicates before AI* on, the run pauses after pass 1 if there are groups none of whose
shots is described yet. *Let the AI compare* sends the top 6 shots of a group (by technical score,
768 px each) in one request and asks which is best; the answer becomes the best shot (marked *AI pick*).
If it fails, the error shows under the group and the best stays as it was. The AI never compares on its
own outside the review.

## 4. AI descriptions

Each photo still worth it (not very blurry, best of its group, not yet described) is downscaled to
1024 px, sent as a JPEG, and the model answers with JSON:

```json
{"caption": "one sentence", "tags": ["beach", "sunset", "people"], "scene": "landscape",
 "people_count": 2, "aesthetic": 7, "interest": 8, "issues": ["tilted horizon"]}
```

`scene` is one of landscape, people, portrait, group, food, city, architecture, nature, animal,
activity, interior, document, other. Scores are 1–10.

- Requests run in parallel (*Parallel AI requests*); each result is saved right away, so a stopped or
  crashed run resumes where it stopped.
- A failed request or an answer that is not a JSON object counts as failed and is retried next run.
- Stars on the cards: the mean of aesthetic and interest, halved and rounded to 1–5.

## 5. Slideshow selection

1. **Candidates**: every described photo that is not a duplicate. Flagged photos stay in, with their
   flags in the list; the AI is told to avoid them.
2. **Prompt**: your description, the seconds per photo, and one line per candidate
   (`id | date | kind | scene | tags | caption | aesthetic/10 | interest/10 | quality flags`, videos as
   `video 12s`). *Photo details* controls how much of each line is sent.
3. **Answer**: an ordered list of `{"id", "reason"}`. Order is chronological unless the description
   says otherwise; a requested count or duration is respected.
4. **Validation**: unknown, repeated or malformed ids are dropped (a bare list of ids is accepted too).
   If nothing usable is left, an error is shown and the old picks stay. There is no score-based fallback.

**Refine** appends the current picks in order (`id | reason`) and your instruction to the same prompt;
the AI returns the complete new list. Current picks stay valid even if they were added by hand (a
duplicate or an undescribed photo). A pick without a new reason keeps its old one. Instructions are kept
as `refinements`.

**Edits** (add, remove, move, sort, swap, bulk) all run under one lock, so parallel clicks, a rescan or an
export never lose a change. Before every change of the picks the old list is saved for one step of *Undo*.

**Length**: photos × seconds per photo (2–15, default 5) plus each video's real length.

## 6. Export

- The target must be outside the photo folder (it would otherwise be scanned again) and new or empty.
  *Replace previous export* empties it first, but only if it holds a `selection.json` and otherwise only
  `NNN_…` files.
- `selection.json` is written first, then each pick is copied as `NNN_<YYYY-MM-DD>_<first two tags>.<ext>`
  (`photo` when there are no tags). RAW files are developed at full size and saved as JPEG (quality 92);
  everything else is copied unchanged with its metadata, with the extension in lower case.
- The folder is remembered as the default for the next export and for *Open folder*
  (`xdg-open` / `open` / `os.startfile`).
