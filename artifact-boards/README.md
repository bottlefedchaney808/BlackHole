# artifact-boards

Interactive-artifact boards for this repo, surfaced in Hermes Desktop by the
`interactive-artifacts` plugin (Interactive pane / `mod+shift+i`).

This is deliberately **not** `artifacts/` — that directory already holds this
repo's generated PNGs, DBs and zips, and pointing the board scanner at it would
mix two unrelated things.

## Layout

    artifact-boards/<id>/
        artifact.json    { "title": ..., "description": ..., "collector": ... }
        index.html       single-file board (inlined CSS/JS; it loads from file://)
        data.json        optional — its `generated_at` drives the staleness dot

`<id>` is lowercase letters, digits and hyphens only.

Boards here are addressed as **`findev/<id>`** (the root name comes from
`roots.json` in the plugin directory under `%LOCALAPPDATA%\hermes\plugins\
interactive-artifacts\`). A bare `<id>` with no prefix means the *hermes* root,
so always qualify boards from this repo.

## Refresh

`refresh_artifact` runs `<repo>/tools/refresh.py <id>`. This repo has no
`tools/refresh.py` yet, so boards here show as **not refreshable** — they are
still listed, viewed and mounted normally; only regeneration is unavailable.
Add that script (argv[1] = board id, regenerate the board in place) and the
Refresh button lights up with no plugin change.
