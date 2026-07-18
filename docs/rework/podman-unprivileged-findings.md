# Podman on the hal0 slot substrate — findings & cleaner solutions

**Date:** 2026-07-18 · Boxes: **150** (privileged, Ubuntu 24.04, podman 4.9.3) and
**143** (unprivileged, Ubuntu 26.04, podman 5.7.0). Both run the R3 quadlet slot runtime.

This doc corrects and extends the halo150/halo143 O8 findings and records two cleaner fixes,
one of which was validated end-to-end on 143.

---

## Correction to the O8 "version gate" model

The R3 renderer emits, for the container quadlet, keys including `AutoRemove=yes`,
`GroupAdd=`, and `SecurityOpt=`. The O8 gate (`5adf6e0f`) assumed these are "native podman-5
quadlet keys" and only need `PodmanArgs=` translation below podman 5.0. **That is wrong.**
Measured against the actual quadlet generators:

| Key | podman 4.9.3 (150) | podman 5.7.0 (143) | Real quadlet key? |
|---|---|---|---|
| `AutoRemove` | rejected | **rejected** | **No** — not a quadlet key on any tested version (only `AutoUpdate` exists) |
| `SecurityOpt` | rejected | **rejected** | **No** — the quadlet key is `SecurityLabel`; `--security-opt` must go via `PodmanArgs` |
| `GroupAdd` | rejected | accepted | Yes on ≥5.x only |

Consequence: the gate's **"native branch" never actually generates a unit on 143** — it emits
`AutoRemove` + `SecurityOpt`, the generator rejects them, no `hal0-slot@…service` is produced, and
the slot lands in `error`. An earlier note that the native branch was "proven on 143" was reading
the rendered `.container` file, not a generated unit — it was incorrect. The **compat render is the
only one that works on either box**, because it routes group-add **and** security-opt through
`PodmanArgs=` (valid on 4.x and 5.x) and drops `AutoRemove` (invalid everywhere).

**Cleanest renderer fix:** stop version-gating these three keys. Always render group-add and
security-opt via `PodmanArgs=`; never emit `AutoRemove` (quadlet lifecycle is systemd's job, not
`--rm`). `GroupAdd` *could* be emitted natively on ≥5.x, but since `PodmanArgs=--group-add` works on
both, a single universal `PodmanArgs` path removes the gate — and its whole probe-version dependency
(the `86589fd1` cache bug, the `.config`-ownership probe fragility) — entirely. If a native
`GroupAdd`/`SecurityLabel` path is still wanted for ≥5.x cleanliness, gate **only** those and still
never emit `AutoRemove`.

---

## Issue 1 — bridge netns teardown fails on unprivileged podman LXC

**Symptom:** slot unit fails on stop/restart with
`netavark: open /run/user/0/netns/…: No such file or directory`; with `AutoRemove`/`--rm` the crashed
container is wiped, hiding logs.

**Root cause:** rootful podman as mapped-uid-0 inside an *unprivileged* LXC uses a rootless-style
netns under `/run/user/0`, and the unprivileged CT can't fully manipulate the host net stack, so
netavark's bridge netns setup/**teardown** fails. Documented, known limitation (ProxmoxVE #12566:
"only remedy is reboot"; unprivileged + podman + bridge is "fundamentally problematic"). The
container itself runs fine — a manual `podman run` on 143 loaded the model and served; only the
bridge netns lifecycle breaks.

**Clean fix — `Network=host` + loopback bind (validated on 143):** host networking skips netns
creation entirely → netavark is never invoked → no teardown → the error cannot occur (podman docs;
"most reliable choice inside unprivileged LXC"). hal0's renderer already supports
`Network={network_mode}` and skips `PublishPort` under host net.

**LAN-fence coupling (security-critical, two inseparable lines):** today the fence that keeps raw
slot ports off the LAN is the `PublishPort=127.0.0.1:PORT:PORT` publish, not the bind — the slot
Exec binds `--host 0.0.0.0` inside its netns. Under `Network=host` there is no publish, so
`--host 0.0.0.0` would bind the CT's LAN IP and expose the unauthenticated slot port. The bind must
become the fence: render **`Network=host` *and* `--host 127.0.0.1`** together. hal0-api, sharing the
CT netns, still reaches the slot at `127.0.0.1:PORT`; dispatch is unchanged.

### Validation (143, podman 5.7, unprivileged)
A hand-authored unit (`PodmanArgs` for group-add + security-opt, no `AutoRemove`, `Network=host`,
`Exec … --host 127.0.0.1 --port 8095`):
- generates + runs; `ss` shows bind `127.0.0.1:8095` (loopback-only);
- **restart is clean — 0 netns/netavark errors** (host-net removes the teardown path);
- from another LAN host (thinMint): `curl 10.0.1.143:8095` → **connection refused**;
  `curl 10.0.1.143:8080/api/health` → **200 `{"status":"ok"}`**.

So host-net fences the raw slot to loopback while hal0's API stays LAN-open — identical exposure to
today, minus the netns fragility (and minus NAT/rootlessport overhead).

**Renderer change (to lane):** substrate-gate `Network=host` + flip the slot bind to `127.0.0.1`
(coupled). Gate on privilege/netns capability, not podman version.

---

## Issue 2 — system-info store split + probe fragility (laned as O12)

**Root cause:** slots run **rootful** (quadlets in `/etc/containers/systemd/`, root's image store),
but hal0-api runs as `hal0` and its own podman calls (`--version` gate probe, `podman images` for
backend-installed states) use **hal0-rootless** — a separate store/context. "sudo podman and podman
maintain separate image stores." Hence backends read `installable`, and the version probe was
fragile on `~hal0/.config` ownership.

**Clean fix (laned, O12):** move all hal0-api podman introspection to the **rootful `sudo -n podman`
context the slots actually run in** (narrow read-only sudoers grants; honest fallback to rootless
when sudo is denied). This fixes the store visibility *and* the version-gate fragility in one move,
and retires the `.config`/`.local` ownership PermRows from `9e07c0d3` (lock-file rows kept).
Alternative considered and rejected as messier: `additionalimagestores` pointing rootless at the
rootful store (permission/precedence gotchas; images show as `nobody`).

---

## Standing test policy
Deploy-affecting changes are validated on **both** boxes: 150 = podman-4.9.3 / privileged / AppArmor
path; 143 = podman-5.7 / unprivileged / native-key + host-net path. Between them they cover every
branch of the renderer and installer.

## Sources
- ProxmoxVE #12566 — unprivileged-LXC podman netns failure: https://github.com/community-scripts/ProxmoxVE/issues/12566
- podman troubleshooting (XDG_RUNTIME_DIR / rootless netns): https://github.com/containers/podman/blob/main/troubleshooting.md
- podman-systemd.unit (quadlet keys; `AutoUpdate`, `SecurityLabel`, `GroupAdd`, `Network`): https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html
- podman basic networking (host mode skips netns/netavark): https://github.com/containers/podman/blob/main/docs/tutorials/basic_networking.md
- Red Hat — additional image stores: https://www.redhat.com/en/blog/image-stores-podman
</content>
