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

## Revalidation on the fix wave (`c98a7bc3`, both boxes redeployed)

Force-reinstalled venv (same-version 0.9.8 hop), `[slots].network_mode=host` set on 143.

| Item | Result |
|---|---|
| **O17 steward auth** | ✅ `service_identity` in venv (3 sites); `/api/brain/chat` no longer 401 |
| **O18 framing** | ✅ singular `{"message"}` no longer trips `No user query` |
| **Phase 4 read-only** | ✅ mutation refused (`tool 'slot_load' refused … read_only=true`); widen `read_only=false` → `slot_load` executes; reverted |
| **Phase 7 render** | ✅ uniform PodmanArgs, gate-free, both boxes |
| **143 host-net lane** | ✅ via hal0: `Network=host` + `--host 127.0.0.1`, bind `127.0.0.1:8082`, restart **0 netns errors**, LAN-fenced (slot refused, API answers) |
| **O14 `--adopt`** | ✅ unknown-flag Error, help clean, both |
| **O16 uninstall** | ✅ `Uninstalled hermes`, HERMES_HOME gone, no 500 (soft "memory teardown skipped" warning), reinstall clean |
| **O13 perms self-heal** | ◑ `doctor perms --fix` heals the slots **dir** root→hal0 (8/9 paths); does **not** recurse into nested `state.json` (stayed root in aggressive test → slots error until `chown -R`). Dir-level fix works; nested-state gap is a follow-up |
| **Phase 6 executor** | ◑ `board.hermes_executor_registered` logs when `HERMES_DASHBOARD_BASE_URL` set (143); board-card dispatch blocked by O20 |

### New findings this pass
- **O19** — auth posture auto-enables on `0.0.0.0` bind but the **dashboard ships no login UI** → operator locked out of the browser (`authentication required` everywhere, nothing to log in with). Workaround applied on these boxes: `HAL0_REQUIRE_AUTH=0` (trusted-LAN open, matches lxc105). Fix: render a login when `auth_required && tier==anon`, or don't gate the UI shell without a login path.
- **O20** — hermes-gateway **kanban board DB uninitialized** (`no such table: tasks` / `kanban_notify_subs`, watcher errors every tick). **Root cause:** schema init is coupled to executor registration — `kanban_db.connect()` auto-inits on first call, but a gateway that *watches* the board opens `/var/lib/hal0/.hermes/kanban.db` via a raw path before any `connect()`, reading an empty file. Confirmed by 150 (no `HERMES_DASHBOARD_BASE_URL` → executor never registered → 0 tables) vs 143 (executor set → `connect()` ran → 8 tables). **Validated fix:** `init_db(<kanban.db>)` created all 8 tables → watcher errors 0 on both. **Lane fix:** init the board at gateway/hal0 startup (unconditional `init_db`/`connect`), or make the watcher use `connect()` not raw `sqlite3.connect` — decouple board existence from executor registration.
- **steward config note** — fresh-box `[brain_chat] model=""` doesn't route (dispatch 404); needs a configured model + a brain slot with ≥8k ctx (steward system prompt ≈ 7.3k tokens). Also observed `read_only=false` persisted post-redeploy (config not clobbered) — reset to the `true` default.
- **O15 not testable here** — 143 now has python3.13 (deadsnakes), so the uv fallback path doesn't trigger; the `_hal0_subprocess_env` HOME-sanitize fix is code-verified only.

**Closed:** O12 (live), O14, O16, O17, O18, Phase 4, Phase 7, 143 host-net. **Open/follow-up:** O13 nested-state, O19, O20, Phase 5 full liveness (memory write/recall via hermes-agent chat — not driven headlessly).

## Standing policy
Validated on both boxes per policy: 150 = podman-4.9.3 / privileged / AppArmor; 143 = podman-5.7 /
unprivileged. Host-net slot lane still queued for 143 (bridge netns teardown) — Phase 7 was
render/generation only on 143.
</content>
