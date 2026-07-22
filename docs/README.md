# hal0 docs

This directory is for user-facing documentation: concepts, getting started,
guides, operations, and reference material.

The `.mdx` files mirror the documentation published at <https://hal0.dev/docs/>.
The canonical source is `Hal0ai/hal0-web` (Starlight, Astro), and a GitHub
Action there syncs website docs back into this repository.

**Do not hand-edit synced website docs in `Hal0ai/hal0`.** Changes landing here
may be overwritten on the next upstream sync. Edit the source in `hal0-web`
instead.

## User-facing docs kept here

- `concepts/`
- `getting-started/`
- `guides/`
- `operate/`
- `reference/`

## Development docs

Implementation plans, handoffs, rework notes, internal audits, generated design
specs, and Superpowers planning artifacts are local development material. Keep
those under the gitignored `docs/.development/` folder rather than in the
user-facing docs tree.
