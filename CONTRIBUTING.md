# Contributing

Thanks for wanting to help! Every kind of contribution counts:

- **Try it and report back.** A short issue saying which AI server and model you used, and how
  well it worked, is genuinely useful; please use the *Model experience* template.
- **Report bugs** with the *Bug report* template.
- **Suggest features**; the roadmap in the [README](README.md#roadmap) shows what's planned.
- **Send a pull request.** For anything bigger than a small fix, open an issue first so we can
  agree on the approach.

## Development setup

```bash
uv sync
uv run pytest                          # runs without an AI endpoint
uv run photos2show-testdata demo_images    # synthetic photos with known defects to try the scan
uv run photos2show-scan demo_images
```

[docs/development.md](docs/development.md) describes the code layout, routes and conventions;
[PLAN.md](PLAN.md) the reasoning behind thresholds and design decisions.

## Guidelines

- Keep it simple: plain Python, FastAPI + Jinja2 templates + HTMX, no JavaScript build step.
- Add or update a test in `tests/` for behaviour changes.
- Update the matching page in [docs/](docs/index.md) in the same pull request, so the documentation stays accurate.
- Never modify the user's photo folder; everything the tool writes goes into the data folder or the export folder.
- Don't commit personal photos, `.env` files or API keys.

By contributing you agree that your contribution is licensed under the [GPL-3.0](LICENSE).
