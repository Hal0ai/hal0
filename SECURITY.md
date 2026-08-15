# Security Policy

## Supported Versions

hal0 is pre-1.0 and ships from `main`. Security fixes land on the latest
release and the current `main` branch. Older tagged releases are not
back-patched.

| Version        | Supported          |
| -------------- | ------------------ |
| `main` (latest)| :white_check_mark: |
| tagged alphas  | latest only        |

## Bundled agent trust boundary

**Treat the bundled agent as root-equivalent on the box it runs on.**

hal0 ships a bundled agent (Hermes) provisioned with `terminal.backend = local`,
so it can execute arbitrary shell commands on the host. It runs under
`hal0-agent@<instance>.service` as `User=hal0` — the same user `hal0-api.service`
runs as. Two consequences follow, and neither is hypothetical:

- **Same UID means same credentials.** Reading `/proc/<pid>/environ` requires
  `PTRACE_MODE_READ_FSCREDS`, which a same-UID process passes. Yama's
  `kernel.yama.ptrace_scope` does not prevent it — that knob gates
  `PTRACE_MODE_ATTACH`, not same-UID reads. So every secret in the API's
  environment (`HAL0_ADMIN_KEY`, provider API keys, `HF_TOKEN`, the agent's own
  session token) is reachable by the agent regardless of the mode or ownership
  of the file those values were loaded from. This has been verified on a live
  box, not merely reasoned about.
- **The `hal0` account is root-equivalent by design.** The sudo grants hal0
  installs (`hal0-systemctl`, `hal0-agentenv`, `hal0-benchctl`, `hal0-update`)
  are issued to the *user* `hal0`, which the agent also is. Slots run under
  rootful podman, so anything that can author a slot's `[Container]` spec can
  `Volume=`-mount arbitrary host paths — `installer/wrappers/hal0-systemctl`'s
  own "HONEST BOUNDARY" comment block has said so since #1740.

  One honest qualifier: `hal0-agent@.service` sets `NoNewPrivileges=yes`, so a
  process *inside the agent unit* cannot invoke those setuid `sudo` wrappers
  directly. That is a real speed bump, not a boundary — `hal0-api.service` sets
  no such restriction, and the admin key the agent can read out of the API's
  `/proc/<pid>/environ` drives the same privileged paths through the API.

Therefore: **if the agent can be made to run a command — including via prompt
injection from content it reads — it can reach every credential the box holds.**

### What the compensating controls do and do not buy

| Control | Buys | Does not buy |
| --- | --- | --- |
| `User=hal0` (unprivileged, not root) | The agent is not UID 0; kernel-level and other-user data stay out of reach | Any separation from `hal0-api`, which is the same UID |
| systemd sandboxing on the units (`ProtectSystem=strict`, `PrivateTmp`, restricted `ReadWritePaths=`) | Integrity: the agent cannot rewrite `/usr`, `/boot`, or most of `/etc` | Confidentiality of anything the `hal0` user may read, including `/proc` of same-UID processes |
| `NoNewPrivileges=yes` on `hal0-agent@` | The agent's own processes cannot invoke the setuid `sudo` wrappers | Anything about `hal0-api`, which sets no such restriction and is reachable with the admin key the agent can read |
| `0600 root:root` on `/etc/hal0/agents/<instance>.env` | The agent cannot read *another* agent's MCP token | Protection of `api.env`-sourced values, which are already in the API's environment |
| Owner-only `0600` on `/etc/hal0/api.env` | Other local accounts cannot read it | Anything against the agent, which is the owner |

Moving secrets into a `root:root 0600` file loaded by pid1 does **not** close
this: it relocates the value from a file the agent can `cat` to a `/proc` node
the agent can `cat`. The axis that matters is which UID the agent runs as.

### Status: accepted, documented risk for 1.0

This is the shipped default for hal0 1.0 and an accepted risk, disclosed here
rather than papered over. Giving the agent its own system user with no sudoers
grants — the change that actually closes the chain — is tracked for 1.1. The
analysis, the evidence, and the rejected alternatives are in
[ADR-0002 — agent credential isolation](docs/adr/0002-agent-credential-isolation.md)
(proposed in [PR #1880](https://github.com/Hal0ai/hal0/pull/1880)).

`hal0 doctor` reports this posture directly: the **Agent UID split** row warns
when an agent unit resolves to the same `User=` as `hal0-api.service`.

### If you cannot accept it

- Run the box without the bundled agent (do not install/enable `hal0-agent@*`), or
- Do not give the agent a local shell (`terminal.backend` other than `local`), and
- Do not feed the agent untrusted content on a box holding credentials you care
  about, and do not reuse those credentials elsewhere.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report privately via one of:

- GitHub's [private vulnerability reporting](https://github.com/Hal0ai/hal0/security/advisories/new)
  (preferred — **Security → Report a vulnerability**)
- Email: **alexander@awideweb.com**

Please include:

- A description of the issue and its impact
- Steps to reproduce (proof-of-concept if available)
- Affected version / commit
- Any suggested remediation

## What to Expect

- **Acknowledgement** within 5 business days.
- An initial assessment and severity triage shortly after.
- Coordinated disclosure: we'll agree on a timeline and credit you in the
  release notes unless you prefer to remain anonymous.

Thank you for helping keep hal0 and its users safe.
