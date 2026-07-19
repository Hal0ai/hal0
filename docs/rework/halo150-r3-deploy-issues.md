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

### O8 — RESOLVED via podman-4.x compat (`PodmanArgs=`) — Phase 2 GREEN
**Resolution:** the compat path already on descar (`5adf6e0f` + tests `a2e04193`) translates the
5.0-only keys to raw flags via `PodmanArgs=--group-add … --security-opt …` (a key 4.x quadlet
supports) and drops only `AutoRemove` (cosmetic crash-path cleanup). Deployed `container.py@a2e04193`
to the box → generator converts, `/run/systemd/generator/hal0-slot@qtest.service` produced,
container **Up**, health `{"status":"ok"}`, inference generates on GPU, teardown clean. **GPU groups
preserved.** No substrate change, no rewrite (~20 lines, container.py only).

**Confirmed substrate:** lxc105 (live R3 reference) runs **podman 4.9.3 / Ubuntu 24.04 — identical
to this box**. R3's real substrate is 4.9.3; the compat path is the correct fix. Template refresh to
a podman-5 base (native keys + auto-remove) is an R5 cutover item, not a blocker.

*Original blocker (for the record):*
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

### O9 — doctor bundle leaks `HAL0_ADMIN_KEY`/`HAL0_CLIENT_KEY` (unmasked)  *(SECURITY — Phase 4.3 RED)*
`hal0 doctor bundle` writes `config/api.env` with the operator credentials in **plaintext**:
```
HAL0_ADMIN_KEY=<raw>
HAL0_CLIENT_KEY=<raw>
```
Grep-verified both keys present verbatim in the bundle. The bundle is meant to be shared for
support, so this leaks the admin/client auth keys.

**Root cause (precise):** `doctor_bundle._write_redacted_env` *is* invoked for api.env and uses
`hal0.api._redact.is_sensitive_key`, but the matcher is:
```
_SENSITIVE_RE = (?i)(?:SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT)
```
It matches `API_KEY` but **not a bare `_KEY` suffix** → `is_sensitive_key('HAL0_ADMIN_KEY')` and
`('HAL0_CLIENT_KEY')` return **False**. `HF_TOKEN`/`API_KEY`/`PASSWORD` mask correctly; hal0's own
auth keys slip through. **Fix:** add `KEY` (or `ADMIN_KEY|CLIENT_KEY`) to `_SENSITIVE_RE` — one line.
Same masker feeds config dumps elsewhere, so the gap is broader than the bundle.

*(Other bundle notes: `rocminfo.txt`/`rocm-smi.txt` = 1 line each — rocm userspace not installed on
this vulkan box, expected; `podman-images` 106 lines ✓; journalctl captured as `logs/hal0-api.log` /
`logs/hal0-agent.log` rather than `logs/journalctl.txt`.)*

### O10 — deprecated `[server].extra_args` mangles JSON in the Exec render  *(minor / deprecated surface)*
Setting `[server].extra_args = "--chat-template-kwargs '{\"enable_thinking\":false}'"` (the exact
runbook 4.2 form, quotes verified intact in the toml) renders into the quadlet `Exec=` as
`--chat-template-kwargs '{enable_thinking:false}'` — **inner double-quotes stripped** → invalid JSON
→ llama-server crashes (slot `error`/exit-code). `shlex` in isolation preserves the quotes, so the
loss is a **double-tokenization** in the Exec builder. **Caveat:** `extra_args` is explicitly
**deprecated** (`container.extra_args_deprecated` warning: "move these to a profile"), and the
supported no-think path (the `enable_thinking` field + `:8080` normalize) works correctly — so this
is a low-severity edge on a deprecated surface, not the core 4.2 assertion.

### O11 — native quadlet render fails on unprivileged podman-5 LXC (halo143) — RESOLVED: uniform render
Characterized on 143 (unpriv, U26.04, podman 5.7): the container runs perfectly under native
flags via manual `podman run` (model loads, serves, clean `--rm` stop) — but the NATIVE quadlet
systemd unit fails to stay up: exit 5 + `netavark: open /run/user/0/netns/…: No such file or
directory` on teardown (podman under a mapped-root LXC uses rootless-netns infrastructure).
The compat (`PodmanArgs=`) unit ran healthy on the same box. The break is the native unit's
systemd lifecycle × unprivileged netns, not the flags.

**Resolution (descar):** the renderer now emits ONE uniform render on every substrate —
`PodmanArgs=--group-add …/--security-opt …`, AutoRemove never — and the podman-version probe
is deleted. Rationale: compat proven on both validation boxes; native proven broken on one;
one render = one behavior (both-boxes policy); the probe machinery (cache poisoning, ownership-
blocked probes) ceases to exist as a failure class.

### O12 — system-info backend states lie under rootful/rootless store split (halo143)
`installed`/`installable` classification probes `podman images` as the hal0 user (rootless
store) while slots pull/run rootful — separate image stores, so backends read `installable`
on a box whose images are all present rootful. The `.config` chown (9e07c0d3) fixed the probe
ERROR, not the visibility. Fix direction: probe the store slots actually use (via the
privileged seam or `podman --root` pointed at the rootful store). Needs a lane row.

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
- **Phase 1** (deploy + health): deploy ✔, API 200 ✔, `doctor all` clean ✔, golden-path load + GPU inference ✔ (after O8 compat).
- **Phase 2** (quadlet `@`-name verify): **GREEN via O8 compat** — `@`-name accepted, generator converts (`PodmanArgs=`), container Up, health ok, teardown clean (file+unit+container gone).
- **Phase 3** (M5 rehearsal on copy): **GREEN** — 9 slots id-keyed (`<id>.toml`, `<id>/state.json`, `slot_id`+`name`), recorded renames match `hal0-slot@<id>`, idempotent 2nd pass (no-op).
- **Phase 4** (live smoke): complete.
  - 4.1 rename: **GREEN** — running slot refused with reason ("must be offline… unit still name-keyed"); offline rename succeeds, `by-id` shows id+port stable, name changed.
  - 4.2 no-think: **GREEN (supported path)** — `enable_thinking` field + `:8080` normalize → `content:'HELLO_HALO'`, `finish:stop`. Secondary: deprecated `extra_args` raw-flag path mangles JSON quotes (O10).
  - 4.3 doctor bundle: **RED — O9** (admin/client keys leak unmasked).
  - 4.4 system-info: **GREEN** — real GPU (AMD Strix Halo, 116GB VRAM, amdgpu/vulkan), backends `rocmfpx`/`vulkanfpx`.
  - 4.5 doctor vs baseline: **GREEN** — nothing newly red (only known WARNs: Hermes O3, migration O4).

## Phase 5 — report

**Greens (flip held-for-deploy):** clone→privileged, GPU passthrough, podman-run (apparmor fix),
fresh R3 git install, auth, doctor (post O2), slot CRUD, **quadlet `@`-name generation on podman
4.9.3 via compat (O8)**, container run + health + GPU inference, teardown, M5 id-keying rehearsal
(+ idempotence), rename semantics, no-think via supported path, system-info real GPU.

**Reds / fix-forward lanes:** O9 (bundle secret leak — security, one-line regex fix), O3 (hermes
start-timeout), and installer-hygiene lane R4/O6/O7 (apparmor preflight, lock ownership, legacy
static-unit removal). O10 (deprecated extra_args quoting) low-priority. O4 migration on-demand.

**Substrate settled:** live reference lxc105 = podman 4.9.3 / Ubuntu 24.04 → R3 compat path is
correct; podman-5 template refresh is an R5 DEPLOY-row item, not a blocker.

**Box left:** on descar tip `8cbc9902` + local `container.py@a2e04193` compat swap (matches descar
`5adf6e0f`); test slots removed; pristine seeded slots offline. For durability, redeploy from descar
tip (which carries the compat) so the swap isn't a one-off.

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
