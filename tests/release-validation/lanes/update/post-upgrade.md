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
     the unfired migration is a fresh regression of its own. Cross-check the updater's relabel
     DECISION against the ACTUALLY-RUNNING container image, not just the resolved/config image —
     regression `slot-drift-ignores-image-and-device` found this exact seam blind:
     `podman inspect <slot ctr> --format '{{.Config.Image}}'` vs the unit's resolved `Image=` vs
     `GET /api/slots/<n>` `actual_image`/`image_mismatch`. A slot whose on-disk unit was
     correctly re-pinned to the new image while the LIVE container keeps running the old one
     (no bounce on a plain update is by-design — `updater.py`'s own comment: a bounce could kill
     a mid-inference request) must still be flagged by `GET /api/updates/slot-drift`; a
     `count: 0` response for such a slot is the defect, not a pass.
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
7. **Memory extraction ctx-preflight, fail-fast and no dangling op.** POST a retain against a
   known-undersized extraction anchor (an upgraded box's historical pin is the likely
   candidate — see check 3's grounding probe) and confirm a rejection creates ZERO operations,
   not merely that the request 400s. This verifies the fail-fast CONTRACT — a rejected request
   that still leaves a dangling `processing`/`failed` op behind is the shape of regression
   `memory-extraction-ctx-preflight-missing`, and a bare 400 with no op created can look like a
   pass if you only check the HTTP status.

8. **Service-user write access to every migration target.** Assert `sudo -u hal0 test -w` on
   `/var/lib/hal0/backups` and every other directory the post-activation migrations write to, not
   just `*.db` files. A `post_swap_migration_failed` / `hal0.runner_system_migration_failed` /
   `hal0.profile_centric_migration_failed` pair in the journal verifies (or refutes) the banner's
   'restart retries automatically' claim.
9. **Drift and routing after a partial migration.** For every slot with `image_mismatch=true` in
   `/api/slots`, `GET /api/updates/slot-drift` must list it or the slot must show an explicit
   unconverted/render_error state (`slot-drift-blind-when-render-fails`, #2660); run this on a box
   whose migration did not apply. `POST /v1/chat/completions model=hal0/agent` must resolve to a
   live slot (this protects 'upgraded boxes keep a bound agent slot', the premise of
   `brain-tool-model-points-at-modelless-slot`). Diff `hal0 profile list` before and after: every
   user-defined profile must survive and none be replaced by a same-named seed. Compare `hal0
   runner-images ls` Store against the 'Runner images converged' line; Store=missing is by design
   (`runner-images-converged-is-catalogue-only`).

10. **Slot `type` audit.** For each `/etc/hal0/slots/*.toml` compare `type` with the shipped seed (embed=embedding, rerank=reranking, tts=tts) and run `output_sanity.skip_reason(cfg)` from the venv; any embed/rerank/tts slot where it returns None cannot load (#2693). Record, do not fix.
11. **Attribution guard.** At lane start and end run `journalctl -u hal0-api --since <start> | grep -E '"(POST|PUT|DELETE|PATCH) '` (excluding /mcp/ and /v1/) and `ls -la /root` for new dirs. Name any foreign actor in the report and stop diffing state after its first call.
12. **Remedy consistency and drift capture.** When `hal0 update` prints 'Run hal0 update --restart-slots', run it once: it must succeed or its refusal must name the real cause (cross-check `skipped_render_error` in `updater.post_start_image_drift_restart`). Capture `/api/updates/slot-drift` and `image_mismatch` together immediately after the migration, before any slot reload.
13. **Units and idempotency.** `systemctl list-unit-files 'hal0-slot@*' | grep masked` against slots with a bound model (hal0 should say 'unit masked', not 'systemctl restart failed'); diff `/etc/systemd/system/hal0*.service` against `/usr/lib/hal0/current/installer/systemd/`; dry-run `hal0 runner-images migrate` and `hal0 profile migrate` must both say 'nothing to convert'.

## Leave behind

The box healthy on the new version, with its accumulated state intact — it is the input to the
*next* release's upgrade lane, so do not reset it.
