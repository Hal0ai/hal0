# ODS extension system → hal0: a source-level comparative study

Scope: the ODS extension mechanism (`/home/user/ods/ods/`), verified against source,
and a concrete proposal for expressing the same shape in hal0's systemd/podman world
(`/home/user/hal0/`). All line numbers are from the trees as read on 2026-09-05.

---

## A. How ODS does it (mechanism-level)

### A.1 The unit of extension is a directory with a manifest

Every ODS service — bundled or user-installed — is one directory whose only
mandatory contents are `manifest.yaml` and (for non-core services) `compose.yaml`.
`/home/user/ods/ods/extensions/README.md:51` states the contract plainly:

> The core contract is simple: a service manifest describes what the service is,
> and a compose fragment describes how it runs. The registry, CLI, dashboard,
> health checks, and compose resolver discover the service from those files.

That is literally true in code. There are **four independent walkers** over
`extensions/services/*/` (plus `data/user-extensions/*/`), each doing its own
manifest read, none of them requiring registration anywhere else:

| Walker | File:line | What it produces |
|---|---|---|
| Bash service registry | `/home/user/ods/ods/lib/service-registry.sh:5,50` | 14 associative arrays: aliases, container names, compose paths, categories, deps, health paths/timeouts, ports/port-envs, host-network flag, display names, setup hooks, GPU backends |
| Compose resolver | `/home/user/ods/ods/scripts/resolve-compose-stack.sh:517` | the `-f a.yml -f b.yml …` flag string for `docker compose` |
| Dashboard API | `/home/user/ods/ods/extensions/services/dashboard-api/config.py:297` | `SERVICES` dict (health checks, quicklinks) + `FEATURES` list |
| Catalog generator | `/home/user/ods/ods/scripts/generate-extensions-catalog.py:123` | `config/extensions-catalog.json` for the dashboard Extensions page |

All four gate on the same sentinel, `schema_version: ods.services.v1`
(`service-registry.sh:136`, `resolve-compose-stack.sh:540`, `config.py:336`), and all
four accept `manifest.yaml` | `manifest.yml` | `manifest.json`
(`service-registry.sh:123-127`, `resolve-compose-stack.sh:524-528`, `config.py:319`).

### A.2 The manifest contract (`extensions/schema/service-manifest.v1.json`)

Root: `schema_version` (const), optional `compatibility{ods_min,ods_max}`, `service`
(required), `features[]`, `tags[]`. The schema is `additionalProperties: true`
throughout, so forks add `x_`-prefixed fields without breaking validation
(`docs/SERVICE_MANIFEST_V2_PLAN.md:79-82`). Its library copy
(`extensions/library/schema/service-manifest.v1.json`) is byte-identical — verified by
diff — and kept so by `scripts/sync-manifest-schema.py`.

Per-field consumers (the load-bearing ones):

- `id` — registry key, compose-service name, CLI arg, container-name default `ods-<id>`
  (`service-registry.sh:158`), directory name (enforced by
  `scripts/audit-extensions.py:463` `service-id-directory-mismatch`).
- `aliases[]` — CLI shorthand; `sr_resolve()` also strips a leading `ods-` so a name
  copied out of `docker ps` resolves (`service-registry.sh:263-269`).
- `port` / `external_port_env` / `external_port_default` — `SERVICE_PORTS` seeded from
  the manifest, then overridden from `.env` by `sr_resolve_ports()`
  (`service-registry.sh:239-247`); the same resolution in Python at `config.py:371-377`.
- `health` / `health_timeout` — the generic probe URL is built as
  `http://127.0.0.1:${port}${health}` in `scripts/health-check.sh:159-188`.
- `category` (`core|recommended|optional`) — `core` means "defined in
  `docker-compose.base.yml`, no compose fragment, cannot be disabled"
  (`ods-cli:2762`, `ods-cli:2879`).
- `compose_file` — relative path; the resolver rejects one that escapes the extension
  dir (`resolve-compose-stack.sh:418-426`) and treats a `.disabled` suffix as "off".
- `depends_on[]` — drives the enable-cascade prompt (`ods-cli:2805-2831`), the
  reverse-dependent warning on disable (`ods-cli:2881-2902`), and installer-time
  `validate_service_dependencies` (`installers/phases/11-services.sh:1133`).
- `gpu_backends[]` — the resolver skips a manifest whose backends don't include the
  detected one, with `all`/`none` as wildcards (`resolve-compose-stack.sh:544-547`);
  `ods enable` warns-and-prompts instead of refusing (`ods-cli:2768-2803`).
- `hooks.{pre,post}_{install,start,uninstall}` / legacy `setup_hook` —
  `SERVICE_SETUP_HOOKS` prefers `hooks.post_install` (`service-registry.sh:196-203`);
  installer phase 11 runs every non-empty hook with `$1=INSTALL_DIR $2=GPU_BACKEND`
  (`installers/phases/11-services.sh:1374-1386`). Live example:
  `extensions/services/langfuse/hooks/post_install.sh` chowns bind mounts for the
  uid-70 postgres / uid-101 clickhouse images.
- `startup_check` / `startup_timeout` — one-shot CLI extensions whose container exits
  cleanly (`service-registry.sh:186`, dashboard `_is_one_shot_extension`,
  `routers/extensions.py:317-325`).
- `host_network` — services with `network_mode: host` have no mapped port and no HTTP
  health; the flag switches off both checks (`service-registry.sh:38-42`,
  `audit-extensions.py:489-503`).
- `ui_path` / `external_link` / `public_url_env` — the dashboard sidebar quicklink,
  built entirely from manifests at `extensions/services/dashboard-api/main.py:1567-1583`.
- `llm{consumes,route,pinning,min_context,probe}` — the swap-safety contract
  (`docs/SWAP-SAFE-EXTENSIONS.md:38-72`). `route: gateway` means
  `http://litellm:4000/v1` + model `ods/current`; the schema conditionally *requires*
  `route`, `pinning` and `probe` once `consumes: true`.
- `features[]` — dashboard feature cards. Requirements split into runtime health
  (`requirements.services` / `services_any` / `vram_gb`) and enablement
  (`enabled_services_all` / `enabled_services_any`), plus `launch{type,service,path}`
  and `priority`.

Supporting docs: `docs/EXTENSIONS.md` (authoring + runtime lifecycle, 514 lines),
`docs/COMPOSE_RESOLVER_CONTRACTS.md:38-58` ("Adding A Service" = exactly two files),
`docs/SWAP-SAFE-EXTENSIONS.md` (LLM tiers), `docs/EXTENSION-PR-BRANCHING.md:9-14`
(core vs. library branch targets), `docs/BUILD-ON-ODS-SERVER.md:37-53` (fork guidance),
`docs/SERVICE_MANIFEST_V2_PLAN.md` (planning note only; v1 is the shipped schema).

### A.3 The compose layer model

`scripts/resolve-compose-stack.sh` is 772 lines, of which ~700 are one embedded Python
program. Order of assembly:

1. **Base + hardware overlay** (lines 54-122): a cascade selecting
   `docker-compose.base.yml` plus one of `nvidia|amd|apple|arc|intel|cpu|cloud|
   lemonade-external`, keyed on `--tier`, `--gpu-backend`, `--ods-mode`, and env
   (`LEMONADE_EXTERNAL`, `EXTERNAL_LLM_URL`).
2. **Multi-GPU overlay** (143-146): `docker-compose.multigpu-<backend>.yml` when
   `--gpu-count > 1`.
3. **Bundled extensions** (517-586): for each `extensions/services/<id>/` with a v1
   manifest and a compatible `gpu_backends`, append `compose.yaml`, then — *by
   filesystem probe, not by manifest* — `compose.<backend>.yaml` (563),
   `compose.local.yaml` (578, only in local/hybrid mode, non-Apple, non-external-LLM),
   `compose.multigpu-<backend>.yaml` (584).
4. **User extensions** (607-725): same walk over `data/user-extensions/*/`, but every
   fragment is content-scanned first by `_scan_user_compose_content`
   (`resolve-compose-stack.sh:270-406`) — rejecting `privileged`, `build:`,
   `user: root`, host net/pid/ipc/userns, dangerous caps, `seccomp:unconfined`,
   `devices:`, GPU reservations, docker-socket mounts, absolute bind mounts,
   `extra_hosts`, `sysctls`, reserved `com.docker.compose.*` labels, non-loopback port
   bindings, and any service name colliding with `config/core-service-ids.json`.
5. **`docker-compose.external-llm.yml`** (730-735) and **`docker-compose.override.yml`**
   (742-748, also content-scanned).

Extension composes carry no `networks:` block for the bundled set (see
`extensions/services/searxng/compose.yaml`) — they land on the compose project's
default network and are reachable by their **compose service name**. The library
template does declare `networks: [ods-network]` with `external: true`
(`extensions/library/templates/compose-template.yaml:64-92`).

`ods-cli` caches the resolved string in `$INSTALL_DIR/.compose-flags` and revalidates
every referenced path before using it (`ods-cli:852+`); `_regenerate_compose_flags`
(`ods-cli:755`) re-runs the resolver whenever an extension is enabled or disabled.

### A.4 Enable/disable is a file rename

`ods enable <id>` renames `compose.yaml.disabled` → `compose.yaml`; `ods disable`
stops the container then renames back (`ods-cli:2732-2920`, dispatched at
`ods-cli:6567-6568`). There is no state database. `ods list` derives status by asking
whether the compose path exists *and* appears in the currently active flag string
(`ods-cli:3004-3065`). The dashboard API does the same rename under `flock`
(`docs/EXTENSIONS.md:437-444`), and `config.py:329-333` skips any extension directory
containing `compose.yaml.disabled` so a disabled service vanishes from health checks
and feature recommendations without touching its manifest.

`.env` feature flags (`ENABLE_VOICE`, `ENABLE_WORKFLOWS`, `ENABLE_COMFYUI`, …) are a
**second, installer-only** gate that works by performing the same rename.
`installers/phases/03-features.sh:104-131` is explicit about why:

```bash
# Without this sync, an extension's compose.yaml is ALWAYS picked up by
# resolve-compose-stack.sh regardless of the ENABLE_* flag — the flag then
# only gates cosmetic things (image pre-pull, health checks, summary URLs)
# and the service still starts. Every optional service must be listed here
# or the user can't opt out of it.
```

The 21 `_sync_extension_compose` calls at lines 179-204 are hand-maintained. This is
the single biggest seam in an otherwise auto-discovering system.

### A.5 Ports

`config/ports.json` is **not** a runtime input. Runtime ports come from the manifest
plus `.env`. `ports.json` exists only for `tests/contracts/test-port-contracts.py`,
which cross-checks each declared entry against `.env.schema.json` defaults,
`.env.example` defaults, the manifest's `external_port_env`/`external_port_default`,
and the actual compose port mapping (lines 106-195). Because the test iterates
`ports.json` entries — not manifests — a new extension with a new port env var passes
CI without being added. Adding it is opt-in rigour for bundled services.

### A.6 Health checks

`scripts/health-check.sh` is fully registry-driven: `test_service()` (line 159) reads
`SERVICE_PORT_ENVS`, `SERVICE_PORTS`, `SERVICE_HEALTH`, `SERVICE_HEALTH_TIMEOUTS`,
checks the container state via `docker inspect` first, then curls
`http://127.0.0.1:<port><health>`. It loops all core services (line 304) and all
*enabled* extension services (369, gated on `-f "$ext_compose"`) in parallel; a core
failure is critical (exit 2), an extension failure is degraded (exit 1).

Installer phase 12 (`installers/phases/12-health.sh`) is the exception: it does
`sr_load` + `sr_resolve_ports` (lines 23-30) and pulls ports/paths/container names from
the registry, but each probe is a hand-written line gated on an `ENABLE_*` flag
(e.g. line 616 for n8n). A new extension gets no installer-phase probe for free; it
does get `scripts/health-check.sh` and the dashboard's `/api/services` for free.

### A.7 Dashboard surface

- `GET /api/features` (`routers/features.py:113`) computes per-feature status from
  manifest `features[]` + live service health + GPU VRAM. Vocabulary
  (`features.py:70-77`): `enabled` (all `enabled_services_all` healthy) →
  `insufficient_vram` (total VRAM < `requirements.vram_gb`) → `services_needed`
  (required services not healthy) → `available`. It also emits `suggestions[]`
  ("Your hardware can run X. Enable it?" / "X needs Y to be running.", lines 130-144)
  and coarse `recommendations[]` bucketed by VRAM (162-177).
- `GET /api/extensions/catalog` (`routers/extensions.py:1090`) serves the static
  `config/extensions-catalog.json` enriched by `_compute_extension_status`
  (`extensions.py:328-402`): `installing` / `setting_up` / `error` / `cli_installed` /
  `enabled` / `unhealthy` / `stopped` / `disabled` / `incompatible` / `not_installed`.
  The React page renders exactly that vocabulary
  (`extensions/services/dashboard/src/pages/Extensions.jsx:60-80,336-337`).
- `GET /api/external-links` (`main.py:1567`) turns every manifest with an external port
  into a sidebar quicklink. A new extension appears in the sidebar with no UI change.
- The dashboard plugin registry (`dashboard/src/plugins/registry.js`) is the *only*
  place needing a code edit — and only for an extension that wants its own internal
  dashboard route, not for a quicklink.

Install flow for catalog extensions: `POST /api/extensions/{id}/install` copies from
`$ODS_DATA_DIR/extensions-library/<id>/` to `$ODS_DATA_DIR/user-extensions/<id>/` after
a 50 MB size check and the compose security scan, then asks the host agent
(`bin/ods-host-agent.py`, `POST /v1/extension/start`) to bring it up
(`docs/EXTENSIONS.md:425-455`).

### A.8 Pre-wiring: what an extension author must actually know

Four conventions, and only four:

1. **Docker DNS = compose service name.** `open-webui` reaches search as
   `http://searxng:8080/search?q=<query>&format=json`
   (`docker-compose.base.yml:117`), embeddings as `http://embeddings:80/v1` (127),
   STT as `http://whisper:8000/v1` (159), TTS as `http://tts:8880/v1` (164).
2. **LLM = the gateway alias.** `OPENAI_BASE_URL=http://litellm:4000/v1`, model
   `ods/current`, key `${LITELLM_KEY}` (`docs/SWAP-SAFE-EXTENSIONS.md:17-33`). Direct
   routes are permitted but must declare `pinning: dynamic` + a probe. In practice
   several bundled services take the direct route with a documented fallback chain —
   `extensions/services/perplexica/compose.yaml:13`:
   `OPENAI_BASE_URL=${HERMES_LLM_BASE_URL:-${LLM_API_URL:-http://llama-server:8080}/v1}`.
3. **Ports bind loopback with an env override:**
   `"${BIND_ADDRESS:-127.0.0.1}:${MY_PORT:-1234}:1234"` — enforced for user extensions
   by the resolver scan (`resolve-compose-stack.sh:315-333`).
4. **Data lives at `./data/<id>/`, config at `./config/<id>/`** — see
   `extensions/services/n8n/compose.yaml:24-25`.

Templates encode all of this:
`extensions/library/templates/service-template.yaml` (151 commented lines),
`compose-template.yaml`, `compose-gpu-swap.yaml`, `compose-gpu-only.yaml`,
`dashboard-plugin-template.js`.

### A.9 Three worked examples

**searxng — 3 files, 55 lines of contract.** `manifest.yaml` (22 lines: id, alias
`search`, 8080→`SEARXNG_PORT:8888`, `health: /healthz`, `gpu_backends: [all]`,
`category: recommended`, no features), `compose.yaml` (33 lines: pinned image,
`SEARXNG_SECRET` with a `:?` guard, one `./config/searxng` bind mount, loopback port,
healthcheck, cpu/mem limits), `README.md`. Touched by: registry, resolver,
`ods enable search`, generic health check, `/api/external-links`, `ENABLE_SEARXNG`
(`03-features.sh:180`), and — as a *consumer* — `docker-compose.base.yml:117` and
`perplexica/compose.yaml:11`. Note the coupling inversion: SearXNG knows nothing about
its consumers; they name it.

**n8n — 5 files, one feature card.** `manifest.yaml` (54 lines) adds
`health_timeout: 15`, four documented `env_vars` (two required, one secret), and a
`features[]` block (`workflows`, icon `Workflow`, `launch.type: service`, priority 4).
`compose.yaml` (46 lines) runs as `${ODS_UID}:${ODS_GID}` with a custom entrypoint,
bind-mounting two helper scripts from the extension dir itself
(`./extensions/services/n8n/n8n-entrypoint.sh:/opt/ods/…:ro,z`). Extra surfaces: the
`/api/features` card, a hard-coded enable-instruction block
(`routers/features.py:236`), an installer probe (`12-health.sh:616`), an
`ENABLE_WORKFLOWS` sync (`03-features.sh:184`), a workflow catalog under
`extensions/library/workflows/n8n/`, and a `config/n8n` mount.

**comfyui — the GPU-only pattern.** `compose.yaml` is a 14-line stub `services: {}`
whose only job is to exist so the registry counts ComfyUI as installed; the real
definitions live in `compose.nvidia.yaml` (local `build:` + GPU reservations),
`compose.amd.yaml` (ROCm image, `/dev/dri` + `/dev/kfd`,
`group_add: [$VIDEO_GID, $RENDER_GID]`, MIOpen tuning) and two `compose.multigpu-*`
overlays. The manifest's `gpu_backends: [amd, nvidia]` makes the resolver drop it
entirely on CPU/Apple. Non-manifest coupling: `03-features.sh:82-91` force-disables it
on Tier 0/1 (`shm_size: 8g`), `11-services.sh:1167` adds it to the local-build list.
It is the most-coupled extension in the bundled set — and still needs no registry edit.

---

## B. Minimal-extension proof

**The claim holds.** For a service that runs from a pre-built image on the current GPU
backend, the true minimal set is:

```
extensions/services/<id>/manifest.yaml    ~20-32 lines
extensions/services/<id>/compose.yaml     ~30-50 lines
```

Empirically, the smallest complete shipped examples:

| Extension | manifest | compose | other |
|---|---:|---:|---|
| `extensions/library/services/chromadb/` | 33 | 31 | README only |
| `extensions/library/services/gitea/` | 32 | 49 | README only |
| `extensions/services/searxng/` | 22 | 33 | README only |

**Zero edits to any other file.** No registry list, no CLI case statement, no compose
include, no UI import, no `ports.json` row, no `.env.schema.json` property.

Everything that auto-picks it up from those two files:

1. `lib/service-registry.sh:50` — all 14 lookup tables, on next `sr_load`.
2. `scripts/resolve-compose-stack.sh:517` — compose `-f` flag, plus any
   `compose.{nvidia,amd,apple,cpu}.yaml`, `compose.local.yaml`,
   `compose.multigpu-*.yaml` siblings, purely by filename.
3. `ods list` (`ods-cli:3004`) — appears with category + enabled/disabled status.
4. `ods enable|disable|purge` (`ods-cli:2732,2859,2922`) — including alias resolution,
   dependency cascade, GPU-compat prompt, and Dockerfile-build hint.
5. `ods start|stop|restart|logs|shell <id-or-alias>` — via `sr_resolve`/`sr_container`.
6. `scripts/health-check.sh:369` — parallel extension probe, JSON output, exit code.
7. `extensions/services/dashboard-api/config.py:297` — `SERVICES` entry ⇒
   `/api/services`, `/api/status`, per-service health polling.
8. `GET /api/external-links` (`main.py:1567`) ⇒ dashboard sidebar quicklink with icon,
   port, `ui_path`, health needle.
9. `GET /api/features` (`routers/features.py:113`) ⇒ a feature card, if `features[]`
   present, with status/suggestion/launch.
10. `installers/phases/11-services.sh:1374` ⇒ setup hook execution, if declared.
11. `scripts/audit-extensions.py` + `scripts/validate-manifests.sh` ⇒ CI validation.
12. `scripts/generate-extensions-catalog.py` ⇒ catalog row (library dir only).
13. `ods preset save/apply` ⇒ captured in `extensions.list`.
14. `bin/ods-host-agent.py` `/v1/extension/{start,stop,logs}` ⇒ dashboard lifecycle.

**What still needs a hand-edit, and why:**

| Want | Extra edit | File:line |
|---|---|---|
| An `ENABLE_X` installer opt-out | one `_sync_extension_compose` line | `installers/phases/03-features.sh:179-204` |
| Blocking installer health gate | one `_check_health` line | `installers/phases/12-health.sh:~616` |
| Port-contract CI parity (bundled only) | `config/ports.json` + `.env.schema.json` + `.env.example` | `tests/contracts/test-port-contracts.py:106` |
| A local `build:` for a user extension | not possible — the scan rejects it | `resolve-compose-stack.sh:251-252` |
| An internal dashboard route (not a quicklink) | plugin registration | `dashboard/src/plugins/registry.js:14-20` |
| Feature "how to enable" prose | dict entry | `routers/features.py:224-248` |
| Catalog entry for a *bundled* service | generator only reads `extensions/library/services` | `scripts/generate-extensions-catalog.py:33` |

So: **one to two files for a working extension; three to five if you want it to be a
first-class bundled service with an installer toggle and CI port parity.** The owner's
description is accurate.

---

## C. hal0 today: what exists, what's missing

hal0's runtime model is one podman container per *inference slot* under
`hal0-slot@<name>.service`, rendered as a Podman Quadlet `.container` file
(`src/hal0/providers/container.py:490,803`), plus a small set of companion units.
Mapping ODS concepts onto what exists:

| ODS concept | hal0 nearest analogue | Verdict |
|---|---|---|
| `extensions/services/<id>/manifest.yaml` | `src/hal0/services/registry.py:65` `SERVICES: tuple[ServiceDef, ...]` | **Hardcoded Python, 4 entries** (openwebui, comfyui, hermes, hindsight). Same *fields* as an ODS manifest (id, name, unit, port, probe, `public_url_env`, `actions`, `mdns`, `loopback_port`, `hints`) — but no filesystem discovery. |
| `compose.yaml` fragment | `packaging/systemd/hal0-openwebui.service` (static unit) or `RuntimeLaunchPlan` → Quadlet (`providers/base.py:163`, `container.py:803`) | Two unrelated mechanisms. Slots get a typed, rendered plan; companions get a hand-written `.service` with a literal `podman run`. |
| `resolve-compose-stack.sh` | *(none)* | No layering engine. Hardware selection lives inside `ContainerProvider` + `profiles.toml`, not in a merged file set. |
| `ods enable/disable/list` | `hal0 app install/list/uninstall` (`src/hal0/cli/app_commands.py:33-36`) | **`_SUPPORTED = frozenset({"openwebui"})`**, `_APP_UNITS` a 1-entry dict. The verb shape is right; the registry behind it is a literal. |
| Feature/extension registry | `src/hal0/install/extensions.py:26` `EXTENSIONS: list[Extension]` | Literally named "the first-run Extensions registry (spec §6.4)" — three hardcoded entries (openwebui, comfyui, hermes), with `install_extension()` an if/elif chain (lines 145-165). |
| `/api/features` status vocabulary | `GET /api/capabilities` + `GET /api/services` | Capability cards exist (`src/hal0/capabilities/catalog.py`) but they group *models per capability*, not "which optional services can this box run". No `insufficient_vram` / `services_needed` / `available` vocabulary for services. |
| `config/ports.json` | `src/hal0/ports/authority.py` | **hal0 is better here.** No stored table; claims are recomputed from slot TOMLs, live runtime snapshots, reserved ports and actual LISTEN sockets, so deletion releases a port atomically. |
| `docker-compose.override.yml` | Quadlet drop-ins `hal0-slot@<t>.container.d/` | Exists; explicitly the recommended replacement for the deprecated `extra_args` (`container.py:955`). |
| `extensions/library/` optional catalog | `docs/reference/profile-addons.mdx` (`.hal0profile.json` envelope + `index.json`) and `stacks/portable.py` (`.hal0stack.json`) | The *distribution* pattern already exists — signed-ish JSON envelopes with content checksums, dashboard Import, an addons index served from a site. It carries profiles and stacks; it does not carry services. |
| Setup hooks | `hal0.agents.hermes_provision` (15 phases), `install/extensions.py` | Bespoke per component. |
| Dashboard Extensions page | `ui/src/dash/services.jsx` (431 lines), `services-card.jsx` | Registry-driven from `GET /api/services` and already fail-soft. |

Two findings worth calling out:

1. **hal0's UI already wants this.** `ui/src/dash/dashboard-redesign.jsx:858` and
   `ui/src/dash/services.jsx:4,46` both list `n8n` in `SERVICE_ORDER` and ship an n8n
   icon — but there is no `ServiceDef`, no unit, and no install path anywhere in
   `src/`. The extension-shaped hole is already visible in the product.
2. **The privileged seam is the real constraint.** hal0-api runs as `User=hal0`; every
   unit write goes through `sudo -n hal0-systemctl write-quadlet <token>` and the root
   side **allow-lists the body**: sections `[Unit]/[Container]/[Service]/[Install]` in
   strictly increasing order, exactly 3 `[Unit]` keys, ~14 `[Container]` keys, 9
   `[Service]` keys, and `[Install] WantedBy=hal0.target` only
   (`installer/wrappers/hal0-systemctl:389-516`). Unit-file paths are pinned to the
   `hal0-slot@` prefix (`src/hal0/system/seam.py:283-312`). No `Exec*=` on the host
   side, `PodmanArgs=` restricted to `--group-add/--security-opt/--ipc/--ulimit`.
   **"Drop in an arbitrary third-party unit fragment" is architecturally refused
   today, deliberately (#1740, #1759).**

Net: hal0 has all the *ingredients* — a typed launch plan, a validated unit writer, a
services registry with the right fields, a portable-envelope distribution pattern, a
port authority, a registry-driven Services page, and an `app install` verb — but they
are wired together by hand for four fixed services. There is no manifest, no
discovery walk, and no way for an operator or a third party to add a fifth.

---

## D. Proposed hal0 extension model

**Design goal, restated in hal0 terms:** an extension author writes one manifest; hal0
renders the unit, claims the port, wires the env, registers the service, probes its
health, and shows it in the dashboard — with no code change anywhere.

### D.1 File layout

```
/usr/lib/hal0/extensions/<id>/        # shipped with the release (read-only)
/var/lib/hal0/extensions/<id>/        # operator- or catalog-installed
    manifest.toml                     # REQUIRED  (~25-40 lines)
    unit.toml                         # OPTIONAL  quadlet overrides (rare)
    hooks/post_install.sh             # OPTIONAL
    README.md                         # OPTIONAL
/etc/hal0/extensions.toml             # enable/disable state + operator overrides
/etc/containers/systemd/hal0-ext@<id>.container   # GENERATED, never authored
```

Discovery order mirrors `hal0.config.paths` conventions (`/var/lib` wins over
`/usr/lib`, `HAL0_EXTENSIONS_DIR` overrides both for tests) — exactly the resolution
shape already used by `hal0.bundles.tiers._candidate_roots`
(`src/hal0/bundles/tiers.py:44-56`).

### D.2 Manifest schema (`manifest.toml`, `schema_version = "hal0.ext.v1"`)

```toml
schema_version = "hal0.ext.v1"

[extension]
id = "n8n"                  # [a-z0-9][a-z0-9-]*, == directory name
name = "n8n (Workflows)"
category = "optional"       # core | recommended | optional
hal0_min = "0.4.0"          # ≙ compatibility.ods_min

[runtime]
image = "docker.io/n8nio/n8n@sha256:…"   # digest-pinned, like RUNNER_IMAGES
port = 5678                 # in-container;  publish_port defaults to it
network = "bridge"          # bridge | host   (slots use host)
volumes = [ { source = "$DATA/n8n", target = "/home/node/.n8n" } ]
user = "$HAL0_UID:$HAL0_GID"
# devices/group_add gated — see D.6;  [runtime.rocm]/[runtime.vulkan]/[runtime.cpu]
# sub-tables replace ODS's compose.<backend>.yaml overlays.

[health]
kind = "http"               # http | tcp | container | none
path = "/healthz"
timeout_s = 15

[wiring]                                # <- "prewired to existing slots"
openai_base_url_env = "OPENAI_BASE_URL" # ⇒ http://host.docker.internal:8080/v1
openai_api_key_env  = "OPENAI_API_KEY"
model_env           = "OPENAI_MODEL"    # ⇒ "hal0/brain" — never a concrete id
slot_env = { WHISPER_URL = "slot:stt", TTS_URL = "slot:tts", COMFY_URL = "slot:img" }

[[env]]
key = "N8N_PASS"; required = true; secret = true

[[capability]]                          # ≙ ODS features[]
id = "workflows"; name = "Workflow Automation"; icon = "Workflow"
requires_services = ["n8n"]; requires_vram_gb = 0; priority = 40
launch = { kind = "service", service = "n8n" }

[hooks]
post_install = "hooks/post_install.sh"
```

Near-verbatim ports from ODS (semantics unchanged, TOML instead of YAML, Pydantic
instead of JSON Schema): `id`/`name`/`category`/`aliases`, `port`/`external_port`,
`health`+`health_timeout`, `depends_on`, `env_vars[]`, `hooks{}`,
`compatibility.ods_min` → `hal0_min`, and the whole `features[]` → `[[capability]]`
block including the `enabled_*` vs `requires_*` split and `launch{}`.

Deliberately dropped: `compose_file` (no compose), `gpu_backends` (hal0 derives device
class from `hardware.json`; use `[runtime] requires_device = "gpu-rocm"` instead),
`container_uid` (hal0 owns data-dir ownership through `hal0.install.perms`),
`host_network` (a policy decision, not an author's).

### D.3 Discovery points (the "everything auto-attaches" contract)

| Consumer | Implementation | ODS counterpart |
|---|---|---|
| `hal0.extensions.registry.load()` | walk the two roots, parse+validate manifests, memoise on mtime — a direct Python port of `sr_load` (`lib/service-registry.sh:50`) minus the bash-emitting subprocess | `service-registry.sh` |
| `hal0.services.registry.SERVICES` | becomes `builtin_services() + extension_services()`; `ServiceDef` gains `source: "builtin"\|"extension"` and `manifest_path` | `config.py:load_extension_manifests` |
| `GET /api/services` + Services page | zero changes — it already renders whatever the registry returns (`src/hal0/api/routes/services.py:140`) | `/api/services` |
| Unit rendering | `ExtensionProvider.launch_plan(manifest) -> RuntimeLaunchPlan`, then the **existing** `_render_quadlet_from_plan` (`container.py:803`) | compose fragment |
| Unit write | `SystemCtlSeam.write_quadlet` extended to accept the `hal0-ext@` prefix (`system/seam.py:283`), wrapper `validate_quadlet_body` reused unchanged | resolver `-f` flag |
| Port claim | `hal0.ports.authority.collect_claims` gains an `"extension-config"` source | `config/ports.json` (better) |
| Health | the `probe` field already dispatches http/systemd/comfyui in `services_health.py`; add `tcp`/`container` | `health-check.sh:159` |
| Capability cards | `GET /api/capabilities` merges `[[capability]]` rows and computes `enabled\|available\|insufficient_vram\|services_needed` | `routers/features.py:70-77` |
| mDNS | `services/mdns.py` already writes `hal0-addon-<id>.service` per advertised id — extensions inherit it free | dashboard quicklinks |
| Journald logs | `GET /api/logs?unit=hal0-ext@<id>.service` — already generic | `ods logs <id>` |
| Catalog | `hal0.extensions.portable` — a `.hal0ext.json` envelope + `index.json`, modelled byte-for-byte on `stacks/portable.py:41` and `docs/reference/profile-addons.mdx` | `extensions/library/` + `extensions-catalog.json` |

### D.4 CLI verbs

Promote `hal0 app` from a 1-entry frozenset to the extension registry, keeping the
existing verb names (`src/hal0/cli/app_commands.py`):

```
hal0 app list                     # id, category, state (enabled|disabled|not-installed|incompatible)
hal0 app install <id>             # render unit → seam write → daemon-reload → enable --now → post_install hook
hal0 app enable|disable <id>      # flips /etc/hal0/extensions.toml, converges the unit
hal0 app uninstall <id>           # disable + remove unit; --purge also drops /var/lib/hal0/ext/<id>
hal0 app import <file|url>        # .hal0ext.json envelope, checksum-verified
hal0 app doctor [<id>]            # manifest validation ≙ scripts/audit-extensions.py
```

`hal0 doctor` gains an extensions arm; the ODS `audit-extensions.py` check list
(schema version, id↔directory match, category/type enum, positive port, health starts
with `/`, alias collisions, dangling `depends_on`, duplicate capability ids) ports
almost line-for-line into `hal0/extensions/audit.py`.

### D.5 Pre-wiring, translated

ODS pre-wiring is *docker-network DNS + compose interpolation*. hal0 has neither:
slot containers run `Network=host` with a loopback fence
(`container.py:_loopback_fence_command`), so there is no service-name DNS. The
translation is **rendered env, computed at unit-render time from live truth**:

- `OPENAI_BASE_URL` → `http://host.docker.internal:8080/v1` for bridge-network
  extensions (podman honours `--add-host=host.docker.internal:host-gateway`, already
  used at `packaging/systemd/hal0-openwebui.service` ExecStart), or
  `http://127.0.0.1:8080/v1` for host-network ones. This is exactly what
  `hal0.openwebui.env_writer` already does for OpenWebUI
  (`src/hal0/openwebui/env_writer.py:11-30`) — generalise that writer.
- `OPENAI_MODEL` → `hal0/brain` (the virtual model that already resolves through the
  dispatcher), *never* a concrete model id. This is the direct analogue of ODS's
  `ods/current` swap-safety rule and should be documented the same way
  (`docs/SWAP-SAFE-EXTENSIONS.md:17-33` ports nearly verbatim).
- `slot:<name>` refs in `[wiring].slot_env` resolve at render time through
  `hal0.ports.authority` to `http://127.0.0.1:<slot port>` — so `WHISPER_URL` follows
  the stt slot even if the operator moves its port. ODS cannot do this; its DNS names
  are static and its ports come from `.env`.
- Env lands in `/etc/hal0/extensions/<id>.env`, written atomically by
  `hal0.config.env.write_env_atomic` (the same primitive slot env files use) and
  referenced from the generated unit — keeping secrets out of the unit file and out of
  the `podman run` argv, which is the lesson of #1466 already recorded in
  `env_writer.py:44-52`.

### D.6 Near-verbatim ports vs. things needing translation

**Near-verbatim (port the logic, change the language):**
- `lib/service-registry.sh:50-235` → `hal0/extensions/registry.py`. The loader is
  already a Python program embedded in bash; lifting it out and dropping the `_esc()`
  bash-quoting layer is a simplification, not a rewrite.
- `extensions/schema/service-manifest.v1.json` → Pydantic models in
  `hal0/extensions/schema.py`. Keep field names that carry meaning (`category`,
  `health`, `depends_on`, `env_vars`, `hooks`, `compatibility`) so ODS-authored
  manifests convert mechanically.
- `routers/features.py:19-110` `calculate_feature_status` → capability-status
  computation, including the four-value vocabulary and the requires/enabled split.
- `scripts/audit-extensions.py:417-747` → `hal0/extensions/audit.py`.
- `docs/EXTENSIONS.md` §"30-Minute Path" + `service-template.yaml` → a hal0
  `write-an-extension.mdx` and a heavily-commented `manifest.toml` template. The
  commented template is a large part of why ODS's model is usable; do not skip it.
- `docs/SWAP-SAFE-EXTENSIONS.md` tiers 1-3 — hal0's slots load/unload constantly, so
  this doc is more necessary here, not less.

**Needs real translation:**
- **Compose merge → one rendered unit.** ODS gets layering free from `docker compose
  -f a -f b`. hal0 must merge itself: `manifest.toml` → `RuntimeLaunchPlan` → optional
  operator override via a `hal0-ext@<id>.container.d/` drop-in. That mechanism already
  exists as the documented replacement for `extra_args` (`container.py:947-957`), so
  the story is *base manifest + typed drop-in*, not *N merged files*.
- **GPU overlays.** ODS picks `compose.<backend>.yaml` by filename; hal0 already
  resolves device class from `hardware.json` and image from `RUNNER_IMAGES`. Use
  `[runtime.rocm]` / `[runtime.vulkan]` / `[runtime.cpu]` sub-tables — one file, not four.
- **The seam allow-list.** `installer/wrappers/hal0-systemctl:389-465` must gain the
  `hal0-ext@` prefix and two new `[Container]` keys (`EnvironmentFile=`, `AddHost=`;
  `Environment=`/`PublishPort=`/`Volume=` are already allowed). Every widening is a
  root-boundary change needing the scrutiny #1740 got. **Highest-risk piece here.**
- **`ENABLE_*` .env gating → `/etc/hal0/extensions.toml`.** ODS's rename-the-file trick
  has no analogue and no merit here; use a state file with the atomic write/rollback
  shape `SlotConfigStore` already implements (`src/hal0/slot_config/__init__.py`).
- **Devices / GPU passthrough.** ODS refuses `devices:` for user extensions outright
  (`resolve-compose-stack.sh:275-276`). hal0 should default to the same refusal, with an
  operator-only escape for locally-authored manifests.

---

## E. Things NOT to copy

1. **Enable/disable by renaming `compose.yaml` ↔ `compose.yaml.disabled`.** It makes
   the *content tree* the state store, so a `git pull`, a backup restore, or an rsync
   silently changes which services run. ODS pays for this with the "stale disabled
   marker" reconciliation at `ods-cli:2833-2842`. hal0 has `SlotConfigStore` with
   atomic commit/revert; use a state file.
2. **The hand-maintained `_sync_extension_compose` table.** 21 lines at
   `installers/phases/03-features.sh:179-204` that must be edited for every new
   optional service — the exact seam the rest of the design avoids, and the reason the
   file carries a 10-line comment explaining why omission is a bug. Derive the toggle
   from `category` + a state file instead.
3. **Two parallel status vocabularies.** `/api/features` returns
   `enabled|available|insufficient_vram|services_needed`; `/api/extensions/catalog`
   returns ten different values. The dashboard has to teach users both
   (`Extensions.jsx:73-80` is a legend). Pick one lifecycle vocabulary and one
   eligibility vocabulary and derive card copy from them.
4. **A generated static catalog JSON checked into the repo.**
   `config/extensions-catalog.json` is produced by `scripts/generate-extensions-catalog.py`
   from manifests and then *committed*, so it can drift (hence the reminder in
   `docs/EXTENSION-PR-BRANCHING.md:12`). Serve it from the live manifest walk.
5. **`config/ports.json` as a fourth place ports are written.** hal0's
   `ports/authority.py` docstring already argues the case against stored allocation
   tables; do not regress.
6. **Bash + embedded-Python + associative arrays.** `service-registry.sh` needs a Bash
   4 guard and re-execs under Homebrew bash on macOS (`health-check.sh:11-19`); the
   compose resolver hard-fails without PyYAML. hal0 is a Python service — the whole
   registry is ~150 lines of ordinary Python.
7. **Fine-grained `llm.probe` in v1.** ODS's post-swap probe descriptor
   (`schema:…llm.probe`) is genuinely useful but only pays off once you have a swap
   harness driving it. Ship `[wiring]` first; add probes when there is a consumer.
8. **Allowing arbitrary unit/quadlet content.** ODS's content scan is a deny-list over
   compose keys (`resolve-compose-stack.sh:249-334`) — a shape that has to be kept in
   sync with Docker's evolving schema, and which is duplicated in three places
   (resolver, dashboard `_scan_compose_content`, host agent). hal0's allow-list over a
   *rendered* unit is strictly stronger. Keep rendering from a typed manifest; never
   accept a unit body an author wrote.

---

## F. Risks and decisions for the owner

**Decision 1 — Are extensions services, or also slots?** ODS conflates them: a
manifest can describe a core LLM server or a git host. hal0's slot layer is a
specialised, heavily-invariant subsystem (GpuArbiter, NPU trio exclusivity,
model registry binding). *Recommendation:* extensions are **companion services only**
(`hal0-ext@<id>.service`), a strictly separate unit family from `hal0-slot@`. An
extension that wants inference asks hal0 over HTTP like every other client.

**Decision 2 — How wide does the seam allow-list open?** Every `[Container]` key added
to `installer/wrappers/hal0-systemctl:407-438` is a root-boundary change. The minimum
new set is `EnvironmentFile=` (pinned to `/etc/hal0/extensions/<id>.env`) and
`AddHost=host.docker.internal:host-gateway` (pinned to that literal). *Recommendation:*
pin both to literals rather than charset regexes, and require that `Volume=` sources for
extensions live under `/var/lib/hal0/ext/<id>/` — the path constraint ODS cannot make
because compose mounts are relative to a project dir the operator owns.

**Decision 3 — Rootful vs. rootless for extension containers.** Slots run rootful
podman today, and the wrapper's own HONEST BOUNDARY note (`hal0-systemctl:60-70`)
concedes that anyone who can author a container spec can mount host paths. Third-party
extensions raise the stakes materially. *Recommendation:* run `hal0-ext@` containers
**rootless under the `hal0` user** from day one. It is easier to start restrictive than
to retrofit, and it removes the sharpest edge in importing third-party manifests.

**Decision 4 — Who may publish?** ODS ships 33 library extensions it does not test on
every hardware class and lets the dashboard install them at a click. hal0's culture
(digest pins, reproducible runner recipes, `packaging/runner/*/manifest.toml`
provenance notes) is much stricter. *Recommendation:* ship a small **first-party**
extension set (n8n is already half-designed in the UI), require digest-pinned images in
the manifest schema, and gate third-party import behind an explicit
`hal0 app import --i-understand` with the same envelope-checksum machinery
`stacks/portable.py` already has.

**Decision 5 — Migration of the existing four.** OpenWebUI, ComfyUI, Hermes and
Hindsight should not all become extensions. ComfyUI *is* a slot
(`hal0-slot@img.service`) and its start verb is GPU-arbiter-special-cased
(`services/registry.py:88-96`); Hermes has a 15-phase provisioner. *Recommendation:*
convert **OpenWebUI only** as the proof — it is a plain podman container with a
prewired env file, i.e. exactly the extension shape — and leave `ServiceDef` as a union
of builtin and extension-derived rows. If OpenWebUI cannot be expressed in
`manifest.toml`, the schema is wrong.

**Risk — the "everything auto-attaches" promise is only as good as the weakest
walker.** ODS delivers it because four independent consumers each re-walk the
directory. hal0 must fund all of them in one go: registry, unit render, port claim,
health probe, capability card, mDNS, logs, CLI. Shipping the manifest without the
capability card or without `hal0 app doctor` produces exactly the half-wired feeling
the owner wants to avoid.

**Risk — id collisions across sources.** ODS mirrors a core-service-id deny list in
three places (`resolve-compose-stack.sh:178-188`, `routers/extensions.py`,
`helpers.py:CORE_SERVICE_IDS_FALLBACK`) and silently drops duplicates
(`service-registry.sh:146-148`). hal0 should make that one function with one test, and
reserve the `hal0-` id prefix outright.

**Open question to settle before code:** may an extension consume a *specific slot*
("n8n needs stt"), or only the API? `[wiring].slot_env` assumes the former, which is
what brings ODS's `depends_on` + `services_needed` status into play and is the main
reason to port `calculate_feature_status`. If the answer is "API only", the whole
capability/eligibility half of the design collapses and the first release gets
meaningfully cheaper.
