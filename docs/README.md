# hal0 docs

This directory has two halves:

## Top-level `docs/*.mdx` — user docs

A **mirror** of the documentation published at <https://hal0.dev/docs/>.
The canonical source is `Hal0ai/hal0-web` (Starlight, Astro) and a GitHub
Action there auto-pushes updates into this folder whenever
`src/content/docs/docs/**` changes on `hal0-web/main`.

**Do not hand-edit these files in `Hal0ai/hal0`.** Any commit landing
here will be overwritten on the next upstream sync. Edit the source in
`hal0-web` instead.

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
