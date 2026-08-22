# Lane: capabilities (stateful, order 5)

Capability slots, the model registry, and profiles — the configuration surface a user actually
drives when setting hal0 up for their hardware.

## Checks

Record `systemctl --failed` verbatim at lane entry and lane exit and diff them. On a box with no
snapshot, residue accumulates anonymously across a serialised run unless each lane bounds its own.

1. **Baseline, cross-checked against the advertised backends.** `hal0 capabilities --help` and
   `list`, plus `GET /api/capabilities`. Then assert that every device named in the shipped
   `/etc/hal0/capabilities.toml` appears in `/api/capabilities` `.backends` **and** in the
   catalog rows the picker actually offers for that child. Also the inverse: catalog rows must
   only advertise backends the box's `.backends` actually offers — rc.6 had ~30 npu rows plus a
   gpu-rocm row that were un-selectable on a box that physically HAS the NPU; either the backend
   should be advertised or the rows hidden. The seed copying a GPU device out of
   the shipped slot TOML is by-design (`known-issues.yaml:
   capabilities-seed-devices-from-slot-tomls`); a default selection naming a backend the host
   never advertises, or one no catalog row can match, is a hard fail — that is where the seed
   costs the user something. Registration path matters here: a LOCALLY-added model
   (`hal0 model add`) can advertise a backend a curated/catalog row for the same capability
   correctly omits — regression
   `capabilities-catalog-advertises-unavailable-gpu-rocm-for-registry-models`. Register a
   throwaway small gguf (`hal0 model add <path> --id zzprobe`) and compare its `backends` list
   against a curated row's for the same capability type; remove it when done. Do not conflate
   this with `catalogs.voice.tts`'s `qwen3-tts` row always listing `gpu-rocm` — that is a
   deliberate two-engine switch (`known-issues: tts-catalog-lists-qwen3-on-gpu-rocm-regardless-
   of-host`), not a bug.
2. **Enable a capability against the real hardware.** Bind the embedding model to the embed
   capability on the backend this box actually has, and enable it. Does the flip from the seeded
   default work cleanly? Does enabling load or require the slot? Verify through both
   `capabilities list` and the API.
2b. **Pull-before-load, for a not-yet-downloaded catalog row** — regression
    `capability-apply-skips-model-pull`. Find a catalog row with `downloaded:false,
    pullable:true` for any capability child, apply it, and check `GET /api/models/pulls`
    IMMEDIATELY (not after settling) for a corresponding pull job. rc.7 found NO pull job is
    ever created — the apply blocks for the full crash-loop dwell (~3 min) then returns 200
    `{"status":"warming"}` while the container dies on a bare model id with no path. Also check
    whether the capabilities dashboard picker marks the row as undownloaded (⬇ or equivalent) —
    the shipped panels currently offer every row unmarked. If the box's rerank slot TOML default
    (`bge-reranker-v2-m3-q4_k_m`) is itself undownloaded at lane entry, that IS this defect's
    first-click blast radius on a fresh install — record it as such rather than only as a
    manufactured probe.
3. **Same for the rerank child**, then verify the path that consumes it: the memory reranker
   config (`[memory.embedding] rerank_model`, `rerank_gateway_url`). Issue a rerank through the
   gateway with a query and three documents. In rc.4 this whole path was dead because profile
   flags never reached argv (#1787). Use the SEEDED default row (`bge-reranker-v2-m3-q4_k_m`):
   the curated base row is unloadable on the pinned runner (regression
   `curated-reranker-base-row-unloadable`) and it is offered FIRST and smallest in the picker —
   load-test every curated row the picker surfaces for at least one model per capability, and a
   row whose gguf the runner cannot parse is a catalog finding, not a rerank finding. A failed
   apply must be judged on ALL its surfaces: the POST's typed 500, `capabilities list` (which
   today silently renders "yes" with no Status column — rc6-polish-rollup item 1), the
   `status` field in GET /api/capabilities, and the persisted `enabled` value — persistence of
   intent is by-design (`known-issues: capability-apply-persists-intent-on-lifecycle-failure`).
4. **Model defaults.** Promote a model to default for its type. Verify `hal0 model list` marks it
   with the `*` in the unlabelled first column. Then probe dispatch with all three client shapes
   — omitted `model`, `model: "default"`, and the explicit id — because only the third normally
   gets tested and it is the one that hides the behaviour. The marker NOT steering the dispatcher
   is by-design (`known-issues.yaml: model-default-marker-is-per-type-fallback`); what is
   reportable is a model-less completion 404ing on a box whose `agent` anchor slot HAS a model
   bound.
5. **Registry mutations.** `hal0 model add` on copies of a gguf staged into a scratch directory
   (copy, never move, and never touch the shared store's originals):
   * Is the capability type auto-detected correctly? Register the **same file twice under two
     different names** — the upstream filename and a neutral one. When the header carries no
     pooling/tags/causal signal, the two differing purely by filename is now ADJUDICATED
     by-design provided the filename-derived one prints `confidence: medium` — read
     `known-issues: model-add-detection-surfacing` for what still re-opens #1838 before filing.
   * Register the same bytes by two different PATHS as well (`hal0 model add` versus the
     install-time auto-scan) and diff the resulting ids and display names.
   * Feed it a file with a `.gguf` name and no GGUF magic. Registering it as `chat` is by-design
     (`known-issues: model-add-detection-surfacing`) PROVIDED it is surfaced honestly — a
     warning line, `detection_confidence: low`, and `detection_warning` present. Accepted as
     `chat` with NO warning and no lowered confidence is the finding.
   * Try one add from a directory the `hal0` service user cannot read (e.g. under `/root`) — the
     first place an operator puts a downloaded gguf. The refusal is correct; an error that
     cannot distinguish "unreadable" from "missing" is not.
   * Then `hal0 model rm <id>`: does it remove the registry entry, the file, or both — and does
     it *ask*? Assert the file survives and that `hal0 model rm <id> < /dev/null` aborts rather
     than proceeding unprompted. A remove that silently deletes files from a shared store is a
     `critical` finding. Clean up the scratch directory afterwards.
6. **Profiles.** `hal0 profile` exists as of rc.5. Inspect what a profile contains, confirm the
   API's view matches what a loaded slot actually got (the same seam as #1787 from the
   configuration side), and diff `hal0 profile show <name>` field by field against both
   `GET /api/profiles/<name>` and the matching row of `hal0 profile list` — the detail view
   already drops `used_by`. Do not destructively rewrite the brain profile; use a throwaway.
7. **Registry consistency after all of the above.** Re-list models and slots. Orphans, dangling
   references, stale capability bindings?

## Leave behind

Capabilities left enabled on the backend this box supports; defaults set; scratch model files
and throwaway profiles removed. Brain and utility slots healthy.
