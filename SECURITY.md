# Security Policy

## Supported Versions

Security fixes land on the latest release and the current `main` branch.
Older tagged releases are not back-patched.

| Version        | Supported          |
| -------------- | ------------------ |
| `main` (latest)| :white_check_mark: |
| tagged release candidates | latest only |

## Bundled agent trust boundary

**Treat the bundled agent as root-equivalent on the box it runs on.**

hal0 ships a bundled agent (Hermes). Its terminal tool — the one that executes
arbitrary shell commands on the host — is **off by default** as of v1.0 and is
an explicit opt-in (`sudo hal0 agent install hermes --terminal-tool`, or
`HAL0_HERMES_TERMINAL=1` for an unattended install). A piped, headless or
non-interactive install never enables it. `hal0 agent status hermes` prints the
current posture.

Turning it on makes the agent root-equivalent, and the rest of this section
describes that state. Even with the terminal tool off, the agent runs under
`hal0-agent@<instance>.service` as `User=hal0` — the same user `hal0-api.service`
runs as. Two consequences follow, and neither is hypothetical:

- **Same UID means same credentials.** Reading `/proc/<pid>/environ` requires
  `PTRACE_MODE_READ_FSCREDS`, which a same-UID process passes. Yama's
  `kernel.yama.ptrace_scope` does not prevent it — that knob gates
  `PTRACE_MODE_ATTACH`, not same-UID reads. So every secret in the API's
  environment (`HAL0_ADMIN_KEY`, provider API keys, `HF_TOKEN`) is reachable by
  the agent regardless of the mode or ownership of the file those values were
  loaded from. The chat-proxy session secret is not an API env var — it is a
  file at `/var/lib/hal0/agents/secret.bin` (`0600`, owned by `hal0`) — but the
  same same-UID logic applies to it too: the agent is the file's owner, so it
  can simply read the file directly, no `/proc` trick required. This has been
  verified on a live box, not merely reasoned about.
- **The `hal0` account is root-equivalent by design.** The sudo grants hal0
  installs (`hal0-systemctl`, `hal0-agentenv`, `hal0-benchctl`, `hal0-podman-ro`,
  `hal0-update`)
  are issued to the *user* `hal0`, which the agent also is. Slots run under
  rootful podman, so anything that can author a slot's `[Container]` spec can
  `Volume=`-mount arbitrary host paths — `installer/wrappers/hal0-systemctl`'s
  own "HONEST BOUNDARY" comment block has said so since #1740. The process
  *inside* the `img` container is the `hal0` account rather than root since
  #3066 (`[Container] User=`, which the wrapper accepts for that one value
  only), so a ComfyUI custom node or workflow that escapes lands on `hal0`;
  that is a smaller blast radius for a bug, not a boundary — `hal0` is
  root-equivalent by the paragraph above.

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
| `User=hal0` (unprivileged, not root) | The agent is not UID 0; kernel-level and other-user data stay out of reach | Any separation from `hal0-api`, which is the same UID and holds `systemd-journal` |
| systemd sandboxing on `hal0-agent@` (`ProtectSystem=strict`, `PrivateTmp`, restricted `ReadWritePaths=`) | Integrity: the agent cannot rewrite `/usr`, `/boot`, or most of `/etc` | Confidentiality of anything the `hal0` user may read, including `/proc` of same-UID processes; `hal0-api.service` sets none of these directives |
| `NoNewPrivileges=yes` on `hal0-agent@` | The agent's own processes cannot invoke the setuid `sudo` wrappers | Anything about `hal0-api`, which sets no such restriction and is reachable with the admin key the agent can read |
| `0600 root:root` on `/etc/hal0/agents/<instance>.env` | The agent cannot *open another instance's file*, and cannot create, replace or unlink one | Confidentiality of that instance's `HAL0_MCP_TOKEN`: with two agent instances running as the same user, either can read the other's `/proc/<pid>/environ`, so the file mode does not isolate the token itself |
| Owner-only `0600` on `/etc/hal0/api.env` | Other local accounts cannot read it | Anything against the agent, which is the owner |

Moving secrets into a `root:root 0600` file loaded by pid1 does **not** close
this: it relocates the value from a file the agent can `cat` to a `/proc` node
the agent can `cat`. The axis that matters is which UID the agent runs as.

### Status: accepted, documented risk for 1.0

This is the shipped default for hal0 1.0 and an accepted risk, disclosed here
rather than papered over. Giving the agent its own system user with no sudoers
grants is tracked for 1.1, but a new `User=` alone does not close the chain:
`/etc/hal0` is `hal0:hal0 2775` (setgid, see
`hal0.install.perms.ownership_table()`), so a split agent left in the shared
`hal0` *group* could still create or replace `hal0.toml`/`api.env` there —
and an unreadable or malformed `hal0.toml` makes the API's own auth gate
(`hal0.api.auth._config_require_auth()`) treat `require_auth` as unset and
fall back to its OFF default, reopening the exact privileged-API path this
document warns about. Closing the chain for 1.1 means the UID split *and*
dropping the agent out of the `hal0` group / config-tree write access — not
the UID split alone. The analysis, the evidence, and the rejected alternatives
are in
[ADR-0002 — agent credential isolation](docs/adr/0002-agent-credential-isolation.md),
accepted for 1.0 as Option C on 2026-08-15 (merged in
[PR #1880](https://github.com/Hal0ai/hal0/pull/1880)).

`hal0 doctor all` reports this posture directly: the **Agent UID split** row warns
when an agent unit resolves to the same `User=` as `hal0-api.service`.

### If you cannot accept it

- Run the box without the bundled agent (do not install/enable `hal0-agent@*`), or
- Do not give the agent a local shell (`terminal.backend` other than `local`), and
- Do not feed the agent untrusted content on a box holding credentials you care
  about, and do not reuse those credentials elsewhere.

### Bridge keys: remote agents that use the bundled agent

An operator can admit a remote coding agent (Claude Code, Codex, a CI job) with
`hal0 bridge add`. Each such **bridge client** gets a key of its own,
`hal0bk_<name>.<token>`; hal0 stores only its sha256, in
`/var/lib/hal0/bridge.db` (`0600`, owned by the `hal0` account, kept apart from
the world-readable `hal0.db`). The design is
[ADR-0043](docs/adr/0043-agent-bridge-and-bridge-keys.md).

**A bridge key is as powerful as asking Hermes to do something, for every
profile hal0 holds a key for. With the `admin` scope it is the agent-tier key
[ADR-0024](docs/adr/0024-agent-credential-tier.md) describes: it opens the admin
MCP server's tools (autonomous reads and writes included, with gated actions
waiting in the approval inbox) and nothing on the REST surface. Treat a bridge
key like an operator credential:** keep it out of dotfiles under version
control, grant `admin` only to an agent that operates the box, and revoke it
with `hal0 bridge revoke <name>` (or replace it with `hal0 bridge rotate
<name>`) the moment it may have leaked. Revocation takes effect on the next
request.

What the design does and does not do:

- A bridge key is accepted on the bridge's own MCP server, and on the memory and
  admin MCP servers only when its client holds that scope. It is refused on
  every non-OPEN `/api/*` and `/v1/*` route, with one exception,
  `GET /api/bridge/me`; OPEN routes such as `GET /api/health` and
  `GET /v1/models` answer it as they answer a caller with no key.
- The bridge asks for a credential even when `[security].require_auth` is off.
  A box with no bridge clients accepts no bridge key at all.
- A bridge client's memory identity is the name in its key, not the
  `X-hal0-Agent` header, which stays unverified for every other caller.
- A bridge key does not make the same-UID risk above smaller: the bundled
  agent, and anything else running as `hal0`, can still open `bridge.db`. It
  holds hashes, not keys, but an attacker with the `hal0` account does not need
  a bridge key.

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
