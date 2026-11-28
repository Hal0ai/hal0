# Dashboard permission errors (files owned by the wrong user)

## Problem

The Hermes dashboard runs inside `hal0-agent@hermes.service`, and the gateway is
`hermes-gateway.service`; both run as the `hal0` user and share the Hermes home,
`/var/lib/hal0/.hermes`. If a file in that home belongs to `root` (for example because someone ran
`hermes` as root against it, or a box was provisioned by an old root-run installer), the dashboard
cannot open it and an endpoint such as `/api/status` fails with a 500 and a `PermissionError`.

## Diagnostic signal

```bash
hal0 doctor logs --unit hal0-agent@hermes -n 200    # look for PermissionError and the path it names
hal0 doctor perms                                    # read-only audit
```

A person with root can read the same journal with `journalctl -u hal0-agent@hermes`.

`hal0 doctor perms` changes nothing unless it is run with `--fix` (which needs root). Its Hermes
section checks the owner of `HERMES_HOME`, `config.yaml`, the Hermes venv, the release tree and the
managed Python, and flags a stray Hermes home under root's account. It does not check the other files inside the
Hermes home, so a clean report does not rule out a single root-owned file there. The dashboard listens on loopback only (`127.0.0.1`, default port 9119, set
per agent type in `src/hal0/cli/agent_shim.py`).

## Fix (the person, as root)

- A single root-owned file the log names: the person fixes that file, for example
  `sudo chown hal0:hal0 <path from the log>`. Name the exact path; do not suggest a `find` over the
  whole Hermes home.
- Drift in the Hermes home directory itself or the other Hermes paths in hal0's ownership table:
  `sudo hal0 doctor perms --fix`. It applies every drifted row in the whole ownership table, not
  only the Hermes rows, and sets the mode as well as the owner and group. It needs root, it skips a
  path that does not exist, and it prompts before changing anything (the person can add `--force`
  to skip the prompt). For Hermes the table covers the `HERMES_HOME` directory (not the files
  inside it), its `plugins` directories, `/var/lib/hal0/hermes` and `/var/lib/hal0/hermes/python`
  (top level only), and the files `provision.json` and `venv.pin` under
  `/var/lib/hal0/state/agents/hermes`. The table has no row for the Hermes venv or for
  `config.yaml`: the audit reports them, but `--fix` cannot change them. The shipped plugin trees
  are covered at every depth, directories only, never the files inside them. So a root-owned file
  inside the venv or inside the Hermes home gets the single-file `chown` above. You do not have
  root: the person runs or approves these commands.
- `sudo hal0 agent reprovision hermes --repair` does not re-own the Hermes home or the contents of
  the venv. Its root part re-owns only the parent directories, the uv trees, a pre-layout venv and
  the plugin trees, and then it runs the provisioning as `hal0`. It forces a fresh release build and
  restarts the agent and the gateway, so tell the person your own session will drop (see
  `../SKILL.md`) if they run it for another reason.

Do not start `hermes dashboard` yourself: `hal0-agent@hermes` owns it.

Afterwards the agent needs a restart to pick the change up: `sudo systemctl restart hal0-agent@hermes`
(unless a `--repair` the person ran for another reason already restarted it).
If root-owned files come back after a restart, tell the person what created them; the units do not.
