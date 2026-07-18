# halo (LXC 150) — R3 deploy issues report

**Box:** privileged LXC 150, hostname `halo`, 10.0.1.150 (cloned from `hal0-rc`/120)
**Target:** hal0 R3 = commit `ab3e88f3` (v0.9.8), fresh git install
**Date:** 2026-07-18
**Reference:** `docs/rework/halo143-runbook-r3.md` (written for .143; run against .150)

---

## Summary

Clone + privileged conversion + fresh R3 git install **succeeded** (`hal0 0.9.8`, API 200).
Six setup blockers hit and resolved along the way; **five issues remain open**, one of
which (slot `agent` state.json EPERM) is a real blocker for runbook Phases 2–4.

---

## Resolved

| # | Issue | Root cause | Fix |
|---|-------|-----------|-----|
| R1 | Unprivileged→privileged not clonable directly | `pct clone` preserves `unprivileged:1`; rootfs UIDs shifted +100000 | Cloned already-privileged `hal0-rc` (120) instead of 143; no vzdump/remap needed |
| R2 | `rework-R3` tag / `ab3e88f3` absent from local + NFS clones | Tag never pushed to GitHub; NFS repo has no GitHub creds | Fetched `ab3e88f3` from `origin/main` on thinMint (gh auth), shipped via `git bundle` |
| R3 | `podman` missing on box | `hal0-rc` template shipped docker only (`docker.io 29.1.3`) | `apt-get install podman` → 4.9.3 (≥4.4 for quadlet generator) |
| R4 | `podman run` failed: `install profile containers-default apparmor: exit status 243` | LXC is `apparmor.profile: unconfined` → container cannot load podman's default AppArmor profile | `/etc/containers/containers.conf` → `[containers] apparmor_profile = "unconfined"` (same class as langfuse-podman fix) |
| R5 | Stale alpha installs (`/opt/hal0` broken, `/usr/lib/hal0/current→0.3.1-alpha.1`) | Template baked an old, half-broken install | Moved aside to `/root/*.pre-r3`; fresh FHS install of v0.9.8 |
| R6 | Auth required but no key → total lockout (all endpoints 401) | `require_auth_enabled()` posture derives from **non-loopback bind** (`HAL0_BIND_HOST=0.0.0.0`), NOT `hal0.toml auth_enabled=false` | Set `HAL0_ADMIN_KEY`/`HAL0_CLIENT_KEY` in `/etc/hal0/api.env`; keys saved to `/root/.hal0-{admin,client}-key` |

---

## Open

### O1 — Slot `agent` state.json permission denied  *(BLOCKER)*
`GET /api/slots` (valid client key) → **400**:
```
slot.config_error: failed to read state.json at
/var/lib/hal0/slots/agent/state.json: [Errno 13] Permission denied
```
One unreadable slot fails the whole `/api/slots` enumeration → slot manager reports
"not wired" → cascades to doctor Runners FAIL + Slot ports / Capability slots WARN.
**Blocks runbook Phase 2 (quadlet `@`-name verify) and Phase 4 (rename/quoting smoke).**
Investigate: ownership/ACL/immutable bit on `/var/lib/hal0/slots/agent/`, and whether
`hal0-api` actually runs as root vs the `hal0` system user (README claims root).

### O2 — `hal0 doctor all` probes anonymously on an auth-on box
Even with `HAL0_ADMIN_KEY`/`HAL0_CLIENT_KEY` exported, doctor reports
Capability slots / Model store / Slot ports / Hindsight / OpenWebUI / Hermes as
"unreachable" — but the same endpoints return **200** with a bearer token
(`/api/models` 200, `/api/system-info` 200). Doctor does not authenticate its own
probes, so it is misleading as the Phase-1 health signal on any auth-enabled box.
Either a doctor bug or it needs a documented key-passing mechanism.

### O3 — `hal0-agent@hermes` stuck `activating`
Install warned "hal0-agent@hermes not yet active" (D-Bus connection reset mid-install);
`systemctl is-active hal0-agent@hermes` = `activating` (never reaches active).
Needs `journalctl -u hal0-agent@hermes -n 40`.

### O4 — model-layout migration pending (1303 links)
Fresh install pointed at `/mnt/ai-models` (617G of pre-existing models). Registry is
fresh and doesn't know them until `hal0 migrate model-layout --apply` is run. Expected,
but existing models are invisible until applied.

### O5 — R3 provenance ambiguity
`rework-R3` tag is **not on GitHub** — only the merge commit `ab3e88f3` on `main`.
Deployed by commit SHA. "What did we validate" is unclear until the tag is pushed.

---

## Runbook status

- **Phase 0** (snapshot/baseline): done — snapshot `/root/halo150-pre-r3-*.tgz`, podman 4.9.3.
- **Phase 1** (deploy R3 + health): deploy ✔, API health 200 ✔, `hal0 --version` 0.9.8 ✔.
  Golden-path round-trip **blocked** by O1 (slots endpoint 400) and O2 (doctor signal).
- **Phase 2** (quadlet `@`-name verify): not reached — needs a loadable slot (blocked by O1).
- **Phase 3** (M5 rehearsal on copy): not started.
- **Phase 4** (live smoke): not reached.

## Environment notes (accepted, not issues)
- Privileged + `apparmor unconfined` ⇒ container root ≈ host root (you requested privileged).
- Privileged maps container-root → real root(0); NFS/ai-models ownership behavior differs
  from the unprivileged box — revisit per the ai-models access model.
- Mounts (bind, local zfs — resilient, no network dep): `/mnt/ai-models`, `/mnt/repos`,
  `/mnt/projects`. thin-mint SSH key installed; `ssh root@10.0.1.150 -i ~/.ssh/thin-mint` works.
</content>
</invoke>
