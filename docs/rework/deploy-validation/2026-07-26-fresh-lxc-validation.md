# Fresh-LXC install validation — 2026-07-26 (stamp `a3v1`)

**Ref under test:** `hardening/alpha3` (branched off `origin/main` `3707cc9c`).
**Scope:** genuinely bare-metal fresh containers — not upgrade-in-place. This is the
thing the 2026-07-19 run (`2026-07-19-r5-install-validation.md`) explicitly could not
do: both boxes there already had hal0 0.9.8 installed and serving.

## Boxes

All created for this run on `pve` (`10.0.1.110`, `pve-manager/9.2.3`, kernel
7.0.6-2-pve), all **unprivileged**, all destroyed-and-recreated from stock templates.
CT105 (live reference), CT150 and CT120 (a PVE template) were never touched.

| CT | Template | Substrate | Purpose | Result |
|----|----------|-----------|---------|--------|
| 160 | ubuntu-24.04-standard_24.04-2 | podman 4.9.3, py3.12 | proven substrate, fresh | `INSTALL_EXIT=0` |
| 161 | ubuntu-26.04-standard_26.04-1 | podman 5.7.0, py3.14 | the substrate that BLOCKED on 2026-07-19 | `INSTALL_EXIT=0` |
| 162 | ubuntu-24.04-standard_24.04-2 | podman 4.9.3, py3.12 | **fixed tree, zero manual prep** | `INSTALL_EXIT=0`, 0 `XX` |

CT160/161 were installed with the pre-fix tree (and needed manual prep, see below);
CT162 is the acceptance box — the fixed tree onto an untouched container.

## Container prerequisites — corrected

The documented set (`features: nesting=1,fuse=1,keyctl=1,mknod=1`) is **not sufficient**
for an unprivileged CT. Empirically required, all three:

```
features: nesting=1,fuse=1,keyctl=1,mknod=1
lxc.apparmor.profile: unconfined
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
```

Without the AppArmor line every `podman run` fails with
`dial udp …: socket: permission denied` — AppArmor confining podman's network setup —
even with all four feature flags correctly set. Note PVE warns that setting
`lxc.apparmor.profile` explicitly overrides `features: fuse,nesting`; keep the
`features:` line anyway.

**GPU passthrough gid is release-dependent.** The `dev0` gid must be the `render`
group's gid *inside* the container:

| Release | in-container `render` gid |
|---------|---------------------------|
| Ubuntu 24.04 | 993 |
| Ubuntu 26.04 | **991** |

Cloning CT150's `gid=993` onto a 26.04 container puts `renderD128` and `accel0` in
group `clock`, and GPU access is silently denied.

## Defect verification

The five defects from `2026-07-19-r5-install-validation.md`, now checked on fresh boxes:

| id | verdict | evidence |
|----|---------|----------|
| **M2** keyring-EDQUOT diagnosis | not reproduced | keyring clean on all three (`/proc/key-users` uid 0 at 91/20000); the branch was never entered. Fix present in code, unexercised here. |
| **M3** render-gid name check | **VERIFIED — gate fired correctly** | CT161: `gpu: /dev/dri/renderD128 is owned by gid 993, which maps to group 'clock' — NOT 'render'` → `Fix on the Proxmox host: dev0: /dev/dri/renderD128,gid=991` → hard stop. The old gate would have PASSED. Applying its own printed remedy fixed the box. |
| **m1** no `/root/.hermes` | **VERIFIED** | absent on 160, 161 and 162 after full installs. |
| **m2** `StartLimitIntervalSec` in `[Unit]` | not exercised | no slot quadlets rendered — a fresh install with no models creates no slots. |
| **m4** `agent status hermes --json` | **VERIFIED** | CT160 emits valid JSON (`{"name": "hermes", "provisioned": true, …}`). |

## New defects found (all fixed on `hardening/alpha3`)

| id | defect | fix |
|----|--------|-----|
| **F0** | **MCP admin surface silently did not mount.** Fresh installs resolve fastapi 0.140.0 / starlette 1.3.1 (`pyproject.toml` pins only `fastapi>=0.115`). Starlette 1.x keeps `include_router`'d routes behind a wrapper, so the route-map autogen's flat walk built an empty map, the catalog-drift guard raised, and `mount_mcp_servers` swallowed it as a warning — all ~82 admin tools gone. **Release blocker.** | `66a78cb7` — recursive walker. CT160 before: `hal0.mcp.mount_failed`. CT162 after, from a source install: `hal0.mcp.mounted`. |
| **F1** | Stock Ubuntu LXC templates have no `curl`; the documented direct-`install.sh` path died at step 1/13. | `93c410f9` — auto-install base prereqs. CT162 (confirmed `NO_CURL` before install): `OK bootstrap prereqs: installing missing curl …`. |
| **F2** | Container-runtime gate swallowed podman's error and blamed `nesting`/`keyctl` when both were already set. | `93c410f9` — always print the runtime error; recognise the socket/namespace-denied signature; name AppArmor + `/dev/net/tun`. |
| **F3** | Every fresh install ended with `doctor perms` reporting STATE.md drift, because the rendered-context atomic write discarded the destination's mode and Hermes re-renders after the installer's `doctor perms --fix` backstop. | `24c6c91a` — preserve mode across the rename. STATE.md is gone from CT162's drift list. |
| **F4** | `doctor perms` wanted `secrets/` and `secrets/agents/` at **0755** while the installer creates them **0700** — so `--fix`, which the installer runs every install, would have **widened** the directories holding the token EnvironmentFiles. | `5708aed0` — table pinned to 0700. |

## CT162 acceptance (fixed tree, no manual prep)

```
INSTALL_EXIT=0        XX errors: 0
/api/health           200
hal0-api, hal0.target active
MCP                   hal0.mcp.mounted
/etc/hal0/api.env     640 hal0:hal0        (was 0644 world-readable)
/root/.hermes         absent
```

**Residual:** `hal0 doctor perms` still reports 3 drift rows on a clean box —
`capabilities.toml.lock` (0644 want 0664), `HERMES_HOME` (2755 want 0700),
`models/chat-templates` (2755 want 2775). None is a security widening. Tracked as
board row `perms-table-converge`. A fresh install that never reports clean is
precisely how the F4 widening stayed invisible, so this is worth closing.

## Notes

- `HAL0_INSTALL_SKIP_VERIFY=1` is required to install an unsigned local tree; the
  provenance gate correctly refuses otherwise. Both refusals were observed and are
  correct behavior.
- The Hindsight memory engine venv reaches **~3.7 GB** and dominates install wall-clock
  (~20 min of a ~25 min install). Not a defect, but it is the bulk of a fresh install's
  time and disk.
- Boxes 160/161 are stopped; 162 is left running as the live reference for this stamp.
  Remove with `pct destroy <id>` when no longer needed.
