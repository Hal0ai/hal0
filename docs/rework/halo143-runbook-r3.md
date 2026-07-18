# halo143 deploy-validation runbook — rework-R3

> Operator-run, on **halo143 (10.0.1.143) only**. NEVER touch lxc105 (`hal0`/`hal0lxc`,
> 10.0.1.142) — untouched live reference. Run phases in order; stop and report at any red.
> Written 2026-07-18 against `main` @ `ab3e88f3` (tag `rework-R3`).

## Phase 0 — Preflight + snapshot (5 min)

```bash
# On halo143:
hal0 --version                       # record current deployed version (rollback ref)
hal0 doctor all                      # BASELINE — save output
sudo tar czf /root/halo143-pre-r3-$(date +%s).tgz /etc/hal0 /var/lib/hal0/slots /var/lib/hal0/*.db 2>/dev/null
podman --version                     # need ≥4.4 (quadlet generator); note if ≥5.x
```

## Phase 1 — Deploy R3 + install validation (15 min)

1. Deploy from git at the tag (side-by-side policy stands; this also clears the
   hot-patched §7.4 inc5 from the old session):
   ```bash
   hal0 update --source git   # target main @ rework-R3 / ab3e88f3 (check hal0 update --help for ref syntax)
   ```
2. Post-deploy health:
   ```bash
   hal0 doctor all && hal0 doctor perms
   curl -s http://127.0.0.1:8080/api/health        # OPEN endpoint, must 200
   ```
   Dashboard loads; existing slots list intact.
3. Live golden-path pass: pull a small model → create slot → load → one inference
   round-trip → unload. All through the dashboard or CLI, your preference.

## Phase 2 — Quadlet `@`-name verify (LOAD-BEARING — 10 min)

The R3 quadlet renderer writes a literally-`@`-named `.container` file and RELIES on
podman's generator producing `hal0-slot@<name>.service` from it. This is the one
assumption only real hardware can confirm.

```bash
# create + load any slot (name e.g. "qtest"), then:
ls /etc/containers/systemd/ | grep hal0-slot          # expect hal0-slot@qtest.container
systemctl list-units 'hal0-slot@*' --all              # expect the generated service, active
podman ps --format '{{.Names}}'                       # expect hal0-slot-qtest running
curl -s http://127.0.0.1:<port>/health                # llama answers on its authority port
# then delete the slot and confirm teardown:
#   file gone, service gone after daemon-reload, container stopped, port released:
curl -s -H "Authorization: Bearer $ADMIN" http://127.0.0.1:8080/api/ports
```

**If the generator rejects the `@` filename**: capture the exact `journalctl -u
systemd-generators`/daemon-reload error verbatim and stop Phase 2 — the fallback
(service rename inside the M5 window) is a one-parameter change in `slots/naming.py`,
but we decide that together with the real error in hand.

## Phase 3 — M5 rehearsal ON A COPY (NOT the live tree — 15 min)

⚠️ The runtime is still name-keyed: M5 on the live tree WOULD BREAK the running
system (the live flip lands atomically with the runtime id-flip in a later lane).
Rehearse against copies only:

```bash
mkdir -p /root/m5-rehearsal/{etc-slots,var-slots}
cp -a /etc/hal0/slots/. /root/m5-rehearsal/etc-slots/
cp -a /var/lib/hal0/slots/. /root/m5-rehearsal/var-slots/
# run the migrator module against the copies (it takes explicit paths + an
# artifact-ops seam; RecordingSlotArtifactOps records unit/podman renames
# instead of executing them — exact kwargs in the module docstring):
/usr/lib/hal0/venv/bin/python - <<'EOF'
from hal0.slots.migrate_id_keying import migrate_slot_id_keying
help(migrate_slot_id_keying)   # confirm signature, then invoke against /root/m5-rehearsal paths
EOF
```
Verify on the copies: every TOML gains `id` + is renamed `<id>.toml`; state moved to
`<id>/state.json` with `slot_id` injected and `name` rewritten; recorded unit/container
renames match `hal0-slot@<id>`. **Run it a second time** — must be a clean no-op
(idempotence). Then delete the rehearsal dir.

## Phase 4 — Live smoke of R3 behaviors (15 min)

1. **Rename**: on a RUNNING slot → must be refused/disabled with a reason. Offline
   slot → rename succeeds; `GET /api/slots/by-id/<id>` shows same id + same port after
   reload (PortAuthority claims by id, not name).
2. **Quoting fix** (the lxc105 nano-crash case): give a slot's model a file
   `chat_template` + `--chat-template-kwargs '{"enable_thinking":false}'` in its
   flags → slot must START (previously: llama JSON parse error). Confirm the answer
   lands in `content` (no-think works).
3. **Doctor bundle on real hardware**: `hal0 doctor bundle` → confirm
   `system/rocminfo.txt`, `rocm-smi`, `podman-images`, `logs/journalctl` populate, and
   `config/api.env` values are `***`-masked (spot-check for any leaked secret).
4. **System info**: `curl -s -H "Authorization: Bearer $CLIENT" http://127.0.0.1:8080/api/system-info`
   → real GPU/NPU fields + per-runner `installed`/`installable` states.
5. `hal0 doctor all` again — compare with the Phase 0 baseline; nothing newly red.

## Phase 5 — Report

Paste back per phase: green/red + any verbatim errors (especially Phase 2). Greens
flip the board's held-for-deploy notes; reds become fix-forward lanes. Do not
attempt fixes on the box beyond reverting to the Phase 0 snapshot.

**Rollback**: `hal0 update` back to the recorded prior version; restore the Phase 0
tarball if config/state was damaged (Phases 2–4 shouldn't touch anything the
snapshot doesn't cover).
