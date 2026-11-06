# Data and files

## Supported files

| Type | Extensions | Notes |
|---|---|---|
| Photos | `.jpg` `.jpeg` `.png` `.webp` `.avif` `.gif` `.bmp` `.heic` `.heif` `.tif` `.tiff` | GIF: first frame. HEIC/HEIF via pillow-heif. |
| Camera RAW | `.dng` `.cr2` `.cr3` `.nef` `.nrw` `.arw` `.orf` `.rw2` `.raf` `.pef` `.srw` | developed with LibRaw, camera white balance |
| Videos | `.mp4` `.mov` `.m4v` `.3gp` `.avi` `.mkv` `.webm` `.mts` `.m2ts` `.wmv` `.mpg` `.mpeg` | 3 sampled frames are analysed |

Extensions are matched case-insensitively. Browsers can't show HEIC, TIFF or RAW, so the app sends those
as a JPEG when you open the original. Browsers play only some video formats (MP4 and WebM everywhere,
MOV/MKV in most); the others are still analysed, the detail panel shows their frames, and the slideshow
preview skips them.

## Dates

Photos and videos are sorted by capture time, found in this order:

1. **Photos**: EXIF `DateTimeOriginal`, else EXIF `DateTime` (RAW: the capture time LibRaw reads).
2. A **phone-style file name**: `YYYYMMDD_HHMMSS` or `YYYYMMDD-HHMMSS` anywhere in the name,
   e.g. `20260725_130945.mp4`.
3. **MP4, MOV, M4V, 3GP**: the local capture time iPhones store with its time zone
   (`com.apple.quicktime.creationdate`), kept as filmed like the photo EXIF; else the `creation_time` of
   the movie header (stored in UTC, shown in this computer's time zone, so a clip from another time zone
   without either of the two above is off by the difference).
4. The **file date**: the earlier of created and modified, because copying resets one and editing the
   other. On Linux the creation time is read with GNU `stat`.

The file name comes before the video header because phones name files in local time, like the photo EXIF.
Video dates and undated photos are re-checked on every scan without a full re-analysis.

## The data folder

Everything the app writes, except exports, goes into the data folder (default
`~/.local/share/photos2show`, see `PHOTOS2SHOW_CACHE`). **Nothing is ever written into your photo folder.**

```
~/.local/share/photos2show/
  projects.json                 # project name -> photo folder
  settings.json                 # Settings page choices (also the API key, if set there; owner-only)
  <folder name>-<12 hex>/       # one per photo folder (hash of its full path)
    photos2show.db              # SQLite: one row per file
    slideshows/
      1.json, 2.json …          # one per slideshow (selection.json before several slideshows; moved here)
      1.prev.json …             # its picks before the last change (Undo)
      current                   # number of the slideshow that is open
    analysed_at                 # time of the last complete analysis
    thumbs/
      <16 hex>.jpg              # grid thumbnail (~400 px)
      <16 hex>_f0.jpg …         # video frames
      <16 hex>_preview.jpg      # detail panel preview (~1024 px)
```

AI results belong to the **folder**, not the project name: renaming a project, or removing it and
creating a new one for the same folder, keeps them. *Change folder* copies them to the new folder (unless it
has results of its own), so after moving the photos only new or changed files are analysed again; the old
copy stays until *Delete unused data* in Settings removes folders no project points to.

The AI results take 30–70 minutes per trip to recreate, which is why this is a data folder rather than
a cache folder.

### `media` table (`photos2show.db`)

| Column | Meaning |
|---|---|
| `id` | number shown on cards (`#12`) and used in the slideshow |
| `path`, `kind` | full file path; `image` or `video` |
| `size`, `mtime` | cache key together with the path |
| `taken_at` | capture time, ISO format |
| `width`, `height` | original size, after rotation |
| `thumb`, `frames` | thumbnail file name; number of saved video frames |
| `metrics`, `score` | JSON of the quality metrics; technical score |
| `phash` | perceptual hash (hex) |
| `flags` | JSON list of quality flags and `duplicate` |
| `duplicate_of` | id of the best shot of the same scene, NULL for the best shot itself |
| `ai` | JSON of the AI description, NULL until described |
| `chosen` | best shot by choice: 0 no, 1 by you, 2 by the AI; 3 kept separately (not similar, never grouped) |
| `excluded` | 1: *Never pick* – not offered to the AI for the slideshow; kept on later scans |

### Slideshow (`slideshows/<n>.json`)

```json
{"name": "Slideshow",
 "description": "10-minute show for my parents …",
 "picks": [{"id": 12, "reason": "ferry arriving at sunrise"}],
 "refinements": ["add more sunsets"],
 "seconds": 5,
 "exported_to": "/home/me/Pictures/Norway_slideshow"}
```

The export folder gets the same file as `selection.json`, without `exported_to`.

## Privacy

- The app runs on your computer and only listens on `127.0.0.1`.
- Only a downscaled JPEG (1024 px; 768 px for an AI compare) of each photo is sent to the AI endpoint
  you configure, plus the text lines for the slideshow selection. With a local model nothing leaves
  your computer.
- Photos that are very blurry or weaker shots of a scene are not sent, unless you click *Describe with AI*
  in the detail panel.
- Originals are never moved, renamed or changed.
