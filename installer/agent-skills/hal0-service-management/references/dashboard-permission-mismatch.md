# Dashboard permission mismatch (root vs hal0)

## Problem

The Hermes dashboard (`hermes dashboard --host 127.0.0.1 --port 9119`) runs as user `hal0`, but the gateway systemd unit runs as `root`. The gateway creates runtime files (`gateway.lock`, `gateway.pid`, `gateway_state.json`, `config.yaml`, `channel_directory.json`, session files, etc.) owned by `root`. When the dashboard's `/api/status` endpoint tries to open these files, it crashes with a 500 and `PermissionError`.

## Diagnostic signal

`curl http://127.0.0.1:9119/api/status` returns 500. Enabling debug mode (`log_level='debug'` in uvicorn, or `app.debug = True`) reveals the traceback:

```
PermissionError: [Errno 13] Permission denied: '/var/lib/hal0/agents/hermes/gateway.lock'
  File "/var/lib/hal0/venvs/hermes/lib/python3.12/site-packages/gateway/status.py", line 466, in is_gateway_runtime_lock_active
    handle = open(resolved_lock_path, "a+", encoding="utf-8")

  File "/var/lib/hal0/venvs/hermes/lib/python3.12/site-packages/hermes_cli/web_server.py", line 545, in get_status
    gateway_pid = get_running_pid()
```

Full traceback from a live session (2026-06-02):

```
File "gateway/status.py", line 937, in get_running_pid
    lock_active = is_gateway_runtime_lock_active(resolved_lock_path)
File "gateway/status.py", line 466, in is_gateway_runtime_lock_active
    handle = open(resolved_lock_path, "a+", encoding="utf-8")
PermissionError: [Errno 13] Permission denied: '/var/lib/hal0/agents/hermes/gateway.lock'
```

## Root-owned files found (12 total, live example)

```
/var/lib/hal0/agents/hermes/gateway.lock          — gateway runtime lock
/var/lib/hal0/agents/hermes/gateway.pid           — gateway PID file
/var/lib/hal0/agents/hermes/gateway_state.json    — gateway state
/var/lib/hal0/agents/hermes/config.yaml           — main config (also blocks config save)
/var/lib/hal0/agents/hermes/channel_directory.json — platform channel map
/var/lib/hal0/agents/hermes/processes.json        — background process state
/var/lib/hal0/agents/hermes/.skills_prompt_snapshot.json — agent runtime state
/var/lib/hal0/agents/hermes/sessions/session_*.json — 4 session files
```

## Fix

Batch chown everything to the hal0 user:

```bash
find /var/lib/hal0/agents/hermes -user root -exec chown hal0:hal0 {} \;
```

Then restart the dashboard:

```bash
sudo -u hal0 HERMES_HOME=/var/lib/hal0/agents/hermes \
  /var/lib/hal0/venvs/hermes/bin/hermes dashboard \
  --host 127.0.0.1 --port 9119 --tui --no-open --skip-build &
```

## Long-term note

The gateway systemd unit runs as `root`, so it will keep creating root-owned files on startup. This is a structural mismatch — the dashboard needs to read gateway state, but permissions block it. Options to fix permanently:

1. Run the gateway systemd unit as `hal0` (change `User=` in the unit or drop-in)
2. Run the dashboard as `root` (simpler but less secure)
3. Add a post-start `chown` hook in the gateway unit
4. Set SGID on `/var/lib/hal0/agents/hermes/` so all new files inherit the `hal0` group

Until one of those is implemented, the `find - chown` command above is the quick-fix after every gateway restart.
