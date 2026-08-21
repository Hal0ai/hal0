# Lane: slots (stateful, order 1)

Slot lifecycle end to end. You run first, so the box is in its post-install state — capture what
that state actually is before you change it.

## Before you start, and again at the end

Record `systemctl --failed` and `hal0 slot list` verbatim at lane entry and lane exit, and diff
them. That makes residue you create attributable instead of anonymous, and it fails loudly if
another agent is mutating the box: if slots appear that you did not create, stop and say so —
capacity, port and failed-unit assertions are worthless on a contended box. Re-diff against the
entry snapshot **before every capacity, port, or failed-unit assertion**, not only at exit —
in rc.6 foreign `zz*` slots appeared mid-lane and invalidated three checks that had only diffed
at the ends.

## Checks

1. **Post-install baseline.** Record every slot, its assigned model, its state, and the unit
   status, before touching anything. On a fresh install this is a finding-rich moment: which
   slots ship seeded, which ship model-less, and does anything already look wrong? Include an
   ownership sweep of the state the daemon writes: for each dir under /var/lib/hal0 that
   hal0-api must write (model-pull-jobs especially), `sudo -u hal0 test -w`, and grep the boot
   journal for `pull_job_persist_failed` after the installer's own pull — regression
   `model-pull-jobs-root-owned`. And spot the API's image view: any slot with
   `container_status: running` must not read `image_status: missing`
   (regression `image-status-wrong-podman-store`). `image_status: unknown` (#1939) is a
   different result — the API could not read the image store rather than reading it wrongly;
   record it as a seam finding with the `reason=` from the journal's
   `podman_ro.image_present_unanswered` line, not as this regression.
2. **Assign + load.** Take the embed slot: `hal0 slot edit embed --model <embedding model>
   --hardware <backend>`, then `hal0 slot load embed`. **Time the load and record the CLI exit
   code** (`out=$(...); rc=$?`) — regression `slot-verbs-10s-client-timeout` (#1832): the client
   caps its read at 10 s against a 180 s server budget, so a load that succeeds can exit 1 with
   `ReadTimeout`. Always verify the server-side outcome before believing either result. Poll
   health until ok (≤120 s on CPU) and watch the state transitions.
3. **A second slot.** Same flow for the rerank slot, so two are live at once.
4. **Restart / unload.** `hal0 slot restart <name>` — comes back healthy? `hal0 slot unload
   <name>` — clean stop, state offline, unit stopped, port released? Record exit codes here too;
   all three mutating verbs share the same client timeout.
5. **Create / delete.** `hal0 slot create --help`, then create a throwaway slot on a free port
   (`hal0 ports`), assign a small chat model, load, verify health, unload, delete. Check for
   residue: unit files, `/var/lib/hal0/slots/<name>/`, claimed ports, registry entries, and a
   new entry in `systemctl --failed`.
6. **A capability slot created by hand** — regression `slot-create-no-profile-501` (#1830).
   Create a `--type reranking` slot AND a `--type embedding` slot with `hal0 slot create`, bound
   to a model registered via `hal0 model add` (which leaves `defaults: null`). Then: does the
   written TOML carry a `profile` key? does the resolved argv carry `--reranking` /
   `--embeddings`? does the endpoint answer, or 501 while the slot reports `ready`? This is the
   path the shipped seeds do not exercise, and it is where #1787 still lives.
7. **Swap on a healthy slot.** `hal0 slot swap <slot> --model <other model>`. Even if the CLI
   throws a `ReadTimeout`, wait **60 s** and only then verify server-side: `systemctl cat
   hal0-slot@<slot> | grep -o -- '--model [^ ]*'` and the runner's own `/v1/models`. Reading it
   immediately shows the OLD model because the previous load still holds the per-slot lock —
   that misreading cost rc.5 a false regression. Then swap back and confirm healthy.
8. **Swap and lifecycle on an UNhealthy slot** — regression `crash-loop-lifecycle` (#1791).
   Induce a crash loop on a throwaway slot by binding it to a file the runner cannot load, then
   poll for **four minutes**: state, `container_status`, `metadata.message`, and
   `systemctl show -p ActiveState -p Result -p NRestarts`. A `warming` reading inside the first
   ~180 s is by-design (`known-issues.yaml: crash-loop-warming-180s-window`) — the finding is a
   state that never converges, an empty message on `error`, or a death nothing surfaces in
   `hal0 status` / `hal0 doctor`. Then swap it to a good model and clean it up.
   * **8b. Output-sanity gate fires (#1922) — now a shipped product gate, not a to-do.** #1922
     merged as PR #1962 (main `b78b3ffc`) and is the permanent net under the rc.7 Vulkan
     restoration (#1948): confirm it exists and is wired into the ready path — grep the
     installed tree for the readiness probe call site (`slots/manager.py` or wherever it
     landed) and confirm it runs before a slot is marked `ready`, with the temp-0 "The capital
     of France is" -> "Paris" shape, on BOTH endpoints (timeouts land in a retryable `warming`
     with a device-derived budget, not a silent pass). Then, if a synthetic bad case can be
     constructed on this box (a degenerate/non-instruct model file, or any still-existing
     explicit override onto a broken backend), load it and confirm the slot lands in `error`
     with a message naming the probe and the expected token — never `ready`, never a silent
     degrade, never an infinite retry. If no bad case can be forced on this box, say so
     explicitly and record it as a coverage gap rather than a pass — a healthy slot's canary
     passing does NOT verify the gate.

   * **8c. gpu-vulkan LLM lane, restored — the positive case.** On this box (render node, no
     /dev/kfd), assign a small chat model to a throwaway slot with `device=gpu-vulkan` and load
     it. Preflight must PASS: `require_kfd_for_gpu_slot`'s `gpu-vulkan` branch
     (`providers/_gpu.py`) delegates the `llama` runtime lane on an AMD host to
     `_require_vulkan_lane_prerequisites`, which does NOT consult `/dev/kfd` at all — it checks
     (1) `image_serves_vulkan_lane()`, membership of the slot's resolved image in the
     `VULKAN_CAPABLE_IMAGE_REFS` allowlist (`config/schema.py`, currently just
     `VULKAN_FIXED_IMAGE = ghcr.io/hal0ai/hal0-combined:0822`), and (2) `render_node_present()`
     for the runner identity (uid 0, the rootful `hal0-slot@` container). Record the resolved
     image ref (`systemctl cat hal0-slot@<slot> | grep '^Image='`) alongside the pass. The #1922
     sanity gate (check 8b) must run and pass before `ready`. Confirm output is coherent past the
     canary: a second, different prompt through the same slot's own port, not just the gate's
     internal probe. Clean the slot up when done.

   * **8d. gpu-vulkan LLM lane, negative case — a stale/broken image must be REFUSED at
     preflight.** Force (or simulate, if the box cannot hold two runner images at once — say so
     either way) a `gpu-vulkan` slot's resolved image onto a ref NOT in `VULKAN_CAPABLE_IMAGE_REFS`
     (the outgoing `ghcr.io/hal0ai/hal0-rocmfpx:ade07ba` pin — a member of
     `STALE_ROCMFPX_IMAGE_REFS` — is the concrete example, #1888's defect). Loading it must raise
     `GpuPreflightError` from `_require_vulkan_lane_prerequisites`'s image gate (`providers/
     _gpu.py`), citing #1888 by number and naming `VULKAN_FIXED_IMAGE` as the repin target —
     confirm both appear in the CLI/API error text, not just the journal. This gate fires BEFORE
     the render-node check, so a box with no render node at all still gets the image-specific
     message when the image is also bad. Grep to confirm the call site:
     `grep -n '_require_vulkan_lane_prerequisites\|image_serves_vulkan_lane' providers/_gpu.py`.

   * **8e. The deliberately-garbage lane — THE single most important check of this release.**
     `ENV_ALLOW_VULKAN_FALLBACK` (`HAL0_ALLOW_VULKAN_FALLBACK` in the environment) downgrades
     BOTH correctness refusals in `require_kfd_for_gpu_slot` to a warning — missing `/dev/kfd` on
     the ROCm path, and the 8d image-allowlist refusal on the restored Vulkan path — never the
     render-node check, which is a passthrough fact no env var changes. Set it, force a
     `gpu-vulkan` slot onto a stale/broken image (as in 8d) with a valid render node, and load.
     Preflight now WARNS (`gpu_slot_vulkan_lane_unvalidated_image_allowed` in the journal) and
     admits the load instead of refusing. The #1922 output-sanity gate (8b) MUST then convict it:
     the slot lands in a terminal `error` naming the failed probe, NEVER `ready`, regardless of
     the env override. This is the check that proves the permanent net actually holds even when
     every earlier refusal in the chain has been deliberately defeated — if this one fails,
     nothing else in this lane matters. Record the exact env/command combination used so it is
     reproducible blind. Same honest-failure rule as 8b: if you cannot construct the fixture on
     this box (no stale image in the store to force, or the override is refused for another
     reason), say so explicitly and record it as a coverage gap rather than a pass — do not
     invent a verdict for a check you could not run.
9. **Reject a bad model file.** Feed `hal0 model add` a file with a `.gguf` name and no GGUF
   magic (`head -c 2000000 /dev/urandom`). The registration-with-warning outcome is now
   by-design (`known-issues: model-add-detection-surfacing`) — what you assert is the warning
   line and `detection_confidence: low` actually appearing. Delete the registration afterwards.
   While a first load of a guaranteed-fatal model is in hand, also assert the FAST-CRASH error
   path: `POST /api/slots/<n>/load` must answer a mapped slot error
   (`slot.spawn_failed`/`slot.crash_looping`), never a generic `system.internal` 500 with an
   empty message — the empty-message shape is regression
   `container-health-readerror-unhandled`, intermittent, so record which envelope you got.
10. **Backend selection is real, not a label.** Diff `systemctl cat hal0-slot@<slot>` between the
    same slot pinned to `cpu` and to the box's GPU backend. What must change is the runner
    **image** and the `AddDevice=` lines; a residual `-ngl -1` on a CPU-pinned slot is inert and
    adjudicated (`known-issues.yaml: cpu-pinned-slot-keeps-ngl-flag`). Restore the original
    device when done.
11. **Memory accounting** — regression `slot-capacity-vram-attribution` (#1839).
    `hal0 slot capacity` must not book VRAM on a box where `/api/hardware` reports no GPU that
    is either `vulkan_capable` or `compute_capable` — a vulkan-only box booking resident memory
    to VRAM is the FIX, not the bug (`known-issues: slot-capacity-vram-on-vulkan-is-declared`).
    Total MB must match `/api/slots/metrics` mem_rss_mb for the same RESIDENT slot in the same
    second. State which figure is real RSS and which is an estimate.

## Leave behind

Brain slot loaded and healthy on the CPU-viable chat model; embed slot loaded (the memory lane
uses it); rerank unloaded; every throwaway slot deleted and its unit reset. State all of this in
`box_state_on_exit`, including anything you could not clean up.
