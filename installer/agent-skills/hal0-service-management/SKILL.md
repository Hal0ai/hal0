---
name: hal0-service-management
category: homelab-ops
description: Operating and troubleshooting hal0-managed user-level systemd services. Use when a hal0 platform service (hermes-gateway, hal0-agent@*, etc.) is misbehaving — missing env vars, not starting, not connecting to Telegram/Discord, etc. Encodes the structural fact that hal0 provisioners write secrets files but do NOT wire service units to load them.
---

# hal0 service management

Hal0 runs platform services as **user-level systemd units** under the agent's user account (often root's user systemd at `/root/.config/systemd/user/`). The orchestrator's provisioners (e.g. `hermes_provision.py`) write secrets to a canonical env file, but they do **not** add the `EnvironmentFile=` directive to the service unit. The unit must be wired by hand. This is the single most common reason a hal0 service comes up silent.

## When to use this skill

- A hal0 platform service is logged as "started" but its platform adapters (Telegram, Discord, etc.) report "no platforms enabled" / "not configured" at boot.
- `/proc/<pid>/environ` for the service process is missing variables that exist in `/var/lib/hal0/secrets/agents/<service>.env`.
- A token / secret was added or rotated, but the service doesn't pick it up after restart.
- A user-level systemd unit is failing silently (no journal output, no error).
- A `delegate_task` subagent reports "no shell execution tool available" — see the `allow_code_exec` and `max_spawn_depth` pitfalls below.
- A dashboard plugin tab shows "Could not load this plugin's script" — see `references/dashboard-plugin-js-loading.md`.

For the Claude Code web-development handoff pipeline (writing handoff JSON, launching Claude Code with correct flags, turn-limit calibration), see the [`hermes-claude-workflow`](../homelab-ops/hermes-claude-workflow/SKILL.md) skill.

## Structural fact (the core lesson)

```
hal0 provisioner  ──writes──▶  /var/lib/hal0/secrets/agents/<service>.env
                                          │
                                          │  EnvironmentFile=...   ◀── YOU must add this
                                          ▼
service unit  ──sources──▶  process env  ──read by──▶  service code (os.environ)
```

The service code reads tokens from `os.environ` only. Nothing on disk is sourced at startup unless the unit says so. The provisioner writes the file but does not edit the unit — that gap is yours to close.

## Diagnostic procedure

Run these in order. Stop at the first one that explains the problem.

### Critical: gateway logs are FILE-BASED, not journald

The hermes gateway writes its application output to `/var/lib/hal0/agents/hermes/logs/gateway.log` (fd 13 of the process). `journalctl --user -u hermes-gateway` shows ONLY systemd lifecycle messages (`Started`, `Stopped`, `Main process exited`) — NOT the platform connection status, NOT the "Channel directory built" count, NOT the Telegram/Discord connect/disconnect lines. Do NOT use journalctl to diagnose gateway platform issues; read the file.

```bash
tail -50 /var/lib/hal0/agents/hermes/logs/gateway.log
# Look for:
#   "✓ telegram connected" / "Connected to Telegram (polling mode)"
#   "✓ discord connected" / "Connected as <bot>"
#   "Gateway running with N platform(s)"
#   "Channel directory built: N target(s)"
#   "kanban dispatcher: embedded in gateway"
```

The companion log `/var/lib/hal0/agents/hermes/logs/gateway-exit-diag.log` records JSON-line lifecycle events (gateway.start, gateway.exit_nonzero, atexit.hook) for every PID that has ever run. Useful for spotting crash loops across restarts.

1. **Confirm the service is running:**
   ```bash
   systemctl --user status <service>.service
   ```
   If it isn't, fix the unit / deps first. If it is, continue.

2. **Read the gateway application log (NOT journalctl):**
   ```bash
   tail -50 /var/lib/hal0/agents/hermes/logs/gateway.log
   ```
   "No messaging platforms enabled" / "X not configured" / "missing required env" all point at wiring.
   "Telegram network error, scheduling reconnect: Bad Gateway" means DNS/proxy routing failed — the gateway's Telegram fallback IP mechanism usually resolves this on the next retry.
   "No user allowlists configured" means `TELEGRAM_ALLOWED_USERS` / `DISCORD_ALLOWED_USERS` are empty or unset.

3. **Compare disk secrets vs process env** — this is the smoking-gun check:
   ```bash
   # The canonical secrets file (written by hal0 provisioner)
   ls -la /var/lib/hal0/secrets/agents/<service>.env
   sudo cat /var/lib/hal0/secrets/agents/<service>.env  # if owned by another user

   # The process env (what the service actually sees) — shell pipeline
   PID=$(systemctl --user show -p MainPID --value <service>.service)
   sudo tr '\0' '\n' </proc/$PID/environ | grep -E '^[A-Z_]+=' | sort

   # If the shell pipeline returns empty for vars you expect to see,
   # fall back to Python (NUL-byte handling in shell pipelines is unreliable
   # against /proc/<pid>/environ on this host):
   sudo python3 -c "
   import os
   d = open('/proc/$PID/environ','rb').read().split(b'\x00')
   for e in d:
       if b'=' in e and any(k in e for k in (b'TELEGRAM_', b'DISCORD_', b'GATEWAY_', b'HERMES_', b'HAL0_')):
           k,v = e.split(b'=',1); m = v[:4]+b'***'+v[-4:] if len(v)>12 else b'***'
           print(f'{k.decode()} = {m.decode()}  (len={len(v)})')
   "
   ```
   Variables present in the file but missing from `/proc/$PID/environ` are not wired.

4. **Inspect the unit file for an `EnvironmentFile=` directive:**
   ```bash
   systemctl --user cat <service>.service
   ```
   If `EnvironmentFile=` is missing or points to the wrong path, that's the bug.

5. **Verify the secret is still valid** (for token-rotation cases):
   ```bash
   # Telegram
   curl -s "https://api.telegram.org/bot<TOKEN>/getMe"
   # Discord
   curl -s -H "Authorization: Bot <TOKEN>" https://discord.com/api/v10/users/@me
   ```

A reusable wrapper for steps 3–4 lives at [`scripts/check-service-env.sh`](scripts/check-service-env.sh). Run it before editing the unit to confirm the hypothesis, after editing to confirm the fix.

The worked example for the most common case (hermes-gateway platform tokens) is at [`references/hermes-gateway-platform-tokens.md`](references/hermes-gateway-platform-tokens.md).

## Fix procedure

### Preferred: use a drop-in (NOT the main unit)

`hermes_cli` regenerates the main `.service` file on every gateway startup (the `refresh_systemd_unit_if_needed()` call). Any `EnvironmentFile=` added directly to the main unit will be clobbered. The correct pattern is a **drop-in** under `.service.d/`:

1. **Create the drop-in directory and conf file:**
   ```bash
   mkdir -p /root/.config/systemd/user/<service>.service.d
   cat > /root/.config/systemd/user/<service>.service.d/10-hal0-secrets.conf << 'EOF'
   # hal0: inject agent secret vault (bot tokens, allowlists, home channels).
   # Lives in a drop-in — NOT the main unit — because hermes_cli
   # refresh_systemd_unit_if_needed() rewrites the main .service file on every
   # gateway startup and would clobber an EnvironmentFile= added there.
   # Drop-ins are merged by systemd and left untouched by that regeneration.
   [Service]
   EnvironmentFile=/var/lib/hal0/secrets/agents/<service>.env
   EOF
   ```

2. **Reload and restart:**
   ```bash
   systemctl --user daemon-reload
   systemctl --user restart <service>.service
   ```

3. **Verify the drop-in took effect:**
   ```bash
   systemctl --user show <service> -p EnvironmentFiles
   # Should show: EnvironmentFiles=/var/lib/hal0/secrets/agents/<service>.env (ignore_errors=no)
   ```

### Fallback: edit the main unit directly

Only use this if the drop-in pattern isn't possible (legacy unit, read-only drop-in dir, etc.). The unit WILL be overwritten on next gateway startup.

## Access control: platform allowlists

Tokens alone don't decide who can talk to the bot. Three vars per platform, plus one global bypass, gate incoming traffic:

| Var | Effect |
|-----|--------|
| `<PLATFORM>_ALLOWED_USERS` | Comma-separated allowlist of user IDs. Empty = nobody gets through. |
| `<PLATFORM>_HOME_CHANNEL` | Where cron output, notifications, and the "home" delivery target go. Separate from access control. |
| `GATEWAY_ALLOW_ALL_USERS` | `true` **bypasses** the per-platform allowlists entirely. Default-off. Flipping it to `true` is a free-for-all. |

Default-on (provisioner-shipped) state is usually `GATEWAY_ALLOW_ALL_USERS=true`, which is the **opposite of safe** — it silently allows anyone to talk to the bot. The right end state for any internet-reachable bot is:

```bash
# In /var/lib/hal0/secrets/agents/hermes.env
GATEWAY_ALLOW_ALL_USERS=false
DISCORD_ALLOWED_USERS=<your_id>,<friend_id_1>,<friend_id_2>
TELEGRAM_ALLOWED_USERS=<your_telegram_id>
DISCORD_HOME_CHANNEL=<channel_id_where_bot_lives>
```

Editing the allowlist is the same edit-restart cycle as rotating a token — see "Fix procedure" above. The provisioner owns the file, so prefer the provisioner's write path if one exists; otherwise a careful `patch` / `write_file` on the secrets file is fine (don't change ownership/perms — they need to stay `0600 root`).

**Caveat: `DISCORD_ALLOWED_USERS` and `TELEGRAM_ALLOWED_USERS` use different ID formats.** Discord wants snowflake IDs (17–19 digit strings). Telegram wants numeric user IDs (8–10 digit strings). They're not interchangeable — putting a Discord snowflake in `TELEGRAM_ALLOWED_USERS` will silently match nobody. The `Channel directory built: N target(s)` line in `gateway.log` reflects the resolved allowlist; if N=0 after a fix, the values are probably wrong-format rather than missing.

## Pitfalls

- **Don't trust secrets-file mtime as a signal.** A file that hasn't changed in months can still be the source of the bug — what changed is the *unit*, not the file.
- **Don't write to the secrets file by hand** for token rotation if the provisioner has a credential-write path. Use `hal0-admin:provider_credential_write` (or whatever the orchestrator's blessed path is) so the file's ownership/permissions stay correct. Hand-editing can silently break secrets-file consumers.
- **Don't add `EnvironmentFile=` to `/etc/systemd/system/`** — these are **user** units, not system units. The path is `/root/.config/systemd/user/`.
- **`systemctl --user` only works in the right user context.** If you're root, root's user systemd is what hal0 uses, and the unit is at `/root/.config/systemd/user/`. Don't try to manage it from a different user's session.
- **Don't `hermes setup` to fix gateway wiring.** It will overwrite the whole model wiring. Use `hal0 agent bootstrap hermes --repair` (or the equivalent) for surgical fixes.
- **A service can be "active" and "running" while every adapter silently no-ops.** Always check `/proc/$PID/environ`, not just the unit state.
- **User-mode journald persists across reboots, but only for the user that owns it.** If you `journalctl --user` and see nothing, check `loginctl` session state.
- **The `patch` tool can no-op on systemd unit files.** On this host, `patch` on `/root/.config/systemd/user/<svc>.service` has been observed to report `success: true` with a clean diff while the file on disk stays byte-identical to the pre-edit state — the mtime bumps but the content does not. Symptom: gateway log says "No user allowlists configured" / "No messaging platforms enabled" on restart even though the secrets file has the keys and the unit file *looks* right in your `cat`. Workaround: use `write_file` to overwrite the whole unit file atomically, then `daemon-reload` + `restart`. Confirm with `systemctl --user show <unit> -p EnvironmentFiles` that the new directive is registered before trusting the restart.
- **If `tr '\0' '\n' </proc/$PID/environ | grep ...` returns empty for vars you expect to see, fall back to Python.** Shell pipelines against `/proc/<pid>/environ` occasionally lose NUL bytes (buffering, sigpipe, or env-block layout). A two-line Python check is the ground truth: read bytes, split on `b'\x00'`, iterate. Don't trust an empty shell grep as proof the var is missing.
- **`delegate_task` subagents cannot execute shell commands by default.** Even with `toolsets=["terminal","file"]`, subagents will report "no shell execution tool available" or produce `TimeoutError` on MCP calls. The fix: in `/var/lib/hal0/agents/hermes/config.yaml`, set `delegation.allow_code_exec: true` (add the `delegation:` block if absent). After editing, restart: `systemctl --user restart hermes-gateway`. Without this, every subagent is MCP-only — it can call hal0-admin/hal0-memory tools but cannot run `which`, `systemctl`, `cat`, or any shell command.
- **`max_spawn_depth` gates orchestrator subagents.** If `delegation.max_spawn_depth` is `1` (or absent; default), all subagents are forced to leaf — even ones requested with `role="orchestrator"`. Leaf subagents cannot call `delegate_task`, `execute_code`, `memory`, or `clarify`. To enable orchestrator subagents that spawn their own workers, raise `delegation.max_spawn_depth` to `2` or higher in `/var/lib/hal0/agents/hermes/config.yaml`. This is critical for Claude Code swarm workflows that rely on opus-orchestrator subagents delegating to implementation workers.
- **Telegram "Bad Gateway" errors can self-resolve via fallback IPs.** The gateway auto-discovers Telegram fallback IPs (e.g., 149.154.166.110) at startup and bypasses DNS/proxy routing when the primary path fails. Gateway log shows: `Auto-discovered Telegram fallback IPs: 149.154.166.110` → `Connected to Telegram (polling mode)`. If you see "Bad Gateway" followed by a successful connection within the same startup, the fallback IPs kicked in. No manual intervention needed. If the error persists across multiple restarts, check that outbound HTTPS to 149.154.167.* is not firewalled.
- **Dashboard plugin `entry` defaults to `"dist/index.js"` when absent from manifest.json.** If a plugin's React components are built into the main dashboard JS bundle (the common case for bundled plugins like kanban), but the manifest doesn't declare `entry` at all, the dashboard tries to dynamically load a non-existent external JS file → 404 → "Could not load this plugin's script." Do NOT set `"entry": ""` — the frontend loader has no empty-entry guard and will still 404 on `/dashboard-plugins/<name>/`. Instead, either (a) remove the `tab` declaration from the manifest to make the plugin API-only, or (b) upgrade hermes-agent to a version that ships the plugin's JS bundle. See [`references/dashboard-plugin-js-loading.md`](references/dashboard-plugin-js-loading.md) for the full diagnostic procedure and `xI()`/`sv()` internals. The gateway creates runtime files (`gateway.lock`, `gateway.pid`, `gateway_state.json`, `config.yaml`, `channel_directory.json`, session files) owned by root. When the dashboard (running as `hal0`) tries to open them, `/api/status` crashes with a 500 `PermissionError` on `gateway.lock`. The fix: `find /var/lib/hal0/agents/hermes -user root -exec chown hal0:hal0 {} \;`. This is a recurring issue — the gateway will keep creating root-owned files on every restart. See [`references/dashboard-permission-mismatch.md`](references/dashboard-permission-mismatch.md) for the full traceback and long-term fix options.

## Token rotation — separate workflow

Rotating a Telegram or Discord token is a different class of action (and a security-relevant one). Don't fold it into "wiring." See `references/hermes-gateway-platform-tokens.md` for the procedure.

## Files this skill touches

- `/root/.config/systemd/user/*.service` — service units (edit, never write from scratch)
- `/root/.config/systemd/user/*.service.d/*.conf` — drop-in configs (preferred place for EnvironmentFile=)
- `/var/lib/hal0/secrets/agents/*.env` — secrets env files (provisioner-owned; avoid hand-edits)
- `/var/lib/hal0/state/<service>/` — service state, logs, channel directory
- `/var/lib/hal0/agents/hermes/config.yaml` — hermes-agent config; the `delegation.allow_code_exec` key controls whether subagents get shell access
- `/var/lib/hal0/agents/hermes/logs/gateway.log` — **primary diagnostic target**; application output for platform connections, cron, kanban
- `/var/lib/hal0/agents/hermes/logs/gateway-exit-diag.log` — JSON-line lifecycle events per PID; useful for spotting crash loops
- `/var/lib/hal0/agents/hermes/logs/gateway-shutdown-diag.log` — verbose teardown diagnostics from last shutdown

For diagnostic traceback of the dashboard 500 error caused by root-owned gateway files, see [`references/dashboard-permission-mismatch.md`](references/dashboard-permission-mismatch.md).

For understanding the dashboard plugin JS loading system (how plugins load scripts, why `entry: ""` doesn't fix things, the `xI()` / `sv()` internals), see [`references/dashboard-plugin-js-loading.md`](references/dashboard-plugin-js-loading.md).

For detailed quirks of gateway connectivity, see [`references/gateway-connectivity-quirks.md`](references/gateway-connectivity-quirks.md).

For navigating the hal0 source tree (`/opt/hal0/`), API routes, dispatcher, agent provisioner templates, and the Hermes↔hal0 integration flow, see [`references/hal0-codebase-map.md`](references/hal0-codebase-map.md).

## Verification

After any fix, the service should:
1. Show `active (running)` in `systemctl --user status`
2. Log the expected platform adapters in **gateway.log** (not journalctl): `✓ telegram connected` / `✓ discord connected` / `Gateway running with N platform(s)` / `Channel directory built: N target(s)`
3. Have all expected env vars visible in `/proc/<main_pid>/environ`
4. Round-trip a test message via the platform adapter

Don't declare victory on 1+2 alone — 3 is the real proof the wiring is right. Also, the gateway writes NO application output to journald; if `journalctl --user -u hermes-gateway` is silent beyond systemd lifecycle messages, that's normal — read `gateway.log` instead.
