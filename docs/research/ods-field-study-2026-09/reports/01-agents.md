# Agents end-to-end: ODS vs hal0

Source-level comparison. Every claim below is cited to `file:line` in the two
read-only checkouts (`/home/user/ods`, `/home/user/hal0`). Where a doc asserts
something the code does not do, it is called out as drift rather than repeated.

---

## A. How ODS does it

### A.1 Hermes is a pinned upstream container, not a fork

ODS runs `nousresearch/hermes-agent:v2026.6.5`
(`ods/extensions/services/hermes/compose.yaml:8`), version-pinned with an
`HERMES_AGENT_IMAGE` escape hatch and a documented bump ritual
(`ods/docs/HERMES.md:142-173` + a bump-history table at `:284-290`). No upstream
code is forked; the whole integration is a packaging layer of five files.

The container runs `gateway run` (`compose.yaml:153-155`) rather than
`dashboard`, because the cron scheduler tick lives in the gateway main loop —
an earlier draft ran only the dashboard and cron jobs silently never fired
(`compose.yaml:139-152`). `HERMES_DASHBOARD=1` embeds the React SPA in the same
process (`compose.yaml:50-52`), and `HERMES_DASHBOARD_TUI=1` (`:87`) turns on
the PTY-backed chat tab that upstream defaults off.

Network posture is the load-bearing decision: Hermes declares `expose: "9119"`
and **no** `ports:` (`compose.yaml:137-138`), and the manifest pins
`external_port_default: 0` specifically so dashboard-api cannot advertise a
non-existent direct URL (`manifest.yaml:19-23`). Health polls `/api/status`
because `/api/health` does not exist in the pinned image and everything else is
session-gated (`manifest.yaml:24-29`, mirrored in the container healthcheck at
`compose.yaml:156-164`). Resource caps, `no-new-privileges`, log rotation and
UID remapping to the host user round it out (`compose.yaml:18-19,167-181`).

### A.2 Config: a template that gets surgically patched

`cli-config.yaml.template` is bind-mounted over the in-image
`cli-config.yaml.example` (`compose.yaml:97`), so Hermes's own entrypoint copies
ODS's defaults into `/opt/data/config.yaml` on first start. The template is
unusually well-reasoned:

* `model.base_url` wins over the `OPENAI_BASE_URL` env var — empirically
  verified on macOS where the env var was ignored (`cli-config.yaml.template:34-42`).
* `model.context_length: 131072` **and** `auxiliary.compression.context_length`
  both set, because Hermes checks the compression model's window against the
  64K floor independently (`:74-84`).
* `model.max_tokens: 1024` to stop a looping local model monopolising the only
  inference slot (`:51-57`).
* `providers.custom.request_timeout_seconds: 180` for slow local prefill (`:59-70`).
* `agent.disabled_toolsets: [terminal, browser]` — a *schema-level* subtraction,
  chosen over prompt-only guidance because removing tools from the schema is the
  only reliable fix for wrong-tool selection on small models (`:86-104`).
* Compression tuned to `threshold 0.75 / target_ratio 0.50 / protect_last_n 40`
  after live testing showed a single 10-15 kB web-search result spiking past a
  0.50 threshold and compressing away the conversation (`:153-186`).
* WhatsApp pre-seeded disabled with `bridge_port: 3010` purely to dodge
  upstream's port-3000 bridge colliding with Open WebUI (`:121-151`).

Because Hermes only copies the template on *first* start, upgrades need a
second writer: `scripts/patch-hermes-config.py` is a line-oriented YAML patcher
that converges only ODS-owned keys and leaves operator sections untouched
(`patch-hermes-config.py:1-8`). It has real nuance — `_ensure_provider_timeout`
only overwrites ODS's own shipped 180s default, never an operator's value
(`:120-156`); `_ensure_whatsapp_bridge` never flips an existing `enabled` state
(`:220-262`); `_ensure_compression` *does* converge every install to the tuned
values so existing boxes migrate on the next bootstrap-upgrade (`:186-215`).

Installer phase 11 drives it: it picks the model name per mode (switchboard
alias `ods/current`, Lemonade, external, or `extra.$GGUF_FILE`), picks
`base_url` (litellm on AMD/switchboard, llama-server otherwise), passes
`--api-key` when routing through LiteLLM, then **verifies the substitution
took** with a `grep -Fqx` and warns loudly if it did not
(`installers/phases/11-services.sh:992-1094`).

### A.3 The 64K context floor

`HERMES_MIN_CONTEXT = 65536` is a shared constant
(`extensions/services/dashboard-api/context_policy.py:3`), declared per-service
in the manifest (`hermes/manifest.yaml:37-45` — `llm.min_context: 65536`,
`route: gateway`, plus a custom probe against `/api/talk/message/stream` with
`auth: cookie:dream-session`), consumed by the dashboard readiness surfaces
(`dashboard-api/main.py:537-587`, `performance_oracle.py:1499`), and — crucially
— *enforced by raising the whole box's context* at install:
`installers/phases/03-features.sh:93-102` bumps `MAX_CONTEXT` to 65536 when
Hermes is enabled and appends the reason to the model recommendation. macOS has
the same logic (`installers/macos/install-macos.sh:1534-1539`).

### A.4 SOUL.md and the generated "About this installation" block

`SOUL.md.template` is a consumer-grade persona written for text-to-speech
delivery — length budgets, "speak in sentences not lists", literal-output
honouring, and hard tool-selection rules that exist because operators watched
models stall chat for minutes reaching for a shell on weather questions
(`SOUL.md.template:103-120`). It contains an eight-step script teaching the
agent to drive OAuth skill setup end-to-end (`:26-60`).

The single line `<!-- INSTALLATION_CONTEXT -->` (`:3`) is replaced at build time
by `scripts/build-installation-context.py`. That script builds a
`{container_name: service_id}` index by regexing every extension manifest
(`:86-127`), runs `docker ps --filter name=ods-` and collapses compound
containers (`ods-langfuse-clickhouse` → `langfuse`) to parent service ids
(`:130-168`), best-effort asks llama-server/Lemonade what model is actually
loaded (`:171-188`), and renders a bounded Markdown block that explicitly tells
the model *not* to run tool calls to second-guess it (`:277-297`). It also
distinguishes the ODS dashboard from Hermes's own `:9119` because the model has
a strong prior to name the wrong one (`:270-287`). A `local-lemonade` profile
emits a much shorter variant for prompt-constrained backends (`:328-377`), and
`build_soul` self-heals the pathological case where Docker auto-created
`data/persona/SOUL.md` as a *directory* (`:406-422`).

Refresh path: `ods-cli`'s `_ods_cli_refresh_soul` regenerates the file and then
`docker exec ods-hermes cp /opt/hermes/docker/SOUL.md /opt/data/SOUL.md`
(`ods-cli:1691-1719`) — a container-side copy chosen because macOS Docker
Desktop's virtiofs refuses nested bind mounts (`compose.yaml:98-127`). It runs
both *before* compose (to repair a directory-shaped SOUL.md that would fail the
mount) and *after* (so `docker ps` sees real state) — `ods-cli:1582-1612`,
mirrored at `installers/phases/11-services.sh:1108-1123` and `:1293-1300`.

### A.5 Auth: magic links, forward_auth, admin-session

`hermes-proxy` is a 50 MB Caddy sidecar (`hermes-proxy/compose.yaml:6`). Its
Caddyfile keeps `/health`, `/healthz`, `/favicon.ico` and `/auth/required*`
public inside an explicit `route {}` block (order matters — global directive
order would otherwise run `forward_auth` first), then forward_auths everything
else to `dashboard-api/api/auth/verify-session`
(`hermes-proxy/Caddyfile:54-129`). Two hard-won details: an earlier draft only
checked that the cookie *header existed*, trivially bypassable (`:8-12`); and
the WebSocket upgrade headers must be stripped from the auth sub-request or
FastAPI 403s a valid session (`:104-119`). Denials 303-redirect (not 302) so a
no-cookie POST does not replay its body (`:96`), and the redirect needs an
explicit `*` matcher or Caddy misparses it (`:123-127`).

The credential itself is a stateless HMAC cookie:
`<random-id>.<expiry-epoch>.<base64url-HMAC-SHA256>`
(`dashboard-api/session_signer.py:1-45`), verified with `hmac.compare_digest`
and an independent server-side expiry check (`:125-171`). An unset
`ODS_SESSION_SECRET` disables issuing entirely rather than silently signing with
an empty key (`:60-66,109-114`). `verify-session` is deliberately *not*
API-key-gated (the cookie is the credential) and returns a byte-identical 401
for every failure mode (`routers/auth.py:65-107`).
`POST /api/auth/admin-session` trades `DASHBOARD_API_KEY` for an identical
cookie so the box owner is not locked out of their own services
(`routers/auth.py:110-180`).

Cookies are minted by magic-link redemption (`routers/magic_link.py:1-46`):
`token_urlsafe(32)`, only the SHA-256 hash persisted, single-use by default,
60-minute guest expiry, per-IP rate limit on redemption, and identical 404s for
invalid/expired/redeemed so a holder cannot fingerprint state. The dashboard's
`/invites` page exposes owner cards and scoped guest invites (`chat` vs
`hermes`) with QR printing (`dashboard/src/pages/Invites.jsx:23-31`).

### A.6 ODS Talk: the server-side bridge that is the best code in the repo

Phones never see Hermes. `dashboard-api/hermes_bridge.py` scrapes the dashboard
token out of Hermes's HTML (`TOKEN_RE`, `:43`, `_fetch_hermes_token:99-113`),
opens the JSON-RPC WebSocket on the Docker network, and returns only simplified
results. Two architectural findings are baked in as comments:

* Hermes scopes streaming events to the WS that *owns* the session, so
  `session.create` and `prompt.submit` must happen on the same socket
  (`hermes_bridge.py:9-15`).
* Therefore a **per-cookie connection pool** keeps one long-lived WS + session
  per phone, so llama-server's KV cache stays warm for the ~16 k-token agent
  system prompt — the second message costs ~1 k tokens of prefill, not 17 k
  (`:17-25`, `_HermesConnection:162-176`).

The pool is genuinely careful: `_POOL_GUARD` is held only for dict bookkeeping
while the network open happens under a per-key lock, so one cold open cannot
head-of-line-block every other phone (`:236-270`); an idle sweeper evicts after
`ODS_TALK_IDLE_EXPIRY` (300 s) but skips connections whose lock is held
(`:279-311`); and exactly one failure mode retries transparently —
`HermesConnectionStale`, raised only when `send_str` fails *before* Hermes
accepted the prompt, so a retry can never duplicate tool calls (`:63-73`,
`:471-528`).

`routers/talk.py` wraps that in SSE with a 5 s keepalive comment frame (iOS
Safari closes idle streams during 30-60 s cold prefill) and client-disconnect
cancellation so an abandoned tab does not pin an inference slot (`:444-460`). A
`_TOOL_LABELS` table maps Hermes tool names to spinner captions with an honest
`Using \`<name>\`…` fallback (`:404-442`). `/api/talk/status` composes Hermes,
whisper and TTS health plus model compatibility into a capabilities dict
including `live_mic_requires_secure_context` (`:560-590`).

### A.7 OAuth passthrough

`routers/oauth_passthrough.py` closes the "copy the code from your URL bar"
gap. `POST /api/oauth/init` mints a nonce bound to `{skill_id, return_url}`;
the provider redirect lands on the unauthenticated `/api/oauth/callback`, which
validates and *consumes* the nonce before writing `{code, state, ts}` to
`data/persona/oauth_callback.json` for the agent to pick up (`:1-66`). The
`return_url` is bound at init, eliminating the open-redirect surface, and
`skill_id` is resolved server-side so an attacker cannot steer which skill the
code is exchanged against (`:52-62`). `oauth-providers.json` is the operator-
facing registry of flows, credential filenames and acceptable redirect URIs,
with an explicit "prefer PKCE / device flow so local appliances need no shared
secret" stance (`oauth-providers.json:22-51`).

### A.8 The rest of the agent surface

* **`dashboard-api/routers/agents.py` is a red herring** — 77 lines of GPU/
  session/throughput metrics (`:14-77`) backed by `agent_monitor.py`, which
  itself admits `tokens_per_second` and `queue_depth` have no data source
  (`agent_monitor.py:27-30`). Real Hermes control lives in `talk.py`,
  `auth.py`, `magic_link.py` and the Hermes SPA itself.
* **APE** (`extensions/services/ape/main.py`) is a competent standalone policy
  gateway: intent classification, allowlists, path guards, deny regexes,
  sliding windows (5 m/1 h/1 d), a circuit breaker, warmup grace, persisted
  state under a file lock, and `require_approval` escalation that deliberately
  does not raise so a framework can route to a human and retry via `/approve`
  (`main.py:1-56,187-200,793-1072`). It is **not wired to Hermes** (see E).
* **`ods/agents/templates/`** is five OpenClaw-era YAML agent templates with a
  README dated 2026-02-11 (`agents/templates/README.md:1-24`); nothing outside
  the files themselves references them.
* **`memory-shepherd/`** is a systemd-timer baseline-reset tool with a genuinely
  good idea (operator-owned baseline above a `---` separator, scratch below,
  archived and reset every 3 h — `README.md:14-51`) but its shipped config still
  points at `config/openclaw/workspace/*.md`
  (`memory-shepherd.conf:19-35`). It is not wired to Hermes.
* **OpenCode** runs as a host user unit on loopback 3003, deliberately
  `UnsetEnvironment=OPENCODE_SERVER_PASSWORD` (`opencode/opencode-web.service:16-19`),
  configured by phase 07 which writes `opencode.json` pointing at LiteLLM (with
  `LITELLM_KEY`) on Lemonade or llama-server direct (`no-key`) otherwise, with
  a jq-rewrite path and a deterministic fresh-write fallback
  (`installers/phases/07-devtools.sh:140-219`). The generated-config writer
  matrix in `docs/INSTALLER-ARCHITECTURE.md:118-135` lists every place each
  surface is written across Linux/macOS/Windows/upgrade — the single most
  transferable *process* artifact in the repo.

### A.9 ODS doc drift found

1. `docs/HERMES.md:110` states compression `threshold 0.50 / target_ratio 0.20`.
   The shipped template is `0.75 / 0.50 / 40` (`cli-config.yaml.template:171-186`)
   and the patcher converges to the same (`patch-hermes-config.py:207-212`).
2. `docs/HERMES.md:115` claims "4 CPUs / 4GB RAM hard limit". The compose default
   is `cpus: ${HERMES_CPU_LIMIT:-1.0}` (`compose.yaml:170`).
3. `docs/MIGRATION-OPENCLAW-TO-HERMES.md:28` claims an "APE policy plugin
   (pre_tool_call hook) routes every tool call through ODS's policy engine".
   `pre_tool_call` appears **nowhere else in the repository** — that doc line is
   its only occurrence — and `docs/HERMES.md:140` says the opposite ("No APE
   policy enforcement yet"). The migration table is marketing, not code.
4. Same table, `:27`, claims voice is "wired through ODS's whisper + kokoro out
   of the box". True at the ODS Talk portal layer (`routers/talk.py:279-368`),
   not inside Hermes; `docs/HERMES.md:182` still lists it as unverified roadmap.
5. `cli-config.yaml.template:20-27` admits the model-name substitution is an
   "open follow-up" requiring hand-edits on AMD/Apple — but
   `installers/phases/11-services.sh:1068-1094` has shipped it. The comment is stale.

---

## B. How hal0 does it today

hal0 runs Hermes as a **pip-installed wheel in a hal0-owned venv under a
sandboxed systemd template unit**, not a container. `BUNDLED_AGENTS` is
`("hermes", "pi")` with single-pick enforced in the manager, not the API or CLI
(`src/hal0/agents/manager.py:117`, `:28-31`).

**Unit.** `installer/systemd/hal0-agent@.service` is `Type=notify` +
`WatchdogSec=60` (`:21-26,64`), `User=hal0`, `NoNewPrivileges`,
`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, the full `Protect*`/
`Restrict*` set (`:69-77`), `RuntimeDirectory`/`LogsDirectory` created before
the namespace is built (`:79-87`), and `StartLimitBurst=5/120s` (`:16-19`).
The `ReadWritePaths=` comment is a model of honesty: it spells out exactly what
the boundary does *not* buy — `/etc/hal0` is hal0-owned 2775, so the agent can
rewrite `api.env` and `upstreams.toml`, and `%i.env` was left 0644 on every
upgraded box by #1876 (`:89-130`). The hermes drop-in sets `HERMES_HOME`,
`HERMES_DASHBOARD_TUI=1`, `HAL0_INFERENCE_BASE`, and an
`ExecStartPre=-/usr/local/bin/hal0-agent %i render-context` so every restart
refreshes live context (`override.conf:11-49`). `HERMES_WEB_DIST` is resolved at
runtime from the venv's real site-packages after a hardcoded `python3.12` path
crash-looped the unit on 3.14 hosts (`override.conf:26-35`).

**Shim.** `src/hal0/cli/agent_shim.py` is pure-stdlib by design so it cannot be
broken by hal0 wheel import drift, translates `serve` into
`hermes dashboard --tui --skip-build --no-open --host 127.0.0.1`, and owns the
sd_notify handshake (`:1-33`).

**Provisioner.** `hermes_provision.py` is 6 769 lines. It is explicitly *not*
checkpointed any more — idempotency comes from every write being a converging
write, and `provision.json` is a last-run snapshot (`:1-21`). The pipeline is 12
steps (`_INSTALL_STEPS:6583-6595`). `config_write` is the standout: it runs
`hermes config migrate` (Hermes owns and schema-migrates its own file), then
layers hal0's keys via `hermes config set`, then deep-merges only the two
irreducible list keys plus an operator `overrides.yaml` — never a wholesale
rewrite (`:2290-2308`). It snapshots to `config.yaml.bak` first (`:2318-2323`)
and derives the terminal-tool posture from *pre-migrate* content so a fresh box
cannot be mistaken for an existing opt-in, failing closed on an unrecordable
fresh opt-in (`:2324-2371`).

**Context files.** `_phase_context_link` renders `SOUL.md`, `/etc/hal0/AGENTS.md`,
`MCP-CLIENTS.md` from Jinja templates with observable fallbacks (`:3247-3330`).
Live state is separated from stable identity: `STATE.md.j2` is a deliberately
lean volatile snapshot with an `_as_of:` stamp (`STATE.md.j2:1-29`), injected
every session by `inject-system-state.sh`, which cats the pre-rendered file and
only kicks a **detached** background refresh when it is older than 300 s — never
blocking the session (`inject-system-state.sh:20-30`). `hermes_refresh.spawn_context_refresh`
fires the same refresh from the daemon on slot swap / capability apply without
blocking the event loop (`hermes_refresh.py:1-31`).

**Context floor.** `anchor_window.py` reads Hermes's own
`MINIMUM_CONTEXT_LENGTH` out of the installed venv rather than hard-coding it,
falling back to `HERMES_MINIMUM_CONTEXT_LENGTH = 64_000` with a drift test
(`:64-84`). It computes `effective = min(model window, slot ceiling)`, asks the
by-id route because `hal0/agent` is a virtual name that may resolve elsewhere,
treats "unknown" as *not a pass*, and is deliberately read-only — it names the
slot, its ceiling and the repair command instead of silently rewriting operator
config (`:1-50`).

**Discovery.** hal0 ships two real Hermes plugins rather than pointing a base
URL: `hal0-provider` is a `ProviderProfile` advertising hal0-api with live
`/v1/models` discovery (`X-hal0-Model-Filter: hal0`, alias-stripping, no cache)
and `default_aux_model = "hal0/agent"` so retargeting a role hot-swaps with no
gateway restart (`installer/agents/hermes/plugins/hal0-provider/profile.py:1-27`);
`hal0-memory` is an exclusive memory provider wiring Hermes durable memory to
Hindsight through `/api/memory/*` (`plugins/hal0-memory/plugin.yaml`).

**Chat surface.** `chat_proxy.py` bridges the browser to loopback Hermes over
JSON-RPC WS; WS upgrades are gated by an Origin allowlist + HMAC session cookie,
the outbound hop carries the `runtime.json` embed token as a Bearer header
(never a query string), `tool.progress` is coalesced at 100 ms while
`message.delta` passes through untouched, and reconnect is the browser's job
(`:1-40`). `_auth.py` documents *why*: an unauthenticated `0.0.0.0:8080`
JSON-RPC bridge into Hermes's tool surface is LAN-RCE (`:1-33`); the cookie is
`HMAC-SHA256` over a JSON payload with an 8 h TTL and a 0600 secret at
`/var/lib/hal0/agents/secret.bin` (`:60-80`).

**Policy.** `mcp_client.py` is a transport-agnostic *policy* layer with a
three-tier verdict (`allow`/`gated`/`blocked`, plus `unknown_server`/
`unknown_tool`) that the wire client consults (`:1-45`). Gated calls land in
`ApprovalQueue`, surfaced by `/api/agent/approvals` with an SSE stream that
replays the pending set on subscribe (`api/routes/approvals.py:1-27`).
`personas.py` owns a TOML store with `active.txt`, conservative default
auto-approve/require-approval globs, and a distinct brain-profile memory
identity (`:1-55`).

**Operator surfaces.** `api/routes/agents.py` gives list / persona-enums /
skills / install / uninstall / activity (`:68-347`); `restart.py`, and
`memory_stats.py` back the sidebar chips; `api/plugins/manifest_proxy.py`
reverse-proxies Hermes plugin manifests while stripping `Authorization`/`Cookie`
inbound, injecting `X-hal0-Agent` outbound, enforcing SRI hashes and rejecting
traversal (`:1-36`). The v3 dashboard renders agents as trading-card style
`agent-cards` with live health, throughput and restart wired to real endpoints
(`ui/src/dash/agents/agents-overview.jsx:1-20`). Test coverage is substantial:
30 files under `tests/agents/`, 56 agent-related test modules overall.

### B.1 hal0 doc drift found

1. `ARCHITECTURE.md:322-329` describes a "15-phase" pipeline including
   `persona_seed`, `namespace_register`, `model_automap` and `self_report`.
   `_INSTALL_STEPS` has 12 entries; `model_automap` is gone and the other three
   relocated to the hal0-api lifespan (`hermes_provision.py:6570-6595`).
2. `ARCHITECTURE.md:325` says "Idempotent + checkpointed via …provision.json".
   The module says the opposite — checkpoints are gone, the file is a snapshot
   (`hermes_provision.py:9-21`).
3. `ARCHITECTURE.md:272-274` says `BUNDLED_AGENTS = ("pi-coder", "hermes")`;
   code says `("hermes", "pi")` (`manager.py:117`).
4. The unit and its drop-in both point `Documentation=` at
   `docs/agents/hermes/SERVICE.md` (`hal0-agent@.service:8`,
   `override.conf:9`); that path does not exist in the tree.
5. `installer/agents/hermes/plugins/hal0-memory/provider.py` and
   `src/hal0/agents/hermes/plugins/memory_hindsight/provider.py` are
   **byte-identical 629-line copies** with no visible sync mechanism.
   `hermes_provision.py:976-979` names the installer tree as canonical.

---

## C. Honest scorecard

**ODS is genuinely better at:**

1. **Auth as a product feature.** Signed stateless cookie + `forward_auth` +
   magic links + owner cards + admin-session bootstrap is a complete,
   proxy-agnostic story (`session_signer.py`, `routers/auth.py`,
   `routers/magic_link.py`, `hermes-proxy/Caddyfile`). hal0 has one HMAC seam on
   the chat proxy and explicitly unauthenticated approval endpoints
   (`approvals.py:24-27,46-48`) that execute `model_pull` / `slot_delete` /
   `config_write`. That is the single largest gap.
2. **A mobile chat portal that never exposes the agent.** `hermes_bridge.py` +
   `talk.py` is better engineering than anything on either side: per-cookie WS
   pooling for prompt-cache warmth, one narrowly-scoped transparent retry,
   SSE keepalive, disconnect cancellation, tool-name spinner captions.
3. **Persona craft.** `SOUL.md.template` encodes hard-won behavioural rules
   (TTS-shaped output, literal-output honouring, "web_search first, never curl")
   that hal0's admin-flavoured `SOUL.md.j2` does not attempt.
4. **Agent-driven OAuth.** `oauth_passthrough.py` + `oauth-providers.json` +
   the SOUL.md script is a complete capability hal0 lacks entirely.
5. **Cross-platform config-writer discipline.** The generated-config-writers
   matrix (`docs/INSTALLER-ARCHITECTURE.md:118-135`) is a process artifact hal0
   would benefit from copying verbatim.
6. **Doc density for operators** — bump history, troubleshooting per symptom,
   explicit "known limitations" tables (`HERMES-SSO.md:129-146`).

**hal0 is genuinely better at:**

1. **Provisioning rigour.** Converging writes over checkpoints, `config.yaml.bak`,
   `hermes config migrate` + `config set` (working *with* upstream's own config
   surface instead of regexing YAML), operator `overrides.yaml`, and repair-mode
   ownership reconciliation. `patch-hermes-config.py` is a clever hack; hal0's
   `config_write` is the right answer.
2. **Context-floor correctness.** Reading the floor from the installed Hermes,
   resolving the *effective* window through the by-id route, and refusing to
   treat unknown as a pass (`anchor_window.py:1-50`) is strictly better than
   ODS's hard-coded 65536 and auto-bump of `MAX_CONTEXT`.
3. **Live-state injection.** A lean `STATE.md` refreshed on restart, on slot
   swap, and lazily on a 300 s TTL beats regenerating a large SOUL.md and
   `docker exec cp`-ing it.
4. **Sandboxing.** The systemd unit's `Protect*` set plus the brutally honest
   `ReadWritePaths` comment beats a container with `no-new-privileges` and full
   bridge-network egress (`docs/HERMES.md:139`).
5. **Deep integration.** Real Hermes plugins (provider + memory) with
   restart-free role aliases and live model discovery, versus ODS's static
   `model.default` string that needs an installer patcher.
6. **Policy that is actually wired.** The MCP allow-list → approval-queue path
   is real code; APE is a fine engine attached to nothing.
7. **Test coverage.** 30 test modules under `tests/agents/`, including
   idempotency, upgrade, ownership and security-deliverable suites.

**Equivalent:** image/wheel pinning with human-gated bumps (`HERMES.md:142-173`
vs `[tool.hal0.upstream-hermes]` + the weekly drift action); dashboard agent
cards; per-agent restart; upstream-owns-runtime posture.

---

## D. Port candidates

**D1. Signed-cookie session + `verify-session` + `admin-session` (highest value).**
Source: `ods/extensions/services/dashboard-api/session_signer.py` (171 lines),
`routers/auth.py` (180 lines). Target: `src/hal0/api/auth/session_signer.py` +
`src/hal0/api/routes/auth.py`. hal0 already has HMAC cookie machinery in
`api/agents/_auth.py:60-120` — the port is generalising it off the agent
namespace and adding the two endpoints so *any* reverse proxy can gate on it.
Shape worth copying verbatim:

```python
def verify(cookie_value: str) -> Tuple[bool, str]:
    if not _SECRET:                      return False, "no-secret"
    parts = cookie_value.split(".")
    if len(parts) != 3:                  return False, "malformed"
    random_id, expiry_str, claimed_sig = parts
    if not hmac.compare_digest(_sign(f"{random_id}.{expiry_str}").encode(),
                               claimed_sig.encode()):
        return False, "bad-signature"
    if int(expiry_str) <= int(time.time()):  return False, "expired"
    return True, "ok"
```

Size ~250 lines + tests. Dependencies: none (stdlib). Risk: **low**. Immediate
win — put `/api/agent/approvals/*` behind it.

**D2. Caddy `forward_auth` sidecar.** Source: `hermes-proxy/Caddyfile:54-129`,
`compose.yaml`, `auth-required/index.html`. hal0 has no container layer, so port
the *Caddyfile* and run Caddy as another systemd unit in front of hal0-api on
the LAN port. The three non-obvious lines to copy exactly: the explicit
`route {}` wrapper, the six `header_up -Sec-Websocket-*`/`-Upgrade`/`-Connection`
strips, and `redir * /auth/required 303`. Size ~150 lines of config. Depends on
D1. Risk: **medium** (changes the LAN trust model — see F).

**D3. OAuth passthrough + provider registry.** Source:
`routers/oauth_passthrough.py` (570 lines), `hermes/oauth-providers.json`,
`SOUL.md.template:26-60`. Target: `src/hal0/api/routes/oauth.py` +
`installer/agents/hermes/oauth-providers.json` + an addendum to
`hermes_templates/SOUL.md.j2`. Size ~600 lines + a persona section.
Dependencies: a shared operator-owned directory both hal0-api and the agent can
read — hal0 already has `/var/lib/hal0` and both run as `hal0`, which is
*simpler* than ODS's cross-container arrangement. Risk: **low-medium**; keep
the nonce-consumed-before-write ordering and the init-bound `return_url`.

**D4. The pooled WS bridge pattern.** Source: `hermes_bridge.py:162-528`. Even
though hal0's `chat_proxy.py` proxies the browser's own WS rather than pooling
server-side, the *findings* transfer directly: same-WS session create+submit,
warm-prompt-cache pooling, per-key opening locks, and — most importantly — the
`HermesConnectionStale` discipline of retrying **only** before `prompt.submit`
lands. If hal0 ever adds a REST/SSE chat surface (a phone portal), this is the
blueprint. Size ~400 lines. Risk: medium.

**D5. Tool-name → spinner caption table.** Source: `routers/talk.py:404-442`.
Target: `ui/src/dash/agents/` or the chat proxy. ~40 lines, zero dependencies,
risk none. Copy the honest fallback:

```python
return f"Using `{tool_name}`…"
```

**D6. Generated-config-writers matrix.** Source:
`docs/INSTALLER-ARCHITECTURE.md:118-135`. Target: a section in hal0's
`ARCHITECTURE.md` or `CONTRIBUTING.md` listing every writer of `config.yaml`,
`hermes.env`, the gateway drop-in, personas and `runtime.json` across
install / reprovision / repair / upgrade. Documentation only; risk none; high
value given hal0 has at least four writers of Hermes state.

**D7. `ods doctor` / `ods repair hermes-workers` pattern.** Source:
`scripts/ods-doctor.sh:479-489`, `scripts/prune-hermes-slash-workers.sh`,
policy env knobs `HERMES_SLASH_WORKER_MAX_COUNT/MAX_AGE_SECONDS`. hal0's
watchdog restarts a *hung* agent but nothing prunes leaked children. Size ~120
lines. Risk: low. Explicitly manual, never automatic — copy that stance too.

**D8. Persona voice guidance.** Source: `SOUL.md.template:18-24,77-83,103-120`.
Target: a `voice`/`concise` persona TOML in hal0's persona store rather than
overwriting the admin `SOUL.md.j2`. ~80 lines of prose. Risk none.

---

## E. Do not copy

* **`ods/extensions/services/ape/`.** The engine is decent, but it is wired to
  nothing — `pre_tool_call` appears only in a doc, and `docs/HERMES.md:140`
  concedes there is no enforcement. Its default `WriteFile.allowed_paths`
  still names `/home/node/.openclaw/workspace` (`ape/main.py:127-140`). hal0's
  `mcp_client.py` three-tier classifier plus `ApprovalQueue` already occupies
  this slot and is actually on the call path. Cherry-pick only the *ideas* —
  sliding-window tiers, circuit breaker, `require_approval` as advisory
  non-raising escalation (`main.py:36-52`).
* **`ods/agents/templates/*.yaml`.** OpenClaw-era, unreferenced by any code,
  validated once in Feb 2026 against a model neither project ships.
* **`memory-shepherd/` as shipped.** The baseline/`---`/scratch pattern is worth
  stealing conceptually, but the shipped config targets OpenClaw workspace paths
  (`memory-shepherd.conf:19-35`) and the whole thing is orthogonal to Hermes's
  own memory. hal0's Hindsight namespaces already solve durable memory;
  bolting on a periodic reset would fight it.
* **`patch-hermes-config.py`'s regex YAML surgery.** hal0's
  `hermes config set` + targeted deep-merge is strictly better. Do not regress.
* **`dashboard-api/routers/agents.py` + `agent_monitor.py`.** Metrics with no
  data source (`agent_monitor.py:27-30`) rendering an HTML fragment from an
  f-string. hal0's `/api/agents` + `memory_stats` + `activity` are better.
* **Hard-coding `HERMES_MIN_CONTEXT = 65536`** (`context_policy.py:3`) and
  auto-raising `MAX_CONTEXT` at install (`03-features.sh:93-102`). hal0's
  read-it-from-the-installed-Hermes + refuse-to-mutate posture is the correct
  one; adopting ODS's would be a regression.
* **`HERMES_DASHBOARD_INSECURE=1` (`compose.yaml:59`).** Only defensible because
  9119 is unbound. hal0's Hermes is already loopback-bound and does not need it.
* **Scraping `window.__HERMES_SESSION_TOKEN__` out of HTML**
  (`hermes_bridge.py:43`). hal0 has a real `runtime.json` embed token; regex-ing
  the SPA is a fragile fallback, not a pattern.

---

## F. Open questions for the owner

1. **Container Hermes vs pip Hermes.** ODS gets a hermetic multi-GB image with
   playwright/ffmpeg/chromium and one-line rollback; hal0 gets systemd
   sandboxing, no Docker dependency, and direct loopback access, at the cost of
   a `git clone` + venv build and an OS toolchain preflight
   (`installer/agents/hermes-prereqs.sh:1-40`). **Recommendation: keep pip.**
   The container's isolation is weaker than the unit's `ProtectSystem=strict`,
   and ODS itself had to punch `HERMES_DASHBOARD_INSECURE=1` through it.
2. **Trusted-LAN vs authenticated.** hal0's standing decision is "LAN trust plus
   an upstream reverse proxy own authentication" (`ARCHITECTURE.md:405-408`),
   yet `/api/agent/approvals/{id}/approve` executes gated tools unauthenticated
   (`approvals.py:24-27`). Either the standing decision needs a carve-out for
   mutation endpoints (D1 alone), or hal0 adopts the full ODS posture (D1+D2).
   This is the decision that gates D2, D3 and D4.
3. **Do you want a phone surface?** ODS Talk (magic link → mobile portal →
   pooled bridge → TTS) is roughly three of the port candidates and the single
   biggest feature difference. If the answer is no, D2/D3/D4 drop to
   nice-to-have and D1 alone is sufficient.
4. **Whose persona wins?** ODS's SOUL is a consumer voice assistant; hal0's is a
   homelab admin. Porting D8 as a *persona option* rather than a replacement
   keeps both, but someone must decide the default.
5. **Policy engine: build up `mcp_client` or adopt an APE-shaped sidecar?**
   Recommendation: extend `mcp_client.py` with APE's window tiers and circuit
   breaker rather than running a second service.
6. **Fix hal0's own drift first.** The five items in B.1 — especially the
   duplicated 629-line memory-provider tree and the stale 15-phase description —
   will mislead anyone doing this porting work. They are cheap to fix and should
   land before any port.
