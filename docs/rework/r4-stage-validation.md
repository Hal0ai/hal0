# R4-stage both-boxes validation — results

**Date:** 2026-07-19 · Checkpoint `c91d0cf5` (tag `rework-R4-stage`) · Boxes wiped + fresh-installed.
150 = privileged / Ubuntu 24.04 / podman 4.9.3 · 143 = unprivileged / Ubuntu 26.04 / podman 5.7.
Runbook: `docs/rework/both-boxes-runbook-r4-stage.md`.

## Phase table

| Phase | 150 | 143 | Notes |
|---|---|---|---|
| 0-1 install + health | ✅ | ✅ | fresh install, 200, auth-on |
| 2 O12 rootful seam | ✅ | ✅ | `podman_context:rootful`, backends **installed**, `hal0-podman-ro` + sudoers |
| 3 install_hermes convergence | ✅ | ✅* | run-2 zero-mutation, marker, both plugin trees, key×1 · *143 needed python3.13 (O15) |
| 4 read-only steward | ⚠️ | ⚠️ | `read_only=true` default ✅; behavioral **BLOCKED by O17+O18** |
| 5 plugin liveness | ◑ | ◑ | trees present, no import errors; full provider/memory liveness pending steward |
| 6 HP-executor | ⏸ | ⏸ | needs working steward/board dispatch — deferred behind O17/O18 |
| 7 uniform render | ✅ | ✅ | **PodmanArgs-only, gate REMOVED**, identical on both |
| 8 uninstall gate | ❌ | — | O16 |

## Greens worth calling out
- **O12 rootful introspection** landed clean on both: `podman_context:rootful`, backend states flip
  `installable → installed`, `hal0-podman-ro` helper + pinned sudoers, `.config`/`.local` PermRows retired.
- **Phase 7 uniform render**: R4-stage ships the **gate-free universal `PodmanArgs`** render
  (`_podman_major_version` gone). Identical shape on podman 4.9.3 and 5.7 — the cleaner solution from
  `podman-unprivileged-findings.md` shipped. No `AutoRemove`/native `GroupAdd=`/`SecurityOpt=` keys.
- **install_hermes convergence**: run-2 reports "nothing to do" / "already installed" — zero mutation,
  key not rotated, both plugin trees + `.hal0-managed` marker present.

## Findings (O-series)

### O17 — steward chat dead on auth-enabled boxes  *(MAJOR, fix validated)*
`brain/chat.py::_primary_completion` POSTs to the box's own `/v1/chat/completions` with **no
`Authorization` header**. `/v1` requires a bearer when auth is on → **`primary slot HTTP 401
auth.required`**. Auth-on is the default (non-loopback bind), so the steward (headline R4 feature)
is non-functional out of the box. Operator reproduced it immediately in the dashboard. Brain slot was
loaded — purely the internal-auth gap (same class as O2's CLI fix).
**Validated fix:** attach the box key (`HAL0_ADMIN_KEY`/`HAL0_CLIENT_KEY` from env) to the internal
POST → 401 gone. (A local one-line patch is on 150's `brain/chat.py` for this pass — not laned.)
Real fix should forward the caller's bearer or use the service identity.

### O18 — brain-chat message framing trips the model chat template  *(surfaced after O17)*
With auth fixed, the steward reaches the LLM but the slot returns **500 `Jinja Exception: No user
query found in messages` (`multi_step_tool`, template line 79)**. The brain-chat request shape doesn't
satisfy the model's chat-template user-query guard. Blocks the read-only behavioral test. Needs the
brain-chat payload ↔ template contract reconciled (repro: `POST /api/brain/chat {"message":"…"}`
against a qwen3.5 brain slot).

### O16 — uninstall leaves HERMES_HOME  *(marker-gate regression)*
`hal0 agent uninstall hermes` → **HTTP 500**, `/var/lib/hal0/.hermes` remains. The exact marker-gate
this checkpoint claims to fix. (Traceback not captured in-window; CLI surface: `DELETE
/api/agents/hermes → 500`.)

### O15 — hermes provision uv-fallback HOME leak  *(py3.14-only hosts)*
On Ubuntu 26.04 (Python 3.14 only, out of hermes range) the provision takes the uv fallback for
Python 3.13, but runs uv as `hal0` with `HOME=/root` → `failed to open /root/uv.toml: Permission
denied` → `bootstrap failed`. 150 (Python 3.12 in range) uses system+pip, unaffected.
**Unblock used:** `python3.13` from deadsnakes (`3.13.14-1+resolute1`) → provision uses system
interpreter, no uv. Real fix: reset `HOME` (or set `UV_*`) when the uv step drops to `hal0`.

### O14 — `--adopt` not retired
`hal0 agent install --help` still shows `--adopt  Hermes only: capture an existing (foreign)…`.
Runbook's "retired-path negative" (adopt/foreign detection deleted) does not hold in `c91d0cf5`.

### O13 — fresh install leaves `/var/lib/hal0/slots` root-owned
API runs as `hal0`; the slots tree + `state.json` are `root:root` after install → 6 slots degrade to
`error` (O1 code degrades gracefully). **`doctor perms --fix` does not cover the slots tree** →
not self-healing; needs a manual `chown -R hal0:hal0 /var/lib/hal0/slots`. Both boxes.

## Standing policy
Validated on both boxes per policy: 150 = podman-4.9.3 / privileged / AppArmor; 143 = podman-5.7 /
unprivileged. Host-net slot lane still queued for 143 (bridge netns teardown) — Phase 7 was
render/generation only on 143.
</content>
