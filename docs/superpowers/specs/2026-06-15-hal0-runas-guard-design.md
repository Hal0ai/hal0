# Design: hal0 run-as guard — prevent wrong-user clobber of agent state

- **Date:** 2026-06-15
- **Issue:** [#843](https://github.com/Hal0ai/hal0/issues/843)
- **Status:** Approved design, pre-implementation
- **Related:** PR #769 (non-root install fails fast), P5H-4 in
  `docs/superpowers/plans/2026-06-06-hindsight-engine-swap.md`,
  `docs/agents/hermes/SERVICE.md` (operator runbook)

## Problem

hal0-managed processes must run as the unprivileged `hal0` user with
`HOME=/var/lib/hal0`. The systemd template (`hal0-agent@.service` → `User=hal0`)
enforces this for the *service* path, but nothing stops a human (or the
installer) from launching the same binaries as **root** — `sudo hermes …`, a
root TUI session, or root-context bootstrap. When that happens, two failures
occur, usually together:

1. **Split-brain home.** As root, a `HOME`-relative path like `~/.hermes`
   resolves to `/root/.hermes` instead of `/var/lib/hal0/.hermes`. The process
   writes a competing config/state tree the `hal0`-user service never reads.
2. **Permission clobber.** Where root writes into `/var/lib/hal0/.hermes`, files
   land `root:root` (`config.yaml` 0600, the `runtime.json` embed token 0600,
   the venv). The `hal0`-user service then hits `EACCES` — or worse, silently
   falls back to the **default provider** because it cannot read its own config.

The silent fallback is the real villain: the system keeps "working" while
answering from the wrong model. This is the documented "root-clobber regression"
(P5H-4); it keeps coming back because past fixes were applied on the live CT105
box rather than in the repo's wrapper/installer.

This design generalises the fix: **one enforcement primitive that every
hal0-managed process inherits**, so no hal0 process can run as the wrong user
and clobber state — Hermes today, any future `hal0-agent@` instance tomorrow.

## Goals / Non-goals

**Goals**
- A single, shared chokepoint that auto-corrects wrong-user launches by
  re-execing as the canonical user with the correct `HOME`.
- Fixing the privilege-clobber *and* the split-brain home in one move (a correct
  re-exec sets `HOME=/var/lib/hal0`, so `~`-relative paths resolve correctly).
- Defense-in-depth ownership handover for artifacts the installer/bootstrap
  legitimately creates as root.
- Loud detection of pre-existing drift; convergence via an explicit repair.

**Non-goals**
- No automatic, behind-your-back `chown -R` from healthchecks/timers (footgun:
  a misconfigured canonical path would happily chown the wrong tree).
- Not changing the supported install model (system install still runs as root;
  PR #769's non-root-install fail-fast stays as is — this is its inverse).
- Not hardening against a malicious root; this prevents *accidental* misuse, not
  an adversary who already has root and means harm.

## Architecture: three layers

### Layer 1 — Prevent: shared shell guard (`runas.sh`)

A tiny POSIX shell library installed once at a stable absolute path (e.g.
`/usr/local/lib/hal0/runas.sh`, final path confirmed against install.sh
conventions). It exposes one function:

```sh
# hal0_ensure_runas <user>
# If EUID != 0  -> return (we are some user; the caller's own perms apply).
# If EUID == 0:
#   - honor opt-out: if $HAL0_ALLOW_ROOT (or per-tool $HAL0_HERMES_ALLOW_ROOT)
#     is set, return without re-exec (deliberate root debugging).
#   - if target <user> does not exist -> return (nothing to drop to).
#   - else exec the current command as <user> with a clean, correct env:
#       prefer:  runuser -u <user> --        (util-linux; sets HOME via PAM)
#       else:    setpriv --reuid <user> --regid <user> --init-groups env -- ...
#       else:    sudo -H -u <user> --
#     stripping any inherited HERMES_HOME (env -u HERMES_HOME semantics) so the
#     re-exec resolves HOME=/var/lib/hal0 and never /root.
```

Every generated wrapper sources this lib as its **first executable line** and
calls `hal0_ensure_runas hal0` before doing anything else. Because it `exec`s,
the rest of the wrapper (including the existing cwd-guard at `hermes:64-74`)
then runs *as hal0* and behaves correctly.

**Why a shared shell lib (approach A), not a launcher binary or Python guard:**
Hermes is a third-party binary we only control at the wrapper. Wrappers are
already generated/installed by `install.sh`, so one sourced file is inherited by
every wrapper with no per-wrapper duplication, and it works for both
third-party-wrapped binaries and hal0's own shell entrypoints. A standalone
`hal0-run` launcher adds a command + indirection for the same edit surface; a
Python-entry guard can't cover the wrapped Hermes binary, so we'd build the
shell guard anyway.

**Coverage of the systemd path:** the unit's `ExecStart` reaches the wrapper
(confirmed via recon of the `hal0-agent` shim). Running as `hal0` already, the
guard is a no-op there — correct and harmless. If the shim execs the venv binary
directly (bypassing the wrapper), the design routes it through the wrapper or
sources the guard in the shim launch path so the chokepoint is not bypassable.

**Opt-out:** `HAL0_ALLOW_ROOT=1` (global) / `HAL0_HERMES_ALLOW_ROOT=1` (per
tool) lets a deliberate root debug session through, explicitly.

### Layer 2 — Prevent: bootstrap chowns its own artifacts

The installer/bootstrap legitimately runs as root and creates files *before* any
service runs; the guard can't cover that. So each creation site hands ownership
to `hal0:hal0` immediately after creating, guarded on running-as-root and the
hal0 user existing, idempotent:

- `hermes_provision.py:_install_venv` (~:447) → `chown -R hal0:hal0` the venv.
- `_claim_hermes_home` (~:620-626) / `_phase_home_init` (~:654-655) →
  `chown -R hal0:hal0` the `HERMES_HOME` tree.
- `_write_runtime_json` (~:3340-3373) → chown `runtime.json` to hal0 *after* the
  0600 chmod (embed token must be hal0-readable).
- `install.sh` → explicit `chown -R hal0:hal0 /var/lib/hal0/venvs/hermes`
  alongside the existing `.cache`/`memory` chowns (~:1022,:1142), matching their
  idiom. Reuse existing canonical user/home vars, do not hardcode.

### Layer 3 — Detect (auto, read-only) + Repair (explicit)

- **Detect:** a read-only check surfaced in the agent healthcheck /
  `hal0`-doctor path that flags, loudly: `root:root` ownership on
  `config.yaml` / `runtime.json` / venv root, and the existence of
  `/root/.hermes`. This converts the silent "wrong model" failure into a visible
  one. Exact host of the check confirmed against the CLI surface (a `doctor`
  subcommand if one exists, else the existing `hal0-agent <id> status` path).
- **Repair:** an *explicit*, idempotent reconciliation the operator invokes —
  extend `hal0 agent bootstrap hermes --repair` to also chown the `.hermes`
  tree/venv/runtime.json to `hal0:hal0` and (after confirmation) drop a stray
  `/root/.hermes`. Convergence for already-broken boxes (incl. CT105) without
  any automatic filesystem mutation.

## Data flow

```
launch (any user) ──▶ wrapper sources runas.sh ──▶ hal0_ensure_runas hal0
   EUID!=0 ─────────────────────────────────────▶ proceed as caller
   EUID==0 + opt-out ───────────────────────────▶ proceed as root (explicit)
   EUID==0 ─────────────▶ exec runuser/setpriv/sudo as hal0 (HOME=/var/lib/hal0)
                          └▶ wrapper re-runs as hal0 ▶ cwd-guard ▶ exec hermes
```

Install/bootstrap (root) ─▶ create artifact ─▶ chown hal0:hal0 (Layer 2).
Healthcheck/doctor (any) ─▶ stat probe ─▶ flag drift (Layer 3 detect).
`bootstrap --repair` (root) ─▶ reconcile ownership + drop /root/.hermes (Layer 3 repair).

## Components & isolation

| Unit | Purpose | Interface | Depends on |
|---|---|---|---|
| `runas.sh` | re-exec a command as a target user | `hal0_ensure_runas <user>` (sourced) | `runuser`/`setpriv`/`sudo`, `id` |
| wrapper edit | invoke the guard first | sources `runas.sh`, calls the fn | `runas.sh` at a fixed path |
| install.sh wiring | install the lib + chown artifacts | shell, root-guarded | existing canonical vars |
| provision chowns | hand artifact ownership to hal0 | python helper, root-guarded | hal0 user, canonical paths |
| detect check | read-only drift report | `stat` probe in healthcheck/doctor | canonical paths |
| repair action | explicit ownership reconcile | `bootstrap … --repair` | hal0 user, canonical paths |

Each unit is independently testable: the guard via a fake-`runuser` shim, the
chowns via a root-context install assertion, the detect/repair via crafted
`root:root` fixtures.

## Testing

A regression smoke test (mirrors P5H-4 checks), runnable in CI/harness:

1. **Guard re-exec:** invoke the wrapper with `EUID==0` simulated and a stubbed
   `runuser` on `PATH`; assert the stub is called with `-u hal0`, the original
   args are preserved, and `HERMES_HOME` is not leaked from root's env.
2. **Opt-out:** with `HAL0_ALLOW_ROOT=1`, assert no re-exec attempt.
3. **Non-root passthrough:** `EUID!=0` → guard is a no-op, command runs as-is.
4. **Bootstrap ownership:** after a root-context install/bootstrap,
   `stat -c '%U:%G %a'` on `config.yaml`, `runtime.json`, and the venv root all
   read `hal0:hal0` (NOT `root:root 0600`); `/root/.hermes` does not exist.
5. **No-clobber:** `sudo hermes <version>` creates/rewrites no `root:root` file
   under `/var/lib/hal0/.hermes`.
6. **Detect:** with crafted `root:root` fixtures, the detect check reports drift
   (non-zero / flagged); clean fixtures report healthy.

Test home + style confirmed against the existing installer/wrapper test layout
(recon). Shell-level assertions where the unit is shell; pytest where python.

## Risks & mitigations

- **`runuser`/`setpriv` absent on a host** → fall back through the tool chain
  (`runuser` → `setpriv` → `sudo -H -u`); if none can drop privilege, the guard
  must not proceed as root silently — log a clear message. (Per the chosen
  philosophy we auto-correct; the unreachable-tool case degrades to a loud
  no-drop warning, not a silent root run.)
- **Re-exec loop** → the guard returns immediately when already `EUID!=0`, so a
  re-exec'd invocation cannot re-trigger it.
- **Breaking the systemd path** → guard is a no-op when already `hal0`; covered
  by health smoke test post-change. Do NOT deploy to CT105 (shared host) as part
  of this change; ship a green PR and let an operator deploy.
- **Chown touching the wrong tree** → all chowns reference existing canonical
  vars, are root-guarded, and only target the known `.hermes`/venv paths.
- **`/var/lib/hal0` is `2775` setgid root:hal0** (install.sh) — preserve; this
  design only changes the `.hermes` subtree + venv ownership, not the parent.

## Rollout

1. Implement Layers 1–3 in the repo via TDD on `fix/hal0-runas-guard`.
2. Smoke test green in CI.
3. Open PR referencing #843; include the SERVICE.md runbook edit (already on the
   branch).
4. Operator deploys to CT105 (out of scope here; another session is currently
   active on that runtime) and runs `bootstrap --repair` to converge the box.

## As-built (implemented on `fix/hal0-runas-guard`)

Concrete decisions made during TDD implementation:

- **Layer 1 (wrapper):** guard lib lives at `installer/lib/run-as-hal0.sh`,
  installed by `install.sh` to `/usr/lib/hal0/guards/run-as-hal0.sh` (matches the
  hermes-hooks `install -m 0755` idiom; dev-mode PREFIX shadows it). Only
  `installer/wrappers/hermes` sources it — `hal0-hermes` is a symlink to that
  wrapper, so it inherits the guard for free. Re-exec wraps the command in
  `env HOME=<home> -u HERMES_HOME` so HOME is correct and HERMES_HOME is dropped
  regardless of which dropper (runuser → setpriv → sudo) is used.
- **Layer 1 (systemd path):** the `hal0-agent` shim execs the venv binary
  directly (NOT the wrapper), so the guard there is `_runas_popen_extras()` in
  `agent_shim.py`, which adds `user=`/`group=` to the `Popen` and corrects
  `env['HOME']` when running as root. No-op under the unit (already `User=hal0`).
- **Layer 2 (chown):** `_chown_tree_to_hal0()` in `hermes_provision.py`, called
  from `_phase_install` (venv), `_phase_home_init` (HERMES_HOME tree), and
  `_phase_install_artifacts` (runtime.json). No-op unless root, so `--repair`
  reconciles ownership on already-broken boxes.
- **Layer 3 (detect):** `hal0 doctor perms` — read-only ownership audit
  (`check_hermes_ownership` / `has_ownership_drift` in `doctor_commands.py`).
  Exits non-zero and points at `bootstrap --repair` on drift. No auto-repair.
- **Opt-out:** `HAL0_ALLOW_ROOT=1` honored by both the shell guard and the shim.
- **Test seam:** `HAL0_RUNAS_TEST_UID` lets the guard's euid be faked in CI
  (we can't become root); production never sets it.
- **Deferred:** a root-context harness smoke row (real `stat` ownership +
  no-`/root/.hermes` after a root install) — can't run in non-root CI; tracked
  as a follow-up. Ownership behavior is covered by the helper unit tests.
