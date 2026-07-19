# Golden-paths — halo143 acceptance runbook (deploy-only halves)

Scripted operator steps for the golden-path scenarios that **cannot** run in
CI because they need a real box (LXC / installer / systemd / podman / NFS).
The CI-automatable subset and the coverage map live in
`tests/golden_paths/__init__.py` — this file is the *deploy* half of that
map. No scenario is silently skipped: every one is either a green CI test,
owned by an existing suite, or a step below.

**Standing policy:** deploy-affecting scenarios validate on **BOTH** boxes —
**150** (podman 4.9.3, privileged) and **143** (podman 5.7, unprivileged
in-LXC). Record results per box. Never deploy to lxc105 (untouched live ref).

Legend: ☐ pending · ✔ passed (record commit + box) · ⚠ finding (file an O-row).

---

## Pure deploy-only scenarios (no CI half)

### #1 — Fresh install on a clean LXC
- ☐ Provision a clean LXC; run the installer end to end (git or wheel path).
- Assert: `hal0-api` active, `hal0 doctor` clean, born-owned perms (one `hal0`
  user, `doctor perms` clean, no root-owned slots tree), auth bootstrap key
  written `0640`. Exit 0.
- Prior findings to re-check: NFS chmod tolerance, self-port :3001 check,
  venv same-version force-refresh.

### #2 — Installer rerun over a healthy installation
- ☐ Re-run the installer over the box from #1 (no uninstall).
- Assert: **idempotent** — config byte-identical after rerun (the
  `install_hermes(repair=False)` convergence contract), no duplicate units,
  no ownership drift, services still healthy. `--repair` reconciles ownership
  without mutating good state.
- Unit half (config convergence / double-run) already covered:
  `tests/agents/test_hermes_provision*.py` (run-2-mutates-nothing).

### #3 — Upgrade from the current stable release
- ☐ Install current stable, then upgrade in place to this build.
- Assert: migrations apply forward (registry + store + board + slot-state as
  applicable), no data loss, slots reconcile to running, version flips, old
  units cleaned (legacy static-unit cleanup, O7 class).

### #13 — NFS-backed model storage
- ☐ Point the model store at an NFS mount; pull + serve a model.
- Assert: pull writes through NFS (chmod tolerance — no hard `chmod` failure
  on root-squash), refcount GC works, slot launches with NFS-resident weights.

---

## Deploy halves of the "CI here" scenarios

The CI test asserts the interface/intent boundary; the step below asserts the
real host effect. Cross-reference `tests/golden_paths/__init__.py`.

### #5 — Model pull, slot assignment, and real inference
- ☐ Pull a served model, assign a slot, run a real completion (GPU).
- Assert: container Up + health, GPU inference returns non-empty `content`
  (watch the reasoning-channel/no-think default — MiniCPM5/saber land answers
  in `reasoning_content` unless `enable_thinking=false`), clean teardown.

### #9 — Slot rename (live)
- ☐ Rename a running slot on the host.
- Assert: live unit `hal0-slot@<name>` → `@<id>` rename + `podman rename`,
  port claim stable, no broken references, M5 id-keying migrator idempotent
  on real state. (CI covers the offline relabel only.)

### #10 — Slot delete (live teardown)
- ☐ Delete a running slot.
- Assert: `systemctl stop/disable hal0-slot@<name>`, `podman rm`, Quadlet
  unit-file removal, port claim released, state/config erased on host.

### #14 — API restart without stopping slots
- ☐ Restart `hal0-api` while slots run.
- Assert: slot systemd units survive (independent lifetimes), reconcile issues
  NO stop/start, running containers keep serving across the bounce.

### #15 — Core operation with Hermes disabled or removed
- ☐ Provision a box WITHOUT `hal0 agent install hermes`.
- Assert: core routes serve, board runs with `hermes_kanban=None`, brain chat
  works, no hermes venv/gateway present, uninstall leaves no `HERMES_HOME`.

---

## Sign-off
Record per box (150 / 143): scenario #, ✔/⚠, commit under test, date, notes.
A ⚠ becomes an O-row on `REWORK_BOARD.md` under the halo deploy-validation
section, fixed forward on `rework/descar`.
