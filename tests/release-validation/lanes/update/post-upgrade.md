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
   material. Add a service-DB writability probe: for every `*.db` under /var/lib/hal0, assert
   the hal0 service user can write it, and grep the first minute of the post-upgrade journal for
   `*.init_failed` — this catches the #1546 family (legacy root-owned DBs) that convergence and
   doctor both miss; an upgrade never repairs ownership the perms table has no row for.
2. **Seed reconciliation.** New seeds shipped by the release (profiles, slots, curated models)
   must be *added* without clobbering user modifications. Find a seed the user had modified and
   confirm the modification survived. Tombstoned/retired seeds must actually disappear rather
   than resurrect. **The seed loop does not back-fill existing files**, which makes an upgraded
   box MORE exposed than a fresh one to two rc.5 defects — check both here: capability slots the
   operator created historically carry no `profile` key and 501 their own endpoint (#1830), and
   slot TOMLs carrying larger ceilings written by earlier releases widen the advertised-vs-served
   context gap (#1835).
   * **2b. Vulkan-slot relabel migration (#1934), gated on #1960.** The upgrade stage's
     "before" snapshot recorded every slot TOML's `device` value — diff against it. The
     DESIRED end state: every llama.cpp-backed slot that was `gpu-vulkan` now reads `gpu-rocm`
     on this box (`/dev/kfd` present); only the `device` key changed (flat or nested `[slot]`
     shape), every other key byte-identical modulo TOML re-serialization; any non-llama GPU
     slot (Kokoro TTS / whisper.cpp / ComfyUI, if present) untouched — still `gpu-vulkan`;
     relabel journal breadcrumbs (`updater.slot_vulkan_relabeled_rocm`, or `_cpu_fallback` on
     a kfd-absent box) present with `job_id` populated (#1935); re-running the activation step
     adds no new relabel lines (idempotency). **Known gate:** as of kit v5, #1960 means the
     updater runs migrations pre-swap in the OUTGOING tree, so on an rc.6→rc.7 update the
     relabel will NOT fire unless #1960's fix landed in the release under test. If TOMLs are
     unchanged and no relabel breadcrumbs exist, first check whether #1960 is fixed in this
     release: if not, record the result against #1960 (do not file a duplicate); if it is,
     the unfired migration is a fresh regression of its own.
3. **Functional smoke on the upgraded box.** A short version of the fresh-box lanes: chat
   completion, embeddings, a memory retain and recall, a brain steward question, the dashboard
   loading with real data. Anything broken here that works on the freshly installed box is an
   upgrade-path defect, and those are the most expensive kind to ship. Two grounding probes that
   caught real defects in rc.6: retain a unique marker and verify every fact recalled under that
   document id derives from the marker (catches extraction-prompt leakage — an upgraded box's
   historical anchor pins make it the likelier victim, see
   `known-issues: memory-extraction-quality-is-anchor-dependent`); and ask the steward a
   system-state question with a known answer (the running version) and check it against
   /api/health — separates SSE mechanics from steward grounding.
4. **Version consistency.** Every surface reports the new version: CLI, API, dashboard, MCP
   `serverInfo`, systemd unit descriptions.
5. **Unit and quadlet refresh.** Did unit files, quadlets, and runner image references actually
   get rewritten to the new release's values, or are stale ones still on disk? Check for leftover
   files from the previous version's layout. Prove `updater.unit_rerender rewritten=0` is
   correct rather than a miss: grep /etc/systemd/system and /etc/containers/systemd for the
   PREVIOUS version's tree path — zero hits means the units were version-independent all along.
   Cross-check /api/updates/slot-drift against each slot unit's ActiveEnterTimestamp to prove
   slots were (not) bounced as documented.
6. **Second update check.** `hal0 update --check` on the upgraded box must now report up to
   date, against the same manifest.

## Leave behind

The box healthy on the new version, with its accumulated state intact — it is the input to the
*next* release's upgrade lane, so do not reset it.
