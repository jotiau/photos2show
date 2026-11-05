# User guide

## Install and start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (it installs Python for you)
and a vision-capable AI model (see [Configuration](configuration.md)).

```bash
git clone https://github.com/jotiau/photos2show.git
cd photos2show
uv sync
cp .env.example .env          # enter your endpoint, key and model
uv run photos2show-smoke      # sends one test picture to check that the model answers
uv run photos2show-web        # opens http://127.0.0.1:8000 in your browser
```

**Local AI in one step.** To run the AI on your own computer with Ollama, run the setup script
instead of the commands above (from the cloned folder):

```bash
./setup.sh                                          # Linux, macOS
powershell -ExecutionPolicy Bypass -File setup.ps1  # Windows
```

It installs uv and the app, checks for Ollama (if it is missing, it asks whether to install it
automatically or whether you install it yourself), detects your RAM and GPU, suggests an analysis and
a slideshow model from [Recommended pairs](configuration.md#local-models) (press Enter to keep them or
type other ones), downloads them, writes `.env` (an existing one is kept as `.env.bak`) and starts the
app. Running it again is safe: what is installed is skipped.

`photos2show-web --no-browser` starts the server without opening a browser tab. The app only listens
on your own computer (`127.0.0.1`).

The app has four tabs: **Projects**, **Library**, **Slideshow** and **Settings**. The project you work
on is chosen in the dropdown in the header; clicking the *photos2show* title opens its Library.

## Projects

One project per trip. Each project is a name plus a photo folder; subfolders are included.

- **New project**: enter a name (must be unique) and the folder, either typed or picked with *Browse…*
  (opens your system's folder dialog). *Create & analyse* saves the project and starts the analysis.
- **Cards**: every project shows a cover (the photo the AI liked best), the number of files,
  AI-described files and slideshow picks, when it was last analysed, and a warning if the folder is
  missing (e.g. an unplugged drive). *Open* switches to it.
- **Edit**: *Rename* the project, or *Change folder* to point it to another folder (e.g. after moving the
  photos; press *Analyse again* in the Library afterwards).
- **Remove project…**: asks in a dialog and never deletes photos. Saved AI results and thumbnails stay
  in the data folder, so a new project for the same folder picks them up again. Tick *also delete its saved
  AI results* to remove them too (they are kept anyway while another project uses the same folder).

Projects cannot be changed while an analysis is running.

## Analysis

Analysis runs in the background and shows its progress at the top of the *Library*.

1. **Scanning** – local checks for blur, exposure, contrast, monotony and same-scene shots. No AI, no
   upload. Takes a few minutes the first time, seconds afterwards.
2. **Review duplicates** (only when *Review duplicates before AI* is on, the default, and there are new
   groups) – a dialog shows each group of same-scene shots with the automatic pick in green.
   - Click another shot to use it instead.
   - *Let the AI compare* (per group) or *Let the AI compare all* sends up to 6 shots of a group to the
     AI side by side; its pick is marked *AI pick*. You can still click another shot.
   - Shots that are not similar at all: *Keep separately* under a shot takes it out of the group, *Keep all*
     (per group) dissolves the whole group. Each such shot is its own picture: described by the AI and
     offered for the slideshow. The choice is kept on later scans.
   - *Continue with AI* starts the next step; *Stop* ends the run. The run waits for you.
3. **Describing with AI** – each remaining photo goes to the model (2 at a time by default, see
   *Parallel AI requests*). Captions and tags appear live; while you browse, a *Show N new results*
   button reloads the grid instead of it jumping. The progress bar shows the time left.

*Stop* lets the requests already sent finish and skips the rest. A stopped or interrupted run continues
where it ended when you press *Analyse again*. Re-runs only process new or changed files.

When it is done, a summary shows the number of files and how many of them have an AI description in
total, then in brackets what this run added (newly scanned, newly described), and any
errors (expand *N errors* to see them). A folder without supported files says
"No photos or videos found".

Photos that are **not sent to the AI** are marked *no AI*: very blurry photos, and the weaker shots of
a same-scene group. They cost no time or money and are not offered to the slideshow AI (you can still
add them by hand). *Describe with AI* in the detail panel sends one anyway; a very blurry photo described
that way is then offered to the slideshow AI (with its flag), a weaker shot only once it is the best of
its group.

## Library

A grid of thumbnail cards. Each card shows the file name, date, 1–5 stars (from the AI's aesthetic and
interest scores), the caption, quality flags, a video icon, `✓ N` if the photo is pick number N in
the slideshow, and *never pick* if you excluded it from the slideshow.

- **Stacks**: same-scene shots are stacked behind the best one, shown with `+N`. *Show all shots*
  unstacks them; *Stack same-scene shots* stacks them again.
- **Filters** (chips with counts; empty ones are hidden): all, ok (no flag), skips ai, very blurry,
  blurry, too dark, too bright, low contrast, monotone, duplicate, video, in slideshow, never pick.
- **Tags**: the 30 most common AI tags with counts; click one to filter by it (also from the detail panel).
- **Search**: every word must appear in the caption, tags, scene, issues or file name (any case),
  e.g. `boat sunset`.
- **From / to**: only photos taken on these days (both included), e.g. the second week of a trip.
  Either field can stay empty; photos without a date are hidden while a range is set.
- **Sort by**: date (oldest or newest first), aesthetic, interest, technical quality, file name.
  Photos without a value (no date, not described) go to the end.
- **The view lives in the URL**: reload, back/forward and bookmarks keep filter, search, sort, date
  range and the open photo. Opening the bare start page restores the last view.

### Detail panel

Click a card to open it: a larger preview (click it for the original), path, date, dimensions, flags, the AI
caption, scene, people count, scores, tags and issues, and the technical metrics under
*Technical details*. Videos show a player (if the browser can play the format) and their sampled frames.

- **Add to slideshow** / **Remove from slideshow (#N)** toggles the photo; N is its position in the show.
- **Never pick** keeps the photo out of the AI's choice (*Select photos* and *Refine picks*), e.g. a
  stranger in the frame or a shot you don't like; it also leaves the current slideshow. *✓ Never pick*
  turns it off again. You can still add the photo by hand. The choice is kept on later scans.
- **Caption**: click the caption to correct it, Enter (or leaving the field) saves. The slideshow AI
  chooses by the captions, so a wrong one ("dog" for a fox) leads to wrong picks. *Describe again*
  replaces it with a new AI caption.
- **Describe again** sends this one file to the AI now, with the current *Analysis model*, e.g. after
  switching to a better model. Files without a description (failed, very
  blurry, weaker shot of a scene) show **Describe with AI** instead. The old description stays until the
  new one has arrived; if the request fails, it is kept and the error is shown.
- **Same scene**: the other shots of the group; the best shot is outlined green. *Make this the best
  shot* makes the open shot the best of its group; if it was never described, it is described by the AI
  right away. The choice is kept on later scans.

### Selecting several cards

Ctrl/⌘-click toggles a card, Shift-click selects every card from the last clicked one. A bar shows how
many are selected, with *Add to slideshow*, *Remove from slideshow* and *Clear*.

### Keyboard

| Key | Action |
|---|---|
| Tab | move to a card |
| Enter / Space | open the focused card |
| ← → | previous / next photo in grid order |
| A | add to / remove from the slideshow |
| Esc | close the detail panel |

## Slideshow

Each project has one slideshow. To make a second show from the same trip, export the first, then select again.

### ① Describe

Write what you want: length or number of photos, audience, order, content. Then press *Select photos*;
a spinner shows the elapsed time while the model works (a few minutes for ~1000 files). Next to the
button you can change *Photo details* (how much the AI reads per photo, see
[Configuration](configuration.md#settings-page)). Once there are picks, this step collapses to one line
with *Edit*.

If the answer is unusable, an error is shown and the current picks stay as they are; press *Select
photos* again or rephrase.

### ② Review picks

The picks are tiles in order, each with the AI's reason. The toolbar shows the count and estimated
length (videos count with their real length).

- **Seconds per photo**: 2–15, default 5.
- **Reorder**: drag and drop, or ‹ › (move earlier / later). **Sort** the whole set by date, aesthetic, interest, technical
  quality, file name, or reverse the current order.
- **Remove** with ×. **Add** from the Library (detail panel or bulk selection).
- **⇄ N other shots**: swap a pick for another shot of the same scene, in the same place and with the
  same reason.
- **Refine picks**: type an instruction (e.g. "add 10 more sunsets, fewer people, start with the
  ferry"). The model keeps the rest, including your hand edits, and returns the full new list.
- **Undo** goes back one change (any change of the picks: select, refine, sort, move, add, remove, swap).
- Click a tile to step through the picks in a **lightbox** (← →).
- **▶ Play** shows the show full screen, scaled to the window. Move the mouse or tap to see why a photo
  was picked and the controls (back, hold, forward, close). Keys: ← → Space Esc. Videos the browser
  cannot play are skipped in the preview (they are still exported).

### Export

Press *Export…*, choose a folder **outside the photo folder** and press *Export*, then *Open folder*.
The default is the last export folder, else `<photo folder>_slideshow`.

- Files are named `001_<date>_<first two tags>.<ext>` and copied as they are; camera RAW photos become
  full-size JPEGs, because TVs and slideshow apps cannot show RAW.
- A `selection.json` next to them records your description, refinements, seconds per photo and the
  reason for every pick.
- The folder must be new or empty. To export again after changes, tick *replace previous export in this folder*: this
  empties a folder only if it contains an earlier export's `selection.json` and numbered files, never a
  folder with other files in it.

## Settings

See [Configuration – Settings page](configuration.md#settings-page) for every option. The *Settings* tab
also has **Delete unused data**: it removes saved AI results and thumbnails of folders no project uses
any more (also folders only analysed with `photos2show-scan`). Photos are never touched.

**Open documentation** (at the bottom of Settings) opens this documentation in a new browser tab, with the
list of pages on the left.

## Troubleshooting

| Problem | What to do |
|---|---|
| "OPENAI_BASE_URL is not a URL" | Check `.env`; variables it references (e.g. `${MY_BASE_URL}`) must be set where the app starts (an IDE may not load `~/.bashrc`). |
| AI requests fail or time out | Run `uv run photos2show-smoke` to test the endpoint and list the model ids. Failed photos are retried on the next *Analyse again*, or one by one with *Describe with AI* in the detail panel. |
| Slideshow ignores the first photos of the trip | The model's context is too small and the list was cut off. Raise the context (32k+) or set *Photo details* to short or minimal. |
| Local model is slow or stalls | Set *Parallel AI requests* to 1. |
| A video shows no player | The browser can't play that format (common for AVI, MTS, WMV). It is still analysed and exported. |
| Wrong dates | See [Data and files – Dates](data-and-files.md#dates). |
| "Folder not found" | The photo folder was moved or the drive is unplugged; reconnect it or change the folder under *Edit*. |
