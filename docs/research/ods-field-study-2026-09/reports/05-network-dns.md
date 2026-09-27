# Networking, DNS/mDNS, LAN access, onboarding, and auth posture: ODS vs. hal0

Scope: `/home/user/ods/ods` (ODS, Apache-2.0) vs. `/home/user/hal0` (hal0). All
paths below are repo-relative to those roots unless given in full. Both repos
were read-only; nothing was modified.

## A. ODS mechanism

### A1. mDNS and the single-:80 entry

ODS's `.local` story has two independent layers, and the docs are explicit
about how they compose (`docs/MDNS.md:5-12`, `docs/ODS-PROXY.md:1-20`):

- **Name → IP**: `bin/ods-mdns.py` (381 lines) is a standalone Python daemon,
  installed as `scripts/systemd/ods-mdns.service`, using `python3-zeroconf`
  — not a container, not host networking, a plain systemd unit as the
  install user. It polls `.env` every 30s (`ODS_MDNS_POLL_INTERVAL`,
  default 30, `bin/ods-mdns.py:93`) and republishes on change; it's a no-op
  on macOS (Bonjour already does this) and unimplemented on Windows
  (`bin/ods-mdns.py:338-350`). `_build_services()`
  (`bin/ods-mdns.py:173-259`) emits two record shapes: direct-port SRV
  records (`<device>-chat._http._tcp.local` → :3000), gated on
  `BIND_ADDRESS` actually being LAN-facing
  (`_direct_ports_lan_reachable`, lines 161-171); and per-subdomain A
  records (`chat.`, `dashboard.`, `auth.`, `api.`, `hermes.`, `talk.`, bare
  `<device>.local`) that always resolve to the proxy's IP:port regardless
  of `BIND_ADDRESS`, since the proxy is the one thing meant to be reachable.
  The installer apt/dnf/pacman/zypper-installs `python3-zeroconf` or falls
  back to `pip install --user` (`installers/phases/07-devtools.sh:479-511`)
  and renders/enables the systemd unit (lines 518-563) — non-fatal on
  failure ("device still reachable by IP").
- **Port → service**: `extensions/services/ods-proxy` is Caddy
  (`caddy:2.11.3-alpine`, `extensions/services/ods-proxy/compose.yaml:5`)
  doing **host-based** routing, not path-based — an earlier path-based draft
  broke Open WebUI's root-relative assets, websockets, and OAuth callbacks
  (`docs/ODS-PROXY.md:22-30`). The Caddyfile
  (`extensions/services/ods-proxy/Caddyfile`, 156 lines) has one `http://`
  block per subdomain, e.g.:

  ```caddyfile
  http://chat.{$ODS_DEVICE_NAME:ods}.local {
      request_body { max_size 200MB }
      reverse_proxy open-webui:8080 {
          header_up X-Forwarded-Proto {scheme}
          header_up X-Forwarded-Host {host}
      }
  }
  http://{$ODS_DEVICE_NAME:ods}.local {
      redir http://chat.{$ODS_DEVICE_NAME:ods}.local{uri} 302
  }
  ```
  (`extensions/services/ods-proxy/Caddyfile:66-78,154-156`)

  The proxy is the **one** exception to the loopback-by-default rule:
  `ODS_PROXY_BIND` defaults to `0.0.0.0` while every other service keeps
  `BIND_ADDRESS=127.0.0.1` (`extensions/services/ods-proxy/compose.yaml:26-31`,
  `extensions/services/ods-proxy/manifest.yaml:55-60`). The
  `ods-session` cookie is `Domain=<device>.local`-scoped
  (`ODS_COOKIE_DOMAIN`, installer-set), so one magic-link redemption on
  `auth.<device>.local` authenticates chat/dashboard/hermes subdomains too
  (`docs/ODS-PROXY.md:32-37`). TLS is out of scope for v1 — HTTP only, with
  Tailscale certs or a self-signed CA documented as follow-ups
  (`docs/ODS-PROXY.md:80-89`).

`docs/NETWORK.md` covers a third, unrelated thing: Wi-Fi *joining*
(`dashboard → dashboard-api → ods-host-agent(root) → nmcli`,
`docs/NETWORK.md:19-28`), Linux+NetworkManager only, `501` elsewhere.

### A2. Docker-internal DNS and ports

`docker-compose.base.yml:7` names the project `ods`; the network is pinned
explicitly: `networks: default: name: ods-network`
(`docker-compose.base.yml:473-475`). Services address each other by Compose
service name on the internal port (`http://llama-server:8080`,
`docker-compose.base.yml:94,217,251`; `searxng:8080`, line 117). Only
`dashboard-api` gets `extra_hosts: host.docker.internal:host-gateway`
(`docker-compose.base.yml:197-198`), used for the Apple-silicon path where
`llama-server` runs natively on the host and containers reach it via
`host.docker.internal:8080` (`ARCHITECTURE.md:120`).

`config/ports.json` (135 lines) is the canonical external-port contract —
one JSON array of `{env_var, external_default, service_id, internal_port,
manifest_service}` per service (llama-server→11434/8080,
open-webui→3000/8080, dashboard→3001, dashboard-api→3002, litellm→4000,
qdrant→6333/6334, etc.). Every port binding in every compose file is
`"${BIND_ADDRESS:-127.0.0.1}:${WEBUI_PORT:-3000}:8080"`-shaped
(`docker-compose.base.yml:32,172,200,454`), so one env var demotes/promotes
every service at once. `--lan` sets exactly that var
(`install-core.sh:267: --lan) BIND_ADDRESS="0.0.0.0"...`), and `ods-proxy`'s
own bind is a separate, proxy-only override
(`ODS_PROXY_BIND`) precisely so `--lan` isn't required to get the friendly
URL — `docs/ODS-PROXY.md:57` explicitly warns against setting global
`BIND_ADDRESS=0.0.0.0` "just for this."

Phase 04 (`installers/phases/04-requirements.sh:150-297`) checks port
conflicts with a three-tool fallback (`lsof` → `ss` → `netstat`,
lines 154-213), special-cases a running host `ollama` process
(lines 232-256), and only checks the ports of *enabled* features
(lines 275-283) — voice, workflows, qdrant, comfyui ports are conditional.

### A3. Headless / first-boot

Three complementary documents, all pointing at real code
(`docs/HEADLESS-SETUP.md:49-59` is the index):

- **AP mode** (`docs/AP-MODE.md`, `scripts/ap-mode.sh`,
  `scripts/systemd/ods-ap-mode.service`) — the device hosts its own
  `ODS-Setup-XXXX` Wi-Fi AP via `hostapd`+`dnsmasq`, DNATs every :80/:443
  request to the gateway IP (captive-portal trick,
  `docs/AP-MODE.md:22-34`), and lands the phone on the dashboard's
  `/setup` wizard through `ods-proxy`. **Disabled by default** — "Bringing
  up an AP is destructive... auto-enabling that would disconnect [users]
  from their own Wi-Fi" (`docs/AP-MODE.md:7-11`). Linux-only, requires
  NetworkManager release/reclaim of the wireless interface.
- **Setup cards** (`docs/SETUP-CARD.md`,
  `scripts/generate-setup-card.py`) — a 4×6", 300 DPI PNG/PDF with a Wi-Fi
  QR (`WIFI:T:WPA;S:...;P:...;;`) and a setup-URL or owner-magic-link QR
  side by side, meant to ship in the box (`docs/SETUP-CARD.md:6-16`).
- **Terminal QR fallback** (`lib/qrcode.sh`, 153 lines) — `print_dashboard_qr()`
  shells out to `qrencode -t ANSIUTF8` if present, else falls back to an
  ASCII box (`lib/qrcode.sh:32-53,56-68`); `print_success_card()` prints a
  boxed local+LAN URL summary plus the QR (`lib/qrcode.sh:75-127`). LAN IP
  detection reads the `src` field of `ip route get 1.1.1.1`
  (`lib/qrcode.sh:16-28`).
- `installers/mobile/install-mobile.sh` (23 lines) is a **stub** — it
  detects `android-termux`/`ios-ashell` and then exits 1 with "Mobile
  preview installer is split into a follow-up PR" (lines 12-18). Not
  implemented today.
- `EDGE-QUICKSTART.md` is marked "**Status: Planned — Not Yet Available**"
  (line 3); `docker-compose.edge.yml` does not exist.

### A4. Remote access + auth

- **Tailscale** (`docs/TAILSCALE.md`, `extensions/services/tailscale/`) —
  `network_mode: host` so the tailnet IP lands on the host itself
  (`extensions/services/tailscale/compose.yaml:21`); the doc is emphatic
  that this alone does nothing — `ods-proxy` + `BIND_ADDRESS=0.0.0.0` are
  still required for anything to answer on the tailnet interface
  (`docs/TAILSCALE.md:47-54`). Opt-in, Linux-only, no Funnel by default.
- **remote-provider-egress / remote-provider-ssh-tunnel** are *not*
  user-facing remote access — they are internal-only (`port: 0`,
  `external_link: false`), an "egress boundary" that injects a private API
  key at the last hop for optional **cloud** LLM providers
  (`extensions/services/remote-provider-egress/manifest.yaml:22-26`) and an
  SSH transport supervisor for the same
  (`extensions/services/remote-provider-ssh-tunnel/manifest.yaml:22-25`).
  Do not confuse with Tailscale — this is outbound-to-a-vendor, not
  inbound-for-a-user.
- **hermes-proxy** gates the Hermes agent with real cryptographic
  verification, not header-presence: Caddy `forward_auth` to
  `dashboard-api:3002/api/auth/verify-session`
  (`extensions/services/hermes-proxy/Caddyfile:101-129`), which HMAC-verifies
  the `ods-session` cookie (`extensions/services/dashboard-api/routers/auth.py:65-107`).
  The Caddyfile comment is candid about the prior, weaker design: "An
  earlier draft... only checked that the cookie HEADER was present... any
  LAN user can set `Cookie: ods-session=foo`" (`hermes-proxy/Caddyfile:8-12`).
  `docs/HERMES-SSO.md:9-13` is equally candid that this is gateway auth, not
  per-user auth: Hermes itself is one process with one session token baked
  into the SPA HTML, so "anyone with a valid invite" shares one identity.
- **Magic links** (`extensions/services/dashboard-api/routers/magic_link.py`,
  896 lines) — 32-byte `secrets.token_urlsafe` tokens, only the SHA-256
  hash persisted, single-use guest links (60 min TTL) vs. reusable
  revoke-only owner cards, 5-failed-attempts/min rate limit, QR rendered
  server-side via the `qrcode` PyPI package into a `data:image/png;base64`
  URL (`magic_link.py:376-395`). `dashboard-api/security.py` (43 lines)
  separately gates the **admin** `/api/*` surface with a Bearer
  `DASHBOARD_API_KEY`, auto-generated and written to
  `/data/dashboard-api-key.txt` (mode 0600) if unset
  (`security.py:13-23`).
- `docs/MULTI-USER-SETUP.md` is explicit that the *out-of-the-box* posture
  is single-user: "Binds every service to `127.0.0.1`... Launches
  `llama-server` with `--parallel 1`" (`docs/MULTI-USER-SETUP.md:15-19`);
  going multi-user is a six-step opt-in the operator does deliberately.
  `docs/OAUTH_PROVIDER_SETUP.md` is a separate, narrower thing — an OAuth
  *callback capture* helper for Hermes skill setup, metadata-only, no
  secrets committed.

### A5. Security posture summary

Default posture: everything on `127.0.0.1` except `ods-proxy`, which is
LAN-open by design once enabled (`SECURITY.md:55-63`). `--lan` (or
`BIND_ADDRESS=0.0.0.0` + `ods restart`) widens every service at once
(`SECURITY.md:57-68`); the host-agent has its own separate, narrower
`ODS_AGENT_BIND` that auto-detects the Docker gateway IP on Linux rather
than defaulting to `0.0.0.0` (`SECURITY.md:84-102`). Behind the proxy, each
backend keeps its own check — dashboard-api's Bearer key, Open WebUI's own
`WEBUI_AUTH`, hermes-proxy's `forward_auth` — the proxy itself "adds NO auth
layer... duplicating without strengthening" (`docs/ODS-PROXY.md:62-72`).
`privacy-shield` (`extensions/services/privacy-shield`, port 8085) is
content-layer, not access-layer — a PII-scrubbing proxy in front of the LLM,
unrelated to who's allowed to connect.

## B. hal0 today (verified by grep)

**Correction to the task's framing**: `grep -rn -i 'avahi\|mdns\|\.local\|zeroconf\|bonjour'` across hal0 returns **over 300 matches** in `src/`, `installer/`, `ui/`, `tests/`, and `docs/` — hal0 already has a real, tested mDNS subsystem. It is architecturally different from ODS's, not absent.

- **`src/hal0/services/mdns.py`** (150 lines) is the mirror of
  `bin/ods-mdns.py`, but it does not run its own responder — it writes
  standard **avahi service-group XML** files that the *system's*
  `avahi-daemon` inotify-watches (`services/mdns.py:10-11`), the same
  mechanism ODS's macOS path leans on natively. `mdns_hostname()` returns
  `<HAL0_HOSTNAME or socket.gethostname()>.local` (`services/mdns.py:40-49`);
  `advertise()`/`withdraw()` write/prune one `hal0-addon-<id>.service` file
  per LAN-published, mDNS-capable service (`services/mdns.py:100-141`):

  ```python
  def _service_group_xml(name: str, port: int) -> str:
      safe = escape(name)
      return f"""<?xml version="1.0" standalone='no'?>
  <service-group>
    <name replace-wildcards="yes">{safe} on %h</name>
    <service>
      <type>_http._tcp</type>
      <port>{port}</port>
    </service>
  </service-group>
  """
  ```
  (`services/mdns.py:81-97`, abridged)

  Only two services are `mdns=True` in the catalog:
  `services/registry.py:65-114` — OpenWebUI (:3001) and ComfyUI (:8188).
  Hermes (:9119) and Hindsight (:9177) are `loopback_port`-only with **no**
  mDNS/host:port fallback at all (`registry.py:96-113`) — closer to ODS's
  "loopback stays loopback" instinct than the headline framing suggests.
- This is wired to a real UI: `ui/src/dash/services.jsx` has a
  `DiscoveryCard` ("DISCOVERY (mDNS)") with an avahi active/inactive dot and
  an advertise/withdraw toggle (`services.jsx:279-315`), backed by
  `GET/POST /api/services/mdns` (`api/routes/services.py:269-311`,
  `useMdnsAdvertise`/`useMdnsStatus` in `useServices.ts:114-119`), and
  covered by `tests/api/test_services_page.py:262-309` and
  `tests/installer/test_avahi_hostname.py` (avahi-daemon.conf `host-name=`
  pinning, `installer/install.sh:1372-1451`).
- **Gap found in this subsystem**: `mdns.status()` reports
  `base_advertised: (services_dir()/"hal0.service").is_file()`
  (`services/mdns.py:76`), i.e. whether *hal0-api itself* (not an addon) is
  announced. Nothing in the current tree writes that file — the docstring
  says "if something else drops" it (`services/mdns.py:3`), and
  `CHANGELOG.md:3676` records a stale `avahi/hal0.service` **unit** being
  deleted, not a service-group file being added. In practice
  `base_advertised` is very likely always `False` on a stock install; the
  dashboard's own copy says as much ("No base `hal0.service` avahi file —
  the installer writes it when avahi is present," `services.jsx:304`) —
  a promise the installer does not currently keep.

**Bind posture** — confirmed exactly as briefed, with one nuance the brief
undersells:

- `src/hal0/install/network.py:43`: `DEFAULT_BIND_HOST = "0.0.0.0"` — this
  is what `install.sh` seeds into `/etc/hal0/api.env`. `hal0-api` binds
  `0.0.0.0:8080` on a stock install, no `--lan` equivalent needed.
- `src/hal0/openwebui/env_writer.py:164`: OpenWebUI's companion follows the
  *same* `HAL0_BIND_HOST` (also defaulting to `0.0.0.0`) — but this is a
  **fix**, not the original design: `CHANGELOG.md:3091` documents #1515/#1514,
  where OpenWebUI used to hardcode `-p 0.0.0.0:3001:8080` regardless of the
  operator's `HAL0_BIND_HOST`, so someone who bound the API to loopback got
  "a wide-open chat UI on :3001 regardless." `WEBUI_AUTH=False` unless
  `HAL0_OWUI_TRUSTED_EMAIL_HEADER` is set (`env_writer.py:166-172`).
- **Nuance**: inference *slot* containers (llama-server, the NPU trio,
  ComfyUI-as-slot) are **not** on `HAL0_BIND_HOST` — they publish
  loopback-only by default (`[slots].publish_host`, fail-soft to
  `127.0.0.1`, `src/hal0/providers/container.py:17-23,550-561`), and under
  `Network=host` the code rewrites any `--host 0.0.0.0`/`--listen 0.0.0.0`
  in the child's own argv back to loopback, since host networking has no
  `PublishPort` fence (`container.py:610-651`). So: control-plane API +
  chat UI default open, raw inference ports default closed.
  `src/hal0/config/network.py:34`'s own library fallback (no env at all)
  is also loopback — it's specifically the installer's choice to write
  `0.0.0.0` into `api.env` that makes the shipped default LAN-wide.
- `src/hal0/api/agents/_auth.py:1-8` states the underlying incident plainly:
  "exposing a long-running JSON-RPC bridge to the hermes runtime over an
  unauthenticated `0.0.0.0:8080` is LAN-RCE."

**`src/hal0/service_identity.py`** (307 lines) resolves the box's own
service-to-service key (`HAL0_ADMIN_KEY`/`HAL0_CLIENT_KEY`, env then
`/etc/hal0/api.env`, `service_identity.py:71-88`) for hal0's own internal
callers, and owns key rotation (`rotate_api_env_key`, atomic tmp+rename,
`0640`, `service_identity.py:148-234`) plus keeping `hindsight-llm.env` in
sync (`refresh_hindsight_llm_env`, lines 245-296).

**`src/hal0/security/`** — `exposure.py` (436 lines) is a single ordered,
first-match-wins classification table (`RULES`, lines 153-326) sorting
every route into `OPEN` / `BOOTSTRAP` / `CLIENT` / `ADMIN`
(`AuthClass`, lines 71-77), **deny-by-default** for anything unlisted
(`classify()`, lines 346-354). `ratelimit.py` (138 lines) backs the login
and key-rotation rate limiter. This is consumed by
**`src/hal0/api/auth.py`** (542 lines): `AuthEnforcementMiddleware` is a
pure-ASGI gate (lines 431-530) resolving a request to `anon`/`client`/
`admin` via cookie → Bearer → `?api_key=` (`resolve_principal`, lines
289-321), plus an Origin-based CSRF check for browser-driven state changes
(`_origin_allowed`, lines 344-382). Crucially, **`require_auth_enabled()`
defaults to `False` unconditionally** (`api/auth.py:128-157`) — a
documented reversal: an earlier KB-1 design auto-enabled enforcement
whenever the bind was non-loopback or a key existed, and that "locked
operators out of a dashboard that shipped no login UI... so they disabled
auth wholesale" (`api/auth.py:137-145`). So today: LAN-open bind **and**
auth-off are both the shipped default, independently.

**`src/hal0/ports/`** — `authority.py` (389 lines) is `PortAuthority`, the
single writer of port *ownership* (one `port_claim` DB row per live slot,
partial-unique-indexed, `authority.py:1-19`); `__init__.py` (229 lines,
not fully read) is the read-side harvester. Unlike ODS's static
`config/ports.json` contract, hal0's port map is *dynamic and allocated at
runtime* per slot — there is no single canonical file enumerating "which
port serves what," except the small, fixed `services/registry.py` catalog
for the four companion services.

**Docs already say this out loud**: `README.md:563-565` ("Auth ships off by
default. A fresh v1.0 install is trusted-LAN-open — the right posture for a
homelab appliance"), `docs/operate/auth.mdx` (336 lines, full three-tier
reference, `<Aside type="caution">` at lines 15-27), and
`docs/concepts/security.mdx:3` ("hal0 ships open on your LAN by default,
but v1.0 adds a real, opt-in three-tier auth layer"). Install itself prints
a QR to the dashboard URL via `qrencode` when present
(`installer/install.sh:3908-3916`, `HAL0_NO_QR=1` to skip) and an explicit,
if quietly-styled, line in the same summary box:
`"Auth        open on the trusted LAN — front with a reverse proxy if
exposed"` (`installer/install.sh:3934`).

**Issue #1822, pinned to the exact gap**: `src/hal0/cli/doctor_all.py:62-83`,
the one function whose entire job is to flag auth misconfiguration:

```python
def check_auth_posture(auth: dict[str, Any] | None) -> Check:
    """... The only misconfiguration we flag is "auth required but no admin
    key configured" (nobody can log in); an intentionally open dev install
    passes with a note.
    """
    ...
    if not required:
        return Check("auth", "Auth posture", _PASS,
                      "open (auth not required — dev/loopback)")
```

`grep -n 'bind_host\|BIND_HOST\|0\.0\.0\.0' src/hal0/cli/doctor_all.py`
returns nothing: this check never looks at `HAL0_BIND_HOST` at all. A stock
install — `0.0.0.0` bind, auth off — gets an unconditional green `PASS`
whose own message ("dev/loopback") describes a configuration the box is
*not* actually in. `hal0 doctor all` / `hal0 doctor verify` and `GET
/api/doctor` (`security/exposure.py:252` classifies it ADMIN) are the tools
built precisely to catch this class of drift, and none of them cross-check
bind against auth. The install-time line (`install.sh:3934`) and the
Settings ▸ Security page (`ui/src/dash/settings/pages/server/
SecurityPage.jsx:154`: "Auth is off — hal0 runs trusted-LAN open") are the
only two surfaces that say it, and both require the operator to already be
looking in the right place, once, rather than being told by the tool whose
job is to tell them what's wrong.

One historical note for anyone tracing "why no Caddy/auth already":
`CHANGELOG.md:5050-5053` records `v0.3.0-alpha.1` as "**Caddy and the auth
surface are removed**" per **ADR-0012**, which superseded ADR-0001's softer
plan. `docs/adr/` on disk only goes up to `0006` and has no `0012` — this
is not drift, `CHANGELOG.md:14` explains ADRs are filed under a
`docs/internal/` tree that is gitignored (issue `#638`), same convention
as this repo's current `docs/.devdocs/`/`docs/superpowers/`. So ADR-0012's
own reasoning is genuinely unavailable in this checkout; only its effects
(the Caddy+auth removal, and the later KB-1 opt-in re-addition) are
reconstructable from `CHANGELOG.md` and the code. **`ARCHITECTURE.md:
111-113`** ("dedicated auth packages... were removed") is accurate about
that historical cut but silent about `api/auth.py`/`security/exposure.py`
existing today as an opt-in, off-by-default replacement — a live instance
of this repo's own "verify against source, not memory" rule.

## C. Better / worse / equivalent

| Dimension | ODS | hal0 | Verdict |
|---|---|---|---|
| Name→IP mechanism | Own zeroconf daemon + systemd unit + pip/apt dep | System avahi-daemon + plain XML files, no extra process | **hal0 lighter, equivalent outcome** |
| Port→service | Single Caddy on :80, host-based vhosts, one URL | Each own port; dashboard derives links from request Host (`_behind_proxy`/`_resolve_host`, `services.py:38-73`) | **ODS better UX; hal0 needs no proxy at all for LAN-IP/raw-port use** |
| Canonical port map | `config/ports.json`, static, one file | `services/registry.py` (4 svcs) + dynamic `PortAuthority` DB for slots | **Different shapes, each fits its own domain** |
| Default bind (control plane) | Loopback; proxy is the sole opt-in LAN surface | LAN-open (`0.0.0.0`) by default | **ODS safer default** |
| Default bind (inference) | Loopback (`BIND_ADDRESS`) | Loopback (`[slots].publish_host`) | **Equivalent** |
| Auth model | Cookie SSO across Caddy subdomains + per-service checks + magic links | Deny-by-default classifier, three tiers, off by default | **hal0 more rigorous design; ODS safer default** |
| Auth-off visibility | N/A — LAN needs `--lan`, an explicit act | Told once at install + in Settings, but `doctor_all.py:75` actively says "PASS" | **ODS better — hal0's own health-check contradicts its docs** |
| Headless/QR onboarding | Full stack: AP mode, setup cards, `lib/qrcode.sh`, mobile stub | One QR to the dashboard URL at install; no AP mode, no card | **ODS far ahead** |
| Remote access | Tailscale extension, documented prerequisites, opt-in | None | **ODS ahead (feature gap)** |
| Egress-boundary services | remote-provider-egress/ssh-tunnel: real, scoped, internal-only | No equivalent (`upstreams/` handles external providers differently) | **Different architectures, not a gap** |

## D. Port candidates

| # | Candidate | hal0 target file(s) | Size | Risk | Notes / excerpt |
|---|---|---|---|---|---|
| 1 | Close the #1822 gap: cross-check bind + auth in the doctor | `src/hal0/cli/doctor_all.py:62-83` (`check_auth_posture`); needs `hal0.config.network.bind_host()` (`src/hal0/config/network.py:37-45`) or `GET /api/config/urls` as the bind signal | ~20-30 line diff + 1 test | **Very low** — read-only, additive, no behavior change | Demote to `_WARN` when `bind_host() not in _LOOPBACK_BIND_HOSTS and not required`; message: `"open on a LAN-reachable bind — run 'hal0 auth require on' or set HAL0_BIND_HOST=127.0.0.1"`. This is the single highest-leverage, lowest-risk fix in this whole report. |
| 2 | Write the missing base avahi file | `src/hal0/services/mdns.py` (extend `advertise()`/add a `advertise_base()`); hook from `installer/install.sh` near the existing avahi block (`install.sh:835-851`) | ~30-50 lines | **Low** — same fail-soft, tmp+rename pattern already proven for addons | Makes `base_advertised` (`services/mdns.py:76`) true on a stock install, and the dashboard's own caption (`services.jsx:304`) stop describing a promise nothing keeps. |
| 3 | Single-port LAN gateway (ODS-proxy-equivalent) | New `packaging/hal0-proxy/Caddyfile` + a systemd/quadlet unit; `installer/install.sh` hookup; must respect `X-Forwarded-Host` in `_behind_proxy`/`_resolve_host` (`services.py:38-73`) so links don't regress to raw ports | ~150-250 lines | **Medium** — WS/SSE passthrough needs the same header-stripping care `hermes-proxy/Caddyfile:104-119` documents; must not fight hal0's existing request-Host link trick (`useConfigUrls.ts:1-12`) | Ports almost verbatim from `ods-proxy/Caddyfile`'s `chat.<device>.local` block (lines 66-78), service names swapped for `127.0.0.1:8080`/`:3001`/`:8188`. |
| 4 | Headless setup card + two-QR onboarding | New `installer/lib/qrcode.sh`-equivalent (model: `lib/qrcode.sh`, 153 lines) + a `hal0 setup-card` command reusing `detect_lan_ips` (`install/network.py:58-96`) | ~100-150 lines | **Low** — additive, operator-invoked, no daemon | hal0 already has the terminal-QR primitive (`install.sh:3908-3916`); the gap is a printable card and, separately, a phone-driven first-boot path — `install.sh` is a foreground console script, not a served wizard. |
| 5 | Elevate the install-summary auth line to a real warning | `installer/install.sh:3934` (currently `${DIM}`-styled) | Trivial | **Very low** | Cheapest partial mitigation for #1822 if the doctor fix (D1) is deferred. |
| 6 | AP mode / true out-of-box Wi-Fi setup | Greenfield: `hostapd`/`dnsmasq`/`iptables` orchestration, captive portal, wizard integration | 300-600+ new lines + unit + hardware testing | **Medium-high** — ODS itself ships this disabled, calling it "destructive" (`docs/AP-MODE.md:7-11`) | Worth it only if hal0 moves toward a sealed-appliance framing, not "operator runs the installer on their own box." |

## E. Do-not-copy

- **ODS's dedicated zeroconf daemon wholesale.** `bin/ods-mdns.py` +
  `python3-zeroconf` + its own systemd unit exists because ODS wants
  fine-grained control over SRV *and* subdomain records independent of
  whatever avahi happens to be doing. hal0's existing avahi-XML approach
  (`services/mdns.py`) gets the same `.local` resolution with zero extra
  process, zero extra dependency, and no polling loop — for hal0's simpler
  shape (one host, a couple of LAN ports) it is strictly less to run and
  maintain. Adopting ODS's daemon would be a regression in simplicity for
  no discovery-fidelity gain hal0 currently needs.
- **The `ODS_COOKIE_DOMAIN=<device>.local` multi-subdomain SSO trick** in
  isolation. It solves a problem (one login across `chat.`/`dashboard.`/
  `hermes.` origins) that only exists because ODS chose host-based routing.
  hal0's `hal0_session` cookie (`api/agents/_auth.py`) is already
  host-only and origin-checked; grafting a `Domain=` scope on without
  first adopting subdomain routing does nothing useful and widens the
  cookie's reach for no reason.
- **AP mode, unless the product direction genuinely wants a sealed
  appliance.** ODS itself treats it as dangerous enough to ship disabled
  (`docs/AP-MODE.md:7-11`); it is Linux-only, chipset-dependent
  (`docs/AP-MODE.md:137`), and every failure mode stops the box from
  joining any network at all.
- **remote-provider-egress/ssh-tunnel as a "remote access" model.**
  They are outbound egress-boundary services for optional cloud LLM
  providers, unrelated to letting a user reach the box from outside. Don't
  reach for them when the actual ask is "Tailscale for hal0."
- **The full magic-link/owner-card persistence subsystem
  (`magic_link.py`, 896 lines) as a literal port.** It's built for a
  multi-person-household product frame (revoke-only owner cards, guest
  invites, redemption audit trail). hal0's KB-1 model (one admin-equivalent
  browser session, two static keys) is deliberately simpler for a
  single-operator appliance; porting the whole subsystem imports a
  product assumption hal0 hasn't made yet.

## F. Owner decisions

1. **Fix #1822 now, cheaply.** Candidate D1 (doctor cross-check) is a
   ~20-line, no-risk change that stops `hal0 doctor all` from actively
   asserting "PASS... dev/loopback" on a box that is neither. This should
   ship regardless of anything else below.
2. **Pick a LAN-exposure model deliberately, don't drift into one.**
   Either (a) keep today's "each service its own port, links follow
   request Host" shape and simply default `HAL0_BIND_HOST` to loopback the
   way `hal0.config.network`'s own library fallback already does
   (`config/network.py:34`), pushing LAN exposure behind an explicit
   opt-in the way ODS's `--lan` does; or (b) build the single-gateway
   proxy (D3) and accept the added moving part in exchange for one URL and
   one audited edge. KB-1's own history (`api/auth.py:137-145`) shows an
   auto-enable-on-LAN-bind policy was tried and reverted because there was
   no login UI to catch the user — that objection no longer holds now that
   `docs/operate/auth.mdx` and the Settings ▸ Security page exist, so
   revisiting a safer default bind is worth a fresh look, not a repeat of
   the same argument.
3. **Decide how much headless/appliance investment is warranted.** ODS's
   full stack (AP mode, setup cards, mobile installer) is aimed at
   ship-in-a-box, hand-to-a-relative use. If hal0 stays "operator runs the
   installer on their own hardware," candidates D4/D5 (QR card, sharper
   warning) are enough; D6 (AP mode) is only worth it if hal0 explicitly
   adopts the sealed-appliance framing.
4. **Close the dead `base_advertised` path (D2)** — small, and removes a
   UI caption that currently describes something the installer doesn't do.
5. **Scope remote access separately from egress.** If Tailscale-for-hal0
   is wanted, design it as its own feature against hal0's actual shape
   (single systemd unit, not docker-compose extensions) rather than by
   analogy to ODS's `remote-provider-*` services, which solve a different
   problem.
