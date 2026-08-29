# hal0 docs

This directory has two halves:

## The published sections — user docs

The **canonical source** of the documentation published at
<https://forum.hal0.dev/c/docs/11>. `hal0.dev/docs/*` no longer hosts the
pages; it 301s to the matching forum topic.

`.github/workflows/sync-docs-discourse.yml` pushes the published sections
(`getting-started/`, `concepts/`, `guides/`, `operate/`, `reference/`)
from here into Discourse on every push to `main` that touches them. Each
doc becomes a topic keyed by a path-derived `external_id`; a section lands
in its own subcategory when the forum has one, and gets an index topic
either way. The implementation is `scripts/docs_discourse_sync/`;
`discovery.py` walks the five section directories with a plain `rglob`, so
a **new `.mdx` in one of them publishes itself** — there is no manifest and
no registration step.

Publishing and redirecting are separate, though. The sync run uploads a
redirect-map artifact, but hal0-web's 301 layer reads a copy committed in
that repo (`src/content/../docs-redirects.json`), so a newly-added doc is
live on the forum before any `hal0.dev/docs/<path>` redirect exists for it.
Only old links need that redirect, so this matters when a doc is **renamed
or moved**, not when one is added.

**Edit these files here, in `Hal0ai/hal0`.** The forum copy is generated:
editing a topic by hand is discarded by the next sync run that touches
that doc.

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
