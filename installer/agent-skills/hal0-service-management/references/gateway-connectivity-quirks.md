# Gateway connectivity quirks — hal0

## Telegram fallback IP mechanism

The gateway auto-discovers Telegram datacenter fallback IPs at startup. When the primary DNS/proxy route to `api.telegram.org` returns a "Bad Gateway" (typical when routing through Traefik/CLIProxyAPI on CT200), the gateway falls back to direct IP connections.

Evidence from gateway.log on hal0 (2026-06-02 22:06):
```
INFO gateway.platforms.telegram: [Telegram] Auto-discovered Telegram fallback IPs: 149.154.166.110
INFO gateway.platforms.telegram: [Telegram] Telegram fallback IPs active: 149.154.166.110
INFO gateway.platforms.telegram: [Telegram] Connected to Telegram (polling mode)
```

This means a "Telegram network error: Bad Gateway" followed by silence (no reconnection log in journalctl, but `tail -f gateway.log` shows success) is normal. The fallback IPs resolved it.

### When it fails
If the fallback IPs also fail, check:
```bash
curl -s --connect-timeout 5 https://149.154.167.91/ -H "Host: api.telegram.org"
```
Firewall rules on the LXC or Proxmox host may block outbound connections to Telegram's IP ranges.

## Gateway log file locations

All paths relative to `/var/lib/hal0/agents/hermes/logs/`:

| File | Owner | Content |
|------|-------|---------|
| `gateway.log` | root | Application output: connection status, platform events, cron ticks, kanban dispatcher |
| `gateway-exit-diag.log` | root | JSON-line lifecycle events per PID: gateway.start, gateway.exit_nonzero, atexit.hook |
| `gateway-shutdown-diag.log` | root | Verbose shutdown/teardown diagnostics from last stop |
| `agent.log` | hal0 | Agent-level messages (model calls, tool use, errors) |
| `errors.log` | hal0 | Stack traces and error details |

**Critical**: `journalctl --user -u hermes-gateway` shows ONLY systemd messages (`Started`, `Stopped`, `Main process exited`). To see whether Telegram actually connected, read `gateway.log`. The journalctl output being silent beyond systemd messages is NORMAL.

## Kanban dispatcher

The kanban dispatcher is embedded in the gateway process and ticks every 60 seconds. Startup line:
```
INFO gateway.run: kanban dispatcher: embedded in gateway (interval=60.0s)
```

When a `kanban` toolset is assigned to a platform (e.g., `telegram: [kanban, ...]` in config.yaml), cards are managed in-process — no separate kanban service to start or monitor.

## Process fd layout (for debugging)

From `/proc/<pid>/fd/` of a healthy gateway:
```
0 → /dev/null            (stdin)
1 → socket:[...]         (stdout → journald socket)
2 → socket:[...]         (stderr → journald socket)
3 → agent.log
13 → gateway.log
14 → state.db
19 → gateway.lock
```

stdout/stderr go to journald (but hermes writes nothing useful there). Application output always goes to fd 13 (gateway.log).
