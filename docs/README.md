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

## `docs/internal/` — repo-only architecture docs

Hand-maintained, not on the website. Lives with the code so PRs can
update implementation notes and architectural decisions in the same
commit.

- `internal/adr/` — Architecture Decision Records.
- `internal/api-errors.md` — error envelope contract.
- `internal/migration.md` — haloai → hal0 migration playbook.
- `internal/models-slots-impl-plan.md` — slot/model registry impl plan.
- `internal/release-manifest.md` — `hal0.releases.v1` manifest schema.
