# Both-boxes deploy-validation runbook — rework-R4-stage

> Operator-run, on **halo150 (10.0.1.150, privileged LXC / podman 4.9.3)** and
> **halo143 (10.0.1.143, unprivileged LXC / podman 5.7)** — standing policy: BOTH
> boxes, same pass. NEVER touch lxc105 (`hal0`/`hal0lxc`, 10.0.1.142) — untouched
> live reference. Run phases in order per box; stop and report at any red.
> Written 2026-07-19 against `main` @ `c91d0cf5` (tag `rework-R4-stage`).
>
> R4-stage deltas this pass exists to validate: linear convergent `install_hermes`
> (provision inc-2), O12 rootful introspection seam (`hal0-podman-ro`), the steward's
> SHIPPED read-only default (KB-2/3), hal0-memory + hal0-provider plugin trees,
> HP-executor's Hermes worker path (first live contact — endpoint is unpinned),
> uniform quadlet render on both substrates, restored `.hal0-managed` uninstall gate.

## Phase 0 — Preflight + snapshot (5 min/box)

```bash
hal0 --version                       # record (rollback ref)
hal0 doctor all                      # BASELINE — save output
sudo tar czf /root/$(hostname)-pre-r4-$(date +%s).tgz /etc/hal0 /var/lib/hal0/slots /var/lib/hal0/*.db 2>/dev/null
podman --version                     # 150: 4.9.3 · 143: 5.7 — record
```

## Phase 1 — Deploy + baseline health (10 min/box)

```bash
hal0 update --source git             # target main @ rework-R4-stage / c91d0cf5
hal0 doctor all && hal0 doctor perms # expect: NO .config/.local ownership findings
                                     # (those rows are RETIRED — a finding here is a bug)
curl -s http://127.0.0.1:8080/api/health   # OPEN endpoint, must 200
```
Dashboard loads; existing slots intact; existing model list intact.

## Phase 2 — O12 rootful seam (10 min/box)

The installer must lay down `/usr/lib/hal0/bin/hal0-podman-ro` (root:root 0755) +
`/etc/sudoers.d/hal0-podman-ro` (0440, pinned to the helper).

```bash
ls -l /usr/lib/hal0/bin/hal0-podman-ro /etc/sudoers.d/hal0-podman-ro
sudo visudo -cf /etc/sudoers.d/hal0-podman-ro
sudo -u hal0 sudo -n /usr/lib/hal0/bin/hal0-podman-ro images   # root's store, no password
curl -s -H "Authorization: Bearer $ADMIN" http://127.0.0.1:8080/api/system-info | grep -o '"podman_context":"[^"]*"'
```

**Expect `"podman_context":"rootful"`** and backend states that MATCH the images
actually present in root's store (the "installable-though-installed" split is the
bug this fixes). `"rootless"` after install = seam not installed / sudoers missing —
capture which and stop the phase.

## Phase 3 — `install_hermes` convergence (LOAD-BEARING — 15 min/box)

The phase machinery is gone; `hal0 agent install hermes` now runs ONE linear
convergent pass.

```bash
hal0 agent install hermes            # run 1 — record which steps report changed
hal0 agent install hermes            # run 2 — THE ASSERTION: zero mutating steps
hal0 agent status hermes             # report renders (flat provision.json kept)
```

**Run 2 must report every install step converged/skipped and change NOTHING**
(brain/persona steps are exempt — they publish to the memory store and carry
`RELOCATE(brain-lane)` markers). Also verify:

```bash
ls /var/lib/hal0/.hermes/.hal0-managed          # marker stamped (uninstall gate)
ls /var/lib/hal0/.hermes/plugins/hal0-memory/   # memory plugin tree
ls /var/lib/hal0/.hermes/plugins/model-providers/hal0/   # provider plugin tree (NEW)
ls -ld /var/lib/hal0/.hermes/scratch            # terminal.cwd target, hal0:hal0
systemctl is-active hermes-agent hermes-gateway 2>/dev/null   # units up (names per box)
sudo grep -c API_SERVER_KEY /var/lib/hal0/secrets/agents/hermes.env   # 1, never rotated by run 2
```

Then the retired-path negative: `hal0 agent install --help` — `--adopt` gone;
no `.hal0-managed`-claim refusal anywhere (adopt/foreign detection is deleted).

## Phase 4 — Read-only steward (10 min, one box is enough; smoke the other)

Shipped default: `[brain_chat] read_only=true` — **deploy-visible change**.

1. Dashboard steward chat: ask something read-shaped ("list slots") → answers.
2. Ask for a mutation ("load slot X") → refused with the stable surface
   `read-only mode ([brain_chat] read_only=true)`. Nothing executes, no approval
   frame appears (guardrail beats gate).
3. Widen: set `[brain_chat] read_only = false` in hal0.toml, restart hal0-api,
   repeat the mutation → now executes (or parks `pending_approval` if gated —
   approve via the bell and confirm execution).
4. Revert to `read_only=true` (or delete the key — true IS the default now) and
   confirm the refusal returns.

## Phase 5 — Hermes plugin liveness (10 min)

```bash
# provider: model discovery through the gateway (restart-free aliases)
# in Hermes chat/config: the "hal0" provider lists live hal0 models (aliases filtered)
# memory: write a fact via chat ("remember: <marker>"), then recall it —
# and confirm the recall block renders as DATA (provenance-annotated bullets)
journalctl -u hermes-gateway -n 50   # no plugin import errors
```

hal0-provider registers under the module seam; if the gateway logs show it failed
to import, capture the traceback verbatim (contract-fixture mismatch = lane bug).

## Phase 6 — HP-executor first live contact (10 min, 143 preferred)

The executor is INERT unless `HERMES_DASHBOARD_BASE_URL` is set for hal0-api. Its
worker path (`/api/plugins/kanban/runs`) is **unpinned by the contract fixtures** —
this phase is where reality votes.

```bash
# with HERMES_DASHBOARD_BASE_URL set in hal0-api's env, restart hal0-api, then:
journalctl -u hal0-api -n 20 | grep hermes_executor   # board.hermes_executor_registered
# dispatch one board card at Hermes (dashboard board → dispatch), then:
#   - card's lane/deps/approval UNCHANGED on the hal0 side (no board mirroring)
#   - run/event appended with the attempt handle
```

**If Hermes 404s the runs path**: capture the exact status + the path Hermes DOES
serve for run dispatch (`hermes gateway routes` or the dashboard's network tab) —
that's a one-constant fix (`WORKER_BASE_PATH`), flagged on the board as expected.
Unset the env var afterward if you don't want the bridge live yet.

## Phase 7 — Slot regression + uniform render (10 min/box)

```bash
# create + load a slot ("qtest"), then:
grep -E "PodmanArgs|AutoRemove|GroupAdd|SecurityOpt|Network" /etc/containers/systemd/hal0-slot@qtest.container
```

**Expect**: `--group-add`/`--security-opt` ONLY inside `PodmanArgs=`; NO
`AutoRemove`/`GroupAdd=`/`SecurityOpt=` keys anywhere; identical render shape on
BOTH boxes. Inference round-trip; slot delete tears down clean (143: zero
netavark/netns errors in the journal — bridge is fine now that AutoRemove is gone;
host-net is a SEPARATE queued lane, don't flip it this pass).

## Phase 8 — Uninstall gate + wrap (10 min, one box)

```bash
hal0 agent uninstall hermes          # must ACTUALLY remove HERMES_HOME (marker present)
ls /var/lib/hal0/.hermes 2>&1        # gone
hal0 agent install hermes            # reinstall clean (convergent pass, fresh key)
hal0 doctor all                      # final green
```

An uninstall that leaves the home behind = the marker gate regression this
checkpoint specifically fixed — capture `hal0 agent uninstall` output verbatim.

---

**Record per box**: phase table (green/red + notes), run-1 vs run-2 changed-step
lists from Phase 3, the `podman_context` value, and any verbatim errors. Findings
come back to the orchestrator as O-series rows; fixes go forward on descar via
PR #1311.
