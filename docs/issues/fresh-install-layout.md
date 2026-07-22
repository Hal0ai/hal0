# Fresh install issues (lxc150)

Observed after `--purge` uninstall followed by a fresh install as root on lxc150.
All DRIFTs reported by `hal0 doctor` (driven by `hal0.install.perms.ownership_table`).

---

## Summary

The `install.sh` script runs as root and creates directories/files with root
ownership and no setgid bit.  The `hal0` daemon runs as `hal0:hal0` and needs
write access to config (`/etc/hal0`) and state (`/var/lib/hal0`) trees.

The ownership table (`hal0.install.perms`) expects the hardened P3-perms layout:
config root `2775` (setgid, `hal0:hal0`), mutable files `hal0:hal0`, runtime
state `hal0:hal0`.  The installer doesn't apply these — `doctor perms --fix`
should correct them post-install, but some rows are also just **never created**
(they appear on first use / first-run).

---

## Detailed DRIFTs

### `/etc/hal0` — config root

| Path | `is` (on disk) | `want` (perms table) |
| --- | --- | --- |
| `/etc/hal0/` | `hal0:hal0 2755` | `hal0:hal0 2775` |
| `hal0.toml` | `root:hal0 0600` | `hal0:hal0 0600` |
| `profiles.toml` | **absent** | `hal0:hal0 0600` |
| `hardware.json` | `root:hal0 0644` | `hal0:hal0 0644` |
| `openwebui.env` | `root:hal0 0600` | `hal0:hal0 0600` |
| `*.lock` (dir) | `hal0:hal0 2755` | `hal0:hal0 2775` |
| `capabilities.toml.lock` | `hal0:hal0 0644` | `hal0:hal0 0664` |
| `agents/` | `root:hal0 2755` | `root:root 0755` |

**Root causes:**

1. **Missing setgid (2755 → 2775).**  `install.sh` line 806:

   ```bash
   chmod 0755 "${ETC_DIR}" 2>/dev/null || true
   ```

   This resets the dir to `0755`, stripping any setgid bit.  The perms table
   expects `2775` so temp-file + `rename` writes by the hal0 daemon work.

2. **Files owned `root:hal0` instead of `hal0:hal0`.**  `install.sh` copies
   seed files as root (e.g. `cp`, `install -m`), and the script never calls
   `chown hal0:hal0` on them.  The daemon cannot atomically rewrite them.

3. **`profiles.toml` absent.**  Created by `hal0 setup` or the first-run
   wizard, not by `install.sh`.  No issue — expected.

4. **`agents/` wrong owner + mode.**  Created by `install.sh` but with the
   wrong group and setgid bit inherited from the parent dir.

### `/var/lib/hal0` — state root

| Path | `is` | `want` |
| --- | --- | --- |
| `.first-run.lock` | **absent** | `hal0:hal0 0664` |
| `slots/flm/` | `hal0:hal0 2755` | `hal0:hal0 2775` |
| `slots/utility/` | `hal0:hal0 2755` | `hal0:hal0 2775` |
| `models/collections/` | `root:hal0 2755` | `hal0:hal0 2775` |
| `models/collections/omni/` | `root:hal0 2755` | `hal0:hal0 2775` |
| `collections/omni/*.json` | `root:hal0 0644` | `hal0:hal0 0644` |
| `agents/` | **absent** | `hal0:hal0 0711` |
| `secrets/` | `root:root 0700` | `root:root 0755` |
| `secrets/agents/` | `root:root 0700` | `root:root 0755` |
| `benchmarks/` | `hal0:hal0 0755` | `hal0:hal0 2775` |
| `benchmarks/logs/` | `hal0:hal0 0755` | `hal0:hal0 2775` |
| `benchmarks/runs/` | `hal0:hal0 0755` | `hal0:hal0 2775` |
| `benchmarks/server-ab/` | `hal0:hal0 0755` | `hal0:hal0 2775` |
| `STATE.md` | `hal0:hal0 0644` | `hal0:hal0 0664` |

**Root causes:**

1. **Missing setgid (2755 → 2775) on slot runtime dirs.**  `install.sh` creates
   `slots/` via `mkdir -p` under root's umask, then applies `chmod 2775` only
   to the parent `slots/` dir.  Subdirs created later by the slot loader lack
   the setgid bit.

2. **`models/collections/` owned `root:hal0`.**  The installer copies
   collection JSON manifests from `installer/etc-hal0/` into
   `/var/lib/hal0/models/collections/` while running as root.  No `chown -R`
   is applied to this subtree.  The perms table expects `hal0:hal0`.

3. **`.first-run.lock` absent.**  Written by the first successful completion of
   `hal0 setup` or the dashboard first-run wizard.  Expected absent on a
   fresh install that hasn't completed first-run yet.

4. **`agents/` (per-agent sub-homes) absent.**  Created on demand by
   `hermes_provision`.  Expected absent.

5. **`secrets/` + `secrets/agents/` mode 0700 vs 0755.**  The hardened root
   umask on lxc150 leaks into `mkdir -p` calls.  install.sh restores umask to
   022 at the top, but the secrets directories are created by a different code
   path (hal0-agentenv seam) that inherits the caller's umask.

6. **Setgid missing on `benchmarks/` subtree (0755 vs 2775).**  Created by the
   install script with `mkdir -p`; the setgid chmod is only applied at
   install.sh line 1248 (`chown -R hal0:hal0 ...`) which doesn't set the
   setgid bit.

7. **`STATE.md` mode 0644 vs 0664.**  Written by the Hermes session-start
   hook; the birth mode from `open()` is 0644.  The perms table expects 0664
   (group-writable) so multiple group members can overwrite.

### Hermes gateway install fails

```
Installing hermes gateway (Telegram/Discord bridge)…
System gateway install requires root. Re-run with sudo.
hermes gateway install failed — Telegram/Discord bridge unavailable; continuing.
```

The install script runs as root but the hermes gateway install sub-step
requires root context it doesn't get.  The effect is the
`hermes-gateway.service` systemd unit is never installed.

---

## Pattern summary

| # | Issue | Scope | Fix |
| --- | --- | --- | --- |
| 1 | Setgid (2775) not set on config/state dirs | `/etc/hal0`, `slots/*/`, `models/`, `benchmarks/*/` | `install.sh` must `chmod 2775` instead of `0755` |
| 2 | Files owned `root:hal0` not `hal0:hal0` | `hal0.toml`, `hardware.json`, `openwebui.env`, `models/collections/*` | `install.sh` must `chown hal0:hal0` after seeding |
| 3 | Sealed-by-root umask `0700` instead of `0755` | `secrets/`, `secrets/agents/` | `chmod 0755` after `mkdir -p` |
| 4 | Lock-file mode `0644` instead of `0664` | `capabilities.toml.lock` | Writer (or fix) must use `0664` |
| 5 | `agents/` in `/etc/hal0` wrong group | `agents/` dir | `chown root:root` + `chmod 0755` |
| 6 | Hermes gateway unit not installed | hermes-gateway.service | Run gateway install as root or pass `--system --run-as-user hal0` |
