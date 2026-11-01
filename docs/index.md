# photos2show documentation

photos2show turns a folder of holiday photos and videos into a slideshow. It sorts out the technical
rejects on your own computer, has an AI model describe the rest, and builds a slideshow from a plain
sentence such as *"10-minute show for my parents, chronological, mostly landscapes, little food"*.
The result is a new folder with numbered copies. **Your originals are never moved or changed.**

In the app, *Settings → Open documentation* shows these pages in a new tab.

## Contents

| Page | What it covers |
|---|---|
| [User guide](user-guide.md) | Installing, the four tabs (Projects, Library, Slideshow, Settings), keyboard shortcuts, export |
| [Configuration](configuration.md) | `.env` variables, the Settings page, choosing and setting up an AI model |
| [How it works](how-it-works.md) | The analysis pipeline: quality checks, same-scene grouping, AI descriptions, slideshow selection, export rules |
| [Data and files](data-and-files.md) | Supported formats, how dates are found, the data folder, what is sent to the AI |
| [Development](development.md) | Code layout, modules, web routes, command line tools, tests, conventions |

## In one minute

1. Create a project in the *Projects* tab: a name and the folder with your photos.
2. The app scans every file locally (blur, exposure, contrast, same-scene shots), then sends the
   photos worth keeping to the AI for a caption, tags and scores.
3. Browse and filter the results in the *Library*.
4. In the *Slideshow* tab, describe the show you want; the AI picks and orders the photos. Adjust by
   hand or in words.
5. Export to a new folder: `001_2026-08-11_landscape.jpg`, `002_…`, ready for any TV, photo frame or
   slideshow app.
