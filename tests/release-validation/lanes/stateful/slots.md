# Lane: slots (stateful, order 1)

Slot lifecycle end to end. You run first, so the box is in its post-install state — capture what
that state actually is before you change it.

## Before you start, and again at the end

Record `systemctl --failed` and `hal0 slot list` verbatim at lane entry and lane exit, and diff
them. That makes residue you create attributable instead of anonymous, and it fails loudly if
another agent is mutating the box: if slots appear that you did not create, stop and say so —
capacity, port and failed-unit assertions are worthless on a contended box.

## Checks

1. **Post-install baseline.** Record every slot, its assigned model, its state, and the unit
   status, before touching anything. On a fresh install this is a finding-rich moment: which
   slots ship seeded, which ship model-less, and does anything already look wrong?
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
9. **Reject a bad model file.** Feed `hal0 model add` a file with a `.gguf` name and no GGUF
   magic (`head -c 2000000 /dev/urandom`). rc.5 admitted it as `capabilities: chat` (#1838) —
   precisely the input that makes check 8's crash loop reachable by accident rather than on
   purpose. Delete the registration afterwards.
10. **Backend selection is real, not a label.** Diff `systemctl cat hal0-slot@<slot>` between the
    same slot pinned to `cpu` and to the box's GPU backend. What must change is the runner
    **image** and the `AddDevice=` lines; a residual `-ngl -1` on a CPU-pinned slot is inert and
    adjudicated (`known-issues.yaml: cpu-pinned-slot-keeps-ngl-flag`). Restore the original
    device when done.
11. **Memory accounting** — regression `slot-capacity-vram-attribution` (#1839).
    `hal0 slot capacity` must not book VRAM on a box where `/api/hardware` reports no
    compute-capable GPU, and its Total MB must be reconcilable with `hal0 slot metrics` Mem MB
    for the same slot in the same second. State which figure is real RSS and which is an
    estimate.

## Leave behind

Brain slot loaded and healthy on the CPU-viable chat model; embed slot loaded (the memory lane
uses it); rerank unloaded; every throwaway slot deleted and its unit reset. State all of this in
`box_state_on_exit`, including anything you could not clean up.
