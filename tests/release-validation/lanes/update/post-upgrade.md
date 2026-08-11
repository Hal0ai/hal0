# Lane: post-upgrade (update box, order 2)

Diff the box against the "before" snapshot the `upgrade` stage recorded, then prove the upgraded
install works — not just that it starts.

The question this lane answers: **did the user lose anything, and did anything change that they
did not ask for?**

## Checks

1. **State diff.** Re-capture everything the upgrade stage snapshotted and diff it item by item:
   * slots — same names, same assigned models, same ports, same profiles? A slot silently
     re-seeded to a shipped default has destroyed the user's configuration.
   * config — user-set values preserved? New settings introduced with sane defaults, rather
     than overwriting an explicit choice?
   * capabilities and model registry — bindings intact, no orphans, no duplicated entries
   * memory banks — counts preserved, nothing dropped, no new failed operations
   * `/etc/hal0` and `/var/lib/hal0` — ownership and permissions unchanged
   Report every difference, including the intended ones; the intended ones are the release note
   material.
2. **Seed reconciliation.** New seeds shipped by the release (profiles, slots, curated models)
   must be *added* without clobbering user modifications. Find a seed the user had modified and
   confirm the modification survived. Tombstoned/retired seeds must actually disappear rather
   than resurrect. **The seed loop does not back-fill existing files**, which makes an upgraded
   box MORE exposed than a fresh one to two rc.5 defects — check both here: capability slots the
   operator created historically carry no `profile` key and 501 their own endpoint (#1830), and
   slot TOMLs carrying larger ceilings written by earlier releases widen the advertised-vs-served
   context gap (#1835).
3. **Functional smoke on the upgraded box.** A short version of the fresh-box lanes: chat
   completion, embeddings, a memory retain and recall, a brain steward question, the dashboard
   loading with real data. Anything broken here that works on the freshly installed box is an
   upgrade-path defect, and those are the most expensive kind to ship.
4. **Version consistency.** Every surface reports the new version: CLI, API, dashboard, MCP
   `serverInfo`, systemd unit descriptions.
5. **Unit and quadlet refresh.** Did unit files, quadlets, and runner image references actually
   get rewritten to the new release's values, or are stale ones still on disk? Check for leftover
   files from the previous version's layout.
6. **Second update check.** `hal0 update --check` on the upgraded box must now report up to
   date, against the same manifest.

## Leave behind

The box healthy on the new version, with its accumulated state intact — it is the input to the
*next* release's upgrade lane, so do not reset it.
