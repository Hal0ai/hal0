# P3-perms: declarative OwnershipStore as the single ownership authority

**Repo:** `/home/mint/hal0` @ `rework/descar` · **Scope:** adoption + dead-code removal from §7.2 + §23.3 + §17 of the rework plan · **Mode:** READ-ONLY spec, verified against code.

## 0. Executive summary

hal0's filesystem ownership is set by ~15 scattered `chown`/`chmod`/`install -o` calls across `installer/install.sh` (most concentrated at `install.sh:1620-1692`), two recurring helpers in `hermes_provision.py` (`_chown_tree_to_hal0` `:867`, `_phase_ownership_reconcile` `:4847`), and the `UMask=0002` kludge on `hal0-api.service` (`install.sh:936`). The declarative `OwnershipStore` (`src/hal0/install/perms.py`, 440 lines) exists and is exercised in `doctor perms` but **ships inert** — its default `service_user="root"` reproduces the current on-disk values, so the `plan`/`commit`/`drift` machinery is a pure no-op for existing installs (`perms.py:17-20`). The fix is **the born-owned contract**: drop privileges to `hal0` **before** every config-writing step so files land `hal0:hal0` from the first write, which makes `_chown_tree_to_hal0`, `_phase_ownership_reconcile`, and `UMask=0002` dead code, and lets `hal0-api` drop to `User=hal0` without the `hal0-slotctl` privilege seam. One narrow privileged helper (`hal0-systemctl`, written to the existing `packaging/sudoers/` template) covers the only genuinely-root ops the API still needs (write `/etc/systemd/system/hal0-slot@*.service`, `daemon-reload`, iptables). `OwnershipStore` becomes the single declarative truth, defaults to `service_user="hal0"`, and `doctor perms` becomes audit-only. **Hard prereq for P3 §7.4 (hermes-provision slim) and P3-quadlet** — the installer cannot delete its chown phases until this lands.

## 1. Verification note

All line refs verified against `/home/mint/hal0` on branch `rework/descar` (`git rev-parse --abbrev-ref HEAD` in that working tree = `rework/descar`). The current code still has `User=root` + `UMask=0002` for `hal0-api` (`install.sh:932,936`) and the late always-run `ownership_reconcile` phase (`hermes_provision.py:5058`) — these are the load-bearing scars this spec removes.

---

## PART 0 — EXACT MAP (file:line)

### `src/hal0/install/perms.py` (440 lines) — the declarative truth, ships inert
- `PermRow` `perms.py:60-80` — one path's declared owner/group/mode/glob/child_mode/optional/role.
- `ownership_table(*, service_user="root", service_group="hal0")` `perms.py:83-189` — THE single source of truth. Default `service_user="root"` (`:85,124`) → flipped=False (`:124`) → every row is byte-identical to current on-disk values (`:17-20` docstring). Rows:
  - `/etc/hal0` 0755/2775 root/hal0 `PermRow(etc, etc_owner, etc_group, etc_dir_mode, optional=False, role="/etc/hal0 (config root)")` `:142-144`
  - `/etc/hal0/hal0.toml` 0600 (`:145`)
  - `/etc/hal0/profiles.toml` 0600 (`:146`)
  - `/etc/hal0/api.env` 0644 (`:149`) — FIXME(phase4) wart kept
  - `/etc/hal0/capabilities.toml` 0600 (`:150`)
  - `/etc/hal0/upstreams.toml` 0644 (`:151`)
  - `/etc/hal0/hardware.json` 0644 (`:152`)
  - `/etc/hal0/openwebui.env` 0600 (`:153`)
  - `/etc/hal0/slots/` 0755/2775 root/hal0, glob `*.toml`, child_mode=0o600 (`:154-163`)
  - `/etc/hal0/agents/` 0755 **root:root** (`:166`) — pinned, never flips, per #843
  - `/var/lib/hal0` 2775 root/hal0 `state_owner=hal0` `:167-175`
  - `/var/lib/hal0/.hermes` 0700 hal0:hal0 (HERMES_HOME) `:176-182`
  - `/var/lib/hal0/secrets` 0755 **root:root** (`:186`) — pinned; systemd reads EnvironmentFile here as root
  - `/var/log/hal0` 0755 hal0:hal0 (`:188`)
- `PermObservation` `perms.py:194-206`, `observe()` `:224-236`, `_owner_name()` `:210-214`, `_group_name()` `:217-221`.
- `PermDiff` `perms.py:242-266` (changed logic `:259-266`), `OwnershipPlan` `:269-285` (`drifted` `:284-285`), `_expand_row` `:288-310`, `plan()` `:313-338`.
- `commit()` `perms.py:360-388` — applies with rollback (mirrors `SlotConfigStore.commit`). **Never called in production today**: only entry path would be `doctor perms --fix` which is itself a root-gated no-op (`:374` docstring).
- `audit_rows()` `perms.py:394-426` — feeds `doctor perms` (`:392` docstring; references `cli.doctor_commands.check_hermes_ownership` `:398`).
- `__all__` `:429-439`.

### `installer/install.sh` — imperative chown scatter (the scars)
- `install.sh:932` `User=root` for `hal0-api.service` — **flips to `User=hal0`** in this spec.
- `install.sh:936` `UMask=0002` for `hal0-api.service` — **deleted** in this spec (was the kludge forcing group-writable files because the API was running as `root` with default umask).
- `install.sh:958-964` — explicit cleanup of the old `hal0-api.service.d/20-run-as-hal0.conf` drop-in (the legacy "hardened mode" attempt). **Replaced** by the new `User=hal0` flip.
- `install.sh:1047-1054` — explicit cleanup of the old `hal0-slotctl` helper + sudoers (legacy seam removed).
- `install.sh:1056-1088` — `hal0-agentenv` seam (write `/etc/hal0/agents/<id>.env` + `/var/lib/hal0/secrets/agents/<id>.env` only). Survives; the spec **keeps** it as a seam, because secrets/ stays root:root and must be writable by the API.
- `install.sh:1090-1125` — `hal0-benchctl` seam (GPU benchmarking under rootful podman). Survives.
- `install.sh:1620-1658` — the chown block. `FLM_CACHE_DIR` `chown 1000:hal0 / chown hal0:hal0 + chmod 2775` `:1626-1627`; `.cache` `chown -R hal0:hal0` `:1639`; `STATE.md` `chgrp hal0 / chmod 2775 / touch / chown hal0:hal0` `:1655-1658`. All redundant under the born-owned contract — these directories land hal0:hal0 from creation; `OwnershipStore.ownership_table` becomes the single fix point.
- `install.sh:1669` `mkdir -p "${ETC_DIR}/agents" "${VAR_DIR}/secrets/agents"` — root-owned, intentional (per `perms.py:166,186`). Survives.
- `install.sh:1692` `chown -R hal0:hal0 "${VAR_DIR}/models/collections"` — redundant under born-owned; survives in `OwnershipStore` rows.

### `src/hal0/agents/hermes_provision.py` — the dead-code targets
- `_chown_tree_to_hal0` `hermes_provision.py:867-907` — recursive `lchown` helper. Uses `os.lchown` (not `chown`) so symlinks chown the link, never the target (`:882-887`). No-ops when not root, hal0 user absent, or path missing (`:889-891`). Called at:
  - `hermes_provision.py:1088` — inside the venv install phase (turns the root-owned `uv venv` over to hal0).
  - `hermes_provision.py:1292` — inside `_phase_home_init` (re-chowns `hermes_home` after claim).
  - `hermes_provision.py:4814` — inside `_phase_runtime_install` (`runtime.json`).
- `_phase_ownership_reconcile` `hermes_provision.py:4847-4880` — re-chowns `HERMES_HOME` to hal0 + repairs 0711 on `/var/lib/hal0/agents` (`:4861-4870`). Comment block `:4831-4845` documents the root cause: `home_init` chowns first (phase 4) but `config_write` (phase 7) writes root files after, so config.yaml lands root:root and the `User=hal0` unit can't read it (`:4833-4837`). **The phase exists entirely because the installer is running as root writing config files into a hal0-owned tree.**
- `Phase("ownership_reconcile", _phase_ownership_reconcile, always_run=True)` `hermes_provision.py:5058` — listed late in `PHASES` after `voice_wire`, before `gateway_secrets_wire` / `smoke_tests` / `self_report`. Marked `always_run=True` so re-runs always reconcile. **Removed** in this spec.

### `installer/systemd/` — what already runs as hal0 (no change)
| Unit | User= | Group= | File |
|---|---|---|---|
| `hal0-agent@.service` | `hal0` | `hal0` | `installer/systemd/hal0-agent@.service:31-32` |
| `hal0-bench.service` | `hal0` | `hal0` | `installer/systemd/hal0-bench.service:25-26` |
| `hal0-bench-worker.service` | `hal0` | `hal0` | `installer/systemd/hal0-bench-worker.service:20-21` |
| `hindsight-api.service` | `hal0` | `hal0` | `installer/systemd/hindsight-api.service:11-12` |
| **`hal0-api.service`** | **`root`** | (root) | `install.sh:932` (inline write) — **flips to `hal0`** |

`hal0-openwebui.service` runs inside its own container — UMask is the container's, not the host's (`packaging/systemd/hal0-openwebui.service`, no User=).

### `packaging/sudoers/` — the seam template pattern
Three drop-ins exist; two are load-bearing for `hal0-api` post-flip:
- `packaging/sudoers/hal0-agentenv` — `hal0 ALL=(root) NOPASSWD: /usr/lib/hal0/bin/hal0-agentenv` (write agent `.env` files into root-owned `/var/lib/hal0/secrets/agents/` + `/etc/hal0/agents/`).
- `packaging/sudoers/hal0-benchctl` — `hal0 ALL=(root) NOPASSWD: /usr/lib/hal0/bin/hal0-benchctl` (GPU benchmark run/aggregate under rootful podman).
- `hal0-slotctl` — removed (deleted in install.sh `:1049-1053`; reinstalling would need cleanup).

**This spec adds `packaging/sudoers/hal0-systemctl`** — a new narrow grant for `hal0` → `root` invocation of exactly three commands: `daemon-reload`, `start <hal0-slot@*.service>`, `stop <hal0-slot@*.service>`, plus an `--iptables-*` action restricted to the FORWARD-chain FORWARD ACCEPT pattern from `packaging/systemd/hal0-podman-forward.service`. Modeled exactly on the existing `hal0-agentenv` / `hal0-benchctl` template (validate-args, no shell, no wildcards, no arbitrary file writes).

### Plan anchors
- `/home/mint/hal0-rework-plan.md:461-474` (§7.2 "Container runtime & permissions") — the load-bearing diagnosis: "root-vs-hal0 bug is structural: provisioner runs as root writing root:root files that the User=hal0 runtime must own → a fix-clobber-refix cycle (home_init chown → mid-phases write root files → ownership_reconcile re-chowns)". Same block declares the ownership map the spec encodes.
- `/home/mint/hal0-rework-plan.md:513-515` (§7.4) — "Hard prereq: P3-perms lands FIRST — the installer can't delete the chown phases until it does."
- §17 — sequencing/dependencies (this spec is the gate).
- §23.3 — the **born-owned contract** ("drop privileges to hal0 BEFORE any config-writing step so files are born hal0:hal0").

---

## PART A — Current-state map (the scars, in plain English)

### A.1 Why `hal0-api` runs as `root` today

`hal0-api` writes `/etc/systemd/system/hal0-slot@*.service` (per-slot podman units), runs `systemctl daemon-reload`, calls `iptables` to repair the FORWARD chain (`packaging/systemd/hal0-podman-forward.service`), applies self-updates (writes `/opt/hal0/`, swaps the symlink, restarts itself), and writes `/etc/hal0/*.toml` + `/etc/hal0/*.env`. None of those work as the unprivileged `hal0` user. The hard-perms flip was attempted once (the old `20-run-as-hal0.conf` drop-in), then reverted; the revert added the `UMask=0002` kludge (`install.sh:933-936`) to keep group-writable files writable to the hal0 group, so a root daemon's writes survive a hal0-group reader. This is the entire reason the API is still `User=root`.

### A.2 The imperative chown scatter (15+ sites)

| Site | What | Effect |
|---|---|---|
| `install.sh:1626` | `chown 1000:hal0` FLM_CACHE_DIR, `chmod 2775` | container-uid writes land hal0-group |
| `install.sh:1639` | `chown -R hal0:hal0 ${VAR_DIR}/.cache` | hal0 user HF downloads work |
| `install.sh:1655-1658` | `chgrp hal0 ${VAR_DIR}; chmod 2775; touch ${VAR_DIR}/STATE.md; chown hal0:hal0 ${VAR_DIR}/STATE.md` | STATE.md setgid dir + hal0-owned file so render-context (which runs as hal0) can rename-over |
| `install.sh:1669` | `mkdir -p .../agents .../secrets/agents` | leaves them root:root (correct, intentional) |
| `install.sh:1692` | `chown -R hal0:hal0 ${VAR_DIR}/models/collections` | bundle picker manifests readable by hal0-api (which runs as root — so this is a no-op today) |
| `install.sh:1123-1124` | `chown -R hal0:hal0 ${VAR_DIR}/benchmarks; chmod 2775 ...` | bench artifacts readable by hal0-agent + hal0-benchctl |
| `hermes_provision.py:1088` | `_chown_tree_to_hal0(venv)` | root-created `uv venv` → hal0 |
| `hermes_provision.py:1292` | `_chown_tree_to_hal0(hermes_home)` inside `home_init` | initial home claim |
| `hermes_provision.py:4814` | `_chown_tree_to_hal0(runtime_path)` inside `_phase_runtime_install` | runtime.json hal0-owned |
| `hermes_provision.py:4847` | `_phase_ownership_reconcile` (always_run late phase) | **fixes the order-of-operations bug** below |
| `install.sh:936` | `UMask=0002` on `hal0-api` | root daemon → group-writable files (the kludge) |

### A.3 The fix-clobber-refix cycle (the structural scar)

The phases `preflight → install → env_probe → home_init → install_artifacts → persona_seed → config_write → mcp_wire → context_link → namespace_register → brain_profile_seed → brain_profile_mcp_wire → model_automap → voice_wire` (`hermes_provision.py:5020-5056`) each **write into a hal0-owned tree** (`/var/lib/hal0/.hermes` and `/etc/hal0/`). The installer runs as `root` (`hal0-api` is the bootstrap entry — `install.sh:1738 enable --now hal0-api`; the per-agent provisioner is invoked by the API). Every write therefore lands `root:root` until `_phase_ownership_reconcile` (`:4847`, late, always_run) re-chowns the whole home tree + repairs the 0711 on `/var/lib/hal0/agents` (`:4861-4870`). The phase is marked `always_run=True` (`:5058`) precisely because re-runs need to reconcile drift independently of checkpoint state. **The phase exists to undo damage the earlier phases cause.** That's the entire story; once writes are born-owned the phase has nothing to reconcile and dies.

### A.4 What the docstring of `OwnershipStore` says, vs what ships

`perms.py:22-28` (the "HARDENED FLIP" paragraph) documents the data-only flip from `service_user="root"` → `service_user="hal0"`. `perms.py:102-105` then notes: "the hardened 'unprivileged service_user' install mode and its `hal0-slotctl` privilege seam were removed — hal0-api runs as root. This table is now exercised only with `service_user="root"` (by `hal0 doctor`); the non-root branches are retained for reference but no longer wired in." This is the codebase narrating a past-sunset decision. **This spec re-wires it.**

---

## PART B — Target: OwnershipStore default `service_user="hal0"`

### B.1 The ownership map (encodes §7.2)

| Path | Owner:Group | Mode | Role | File under which it lives |
|---|---|---|---|---|
| `/usr/lib/hal0` | `root:root` | 0755 | shipped binaries/helpers | **NEW row** (read-only root) |
| `/etc/hal0` | `hal0:hal0` | 2775 (setgid) | config root, daemon-rewritable | `perms.py:142-144` |
| `/etc/hal0/hal0.toml` | `hal0:hal0` | 0600 | runtime config | `perms.py:145` |
| `/etc/hal0/profiles.toml` | `hal0:hal0` | 0600 | runtime config | `perms.py:146` |
| `/etc/hal0/api.env` | `hal0:hal0` | 0644 | bind/env (FIXME wart, not changed by flip) | `perms.py:149` |
| `/etc/hal0/capabilities.toml` | `hal0:hal0` | 0600 | runtime config | `perms.py:150` |
| `/etc/hal0/upstreams.toml` | `hal0:hal0` | 0644 | upstream registry | `perms.py:151` |
| `/etc/hal0/hardware.json` | `hal0:hal0` | 0644 | hardware probe facts | `perms.py:152` |
| `/etc/hal0/openwebui.env` | `hal0:hal0` | 0600 | companion env | `perms.py:153` |
| `/etc/hal0/slots/` | `hal0:hal0` | 2775 (setgid) | slot TOMLs | `perms.py:154-163` |
| `/etc/hal0/slots/*.toml` | `hal0:hal0` | 0600 | slot configs (glob, child_mode) | `perms.py:160` |
| `/etc/hal0/agents/` | **`root:root`** | 0755 | Hermes allow-list world (#843, never flips) | `perms.py:166` |
| `/etc/hal0/agents/<id>.env` | **`root:root`** | 0644 | per-agent driver env (root-owned via `hal0-agentenv` seam) | **NEW row (explicit)** |
| `/var/lib/hal0` | `hal0:hal0` | 2775 (setgid) | state root | `perms.py:167-175` |
| `/var/lib/hal0/.hermes` (HERMES_HOME) | `hal0:hal0` | 0700 | hermes home, daemon-readable only | `perms.py:176-182` |
| `/var/lib/hal0/secrets/` | **`root:root`** | 0755 | secrets vault | `perms.py:186` |
| `/var/lib/hal0/secrets/agents/<id>.env` | **`root:root`** | 0600 | per-agent secrets (root-owned via `hal0-agentenv` seam) | **NEW row (explicit, 0600)** |
| `/var/lib/hal0/agents/` | `hal0:hal0` | **0711** | per-agent sub-homes, sibling-traversal denied | **NEW row (was repaired by `_phase_ownership_reconcile` `:4861-4870`)** |
| `/var/lib/hal0/agents/<id>/` | `hal0:hal0` | 0700 | per-agent home | implicit (glob or parent 0711 + child 0700) |
| `/var/lib/hal0/skills/` | `hal0:hal0` | 2775 | drop-in skills | **NEW row** |
| `/var/log/hal0` | `hal0:hal0` | 0755 | logs | `perms.py:188` |
| `/var/lib/hal0/benchmarks/{,runs,logs,server-ab}/` | `hal0:hal0` | 2775 | bench artifacts | **NEW rows** (mirroring `install.sh:1123-1124`) |

Two subtrees stay root:root by design (already documented in `perms.py:108-112,184-186`):
- `agents/` — Hermes allow-list, API reads only (#843).
- `secrets/` — systemd reads EnvironmentFile here as root before dropping to service user; must not be service-writable.

### B.2 The default flip — `service_user="hal0"`

Change `ownership_table()` default from `service_user="root"` to `service_user="hal0"` (`perms.py:85`). With the flip:
- `flipped = True` (`:124`) → every `/etc/hal0/*` row becomes `hal0:hal0` 2775/0600/0644 (`:128-131,142-163`).
- `state_owner = "hal0"` always (`:134`) — matches current on-disk state.
- `agents/` and `secrets/` rows stay root:root (`:166,186`).

**Effect on existing installs:** the `plan()` produced from the table is **not a no-op** anymore — `/etc/hal0` and its files need to chown root→hal0 on the next `doctor perms --fix`. That's the intended migration. `doctor perms` becomes the audit that surfaces this drift and `--fix` applies it (already implemented in `perms.py:360-388`). The one-shot installer migration is automatic on `hal0 doctor perms --fix` post-upgrade.

### B.3 What changes in `OwnershipStore` itself (mechanical)

1. `perms.py:85` default `service_user="root"` → `"hal0"`.
2. Update docstring `perms.py:17-28` ("THE HARDENED FLIP" paragraph) to reflect the **new default**, not a future opt-in. The `service_user="root"` arg path is retained for emergency (`hal0 doctor perms --table-root` or similar) but the `OwnershipStore.commit` is now called as part of `hal0 doctor perms --fix` on every upgrade, not just hypothetical.
3. Add three rows to `ownership_table()`:
   - `PermRow(paths.lib(), "root", "root", 0o755, role="/usr/lib/hal0 (shipped, read-only)")` (where `paths.lib()` returns `/usr/lib/hal0`; add to `config/paths.py` if absent).
   - `PermRow(var_lib / "agents", "hal0", "hal0", 0o711, role="agents/ (per-agent sub-homes)")`.
   - `PermRow(etc / "agents" / "<id>.env", "root", "root", 0o644, ...)` — encoded as the dir row at `perms.py:166`; the per-id files inherit. (No per-id row needed if the dir is correctly root:root 0755.)
   - `PermRow(var_lib / "secrets" / "agents", "root", "root", 0o755, role="secrets/agents/ (per-agent secrets vault)")` to lock the secrets subdir explicitly.
   - `PermRow(var_lib / "secrets" / "agents" / "<id>.env", "root", "root", 0o600, role="secrets/agents/<id>.env (per-agent secrets)")` as a **glob** under the dir.
   - `PermRow(var_lib / "benchmarks", "hal0", "hal0", 0o2775, glob="*", child_mode=0o755, role="benchmarks/ (+ subdirs)")` covering `runs/`, `logs/`, `server-ab/`.
   - `PermRow(var_lib / "skills", "hal0", "hal0", 0o2775, role="skills/ (drop-in agent skills)")` (mirrors `install.sh:1717` skill drop-in).
4. **No change** to `plan`/`commit`/`audit_rows` — the data-only change is what makes them meaningful for the first time.

### B.4 What dies in `install.sh` (the imperative chowns)

After the flip:
- `install.sh:1620-1658` (FLM_CACHE, .cache, STATE.md) — **DELETED**. All four sites become `mkdir -p` only; ownership is born hal0:hal0 because the **process** is hal0 by the time these run (see PART C).
- `install.sh:1669` — survives (mkdir agents + secrets, intentional root:root).
- `install.sh:1692` — `chown -R hal0:hal0 ${VAR_DIR}/models/collections` — **DELETED**. Bundle manifests are cp'd as hal0; `chmod -R u+rwX,g+rX` is sufficient (or just nothing — files born correctly).
- `install.sh:1123-1124` — `chown -R hal0:hal0 ${VAR_DIR}/benchmarks; chmod 2775 ...` — **DELETED**. The new `PermRow(var_lib / "benchmarks", ...)` is applied by `doctor perms --fix` on first boot.
- `install.sh:932` — `User=root` → **`User=hal0`**.
- `install.sh:933-936` — `UMask=0002` line **DELETED**. The default 0022 is correct now (hal0-owned files are born hal0-group-readable via the 2775 dirs).
- `install.sh:958-964` — the `20-run-as-hal0.conf` removal block — **DELETED**. The unit flips directly; no drop-in needed.

### B.5 What dies in `hermes_provision.py` (the dead-code targets)

| Dead code | Lines | Reason |
|---|---|---|
| `_chown_tree_to_hal0` | `:867-907` | No more root-written hal0 trees. The helper no-ops when `geteuid() != 0` (`:893`) — every phase function now runs as hal0 → never enters the body. |
| `_chown_tree_to_hal0(venv)` call | `:1088` | venv install runs as hal0 (uv is invoked via `run-as-hal0`); venv lands hal0:hal0. |
| `_chown_tree_to_hal0(hermes_home)` call in `home_init` | `:1292` | Home init runs as hal0. |
| `_chown_tree_to_hal0(runtime_path)` call | `:4814` | Runtime install runs as hal0. |
| `_phase_ownership_reconcile` | `:4847-4880` | Nothing to reconcile. The 0711 repair on `/var/lib/hal0/agents` is now declarative in `OwnershipStore`. |
| `Phase("ownership_reconcile", ..., always_run=True)` entry | `:5058` | Deleted; re-number subsequent phases. |

After deletion, `hermes_provision.py` sheds **~110 lines** plus one phase entry.

### B.6 What becomes the runtime contract

- **`hal0-api` runs as `User=hal0`** (`install.sh:932`). Writes to `/etc/hal0/*` and `/var/lib/hal0/*` succeed because those trees are now hal0-owned and setgid. Reads from `/etc/hal0/agents/` (root-owned) succeed via `g+r` (default 0755).
- **Daemon-rewrites of `slots/*.toml`** continue to use temp-file + rename (`perms.py:96-100` docstring; `SlotConfigStore.write_slot_toml`), now possible because `slots/` is `hal0:hal0` 2775.
- **Privilege escalations go through one helper:** `hal0-systemctl` (see PART D) for `daemon-reload` + `start/stop hal0-slot@*` + `iptables` patch. Existing `hal0-agentenv` + `hal0-benchctl` seams continue as today (unchanged ownership story).
- **`UMask` is default 0022.** Group-writable files are no longer needed because the daemon is already in the hal0 group and the dirs are setgid 2775.

---

## PART C — The LOAD-BEARING contract: born-owned

### C.1 The contract

> "Drop privileges to `hal0` **before** any config-writing step, so files land `hal0:hal0` from the first write."

This is **§23.3 of the rework plan** and the §7.2 principle. The implementation is straightforward because `installer/lib/run-as-hal0.sh` already exists — the installer shells into it for all post-system-user-creation work.

### C.2 The mechanism: re-exec into the hal0 user

After the `hal0` system user is created (`install.sh` useradd block — already runs as root), every subsequent config-writing operation in the installer must run inside `installer/lib/run-as-hal0.sh` (or equivalent `setpriv`/`runuser`/`sudo -H -u hal0`):

```bash
# install.sh (post useradd)
exec sudo -H -u hal0 -- env -u HERMES_HOME "${REPO_ROOT}/installer/install.sh" "$@" --hal0-user-mode
```

**or**, for the inline-api-shells-itself path (post-install API-driven operations), the Python runtime uses `os.setuid()` / `os.setgid()` after the initial privilege-scoped setup completes (privilege drop is a one-way transition; `setuid(0)` after drop is blocked by `PR_SET_NO_NEW_PRIVS`).

Three concrete options for the daemon-level drop:

| Option | Pros | Cons | Recommendation |
|---|---|---|---|
| **A.** `setuid(hal0_uid)` after dropping supplementary groups via `setgroups([])` + `setresgid()` | Same-process; FastAPI keeps its imports + state. One-way by design. | Requires that the post-drop phase never needs root; must move all privileged IO into a helper. | **RECOMMENDED.** One process, one transition. |
| **B.** Re-exec the daemon as hal0 (fork+exec self with `sudo -u hal0`). | Trivial — leverage the installer seam. | All in-process state lost; uvicorn restart-style. Awkward with FastAPI lifespan. | Reject — adds a fork bomb for no benefit. |
| **C.** Run as hal0 from the start (unit `User=hal0`) and never go through root. | Cleanest; matches the final state. | First install needs root (mkdir, useradd, sudoers drop-ins). | **Use this for the post-install steady state**; option A covers the API process at first-boot time. |

**The unit already provides option C for the post-install case**: `hal0-api.service` ships with `User=hal0`. The **install-time** case uses `run-as-hal0.sh` (option A semantics). Both converge on the same outcome: every file written under `/etc/hal0/` and `/var/lib/hal0/` is born `hal0:hal0`.

### C.3 Where the drop happens

| Phase | Runs as | Writes to | Notes |
|---|---|---|---|
| Pre-useradd (system setup) | `root` (installer) | `/usr/lib/hal0`, `/etc/sudoers.d/`, `/etc/systemd/system/hal0-api.service` | Root-required, atomic, once-only. |
| Post-useradd (config seed) | `hal0` (via `run-as-hal0.sh`) | `/etc/hal0/`, `/var/lib/hal0/` (config dir + state root) | **The born-owned boundary.** |
| Hermes provision | `hal0` (per-agent unit already `User=hal0` — `installer/systemd/hal0-agent@.service:31`) | `/etc/hal0/agents/<id>.env` (via `hal0-agentenv` seam), `/var/lib/hal0/.hermes` (direct), `/var/lib/hal0/secrets/agents/<id>.env` (via seam) | Already in this regime once the unit starts as hal0. |
| Slot runtime (podman) | rootful container | sandbox only | Unchanged. |
| First-boot API restart | `hal0` (unit `User=hal0`) | `/etc/hal0/`, `/var/lib/hal0/` | Now permitted; tree is hal0-owned. |

**The pivotal line:** `install.sh`'s config-seed block (the part that writes `/etc/hal0/*.toml`, `/etc/hal0/*.env`, `/var/lib/hal0/.first_run_done`, `/var/lib/hal0/skills/`, `/var/lib/hal0/agents/`, `/var/lib/hal0/secrets/agents/`) — moves from running as root to running inside `run-as-hal0.sh`. **Every file lands `hal0:hal0` from the first write.** The late `chown` block (`install.sh:1620-1692`) goes away.

### C.4 Why this collapses the `_phase_ownership_reconcile` scar

`_phase_ownership_reconcile` (`hermes_provision.py:4847-4880`) exists because:
1. `home_init` (phase 4) chowns `$HERMES_HOME` to hal0 (`hermes_provision.py:1292`).
2. `config_write` (phase 7) writes `config.yaml` as root → it lands `root:root`.
3. `mcp_wire`, `context_link`, `install_artifacts`, `persona_seed`, `brain_profile_seed`, `brain_profile_mcp_wire` (phases 5–13) all do the same.
4. The phase re-chowns the whole tree after every home-writing phase.

**With the born-owned contract:** phases 5–13 all run inside the `hal0-agent@hermes.service` unit (already `User=hal0` — `installer/systemd/hal0-agent@.service:31`). Their writes are already `hal0:hal0`. There is nothing to reconcile. The phase is deleted.

### C.5 Why this collapses `_chown_tree_to_hal0`

The function is called three times (`:1088, 1292, 4814`). All three are pre-empted by born-owned execution:
- `:1088` — venv install phase. If venv install runs as hal0 (via `run-as-hal0.sh` wrapping `uv venv`), the venv is born hal0-owned.
- `:1292` — `home_init` runs as hal0 (the `hal0-agent@hermes.service` is `User=hal0`). `claim_hermes_home` creates the home; ownership is hal0.
- `:4814` — runtime install runs as hal0. `runtime.json` lands hal0-owned.

All three call sites are deleted; the helper itself is deleted; the `geteuid() != 0` no-op guard at `:893` becomes structurally unreachable.

### C.6 The `hal0-agentenv` seam stays

`hal0-agentenv` writes `/etc/hal0/agents/<id>.env` (0644) and `/var/lib/hal0/secrets/agents/<id>.env` (0600) — both intentionally root-owned. The API (running as hal0) cannot write these files, so it delegates via `sudo -n /usr/lib/hal0/bin/hal0-agentenv` to a helper that validates the agent id, builds the path, and only writes those two fixed paths. This seam is the correct pattern and survives unchanged. It is **not** a workaround for a perms bug — it is the right place for a narrow privilege escalation that the declarative table explicitly allows (the `agents/` and `secrets/` rows stay root:root by design — `perms.py:166, 186`).

---

## PART D — The one narrow privileged helper: `hal0-systemctl`

### D.1 Scope (exactly three op families)

`hal0-api` running as `hal0` needs to perform these root-only operations:

1. **Write `/etc/systemd/system/hal0-slot@<id>.service`** (per-slot podman unit).
2. **`systemctl daemon-reload`** after writing new units.
3. **`systemctl start/stop/restart hal0-slot@<id>.service`** to launch/teardown slot containers.
4. **`iptables -I FORWARD 1 -j ACCEPT`** (and the symmetric `-D`) to repair the FORWARD chain when docker co-exists with podman (`packaging/systemd/hal0-podman-forward.service:36`).

### D.2 The helper

**New file:** `installer/wrappers/hal0-systemctl` (modeled exactly on `installer/wrappers/hal0-agentenv` and `installer/wrappers/hal0-benchctl`).

Pseudocode (bash, matches the existing wrapper style):

```bash
#!/usr/bin/env bash
# hal0-systemctl — narrow privileged seam for hal0-api (User=hal0).
# Validates every argument; never invokes a shell; never accepts wildcards.
#
# Subcommands (whitelisted):
#   write-unit <slot-id>            # read unit body from stdin; write /etc/systemd/system/hal0-slot@<id>.service
#   daemon-reload                   # systemctl daemon-reload
#   start   <slot-id>               # systemctl start hal0-slot@<id>.service
#   stop    <slot-id>               # systemctl stop  hal0-slot@<id>.service
#   restart <slot-id>               # systemctl restart hal0-slot@<id>.service
#   iptables-accept-forward         # iptables -I FORWARD 1 -j ACCEPT
#   iptables-drop-forward           # iptables -D FORWARD -j ACCEPT  (idempotent: swallow "not found")
#
# slot-id must match ^[a-zA-Z0-9_-]+$ and be ≤64 chars.

set -euo pipefail
sub="${1:-}"; shift || true
case "$sub" in
  write-unit)
    id="${1:-}"
    [[ "$id" =~ ^[a-zA-Z0-9_-]{1,64}$ ]] || { echo "bad slot id" >&2; exit 64; }
    path="/etc/systemd/system/hal0-slot@${id}.service"
    install -m 0644 -o root -g root /dev/stdin "$path"  # root:root by design
    ;;
  daemon-reload) exec systemctl daemon-reload ;;
  start|stop|restart)
    id="${1:-}"
    [[ "$id" =~ ^[a-zA-Z0-9_-]{1,64}$ ]] || { echo "bad slot id" >&2; exit 64; }
    exec systemctl "$sub" "hal0-slot@${id}.service"
    ;;
  iptables-accept-forward)
    iptables -I FORWARD 1 -j ACCEPT
    ;;
  iptables-drop-forward)
    iptables -D FORWARD -j ACCEPT 2>/dev/null || true
    ;;
  *) echo "unknown subcommand: $sub" >&2; exit 64 ;;
esac
```

**New file:** `packaging/sudoers/hal0-systemctl`:
```
# hal0 — privileged seam grant for hal0-api (User=hal0) under the born-owned model.
# Covers: per-slot unit writes, daemon-reload, start/stop/restart of slot units,
# and the FORWARD-chain iptables patch (hal0-podman-forward). The helper validates
# every arg; no shell, no wildcards, no arbitrary file writes.
#
# Install (as root):
#   install -m 0440 packaging/sudoers/hal0-systemctl /etc/sudoers.d/hal0-systemctl
#   visudo -cf /etc/sudoers.d/hal0-systemctl
hal0 ALL=(root) NOPASSWD: /usr/lib/hal0/bin/hal0-systemctl
```

### D.3 What does NOT need a helper

- Writing `/etc/hal0/slots/<id>.toml` — the dir is hal0-owned; daemon writes directly.
- Writing `/etc/hal0/hal0.toml`, `profiles.toml`, `api.env`, `openwebui.env` — daemon writes directly.
- Writing `/var/lib/hal0/slots/<id>/state.json` — daemon writes directly.
- Pulling models (`registry/pull.py`) — writes to `/mnt/ai-models` or `/var/lib/hal0/models`, both hal0-owned via the new PermRows.
- Restarting itself (`hal0-api`) on self-update — uses `systemctl restart hal0-api` … which DOES need a helper, but **only for the self-update path**. Either extend `hal0-systemctl` with `restart-self` (`systemctl restart hal0-api.service`, validated against the literal service name), or add a sibling `hal0-selfupdate` helper. **Recommendation:** extend `hal0-systemctl` with the literal-only `restart-self` + `stop-self` subcommands (hardcoded service name = `hal0-api.service`).

### D.4 API-side wiring

`hal0-api`'s slot/container code path (currently calls `systemctl` directly) switches to `subprocess.run(["sudo", "-n", "/usr/lib/hal0/bin/hal0-systemctl", ...], ...)`. Existing call sites:
- `slots/manager.py` — `systemctl daemon-reload` (search the file)
- `slots/manager.py` — `systemctl start/stop hal0-slot@<id>.service`
- `installer/etc-hal0/systemd/*` writers — write to `/etc/systemd/system/hal0-slot@<id>.service`
- `updater.py` — `systemctl restart hal0-api` (self-update path)

All five sites route through the new helper. The helper's existence is invisible to the call site logic; only the subprocess command changes.

---

## PART E — `hal0-api` User flip + UMask removal

### E.1 Unit changes (in `install.sh`)

Edit the inline unit write at `install.sh:922-955`:

```diff
 [Service]
 Type=simple
-User=root
-# Group-writable umask so files the API writes into a shared editable tree stay
-# editable by the hal0 group (Hermes & in-runtime agents) — part of the #843
-# root-clobber fix. Harmless on an immutable FHS install.
-UMask=0002
+User=hal0
+Group=hal0
+# hal0-api writes /etc/hal0/* + /var/lib/hal0/* directly (those trees are
+# hal0:hal0 2775 — see src/hal0/install/perms.py). Privileged IO (systemd unit
+# writes, daemon-reload, iptables) routes through `sudo -n /usr/lib/hal0/bin/
+# hal0-systemctl` — the single narrow seam. UMask is default 0022.
 WorkingDirectory=${API_WORKDIR}
```

Delete `install.sh:958-964` (the "Hardened mode removed" + drop-in removal block — no longer needed; the unit flips directly).

### E.2 Production restart sequence

1. New package installed; new unit file landed at `/etc/systemd/system/hal0-api.service` with `User=hal0`.
2. `OwnershipStore.commit` (via `hal0 doctor perms --fix`) runs as root ONCE post-upgrade, chowning `/etc/hal0/*` root→hal0. Reversible: any individual row can be reverted via `hal0 doctor perms --fix --revert`.
3. `systemctl daemon-reload && systemctl restart hal0-api`.
4. New `hal0-api` starts as hal0, finds everything it needs at the expected paths (because step 2 made them hal0-owned), serves traffic.
5. Subsequent `hal0 doctor perms` runs surface any drift but do not auto-fix (audit-only).

### E.3 What stays root-owned

- `/etc/hal0/agents/` — pin (`:166`).
- `/etc/hal0/agents/<id>.env` — pin (per-agent driver env, set by `hal0-agentenv`).
- `/var/lib/hal0/secrets/` — pin (`:186`).
- `/var/lib/hal0/secrets/agents/<id>.env` — pin (per-agent secrets, set by `hal0-agentenv`).
- `/etc/sudoers.d/hal0-*` — pin (sudo-managed).
- `/etc/systemd/system/hal0-slot@*.service` — root:root 0644, written by `hal0-systemctl` (root-only by construction).
- `/usr/lib/hal0/*` — root-owned shipped tree.

---

## PART F — Edit plan (files, order, delegators/shims)

Order is load-bearing: each step assumes the previous step's invariants hold.

### F.1 Order (8 PRs)

**PR F.1 — `OwnershipStore` adopts `hal0` as the default service user + new rows** *(PR #1, no behavior change)*
- Edit `perms.py:85` default: `service_user="root"` → `"hal0"`.
- Edit `perms.py:17-28, 102-105` docstrings to reflect the new default.
- Add rows to `ownership_table()`:
  - `PermRow(paths.lib(), "root", "root", 0o755, role="/usr/lib/hal0")`
  - `PermRow(var_lib / "agents", "hal0", "hal0", 0o711, role="agents/ (per-agent sub-homes)")`
  - `PermRow(var_lib / "secrets" / "agents", "root", "root", 0o755, role="secrets/agents/ (vault root)")`
  - `PermRow(var_lib / "secrets" / "agents", "root", "root", 0o600, glob="*.env", child_mode=0o600, role="secrets/agents/<id>.env")`
  - `PermRow(var_lib / "benchmarks", "hal0", "hal0", 0o2775, glob="*", child_mode=0o2775, role="benchmarks/ (+ subdirs)")`
  - `PermRow(var_lib / "skills", "hal0", "hal0", 0o2775, role="skills/ (drop-in skills)")`
- Add `paths.lib()` to `config/paths.py` if absent (return `/usr/lib/hal0`).
- Tests: `tests/install/test_perms.py` — flip `service_user="root"` fixture calls to `"hal0"`, assert new rows present, assert the existing on-disk layout matches the new table for an upgraded box.

**Behavior change:** none. `OwnershipStore` was inert; the new default makes its `plan()` produce drifts on existing boxes (the migration the next PR handles). `doctor perms` audits surface the drifts.

**PR F.2 — One-shot migration: `doctor perms --fix` runs `OwnershipStore.commit` on upgrade** *(PR #2, root-gated, runs at boot or via the upgrade script)*
- Wire `hal0 doctor perms --fix` to actually invoke `OwnershipStore.commit` (currently the docstring at `perms.py:374` says it does; the implementation needs to be reachable). The path: add a `doctor perms --fix` command that, as root, calls `OwnershipStore.plan()` then `commit()`.
- Guarded by `os.geteuid() == 0`; refuses otherwise.
- Idempotent: re-running on an already-migrated box is a no-op (everything matches the table).
- Reversible: `doctor perms --revert` walks the `before` snapshot in `PermDiff` and restores.
- One-shot auto-run on upgrade: add a `postinstall` hook (or first-boot systemd `oneshot`) that runs `doctor perms --fix` once if `/etc/hal0/hal0.toml.migrated-perms` is absent. Stamp the file on success.

**PR F.3 — `install.sh`: born-owned config seeding** *(PR #3, the pivotal PR)*
- Move the `/etc/hal0/` and `/var/lib/hal0/` seed writes (hal0.toml, profiles.toml, api.env, capabilities.toml, upstreams.toml, hardware.json, openwebui.env, slots/*.toml, agents/, secrets/agents/, .first_run_done, skills/) into a `run-as-hal0` subshell (use `installer/lib/run-as-hal0.sh`).
- Delete `install.sh:1620-1658` (FLM_CACHE_DIR, .cache, STATE.md chowns) — directories are created inside the hal0 subshell; born hal0:hal0 + 2775.
- Delete `install.sh:1692` (`chown -R hal0:hal0 models/collections`).
- Delete `install.sh:1123-1124` (`chown -R hal0:hal0 benchmarks; chmod 2775`).
- Delete `install.sh:1669` `mkdir -p "${ETC_DIR}/agents" "${VAR_DIR}/secrets/agents"` **inside the subshell** — keep the mkdir (still needed), drop the surrounding root context.
- The mkdir's for `.cache/huggingface/hub` + `STATE.md` survive inside the subshell (born hal0:hal0).

**PR F.4 — `install.sh`: `hal0-api` unit flips to `User=hal0`** *(PR #4, depends on F.2+F.3)*
- Edit `install.sh:932` `User=root` → `User=hal0`, `Group=hal0`.
- Delete `install.sh:933-936` (`UMask=0002` block).
- Delete `install.sh:958-964` (the legacy hardened-mode revert block — no longer needed).

**PR F.5 — New `hal0-systemctl` helper + sudoers** *(PR #5, depends on F.4 for the flip to make sense)*
- New file `installer/wrappers/hal0-systemctl` (script per PART D.2).
- New file `packaging/sudoers/hal0-systemctl` (sudoers drop-in per PART D.2).
- `install.sh` install block (modeled on `install.sh:1064-1088` for `hal0-agentenv`):
  - `install -m 0755 "${REPO_ROOT}/installer/wrappers/hal0-systemctl" "${LIB_DIR}/bin/hal0-systemctl"`
  - `install -m 0440 "${REPO_ROOT}/packaging/sudoers/hal0-systemctl" /etc/sudoers.d/hal0-systemctl` (after `visudo -cf` succeeds).
- Add cleanup to `installer/uninstall.sh` (modeled on `:428-435`):
  - `rm_path "/etc/sudoers.d/hal0-systemctl"`
  - `rm_path "${LIB_DIR}/bin/hal0-systemctl"`

**PR F.6 — `hal0-api` routes systemd/iptables writes through the helper** *(PR #6, depends on F.5)*
- Find every `subprocess.run([..., "systemctl", ...])` and `open(... "/etc/systemd/system/hal0-slot@..." ...)` in `slots/manager.py` + `updater.py` + `installer/etc-hal0/systemd/*`. Route them through `subprocess.run(["sudo", "-n", "/usr/lib/hal0/bin/hal0-systemctl", ...])`.
- New module `hal0/system/seam.py` (thin wrapper around `subprocess.run` for `hal0-systemctl`; same seam pattern as `hal0.slot_config`):
  - `class SystemCtlSeam: write_unit(slot_id, body) -> None; daemon_reload() -> None; start(slot_id) -> None; stop(slot_id) -> None; restart(slot_id) -> None; iptables_patch_forward(accept: bool) -> None; restart_self() -> None`
  - Validates `slot_id` matches `^[a-zA-Z0-9_-]{1,64}$` before invoking; raises `PermissionError` if sudo fails (the helper not installed = the seames is missing = misinstall).
  - Default-construction uses the real `subprocess.run`; tests inject a fake.

**PR F.7 — `hermes_provision.py`: delete `_chown_tree_to_hal0`, `_phase_ownership_reconcile`, the three call sites, the PHASES entry** *(PR #7, depends on F.3 — once install seeds are born-owned, the dead code is truly dead)*
- Delete `_chown_tree_to_hal0` (`hermes_provision.py:867-907`).
- Delete `_chown_tree_to_hal0(venv)` call (`:1088`).
- Delete `_chown_tree_to_hal0(hermes_home)` call in `_phase_home_init` (`:1292`).
- Delete `_chown_tree_to_hal0(runtime_path)` call in `_phase_runtime_install` (`:4814`).
- Delete `_phase_ownership_reconcile` (`:4847-4880`).
- Delete the `Phase("ownership_reconcile", _phase_ownership_reconcile, always_run=True)` entry (`:5058`).
- The `# Comment block` `:4826-4845` explaining the root cause of the reconcile phase goes too — its motivating bug no longer exists.
- Tests: `tests/hermes_provision/test_phases.py` (if exists) — drop assertions about ownership reconcile; add assertions that `OwnershipStore` declares the home 0700 hal0:hal0 (which is what makes the phase unnecessary).

**PR F.8 — `doctor perms` becomes audit-only** *(PR #8, depends on F.1+F.2; surfaces ongoing drift without fixing)*
- `hal0 doctor perms` (no flag): prints the `audit_rows` table; **does not fix**. Exits 0 on `ok`+`absent`, 1 on `drift`.
- `hal0 doctor perms --fix`: explicit root-gated commit (already exists; rewire to actually call `OwnershipStore.commit`).
- `hal0 doctor perms --table-root`: emergency — uses `service_user="root"` table (the old table) to verify the root-era layout matches if a rollback is needed.

### F.2 Delegators / shims that must survive

| Shim | Reason | Lives at |
|---|---|---|
| `hal0-agentenv` | `/etc/hal0/agents/<id>.env` + `/var/lib/hal0/secrets/agents/<id>.env` are root-owned by design; the API can't write them | `installer/wrappers/hal0-agentenv`, `packaging/sudoers/hal0-agentenv` (unchanged) |
| `hal0-benchctl` | GPU benchmark run/aggregate under rootful podman | `installer/wrappers/hal0-benchctl`, `packaging/sudoers/hal0-benchctl` (unchanged) |
| `hal0-systemctl` (NEW) | systemd unit writes + daemon-reload + iptables | per PART D |
| `run-as-hal0.sh` | installer-time privilege drop | `installer/lib/run-as-hal0.sh` (already exists; extended use) |

### F.3 What does NOT change

- `agents/hermes/...` provisioner structure beyond PART F.7's deletions — phases, ordering, checkpoint semantics all preserved.
- `slots/manager.py` behavior beyond the seamed-call refactor in PART F.6.
- `OwnershipStore` API (the `plan`/`commit`/`audit_rows` interface stays).
- Slot TOML write path (`SlotConfigStore.write_slot_toml` + `slot_write_lock`) — unchanged.
- Podman invocation, container security profile, slot sandboxing — unchanged.
- `hal0-agent@*.service` User=hal0 — unchanged.
- `hindsight-api.service` User=hal0 — unchanged.
- `hal0-bench.service` User=hal0 — unchanged.

---

## PART G — Sequencing: P3-perms BEFORE hermes-provision slim and P3-quadlet

### G.1 Hard dependency chain

```
F.1 OwnershipStore flip          (data-only, inert)
  ↓
F.2 doctor perms --fix wired     (migration on upgrade)
  ↓
F.3 install.sh born-owned seeding  (config writes hal0:hal0 from first write)
  ↓ ↓
F.4 hal0-api User=hal0 flip       (API no longer needs root for writes)
  │
  └→ F.5 hal0-systemctl helper    (the seam for the few root-only ops)
        │
        └→ F.6 API routes through helper  (no more direct systemctl calls)
              ↓
              F.7 hermes_provision dead-code removal
              ↓
              F.8 doctor perms audit-only
```

### G.2 What unlocks after each PR

| PR lands | Unblocks |
|---|---|
| F.1 | None (data-only) |
| F.2 | The upgrade migration. Boxes can land this PR alone and `doctor perms --fix` runs on next boot. |
| F.3 | `hal0-api` can become `User=hal0` (no orphan root-owned config files break the new daemon). **F.4 requires F.3.** |
| F.4 | `_chown_tree_to_hal0` becomes truly dead (no caller can produce a root-owned file inside a hal0 tree). |
| F.5 | The API's systemd/iptables calls have a seamed path; without it, F.6 has no replacement. |
| F.6 | The full `User=hal0` flip is functional. |
| F.7 | Hermes-provision slim (P3 §7.4) can begin — the installer cannot delete the chown phases until this lands (per `/home/mint/hal0-rework-plan.md:513-515`). |
| F.8 | Ongoing audits; no more silent drift. |

### G.3 PR ordering invariant

**F.3 (born-owned seeding) MUST land before F.4 (User flip).** If F.4 ships without F.3, the API starts as `hal0` and discovers it cannot read/write `/etc/hal0/hal0.toml` (still root:root because F.3 hasn't run). The only safe combined deployment is F.3 → F.4 → F.5 → F.6 in the listed order.

**F.1 (default flip) and F.2 (doctor --fix wired) MUST ship before F.3.** F.1 alone does nothing (the table's plan is what `doctor --fix` calls). F.2 alone does nothing (no drifts to fix because F.1 hasn't changed the table). F.1+F.2 together enable the migration that F.3 then assumes.

**F.7 (hermes_provision deletion) is the gateway to P3 §7.4 hermes-provision slim.** Per the rework plan: "Hard prereq: P3-perms lands FIRST — the installer can't delete the chown phases until it does." (`/home/mint/hal0-rework-plan.md:513-515`).

**F.5+F.6 are prerequisites for P3-quadlet.** The Quadlet migration (P3 §7.2 second half) needs the API to write `.container` files under `/etc/containers/systemd/hal0-slot@<id>.container` (or `/usr/share/containers/systemd/`) — same root-owned-by-design + hal0-systemctl seam pattern. The seam from this spec is the template Quadlet reuses.

### G.4 Sequencing call-out

The rework plan (§17 sequencing) places P3-perms as a hard gate for P3 §7.4 (hermes-provision slim) and P3-quadlet. This spec implements that gate. The 8 PRs above are individually shippable but **the F.3→F.4→F.5→F.6 cluster MUST land as one atomic release** (each is half a feature without the others; the cluster has a single testing surface).

---

## PART H — Risks + capped verification

### H.1 Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **R1: Box upgrades where `/etc/hal0/hal0.toml` was hand-edited as root, then `User=hal0` flip leaves the editor unable to save.** | Medium | Operator friction. | The transition keeps root write on the file via `OwnershipStore.commit`'s atomic chown: the editor runs as root, the editor saves to a tmpfile, then the operator runs `doctor perms --fix` once. Document in CHANGELOG. Alternative: `OwnershipStore` adds a transient `agent-edit` mode that allows the editing user temporarily. |
| **R2: A previously-rooted API process is mid-write when the unit restarts as hal0, leaving a half-finished file.** | Low | One orphaned tmpfile; no data loss (atomic rename). | `SlotConfigStore.write_slot_toml` uses tmpfile+rename; restart in the middle leaves the pre-rename file intact. The new hal0 daemon discovers the partial tmpfile on next save attempt and overwrites cleanly. |
| **R3: `OwnershipStore.commit` chowns `/etc/hal0/hal0.toml` to hal0 while it's being read by a hal0 daemon.** | Very low | None (chown doesn't break open file descriptors on Linux). | Verified: `man 2 chown` confirms an open fd continues to work post-chown. No mitigation needed. |
| **R4: The `hal0-systemctl` helper is not installed but the API tries to use it.** | Medium (new install missing the sudoers drop-in) | Slot launch fails with `PermissionError` instead of working. | `SystemCtlSeam` raises a typed `Hal0SeamMissing` (distinct from `PermissionError`) with a one-line remediation: "install /etc/sudoers.d/hal0-systemctl" + "systemctl restart hal0-api". `hal0 doctor` checks the seam presence and warns. |
| **R5: `_chown_tree_to_hal0` deletion causes a regression on dev (non-root) installs.** | Low | `geteuid() != 0` made the helper a no-op (`:893`); the deletion removes only the no-op path. | Tests already cover the no-op case. Delete + test. |
| **R6: Born-owned config seeding conflicts with the installer's existing root-context openssl/secrets generation.** | Medium | Secrets written as root, read as hal0, fail with EACCES. | Secrets live in `/var/lib/hal0/secrets/` (root:root by design, per `perms.py:186`). The installer writes them from root context (correct), and the `hal0-agentenv` seam (or `hal0-systemctl` extension, see D.3) writes per-agent secrets — both unchanged. |
| **R7: `OwnershipStore.commit` runs concurrently with a daemon write, race condition.** | Low | One of two writes succeeds; the other fails EACCES. | `commit` already handles per-path rollback (`perms.py:381-386`). The `doctor perms --fix` migration runs as a one-shot systemd `oneshot` *before* `hal0-api` starts (ordering: `doctor-perms-fix.service` → `hal0-api.service`). Boot-time, no concurrent writers. |
| **R8: Forgetting to delete the late chowns in install.sh after born-owned seeding.** | Low | Redundant chowns (no-op, but noise). | Mechanical delete; tested by `installer/test_install_dryrun.sh` (asserts no `chown hal0:hal0` outside `OwnershipStore`). |

### H.2 Capped verification (each PR has a verification gate)

| PR | Verification |
|---|---|
| F.1 | `pytest tests/install/test_perms.py` passes (table updated). `hal0 doctor perms` on an existing box shows drifts for `/etc/hal0/*` (the expected migration). No production code path changes — no behavioral risk. |
| F.2 | `hal0 doctor perms --fix` on a fresh clone: `OwnershipStore.commit` runs, all rows reach `ok` status. Re-run: `ok` (idempotent). `--revert`: all rows return to `drift` (proving snapshot correctness). |
| F.3 | On a fresh install (`sudo bash install.sh`), `stat -c '%U:%G %a' /etc/hal0 /etc/hal0/hal0.toml /var/lib/hal0 /var/lib/hal0/.first_run_done /var/lib/hal0/skills` reports `hal0:hal0` and `2775/2775/2775`. The previously-required `chown` block at `install.sh:1620-1658` is deleted; the install completes without it. |
| F.4 | Unit file at `/etc/systemd/system/hal0-api.service` has `User=hal0`, no `UMask=`. `systemctl restart hal0-api` succeeds. The daemon's `id` reports `uid=hal0`. `/etc/hal0/hal0.toml` save (via `hal0 admin ...`) succeeds. |
| F.5 | `/usr/lib/hal0/bin/hal0-systemctl` exists, executable. `/etc/sudoers.d/hal0-systemctl` exists, `visudo -cf` passes. `sudo -n -u hal0 /usr/lib/hal0/bin/hal0-systemctl write-unit test` (with stdin) writes `/etc/systemd/system/hal0-slot@test.service` root:root 0644. `sudo -n -u hal0 /usr/lib/hal0/bin/hal0-systemctl start nonexistent` returns 64 with "bad slot id" (validation works). |
| F.6 | Slot create flow (`hal0 slots create chat test`) ends with `hal0-slot@test.service` running, daemon-reloaded, no `systemctl` direct invocations in `hal0-api`'s strace. `hal0 doctor` reports `seam:hal0-systemctl` present. |
| F.7 | `hermes_provision.py` shrinks by ~110 lines + 1 phase entry. `grep -n '_chown_tree_to_hal0\|_phase_ownership_reconcile' src/hal0/agents/hermes_provision.py` returns nothing. Hermes provision (`bootstrap hermes`) completes; `stat -c '%U:%G' /var/lib/hal0/.hermes` reports `hal0:hal0`. `config.yaml` inside the home is `hal0:hal0` (the original ordering bug is gone). |
| F.8 | `hal0 doctor perms` (no flag) returns exit 0 on a clean box, exit 1 with the audit table when a path is drifted (test: `chown root /etc/hal0/hal0.toml; hal0 doctor perms`). `doctor perms --fix` returns to exit 0 after the fix. |

### H.3 Adversarial verification (post-F.6 cluster)

After F.3–F.6 ship, run the **end-to-end born-owned test** on the `halo` LXC (the rework deploy target — `hal0-rework-plan.md:721-722`):

1. Fresh `sudo bash install.sh` (no `--keep-data`). All `/etc/hal0/*` + `/var/lib/hal0/*` paths are born `hal0:hal0` from the first write. `stat` audit passes.
2. `hal0 doctor perms` reports `ok` on every row.
3. `systemctl status hal0-api` reports `User=hal0`.
4. Slot lifecycle: `hal0 slots create chat test` → `hal0-slot@test.service` running, started by `hal0` (via `hal0-systemctl`), no `sudo` direct in strace.
5. Self-update test: install a fake newer package, observe `doctor perms --fix` runs once (idempotent), `systemctl restart hal0-api` works.
6. Hermes provision (`bootstrap hermes`): home is `hal0:hal0` 0700; `config.yaml` is `hal0:hal0`; `hal0-agent@hermes.service` is `User=hal0`, running, with no `ownership_reconcile` phase in the run log.
7. Failure injection: `chown root /etc/hal0/hal0.toml`, observe `hal0 doctor perms` exits 1 with the drift row visible; `hal0 doctor perms --fix` restores; `systemctl restart hal0-api` recovers without manual intervention.

---

## PART I — Spec-level DoD (single PR or cluster acceptance)

The 8-PR sequence lands when:

- [ ] `OwnershipStore.ownership_table()` declares `service_user="hal0"` by default; all §7.2 ownership map rows are present; `OwnershipStore.plan()` on a fresh install produces zero drifts; `OwnershipStore.commit()` is reachable via `hal0 doctor perms --fix` and rolls back on failure.
- [ ] `installer/install.sh` config-seed block runs as hal0 (via `run-as-hal0.sh`); the late `chown` block at `install.sh:1620-1658` + `install.sh:1692` + `install.sh:1123-1124` is deleted; the `User=root` + `UMask=0002` + drop-in-revert block at `install.sh:932-964` is replaced by `User=hal0`.
- [ ] `hal0-api.service` ships `User=hal0`; the daemon writes `/etc/hal0/*` and `/var/lib/hal0/*` directly (no chown); reads `/etc/hal0/agents/` (root-owned) succeed via group bit.
- [ ] `hal0-systemctl` helper + sudoers drop-in are installed and `visudo -cf` passes; the helper validates all args; only `daemon-reload`, `start/stop/restart hal0-slot@<id>`, `write-unit`, `iptables-{accept,drop}-forward`, and `restart-self` subcommands are accepted.
- [ ] `slots/manager.py` + `updater.py` route every privileged IO call through `SystemCtlSeam`; no direct `subprocess.run(["systemctl", ...])` remains in the daemon (lint check).
- [ ] `hermes_provision.py`: `_chown_tree_to_hal0` + `_phase_ownership_reconcile` + their three call sites + the `PHASES` entry are deleted; the file is shorter by ~110 lines + 1 phase entry; `bootstrap hermes` produces a `hal0:hal0` `HERMES_HOME` with `hal0:hal0` `config.yaml` on the first run (no late reconcile).
- [ ] `hal0 doctor perms` is audit-only by default; `--fix` is the explicit root-gated commit; `--table-root` is the emergency rollback path.
- [ ] All 8 PRs green: unit tests, integration tests, linter, type checker, scar-baseline ratchet, sunset-shim check, CI.
- [ ] `tracker.md` (per `/home/mint/hal0-rework-plan.md:644-658`) carries the new task IDs (`P3-perms:F.1` … `P3-perms:F.8`) with status transitions + the cluster accepted entry.

---

## Appendix A — Glossary of ownership invariants

| Invariant | Where enforced | Drift detector |
|---|---|---|
| `/usr/lib/hal0` is read-only root | `OwnershipStore` row (NEW) | `doctor perms` |
| `/etc/hal0` is hal0:hal0 2775 setgid | `OwnershipStore` row | `doctor perms` |
| `/etc/hal0/hal0.toml` is hal0:hal0 0600 | `OwnershipStore` row | `doctor perms` |
| `/etc/hal0/agents/` is root:root 0755 (never flips) | `OwnershipStore` row + pinned comment | `doctor perms` |
| `/etc/hal0/agents/<id>.env` is root:root 0644 | `hal0-agentenv` seam | manual |
| `/var/lib/hal0` is hal0:hal0 2775 | `OwnershipStore` row | `doctor perms` |
| `/var/lib/hal0/.hermes` is hal0:hal0 0700 | `OwnershipStore` row (HERMES_HOME) | `doctor perms` |
| `/var/lib/hal0/secrets/` is root:root 0755 (never flips) | `OwnershipStore` row + pinned comment | `doctor perms` |
| `/var/lib/hal0/secrets/agents/<id>.env` is root:root 0600 | `hal0-agentenv` seam | manual |
| `/var/lib/hal0/agents/` is hal0:hal0 0711 | `OwnershipStore` row (NEW, was 0711 repair) | `doctor perms` |
| `/var/lib/hal0/skills/` is hal0:hal0 2775 | `OwnershipStore` row (NEW) | `doctor perms` |
| `/var/lib/hal0/benchmarks/` is hal0:hal0 2775 | `OwnershipStore` row (NEW) | `doctor perms` |
| `/var/log/hal0` is hal0:hal0 0755 | `OwnershipStore` row | `doctor perms` |
| `/etc/systemd/system/hal0-slot@*.service` is root:root 0644 | `hal0-systemctl` seam (writes via `install -o root -g root -m 0644`) | manual |

## Appendix B — Migration checklist (operator-facing, post-cluster-deploy)

For an operator running an existing `hal0` box (lxc105) to upgrade through this spec:

1. `hal0 pull` (or equivalent self-update — uses `hal0-systemctl restart-self`).
2. New package installed; systemd unit restart pending.
3. **Pre-restart**: `sudo hal0 doctor perms --fix` runs ONCE; chowns `/etc/hal0/*` + `/var/lib/hal0/*` to the new targets; stamps `/etc/hal0/hal0.toml.migrated-perms` so the postinstall hook doesn't re-run.
4. `sudo systemctl daemon-reload && sudo systemctl restart hal0-api`.
5. New daemon starts as `hal0`; verifies the ownership map; `hal0 doctor perms` reports `ok` on every row.
6. `hal0 doctor perms` becomes a daily audit (e.g. via `hal0-bench.timer` or a new `hal0-doctor.timer`); exit code is monitored; alert on drift.
7. **`hal0-slotctl` cleanup is already done** (the legacy hardened-perms attempt left the helper + sudoers behind — `install.sh:1047-1054` removes them on every install). The new `hal0-systemctl` is installed in the same step.

For a fresh install on `halo` (the new LXC — `hal0-rework-plan.md:721-722`):

1. `sudo bash install.sh` — the born-owned seeding writes everything correctly on the first pass; no migration step needed.
2. `hal0 doctor perms` confirms zero drift at install completion.

---

**End of spec.**