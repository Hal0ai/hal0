# ADR-0002: Agent credential isolation — root-only secret files

## Status

**ACCEPTED — Option C for 1.0 (operator decision, 2026-08-15). The
agent-UID split (Option B) is scheduled for 1.1.**

The root-only-file design this ADR was asked to evaluate is **rejected**; the
reasoning is in "Why the proposed design does not close the chain" below and
rests on two bypasses, one verified empirically and one already documented
in-tree. Option C's compensating measures shipped with rc.6: the `SECURITY.md`
trust-boundary disclosure, the `hal0 doctor all` agent-UID warn row, and the
`upstreams.toml` 0640 tightening (#1881), alongside making the terminal tool an
explicit opt-in, default off (#1882). The rejected root-only-file design itself
remains unimplemented by intent.

## Context

### The claimed chain

> untrusted content → prompt injection → shell → read credentials → exfiltrate

The bundled Hermes agent is provisioned with `terminal.backend = local`
(`src/hal0/agents/hermes_provision.py:1570`), so it can execute arbitrary shell
commands. It runs under `hal0-agent@hermes.service` as `User=hal0`
(`installer/systemd/hal0-agent@.service`, `[Service] User=hal0 / Group=hal0`).

`hal0-api` also runs as `User=hal0` (`installer/install.sh:1246-1247`, inside
the heredoc that writes `/etc/hal0/hal0-api.service`).

The two run as **the same UID**. That single fact is what this ADR turns on.

### What is actually where — measured, not assumed

Read-only inspection of production (10.0.1.142) on 2026-08-12:

```
/etc/hal0/api.env         hal0:hal0 600
/etc/hal0/upstreams.toml  hal0:hal0 644
/etc/hal0/hal0.toml       hal0:hal0 600
/etc/hal0/agents/hermes.env  root:root 600
/var/lib/hal0/secrets/hal0-api.env  (absent on this box)
```

Key **names** present in `/etc/hal0/api.env` on production (values never read
or echoed): `HAL0_PORT`, `HAL0_MCP_ALLOWED_HOSTS`, `HAL0_LOG_LEVEL`,
`HAL0_UI_DIST`, `HAL0_MEMORY_ENABLED`, `HAL0_FLM_MODELS_DIR`, `HF_TOKEN`,
`MINIMAX_API_KEY`, `HERMES_SESSION_TOKEN`, `OPENROUTER_API_KEY`,
`HAL0_TURNSTONE_TOKEN`, `HAL0_TURNSTONE_CONSOLE_URL`, `HAL0_TURNSTONE_URL`,
`HAL0_BIND_HOST`, `HAL0_HOSTNAME`, `HAL0_ALLOWED_ORIGINS`, `HAL0_ADMIN_KEY`,
`HAL0_HINDSIGHT_PROBE_TIMEOUT_S`, `HAL0_RELEASES_URL`,
`HAL0_COMFYUI_PUBLIC_URL`.

So the premise "every high-value credential is readable by the agent user" is
**confirmed for `api.env`**.

### Correction #1: `upstreams.toml` carries no credential values

The problem statement describes `/etc/hal0/upstreams.toml` as `0644` "with
provider credential entries". The `0644` is real
(`src/hal0/install/perms.py:217` pins it, and production confirms). The
credentials are not.

`Upstream.auth_value_env` is documented as *"Environment variable holding the
API key credential. **Never stored in TOML**"*
(`src/hal0/upstreams/registry.py:141-142`). The registry resolves the secret
from the process environment at request time —
`key = os.environ.get(u.auth_value_env, "").strip()`
(`src/hal0/upstreams/registry.py:599`), and again in the health path at
`registry.py:805`. Deleting an upstream deliberately leaves the credential in
`api.env` (`registry.py:463-464`).

Confirmed on production: the only keys present in `upstreams.toml` are
`advertise_models`, `auth_header`, `auth_style`, `auth_value_env`, `enabled`,
`exclude`, `include`, `kind`, `models`, `name`, `timeout_seconds`, `upstreams`,
`url`, `warmup_strategy`. No value-bearing credential key.

`upstreams.toml` at `0644` is therefore an **integrity** and
**metadata-disclosure** issue (which providers you use, which endpoints, which
env var names), not a credential-confidentiality issue. It does not belong in a
secrets-relocation design; if we care about it, the fix is one `PermRow` mode
change, independently.

### Correction #2: `api.env` is *not* loaded with `ignore_errors`

The brief warns that `EnvironmentFile` is loaded with `ignore_errors=yes`,
which would let the service start silently token-less. For `api.env` that is
false — `installer/install.sh:1255` emits `EnvironmentFile=${API_ENV}` with **no**
leading `-`, so systemd hard-fails `hal0-api` if the file is missing or
unreadable. That is the fail-closed posture we want, and it already exists.

The `-` prefix *is* used, deliberately, on three optional files:

- `installer/install.sh:1258` — `EnvironmentFile=-${ETC_DIR}/hermes-python.env`
- `installer/install.sh:1262` — `EnvironmentFile=-${HF_SECRETS_ENV}`
  (`/var/lib/hal0/secrets/hal0-api.env`)
- `installer/systemd/hal0-agent@.service` —
  `EnvironmentFile=-/etc/hal0/agents/%i.env`

The third one is the real instance of the hazard the brief describes: it
carries `HAL0_MCP_TOKEN`, and a missing/unreadable file silently starts the
agent with no inbound bearer instead of failing loudly. That is a genuine,
cheap, separable bug (Option C item 4 below) — but it is an availability /
diagnosability defect, not part of the exfiltration chain.

### Prior art already in the tree

The pattern the brief asks for **already exists** and works:

1. `HF_TOKEN` at install time is persisted to `/var/lib/hal0/secrets/hal0-api.env`,
   `root:root 0600`, loaded by `hal0-api` via `EnvironmentFile=-`
   (`installer/install.sh:1136-1173`). The installer's own comment states the
   intent: *"Persist: root:root 0600, under secrets/ — NOT api.env"*.
2. The Hermes gateway's platform tokens live in
   `/var/lib/hal0/secrets/agents/hermes.env`, `root:root 0600`, wired in via a
   systemd drop-in (`src/hal0/agents/hermes_provision.py:3944-3958`). The
   vault itself is written and re-pinned root:root by
   `_write_secrets_env()` (`src/hal0/agents/hermes_provision.py:4406-4419`);
   the same root:root/0600 pattern recurs for the other per-agent artifacts
   (driver env, seed TOML) scattered across `hermes_provision.py:4262-5238`.
3. The privileged write seam is `installer/wrappers/hal0-agentenv` +
   `packaging/sudoers/hal0-agentenv` — argv-validated, path-constructed on the
   root side, content on stdin, `merge-secrets` doing the read-merge-write as
   root precisely because the unprivileged caller cannot read the vault.

So the mechanism is proven. The question is not "can we do it" but "does doing
it buy anything".

## Why the proposed design does not close the chain

Two independent bypasses survive making `/etc/hal0/api.env` `root:root 0600`
and injecting it into `hal0-api` via `EnvironmentFile=`.

### Bypass A — `/proc/<hal0-api-pid>/environ` (verified)

`hal0-api` and `hal0-agent@hermes` run as the same UID. Reading
`/proc/PID/environ` requires `PTRACE_MODE_READ_FSCREDS`, which same-UID
processes pass. Yama's `ptrace_scope` does not apply: `yama_ptrace_access_check`
only constrains `PTRACE_MODE_ATTACH`, not `PTRACE_MODE_READ`.

Verified on ct152 (10.0.1.152, the mutable test box) on 2026-08-12, read-only
probe:

```
$ systemctl show hal0-api -p User            -> User=hal0
$ systemctl show hal0-agent@hermes -p User   -> User=hal0
$ sysctl kernel.yama.ptrace_scope            -> 1
$ mount | grep -c hidepid                    -> 0
$ P=$(systemctl show -p MainPID --value hal0-api)
$ sudo -u hal0 cat /proc/$P/environ > /tmp/e ; echo $?
0
```

`rc=0` — the full environment block of `hal0-api` was dumped by a process
running as `hal0`. (ct152 currently carries no provider secrets, so the dump
contained only `HAL0_*` config vars; the *mechanism* is what was tested and it
succeeded unconditionally.)

Therefore: moving the file to `root:root` and injecting it into a `User=hal0`
process moves the secret from a file the agent can `cat` to a `/proc` node the
agent can `cat`. The chain is unbroken.

### Bypass B — the `hal0` account is already root-equivalent (documented in-tree)

The sudoers grants are issued to the **user** `hal0`
(`packaging/sudoers/hal0-agentenv:22`,
`packaging/sudoers/hal0-systemctl` final line,
`packaging/sudoers/hal0-benchctl`, `hal0-podman-ro`, `hal0-update`). The agent
runs as `hal0`, so the agent holds every one of those grants, not just the API.

`installer/wrappers/hal0-systemctl` says so itself, in a block headed **HONEST
BOUNDARY**:

> It does NOT make an hal0-account compromise non-root-equivalent:
> `[Container]` still legitimately carries `Image=`, `Volume=`, `AddDevice=`
> and in-container `Exec=`, whose values are operator/config-derived and cannot
> be pinned here … Slots run under ROOTFUL podman, so anyone who can author a
> slot's container spec can mount host paths into a container they control.

An injected agent does not need to read `/etc/hal0/api.env` at all. It writes a
slot Quadlet with `Volume=/etc/hal0:/host-etc` and `Volume=/var/lib/hal0/secrets:/host-secrets`,
calls the seam's `daemon-reload` + `start`, and reads whatever it likes out of
a rootful container. Root-only file modes are transparent to that.

### The consequence

The requested design is **the wrong axis**. The axis is not *file owner*, it is
*which UID the agent runs as*. If the agent ran as a user that is not `hal0`
and holds no sudoers grant, then `/etc/hal0/api.env` at its **existing**
`hal0:hal0 0600` is already unreadable by the agent, `/proc/<api-pid>/environ`
is already unreadable by the agent, and the slot-authoring escalation is
already unavailable. Zero file relocation required.

That reframing is the main deliverable of this ADR.

## The design, as requested (for the record)

Presented so the operator can execute it if they disagree with the
recommendation. Each numbered item answers a numbered question in the brief.

### 1. Which processes need each secret, and what changes if the file is root-only

| Secret | Needed by | How it is consumed today |
|---|---|---|
| `HAL0_ADMIN_KEY` / `HAL0_CLIENT_KEY` | `hal0-api` (auth layer); `hal0` CLI; `hindsight-api` (client tier, via `/etc/hal0/hindsight-llm.env`) | env first, **then the file** — `service_identity.service_key()` (`src/hal0/service_identity.py:71-88`) falls back to `keys_from_api_env()` (`:46-69`), which does `(cfg_paths.etc() / "api.env").read_text()` |
| `OPENROUTER_API_KEY`, `MINIMAX_API_KEY`, any `auth_value_env` | `hal0-api` only | **env only** — `os.environ.get(u.auth_value_env)` (`src/hal0/upstreams/registry.py:599`, `:805`) |
| `HF_TOKEN` | `hal0-api` (model pulls) | env; already has a root-only home at `/var/lib/hal0/secrets/hal0-api.env` |
| `HERMES_SESSION_TOKEN` | `hal0-api`'s board client | env only — `os.environ.get("HERMES_SESSION_TOKEN")` (`src/hal0/board/__init__.py:87`) |
| `HAL0_MCP_TOKEN` | the agent | already root-only: `/etc/hal0/agents/hermes.env`, `root:root 0600`, injected by pid1 |

**So: what actually changes if the file is root-only but systemd injects the
env?** For the *provider* credentials and `HERMES_SESSION_TOKEN`, nothing —
they are pure-env consumers. For the *box keys*, the file-read fallback breaks
for every non-root reader:

- `hal0-api` itself: fine (systemd injects; env tier wins before the file tier).
- The `hal0` CLI run as **root**: fine.
- The `hal0` CLI run as **any other user** (including an operator, including
  the agent): `service_key()` returns `None` → unauthenticated probes → 401s on
  an auth-enabled box. `src/hal0/cli/_shared.py:28-49` and
  `src/hal0/cli/doctor_all.py:756-758` both depend on this read.
- `hal0.agents.hermes_provision` calls `service_key(prefer="admin")` at
  `:1546`, `:2024`, `:2773`, `:3166`, `:5117`, `:5211`. In-process under
  `hal0-api` that is fine (env). Invoked from a CLI path as a non-root user it
  is not.

### 2. The writer problem

`hal0-api` currently **writes** `api.env` from three code paths, all as `hal0`:

- `src/hal0/api/_env_store.py:88` `upsert_env_value` /
  `:132` `delete_env_value` / `:153` `list_env_keys` — tmpfile + `os.chmod` +
  `os.replace` at `paths.API_ENV_MODE` (`src/hal0/config/paths.py:91`, `0o600`).
- `src/hal0/api/routes/providers.py:518` `_write_credential_to_api_env`, called
  from `POST /api/providers/{name}/credentials` (`:544`, target path resolved
  at `:604`).
- `src/hal0/api/routes/secrets.py` — `GET/POST/PUT/DELETE /api/secrets`
  (`list_secrets` `:196`, `_set_secret` `:248`, `delete_secret` `:285`).
- `src/hal0/service_identity.py:148` `rotate_api_env_key` (and
  `refresh_hindsight_llm_env` for `/etc/hal0/hindsight-llm.env`).

All four die on a `root:root 0600` target — including `list_env_keys`, which
means the dashboard's Secrets tab goes blank, not just read-only.

**Design: `hal0-secretenv`, modelled exactly on `hal0-agentenv`.**

New wrapper `installer/wrappers/hal0-secretenv`, installed `root:root 0755` to
`/usr/lib/hal0/bin/`, granted by `packaging/sudoers/hal0-secretenv` as
`hal0 ALL=(root) NOPASSWD: /usr/lib/hal0/bin/hal0-secretenv`. Verbs:

- `list-keys` — stdout: newline-separated key names. **Never values.**
- `set KEY` — value on stdin; key validated against
  `^[A-Z][A-Z0-9_]{0,63}$` (mirroring `secrets.py:65` `_SECRET_NAME_RE`);
  value rejected if it contains any of `_env_store._LINE_BREAK_CHARS`
  (`_env_store.py:35`) or any `ord < 0x20 / == 0x7F` char (mirroring
  `providers.py:597`). Read-merge-write as root, atomic, `0600 root:root`.
- `delete KEY` — same validation.
- `rotate admin|client` — mints via `generate_service_key()` semantics, writes,
  and echoes back **only** `{tier, key_len, fingerprint}` (never the value,
  matching the contract in `service_identity.py:167-169`). Also refreshes
  `/etc/hal0/hindsight-llm.env`.

`_env_store.py` grows a single dispatch point: if the target is not writable by
the current euid, shell out to the seam; otherwise write directly (preserving
the existing test posture, where `HAL0_HOME` points at a tmpdir the test user
owns). That keeps the change to one module plus the wrapper.

**Split, not wholesale move.** `api.env` is not purely secret — it also carries
`HAL0_PORT`, `HAL0_BIND_HOST`, `HAL0_HOSTNAME`, `HAL0_ALLOWED_ORIGINS`,
`HAL0_UI_DIST`, `HAL0_LOG_LEVEL`, and the installer rewrites a marker-delimited
network block in it on **every** re-run (`installer/install.sh:1107-1122`).
Moving that block behind a sudo seam would make the installer's idempotent
refresh a privileged operation for no benefit. So:

- `/etc/hal0/api.env` — stays `hal0:hal0 0600`, keeps non-secret config only.
- `/var/lib/hal0/secrets/hal0-api.env` — the **existing** root-only file
  (`installer/install.sh:1136`) becomes the home for `HAL0_ADMIN_KEY`,
  `HAL0_CLIENT_KEY`, `HF_TOKEN`, `HERMES_SESSION_TOKEN`, and every
  `auth_value_env` provider credential. Its `EnvironmentFile=` line loses the
  `-` (see §5).

**Known-incomplete, recorded for reference only.** The design above has not
been executed and, as written, has unresolved gaps that would need closing
before it could ship — not that it matters, since Option A is rejected below,
but future readers should not treat this section as execute-ready:

- The `HF_TOKEN` installer writer (`installer/install.sh:1136-1173`)
  wholesale-overwrites `/var/lib/hal0/secrets/hal0-api.env` rather than
  merging into it. Moving more keys into that file means every writer touching
  it — including this section's `hal0-secretenv` — must agree on a
  read-merge-write contract, and the installer path does not have one today.
- A fresh, keyless install never creates `/var/lib/hal0/secrets/hal0-api.env`
  at all. Dropping the `EnvironmentFile=-` leading dash (as §5 proposes) on a
  box that never gathered a token at install time would fail the unit closed
  and break auth-off installs, not just harden auth-on ones.
- The `rotate admin|client` verb, per its own contract, returns only
  `{tier, key_len, fingerprint}` — never the value — so it cannot live-update
  `hal0-api`'s already-running `os.environ`. A rotation still requires a
  process restart to take effect; nothing in this design says so.
- The doctor duplicate-key check called for in the rollback section (item 4 of
  "Migration and rollback") needs a privileged comparison verb of its own —
  comparing values across a `hal0:hal0`-readable file and a `root:root`-only
  one is not something an unprivileged `hal0 doctor` invocation can do without
  a seam, and no such verb is designed here.

### 3. What the agent legitimately still needs — and what this does NOT achieve

State plainly: **this design cannot make the agent credential-free, and does
not try to.**

`$HERMES_HOME` (`/var/lib/hal0/.hermes`, set at
`installer/systemd/hal0-agent@hermes.service.d/override.conf`) must stay
agent-writable. It contains:

- `config.yaml` with `providers.custom.api_key`
  (`src/hal0/agents/hermes_provision.py:1504`) — today the literal
  `"hal0-local"` placeholder, but on an auth-enabled box it is a live bearer.
- `auth.json` and `mcp-tokens/` — real credentials for real upstreams.
- `HAL0_MCP_TOKEN`, injected via `/etc/hal0/agents/hermes.env`, which is the
  agent's own inbound bearer against hal0's MCP surface.

So an injected agent always retains: its own hal0 bearer, its own MCP token,
and whatever provider tokens the operator gave it. There are **no egress
restrictions** anywhere in the chain and this design adds none — the agent
legitimately talks to remote model providers, so `IPAddressDeny=` on its unit
would break its purpose, not harden it.

The honest claim for the full design is therefore narrow: *"an injected agent
loses direct access to credentials it was never given — the box admin key and
the operator's provider keys — while keeping its own."* And per Bypasses A and
B above, **even that narrow claim is false today** unless the UID split lands
first.

### 4. Migration and rollback

**Fresh install:** installer writes secrets to the root-only file from the
start. No `api.env` secret lines ever exist.

**Upgrade:** a one-shot migration in `install.sh`, running as root, moves every
key in a fixed secret allow-list (`HAL0_ADMIN_KEY`, `HAL0_CLIENT_KEY`,
`HF_TOKEN`, `HERMES_SESSION_TOKEN`, plus every `auth_value_env` named in
`upstreams.toml`) from `api.env` into the root-only file, then deletes those
lines from `api.env`. Atomic per file, root-only file written first, `api.env`
pruned second — so a crash mid-migration leaves duplicated secrets (harmless:
systemd applies both `EnvironmentFile=` lines in order and the later one wins)
rather than lost ones. Ordering matters: the root-only `EnvironmentFile=` line
must come **after** `${API_ENV}` in the unit so a straggler in `api.env` is
overridden, not overriding.

**Rollback to a release expecting the old layout:** this is the ugly part. An
older `hal0-api.service` still has `EnvironmentFile=-${HF_SECRETS_ENV}`
(present since #1106), so `HF_TOKEN`, and anything else we put there, is still
loaded — the old unit tolerates the new file. But the old `_env_store` writer
and the old `rotate_api_env_key` write to `api.env`, which the *new* unit
overrides. Result after a rollback-then-rotate-then-roll-forward: a stale key
in the root-only file silently shadows the freshly rotated one in `api.env`.
That is a silent-auth-failure class of bug, and it is exactly the kind of thing
the rc.5 wave has been finding. Mitigation: a `hal0 doctor` row that fails when
the same key name appears in both files with different values. That check must
land in the *same* release as the migration, not after.

**Boxes with a hand-edited `api.env`:** the marker-block refresh already warns
that hand edits inside the markers are overwritten
(`installer/install.sh:1064-1066`), but operator lines *outside* the markers
survive. The migration must preserve comments and unrelated lines — the
`hal0-agentenv merge-secrets` python does exactly this and is the template.

### 5. Blast radius and failure modes

| Failure | Current behaviour | Required behaviour |
|---|---|---|
| Root-only secrets file missing | `EnvironmentFile=-` → **silent start with no credentials**; every upstream 401s at request time (`registry.py:603-609` does at least raise a typed error rather than send an unauthenticated request) | Drop the `-`. Fail closed: unit refuses to start, `systemctl status` names the file. |
| Root-only file unreadable by systemd | same silent start | same — fail closed |
| `api.env` missing | already fails closed (`install.sh:1255`, no `-`) | unchanged — this is the model to copy |
| `hal0-secretenv` seam missing / sudoers not installed | n/a | `POST /api/secrets`, `POST /api/providers/{name}/credentials` and `POST /api/auth/rotate` must return a **5xx naming the seam**, never a silent success. The existing `ProviderCredentialError` envelope (`providers.py:530-540`) already does this shape for `OSError`. |
| Seam present, sudo denies | n/a | same 5xx; audit-log the denial (`secrets.py:267` `_audit_log` is the hook) |
| `/etc/hal0/agents/%i.env` missing | agent starts with **no `HAL0_MCP_TOKEN`** silently (`hal0-agent@.service`, `EnvironmentFile=-`) | **This one should be fixed regardless of this ADR** — see Option C item 4 |

Note the asymmetry: fail-closed on the API's secrets file is safe (the box is
useless without them anyway). Fail-closed on the *agent's* env file is a
judgement call — the `-` there was chosen so a fresh box with no token
provisioned can still boot the agent idle. The right fix is not to drop the `-`
but to have the shim assert the token's presence and emit a loud degraded
state, which it can do because `Type=notify` already gives it a status channel.

## Options considered

**Option A — full design above.** Root-only secrets file + `hal0-secretenv`
seam + migration + doctor rows. Estimated: 1 new wrapper, 1 sudoers file, edits
to `_env_store.py`, `service_identity.py`, `routes/secrets.py`,
`routes/providers.py`, `install/perms.py`, `install.sh` (writer + migration +
unit), `doctor_all.py`, plus tests for a code path that only behaves
differently when euid ≠ file owner (i.e. hard to test in CI, easy to get wrong
on a box). **Security value delivered: approximately zero**, per Bypasses A
and B. Cost: high, in the last week before GA. **Reject.**

**Option B — the UID split.** Give the agent its own system user
(`hal0-agent`), not `hal0`, and grant it none of the sudoers seams. This closes
Bypass A (different UID → `/proc/<api-pid>/environ` denied), closes Bypass B
(no seam grants → no rootful slot authoring), and makes the *existing*
`hal0:hal0 0600` on `api.env` sufficient with **no file relocation at all**.

Cost: `User=`/`Group=` on `hal0-agent@.service`; ownership of
`/var/lib/hal0/.hermes` and everything Hermes writes; `ReadWritePaths=` review;
the shared `/run/hal0` `RuntimeDirectory` (currently `RuntimeDirectoryPreserve`
across both units — needs a group or a split); new `PermRow`s in
`install/perms.py`; a chown migration on upgrade; and the
`ReadWritePaths=/etc/hal0` grant on the agent unit (needed for the
`render-context` `ExecStartPre`) has to become a narrower path or the agent can
still clobber config. This is a real week of work with real upgrade risk. It is
the **correct** fix and it is **not a 1.0 change**.

Open question for 1.1 planning, not covered by the UID split alone:
`_write_driver_env()` (`src/hal0/agents/hermes_provision.py:5180-5211`) writes
`service_key(prefer="admin")` — a literal copy of the box admin key — into the
agent's own `HAL0_MCP_TOKEN` in `/etc/hal0/agents/hermes.env`. Splitting the
UID stops the agent from reading `hal0-api`'s environment or `api.env`, but it
does nothing about the admin key the agent already holds in its *own* env
file. Until that token is scoped down to something narrower than the full box
admin key, an agent under Option B still authenticates to hal0's API surface
with full admin authority — it has simply lost the extra read paths, not the
credential itself. This has to be resolved before Option B can be called
"done".

**Option C — document the boundary, fix the cheap real bugs, defer.**
Recommended. See below.

**Option D — the "cheaper partial" the brief anticipates (move only
`HAL0_ADMIN_KEY`).** Rejected for the same reason as Option A: `HAL0_ADMIN_KEY`
is subject to both bypasses identically, and it is the key with the *most*
readers (`service_identity.keys_from_api_env` is called from the CLI, doctor,
and six sites in `hermes_provision`). It is the worst single key to move first,
not the best.

## Decision (proposed)

**Adopt Option C for 1.0. Schedule Option B for 1.1. Do not implement Option A
at all** — if Option B lands, Option A's benefit is already delivered by the
existing file modes.

### Option C — what actually ships

1. **Say it out loud in `SECURITY.md` and the agent docs**: the bundled Hermes
   agent runs with `terminal.backend = local` as the `hal0` user, which holds
   the `hal0-systemctl` / `hal0-agentenv` / `hal0-benchctl` / `hal0-update`
   sudo grants and can therefore author rootful slot containers. **Treat the
   bundled agent as root-equivalent on the box.** Do not feed it untrusted
   content on a box that holds credentials you care about. This is not a new
   weakness — `installer/wrappers/hal0-systemctl`'s HONEST BOUNDARY block has
   said the equivalent about the `hal0` account since #1740 — it has simply
   never been said where an operator would read it.
2. **`hal0 doctor` row `check_agent_uid_isolation`**: warn (not critical) when
   `hal0-agent@*.service` and `hal0-api.service` resolve to the same `User=`,
   naming the consequence and linking this ADR. Sits next to the existing
   `check_secret_file_modes` (`src/hal0/cli/doctor_all.py:87`), same
   assert-the-property-directly discipline.
3. **`upstreams.toml` → `0640`** (one-line `PermRow` change at
   `src/hal0/install/perms.py:217`). No credential values are in it, but the
   provider/endpoint inventory is not public information either, and nothing
   reads it as a non-`hal0` user. Cheap, no seam, no migration.
4. **Agent MCP-token presence assertion**: `hal0-agent@%i` starts silently
   token-less when `/etc/hal0/agents/%i.env` is absent. The shim
   (`src/hal0/cli/agent_shim.py`) should detect a missing `HAL0_MCP_TOKEN` on
   an auth-enabled box and emit a loud degraded status through its existing
   `Type=notify` channel, plus a `hal0 doctor` row. Fail loudly without
   fail-closed's boot-loop risk.

Items 2–4 are independent, individually revertible, and none of them touch a
privilege boundary.

### Explicitly NOT doing, for 1.0

- Moving any secret out of `api.env`.
- Adding a `hal0-secretenv` sudo seam.
- Any `ReadOnlyPaths=` change (correctly identified in the brief as buying
  integrity, not confidentiality).
- Any egress restriction — the agent's job requires egress.

## Consequences

**If this proposal is accepted:** hal0 1.0 ships with a documented, honest
trust boundary and a doctor row that tells operators where they stand. The
exfiltration chain remains open by design, disclosed rather than papered over.
An operator who cannot accept that runs the box without the bundled agent, or
without `terminal.backend = local`.

**If it is rejected in favour of Option A:** budget the seam, the migration,
the dual-file doctor check, and accept that the chain still closes through
`/proc` and through rootful slot authoring. I would consider that spend
misallocated and say so again in review.

**If it is rejected in favour of doing Option B now:** that is a defensible
call on the merits, but it is a 1.1-shaped change landing in a 1.0 release
week, and the upgrade path (chown of a live `$HERMES_HOME`) is exactly the
class of migration that has produced this wave's regressions.

## Open questions for the operator

1. Is "the bundled agent is root-equivalent on the box" an acceptable
   *documented* 1.0 posture, or a GA blocker? Everything else follows from that
   answer.
2. `terminal.backend = local` is currently unconditional
   (`hermes_provision.py:1553`). Should 1.0 make it an explicit install-time
   opt-in, with `terminal.backend` disabled by default? That is a much smaller
   change than any credential relocation and it breaks the chain at the *shell*
   step rather than the *read* step — but it defaults the agent into being
   substantially less useful.
3. Is the `hal0` account's root-equivalence (rootful podman + `Volume=`)
   something the operator considers already-accepted risk, or news? If it is
   news, that reframes the whole priority order and Option B moves up.
4. Does anything outside this repo run the `hal0` CLI as a **non-root,
   non-`hal0`** user and depend on `service_identity.keys_from_api_env()`
   reading the file? That read is the only consumer that a root-only file
   breaks outright, and I could not enumerate out-of-repo callers.
5. `upstreams.toml` → `0640`: any known consumer that reads it as another user?
   I found none in-tree, but the mode has been `0644` long enough that an
   operator script could depend on it.

## References

- `installer/install.sh:1244-1272` — `hal0-api.service` heredoc: `User=hal0`,
  the three `EnvironmentFile=` lines
- `installer/install.sh:1136-1173` — the existing `root:root 0600` secrets file
- `installer/systemd/hal0-agent@.service` — agent unit: `User=hal0`,
  `EnvironmentFile=-/etc/hal0/agents/%i.env`, `ReadWritePaths=/etc/hal0 …`
- `installer/wrappers/hal0-agentenv` — the privileged-write seam prior art
- `installer/wrappers/hal0-systemctl` — the HONEST BOUNDARY block
- `packaging/sudoers/hal0-agentenv`, `packaging/sudoers/hal0-systemctl` — the
  grants, issued to the user `hal0`
- `src/hal0/service_identity.py:46,71,148` — file-read fallback, key
  resolution, rotation writer
- `src/hal0/api/_env_store.py:58,88,132,153` — the atomic `api.env` writer
- `src/hal0/api/routes/providers.py:518,544,604` — the provider-credential
  writer
- `src/hal0/api/routes/secrets.py:196,248,285` — the `/api/secrets` surface
- `src/hal0/upstreams/registry.py:141,463,599,805` — `auth_value_env` is a
  name, never a value
- `src/hal0/install/perms.py:207,217` — the `api.env` / `upstreams.toml` rows
- `src/hal0/config/paths.py:91` — `API_ENV_MODE = 0o600`
- `src/hal0/cli/doctor_all.py:87` — `check_secret_file_modes`
- `src/hal0/agents/hermes_provision.py:1504,1570,4406` — agent's own
  credential, `terminal.backend = local`, the gateway secrets vault writer
- CHANGELOG `#1466` — the four-writers-three-opinions `api.env` mode bug that
  produced the current 0600 posture
