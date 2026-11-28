# Dashboard permission errors (files owned by the wrong user)

## Problem

The Hermes dashboard runs inside `hal0-agent@hermes.service`, and the gateway is
`hermes-gateway.service`; both run as the `hal0` user and share the Hermes home,
`/var/lib/hal0/.hermes`. If a file in that home belongs to `root` (for example because someone ran
`hermes` as root against it, or a box was provisioned by an old root-run installer), the dashboard
cannot open it and an endpoint such as `/api/status` fails with a 500 and a `PermissionError`.

## Diagnostic signal

```bash
journalctl -u hal0-agent@hermes -n 100 --no-pager | grep -i PermissionError
hal0 doctor perms                      # reports Hermes ownership drift
```

`hal0 doctor perms` audits hal0's ownership table, which includes `HERMES_HOME` and the per-agent
sub-homes, and reports drift; `--fix` applies it. The dashboard listens on loopback only
(`127.0.0.1`, default port 9119, set per agent type in `src/hal0/cli/agent_shim.py`).

## Fix (the person, as root)

```bash
sudo hal0 agent reprovision hermes --repair
```

`--repair`, run as root, reconciles ownership of `HERMES_HOME`, the per-agent sub-homes and the
venv to `hal0:hal0` (`reconcile_ownership_on_repair` in `src/hal0/agents/hermes_provision.py`; it
is skipped when not run as root). Do not `chown` by hand with a `find` over the Hermes home, and
do not start `hermes dashboard` yourself: `hal0-agent@hermes` owns it.

Then ask the person to restart the agent: `sudo systemctl restart hal0-agent@hermes`.
If root-owned files come back after a restart, tell the person what created them; the units do not.
