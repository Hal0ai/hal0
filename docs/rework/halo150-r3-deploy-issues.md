# halo (LXC 150) — R3 deploy issues report

**Box:** privileged LXC 150, hostname `halo`, 10.0.1.150 (cloned from `hal0-rc`/120)
**Target:** hal0 R3 = `ab3e88f3` (v0.9.8); later redeployed to descar tip `8cbc9902` for O1/O2 fixes
**Date:** 2026-07-18
**Reference:** `docs/rework/halo143-runbook-r3.md` (written for .143; run against .150)

---

## Summary

Clone + privileged conversion + fresh R3 git install **succeeded** (`hal0 0.9.8`, API 200).
Redeploy to descar tip picked up the O1/O2 code fixes. **Phase 2 (quadlet verify) is RED**
on a real, product-level blocker: the R3 quadlet renderer emits `AutoRemove=yes`, which
podman 4.9.3 (Ubuntu 24.04) does not support → no slot unit is generated → no slot runs.

---

## Resolved

| # | Issue | Root cause | Fix |
|---|-------|-----------|-----|
| R1 | Unprivileged→privileged not clonable directly | `pct clone` preserves `unprivileged:1`; rootfs UIDs shifted +100000 | Cloned already-privileged `hal0-rc` (120) instead of 143 |
| R2 | `rework-R3` tag / `ab3e88f3` absent from local + NFS clones | Tag never pushed; NFS repo has no GitHub creds | Fetched `ab3e88f3` from `origin/main` on thinMint, shipped via `git bundle` |
| R3 | `podman` missing on box | `hal0-rc` template shipped docker only | `apt-get install podman` → 4.9.3 |
| R4 | `podman run` failed: `install profile containers-default apparmor: exit status 243` | LXC is `apparmor.profile: unconfined` → container cannot load podman's default AppArmor profile | `/etc/containers/containers.conf` → `[containers] apparmor_profile = "unconfined"` **← load-bearing; belongs in installer preflight on unconfined LXC** |
| R5 | Stale alpha installs (`/opt/hal0`, `current→0.3.1-alpha.1`) | Template baked an old broken install | Cleared to `/root/*.pre-r3`; fresh FHS install of v0.9.8 |
| R6 | Auth required but no key → total lockout | `require_auth_enabled()` posture derives from non-loopback bind (`0.0.0.0`), NOT `hal0.toml auth_enabled=false` | Set `HAL0_ADMIN_KEY`/`HAL0_CLIENT_KEY` in `api.env` |
| **O1** | Slot `agent` `state.json` EPERM → `/api/slots` 400 → "slot manager not wired" | `/var/lib/hal0/slots` root-owned (root-run install) while API runs as `hal0` | **Box:** `chown -R hal0:hal0 /var/lib/hal0/slots` + restart. **Code (c1ea4519):** `SlotManager.list()` degrades unreadable slots to `state=ERROR` instead of 400-ing the collection |
| **O2** | `hal0` CLI (incl. doctor) sent anonymous requests → false "unreachable" wall on auth-on box | CLI never attached a bearer token | **Code (c1ea4519):** every CLI→API call attaches bearer from `HAL0_ADMIN_KEY`/`HAL0_CLIENT_KEY` env or `api.env`. Verified: `doctor all` with no env keys is clean (9/9 slots healthy) |
| **O5** | `rework-R3` tag not on GitHub | Collapse missed the tag step | Tag created + pushed: `rework-R3 → ab3e88f3` |
| **O6** | Slot **create** → HTTP 500 `PermissionError: /etc/hal0/slots.lock` | Fresh installer (run as root) left `slots.lock` `root:root`; API (`hal0`) can't write it. **`doctor perms` does not audit `*.lock` files, so `--fix` missed it** | `chown hal0:hal0 /etc/hal0/*.lock`. *Installer/doctor gap — should own/repair lock files.* |
| **O7** | Stale static `hal0-slot@.service` template shadows R3 quadlet units | Alpha debris: `/etc/systemd/system/hal0-slot@.service` (May 18) + `.d/override.conf` drop-ins for dead slots (`nano`,`primary`,`stt`,`embed`…). **Static units in `/etc/systemd/system` win over generator units.** R3 ships no static template (quadlet-only) | `rm /etc/systemd/system/hal0-slot@.service` + `rm -rf hal0-slot@*.service.d` + `daemon-reload`. *Installer should remove legacy static slot units on upgrade.* |

---

## Open

### O8 — R3 quadlet renderer requires podman ≥5.0; box has 4.9.3  *(PHASE-2 BLOCKER)*
Removing the O7 debris revealed the real quadlet outcome. The `@`-filename itself is fine
(generator loads it); conversion then fails on **podman-5.0-only keys**:
```
quadlet-generator: Loading source unit file /etc/containers/systemd/hal0-slot@qtest.container
quadlet-generator: converting "hal0-slot@qtest.container": unsupported key 'AutoRemove' ...
  (after stripping AutoRemove:)                          unsupported key 'GroupAdd' ...
```
→ generator produces **no** unit (`/run/systemd/generator*` empty). Slot can't start;
`hal0-systemctl restart` → exit 5 → API 500.

- Confirmed 5.0-only keys the renderer emits and 4.9.3 rejects: **`AutoRemove`, `GroupAdd`**
  (rejection is one-at-a-time; likely more behind them).
- **`GroupAdd` is load-bearing** — it grants the GPU device groups (gid 993 render / 44 video).
  It cannot be stripped without killing GPU access, and 4.9.3 quadlet has **no** equivalent key.
  → **Option 3 (strip keys) is dead.**
- The runbook assumed podman **≥4.4** (quadlet exists), but the renderer needs **≥5.0**.
  Ubuntu 24.04 (the `hal0-rc` template base) ships **4.9.3** → fundamental substrate mismatch.
- **Correction to first Phase-2 read:** the "generated service active" seen initially was
  the O7 static template masquerading, not the quadlet.

**Verified:** stripping keys in the venv renderer confirmed the chain (AutoRemove→GroupAdd);
box then restored to pristine R3 renderer (no local patch left).

**Remaining fallback options:**
1. **R3 renderer targets 4.9.3 quadlet** — major: `GroupAdd`/`AutoRemove` have no 4.9.3
   quadlet equivalent, so GPU slots would need a non-quadlet path. Effectively a rewrite.
2. **Base R3 boxes on podman ≥5.0** (recommended) — Debian 13 / Ubuntu 24.10+ ship podman 5.x,
   or add a podman-5 repo. Implies the `hal0-rc` (Ubuntu 24.04 + docker) template is stale
   for R3. Check what the live reference (lxc105) runs to confirm the intended substrate.

**Decision needed** — substrate change (podman 5.x base) vs renderer rework.

### O3 — `hal0-agent@hermes` start-timeout loop
`HERMES_DASHBOARD_READY port=9119` logs, then `start operation timed out` (2 min) →
`status=241/CONFIGURATION_DIRECTORY`, restart loop. Never signals systemd readiness.
`doctor perms` surfaced: *"Hermes ownership drift — run `sudo hal0 agent bootstrap hermes
--repair`"*. Provision-lane territory (owner to diagnose).

### O4 — model-layout migration pending (1303 links)
Existing 617G store on `/mnt/ai-models` not adopted until `hal0 migrate model-layout --apply`.
Expected; deferred by choice.

---

## Runbook status

- **Phase 0** (snapshot/baseline): done — `/root/halo150-pre-r3-*.tgz`, podman 4.9.3.
- **Phase 1** (deploy + health): deploy ✔, API 200 ✔, `doctor all` clean after redeploy ✔.
  Golden-path load blocked by O8.
- **Phase 2** (quadlet `@`-name verify): **RED — O8**. `@`-name accepted; `AutoRemove` key rejected → no unit. Stopped here per runbook.
- **Phase 3** (M5 rehearsal on copy): not started.
- **Phase 4** (live smoke): not reached (needs a runnable slot).

## Environment notes (accepted, not issues)
- Privileged + `apparmor unconfined` ⇒ container root ≈ host root (requested).
- Privileged maps container-root → real root(0); NFS/ai-models ownership differs from
  the unprivileged box.
- Mounts (bind, local zfs — resilient): `/mnt/ai-models`, `/mnt/repos`, `/mnt/projects`.
  thin-mint SSH key installed; `ssh -i ~/.ssh/thin-mint root@10.0.1.150` works.

## Product findings for the installer/provision lane
- R4: AppArmor `containers.conf` unconfined fix is load-bearing on unconfined LXC — add to installer convergent preflight.
- O6: installer must own/repair `/etc/hal0/*.lock`; `doctor perms` should audit lock files.
- O7: installer must remove legacy static `hal0-slot@.service` + drop-ins on upgrade (they shadow quadlet units).
- O8: R3 quadlet renderer requires podman ≥5.0 (`AutoRemove`, `GroupAdd` are 5.0 keys; `GroupAdd` is load-bearing for GPU). Either base R3 boxes on podman 5.x, or the renderer needs a 4.9.3-compatible / non-quadlet GPU path. The `hal0-rc` Ubuntu-24.04 template (podman 4.9.3) is the wrong substrate for R3.
</content>
