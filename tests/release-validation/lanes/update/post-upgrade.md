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
   * **2b. User-set `device` values survive the update (kit 13: replaces the stale Vulkan-to-ROCm
     relabel check).** #1973 re-enabled the Vulkan lane and the relabel migration is gone from the
     tree. Diff every slot TOML's `device` against the upgrade stage's "before" snapshot: a
     user-set `gpu-vulkan` slot must still read `gpu-vulkan`, and no `updater.slot_vulkan_relabeled_*`
     breadcrumbs should exist. Cross-check the updater's relabel
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

14. **Validators vs the daemon again** (same as upgrade check): after the update `hal0 config validate` against the boot-time load errors.

15. **Alias list audit.** `/v1/models` must not advertise embed or rerank slot aliases as chat (known remainder of nonchat-slot-type, #2729: record `known`, count the rows).

16. **Ctx preflight without editing api.env.** `systemctl set-environment HAL0_MEMORY_EXTRACTION_FLOOR=100000` plus a restart, and assert the slot named in the 503 is the slot actually serving `hal0/utility` (#2676, both directions at the real floor).

17. **Retain yield since the upgrade.** Count hindsight `STREAMING RETAIN COMPLETE: N units` lines; a box where every retain yields 0 units has no working extraction although every health surface is green (ct150: 41 of 41 at 0 facts, #2713).

18. **Unit files.** Diff `/etc/systemd/system/hal0*.service` against `installer/systemd`; only slot quadlets are re-rendered by the updater.

19. **Idle-reap stop must converge.** After any idle-reap stop on a loaded host, assert slot state returns to `offline` (not `error`) and `hal0/<slot>` aliases keep resolving (embed after a stop over 30 s is #2738). Diff `/api/slots` states against `systemctl is-active hal0-slot@X` for every slot at lane exit to catch persisted false errors.

20. **Floor test needs a daemon-env assertion.** After `systemctl unset-environment`, check `/proc/<MainPID>/environ` for the override; unsetting the manager env does not clear a running daemon, so a second restart is required.

21. **MCP initialize paths.** Use `/mcp/admin/mcp` and `/mcp/memory/mcp`; the bare `/mcp`, `/mcp/` and `/mcp/admin` return 405.

22. **Snapshot profiles in the upgrade stage.** Capture `hal0 profile list --json` in the upgrade stage's before snapshot so the post-upgrade profile diff has a real baseline.

23. **Steward answers carry the version.** Ask the steward for the system version and require the final prose, not only the `tool_result` frame, to contain the actual version string (one attempt showed the tool succeeding while the prose claimed it could not run tools).

## Leave behind

The box healthy on the new version, with its accumulated state intact — it is the input to the
*next* release's upgrade lane, so do not reset it.
