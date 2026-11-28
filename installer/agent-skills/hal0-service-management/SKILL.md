---
name: hal0-service-management
category: homelab-ops
description: Operating and troubleshooting the systemd services hal0 installs for its bundled agent (hal0-agent@hermes and hermes-gateway). Use when one is misbehaving — not starting, "no messaging platforms enabled", a Telegram/Discord token that is not picked up, a dashboard that errors. Encodes where the units, logs, config and secrets live on an installed box, and which actions belong to the person, not to you.
---

# hal0 service management

> **Requires the terminal tool.** hal0 ships Hermes with no terminal tool unless the
> operator opted in (`hal0 agent install hermes --terminal-tool`). Without it this skill
> cannot run its commands — say so plainly instead of improvising.

This describes a standard install. A `--dev` install has no units, so none of the unit commands
below apply to it.

hal0 runs the bundled agent as two **system** units (not user units), both as the `hal0`
user. hal0 writes and rewrites both units itself. You run as `hal0` too, with no root:
you can read state and logs, but changing a unit, restarting a service or touching a secrets
file is the person's job (`sudo`). Say what you found and what to run; do not work around it.

**Restarting either unit ends your own session.** You are the agent: a restart of
`hal0-agent@hermes` stops you, and a restart of `hermes-gateway` drops your messaging
platforms. Before you ask the person to run any restart, reprovision or repair below, tell them
that your session will drop and when; never run one yourself.

## The units

| Unit | What it is | Runs as |
|------|------------|---------|
| `hal0-agent@hermes.service` | The Hermes agent and its dashboard (loopback only, default port 9119). Template `hal0-agent@.service` plus the `hal0-agent@hermes.service.d/10-hal0.conf` drop-in. | `hal0` |
| `hermes-gateway.service` | The Hermes gateway: Telegram, Discord and other messaging platforms, the API server, cron. At `/etc/systemd/system/hermes-gateway.service`, rendered by hal0 from its own template. | `hal0` |

The gateway's platform tokens come from two files. The first is the secrets vault
`/var/lib/hal0/secrets/agents/hermes.env` (`root:root`, mode 0600). hal0 wires it into the unit
through a drop-in, `/etc/systemd/system/hermes-gateway.service.d/10-hal0-secrets.conf`, which holds
one line, `EnvironmentFile=-/var/lib/hal0/secrets/agents/hermes.env`. The leading `-` makes the
vault optional, so a box with no tokens yet starts an idle gateway. The second is
`$HERMES_HOME/.env` (`/var/lib/hal0/.hermes/.env`): Hermes loads it when the gateway starts and it
**overrides** the vault. A stale token in `.env` therefore beats a rotated token in the vault.
**Do not read, print or write any of these: the vault, any `.env` file (including
`$HERMES_HOME/.env`), `/etc/hal0/agents/*.env`, or `/etc/hal0/api.env`.** Several hold live tokens
or keys. To learn whether a credential is set without seeing it, see the token reference.

## When to use this skill

- The gateway is "active" but its platforms report "no messaging platforms enabled" or "not configured".
- A Telegram or Discord token was added or rotated and the gateway does not pick it up.
- `hal0-agent@hermes` or `hermes-gateway` is failing or restart-looping.
- A dashboard plugin tab shows "Could not load this plugin's script" — see `references/dashboard-plugin-js-loading.md`.
- The Hermes dashboard returns 500 on a permission error — see `references/dashboard-permission-mismatch.md`.

## Diagnostic procedure

Run these in order. Stop at the first one that explains the problem.

1. **Is each unit running?**
   ```bash
   systemctl status hal0-agent@hermes
   systemctl status hermes-gateway
   hal0 agent status hermes
   ```
   `hal0 agent status hermes` does **not** report the service state. It prints the last
   provisioning report (one row per provisioning phase, from `provision.json`).
   `hal0 agent list` shows each installed agent's status.

2. **Read the service logs.** Both units write stdout and stderr to journald. You are not in the
   `systemd-journal` group (the installer does not add `hal0` to it), so `journalctl` is denied
   to you; `hal0 doctor logs` reads the same journal through the hal0 API:
   ```bash
   hal0 doctor logs --unit hermes-gateway -n 100
   hal0 doctor logs --unit hal0-agent@hermes -n 100
   ```
   A person with root can run `journalctl -u hermes-gateway -n 100 --no-pager` (and the same for
   `hal0-agent@hermes`).
   `hal0 agent log hermes` is **not** the service log. It prints the provisioning transcripts
   (`/var/lib/hal0/state/agents/hermes/provision-logs/`); `--phase job-<id>` names one repair transcript.
   Hermes also has a `logs/` directory in its home; hal0 creates it but does not document the file
   names in it. If the journal is empty or denied, ask the person to run the command.

3. **Read the platform lines.** Find the lines for the platform at start-up. "requirements not
   met" for a platform means its SDK is not in the live Hermes release, so it never connects (a
   release is built with the SDK of every platform whose credentials are set when it is built).
   Fix: ask the person to run `sudo hal0 agent reprovision hermes`. It rebuilds the release by
   itself when it finds a reason to, including a configured platform's SDK missing from the live
   release. Use `--repair` only if that does not help; see "Repair" below for its cost. The next
   `hal0 update` also folds a missing platform SDK into a fresh release.

4. **Is the vault wired?** You cannot read the vault. Ask the person to confirm the drop-in is in
   effect:
   ```bash
   sudo systemctl show hermes-gateway -p EnvironmentFiles
   ```
   It should name `/var/lib/hal0/secrets/agents/hermes.env`. If it does not, the drop-in is missing:
   `sudo hal0 agent reprovision hermes` (or `sudo hal0 agent install hermes`) writes it again and
   reloads systemd.

5. **Is the token still valid?** Only the person can check this; do not ask for the token in chat.
   If a rotation did not take, ask the person whether `/var/lib/hal0/.hermes/.env` carries a token
   for that platform: it overrides the vault, so a stale value there wins.

The gateway token procedure is in
[`references/hermes-gateway-platform-tokens.md`](references/hermes-gateway-platform-tokens.md).

## Fix procedure

You do not edit units. What the person runs:

- **Restart the gateway:** `sudo systemctl restart hermes-gateway`. Not `hermes gateway restart` or
  `hermes gateway start`: those regenerate the unit and pin it to one release's paths.
  hal0-api also restarts it: `POST /api/agents/hermes/gateway/restart`.
- **Restart the agent:** `sudo systemctl restart hal0-agent@hermes`, or `POST /api/agents/hermes/restart`.
- **Reconverge a drifted install** (missing tools, stale unit; for wrong file ownership see `references/dashboard-permission-mismatch.md`): first
  `sudo hal0 agent reprovision hermes`. It re-runs each provisioning phase whose result drifted,
  and builds a new Hermes release when it finds a reason to (for example a configured platform's
  SDK missing from the live release). Never `hermes setup`: it overwrites the model wiring.
- **Repair** (`sudo hal0 agent reprovision hermes --repair`) is the heavier step; ask for it only
  when plain `reprovision` did not fix it. It forces every provisioning phase again and a fresh
  release build, which needs the network (add `--offline` to refuse the build) and takes minutes.
  It then activates the new release, which **restarts both the agent and the gateway**: your own
  session drops. It re-applies hal0's config keys to `config.yaml`; it does not touch persona
  files (only `sudo hal0 agent install hermes --reset-personas` overwrites those). Tell the person
  this before they run it.
- **A unit written outside hal0** (for example by `hermes gateway install`): `sudo hal0 agent install hermes`
  lays down hal0's unit again, and `hal0 update` rewrites a gateway launcher that is not hal0's.
- **A setting for the agent's environment:** `sudo systemctl edit hal0-agent@hermes`, with `Environment=KEY=value`
  under `[Service]` for a key hal0's generated `/etc/hal0/agents/hermes.env` does not set, or an
  `EnvironmentFile=-/etc/hal0/agents/hermes.override.env` for one it does set. hal0 rewrites
  `hermes.env` on every reprovision, so nothing written in it survives.
- **A setting in Hermes's `config.yaml`:** reprovision sets hal0's own keys in it, then deep-merges
  the person's `/etc/hal0/agents/hermes/overrides.yaml` over it. Changes that must persist go there; ask
  the person to make them.

## Pitfalls

- **Do not hand-edit `hermes-gateway.service` or its drop-in.** hal0 owns both. The drop-in says
  "DO NOT EDIT BY HAND" and is rewritten when its content differs.
- **Do not `hermes gateway install|start|restart`.** They regenerate the unit; the person restarts
  with `systemctl` (see above).
- **Two pollers on one token** make Telegram answer HTTP 409. If another gateway (a hand-installed
  system unit, or a user unit under another account) is live, `hal0 agent install hermes` refuses to
  enable its own and prints how to stop the other one.
- **A gateway the person turned off stays off.** hal0 never enables, starts or stops a disabled or
  masked gateway during `hal0 update` or the installer. `sudo systemctl enable --now hermes-gateway` is the person's call.
- **A service can be "active" while every platform is idle.** Read the platform lines in the journal;
  the unit state is not the proof.
- **Never put a token in chat, in memory, or in a file you write.**
- **A delegated subagent that reports "no shell execution tool available"** is seeing the
  default: hal0 ships Hermes with the terminal tool off. Only the person can turn it on, with
  `sudo hal0 agent install hermes --terminal-tool` (it runs commands as `hal0`, which is
  root-equivalent on the box, so say so). Delegated children run on the `agent` slot unless the
  person sets `delegation_slot` in the `[hermes]` table of `/etc/hal0/agents/hermes.toml`. For any
  other subagent limit (such as a spawn depth), tell the person what it reported; do not edit
  `config.yaml` yourself.

## Files this skill touches (read-only for you)

- `/etc/systemd/system/hermes-gateway.service` and `hermes-gateway.service.d/10-hal0-secrets.conf` — gateway unit and secrets drop-in (hal0-owned).
- `hal0-agent@.service` and `hal0-agent@hermes.service.d/10-hal0.conf` — agent unit and its Hermes drop-in (hal0-owned; installed under the systemd unit directory).
- `/var/lib/hal0/.hermes/` — Hermes's home (`HERMES_HOME`): `config.yaml`, `logs/`, `sessions/`, `skills/`, `plugins/`.
- `/var/lib/hal0/state/agents/hermes/provision.json` and `provision-logs/` — what `hal0 agent status` and `hal0 agent log` print.
- `/etc/hal0/agents/hermes/overrides.yaml` — the person's `config.yaml` overrides.
- Not yours: `/var/lib/hal0/secrets/agents/hermes.env` (the vault), `/var/lib/hal0/.hermes/.env`,
  `/etc/hal0/agents/hermes.env` (the driver env) and `/etc/hal0/api.env`.

For the dashboard 500 caused by root-owned files, see
[`references/dashboard-permission-mismatch.md`](references/dashboard-permission-mismatch.md).
For the dashboard plugin loader, see
[`references/dashboard-plugin-js-loading.md`](references/dashboard-plugin-js-loading.md) and
[`references/dashboard-plugin-entry-mismatch.md`](references/dashboard-plugin-entry-mismatch.md).
For gateway connectivity, see [`references/gateway-connectivity-quirks.md`](references/gateway-connectivity-quirks.md).
For the hal0-admin tool quirks, see [`references/mcp-tool-quirks.md`](references/mcp-tool-quirks.md).
For the source tree, see [`references/hal0-codebase-map.md`](references/hal0-codebase-map.md).

## Verification

After a fix, the person should see:

1. `systemctl status hermes-gateway` and `systemctl status hal0-agent@hermes` both `active (running)`.
2. The platform's connect line in `hal0 doctor logs --unit hermes-gateway`, with no "requirements not met" for it.
3. A test message answered on the platform.

Do not stop at 1: a running unit with an idle platform is the common failure.
