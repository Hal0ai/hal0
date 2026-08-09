# hal0 docs

This directory has two halves:

## Top-level `docs/*.mdx` — user docs

The **canonical source** of the documentation published at
<https://hal0.dev/docs/>. `.github/workflows/mirror-docs.yml` pushes the
published sections (`getting-started/`, `concepts/`, `guides/`,
`operate/`, `reference/`) from here into `Hal0ai/hal0-web`
(`src/content/docs/docs/**`, Starlight/Astro) on every push to `main` —
the direction was reversed by #1622; `hal0-web` used to be the source and
mirror into this repo, but is now the generated copy.

**Edit these files here, in `Hal0ai/hal0`.** Hand-editing the mirrored
copy in `hal0-web` is pointless: the next push to `hal0/main` touching a
published section `rsync --delete`s that section on the web side and
silently discards the change.

The `.mdx` extension is preserved verbatim — the files import Starlight
components (`Card`, `Tabs`, `Steps`, …) that only render inside the
website build. GitHub renders the surrounding markdown body fine.

## `docs/adr/` — Architecture Decision Records

Hand-maintained and tracked. Lives with the code so PRs can update
architectural decisions in the same commit.

## `docs/.devdocs/` — developer docs (local-only, gitignored)

Maintainer/agent-internal material that is not part of the public repo:
planning docs (`PLAN.md`), agent workflow notes (`agents/`), handoffs
(`rework/`), plans/specs from agent sessions (`superpowers/`), internal
audits (`internal/`), and historical snapshots (`archive/`). These files
live only on developer machines and are excluded by `.gitignore`.
