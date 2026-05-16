# hal0 test-harness — findings

Living catalogue of issues the δ-tier harness has surfaced. Each
entry cites a file:line so a fix can land directly. Severity is
**critical**, **high**, **medium**, **low**, **bug** (production
defect), **gap** (missing capability), **env** (host-side issue, not
a hal0 defect), or **info** (drift/diagnostic note).

## Runs

| Date       | Host     | Build                                  | pass | fail | skip | deferred | total |
|------------|----------|----------------------------------------|-----:|-----:|-----:|---------:|------:|
| 2026-05-15 | hal0-dev | repo HEAD (CUDA dev VM)                |   24 |    2 |   10 |        5 |    41 |
| 2026-05-16 | hal0-test LXC | repo HEAD via /opt/hal0-harness   |   33 |    1 |    5 |        2 |    41 |

The 2026-05-16 run also drove the **live prod install** at /opt/hal0
on hal0-test through three additional surfaces beyond the δ-harness:

- **API surface**: 62 distinct route × method tuples, 9/9 auth-error
  contract probes pass. See §11–§14.
- **UI / Caddy edge**: 25 probes through Caddy on :443 + direct on
  :8080. See §15–§17.
- **Real inference**: phi3-mini chat round-trip on Strix Halo Vulkan
  — **TTFT 59 ms, ~85 tok/s** sustained over 10 sequential requests.
  See §18–§21.

---

## 1. `hal0 config validate` crashes with ImportError — **bug** · ✅ RESOLVED 2026-05-16

> Closed by task #19. Confirmed green by the 2026-05-16 hal0-test run
> (`cli/cli-config-validate` and `installer/dev-config-validate` both
> pass). Original report preserved below.


Both `installer` and `cli` tiers caught the same root cause; one
fix kills two rows.

- **Where:** `src/hal0/cli/config_commands.py:76`
- **What:**

  ```python
  from hal0.config.loader import load_hal0_config, load_providers, load_upstreams
  ```

  The names `load_providers` / `load_upstreams` do not exist. The
  loader module (`src/hal0/config/loader.py:315,342`) exports them
  with a `_config` suffix:

  ```python
  def load_providers_config(path: Path | None = None) -> ProvidersConfig:
  def load_upstreams_config(path: Path | None = None) -> UpstreamsConfig:
  ```

- **Symptom:** Every `hal0 config validate` invocation raises
  `ImportError: cannot import name 'load_providers' from
  'hal0.config.loader'` *before* the validator can run.
- **Fix:** rename the imports in `config_commands.py:76, 84, 88` to
  `load_providers_config` / `load_upstreams_config` (or add aliases
  at the bottom of `loader.py`).
- **Impact:** Anyone following the install guide and running
  `hal0 config validate` after first install gets a traceback.

---

## 2. `hal0 slot create` conflates provider and backend — **bug**

The CLI exposes a `--backend` flag whose value is actually the
**provider** (`llama-server`, `flm`, `moonshine`, `kokoro`,
`comfyui`). The slot's **hardware backend** (`vulkan`, `rocm`,
`cpu`, …) is hardcoded.

- **Where:** `src/hal0/cli/slot_commands.py:204–229`
- **What:**

  ```python
  backend: SlotBackend = typer.Option("llama-server", "--backend", "-b", ...)
  ...
  body: dict[str, Any] = {
      ...
      "backend": "vulkan",        # hardcoded
      "provider": str(backend),   # the CLI flag is really the provider
      ...
  }
  ```

- **Symptom:** A user trying to create a ROCm slot via the CLI has
  no way to. The flag they'd reach for (`--backend rocm`) is
  rejected by the SlotBackend enum, which lists provider names not
  hardware targets.
- **Fix options:**
  1. Rename the CLI flag to `--provider` and add a separate
     `--hardware` (default auto-detected from `hardware.json`).
  2. Keep `--backend` but make it accept hardware values, derive
     provider from a separate `--provider` flag.

  Either way, drop the hardcoded `"backend": "vulkan"` on line 228.
- **Impact:** Inability to drive non-Vulkan slots through the CLI;
  workflows that should be one command require hand-editing
  `/etc/hal0/slots/<name>.toml`.

---

## 3. `installer/uninstall.sh` has no `--dev` mode — **gap**

The uninstaller hardcodes FHS paths. If a developer runs it from a
shell where they previously did `bash installer/install.sh --dev`,
it will happily wipe `/etc/hal0`, `/var/lib/hal0`, `/usr/lib/hal0`,
and the systemd units on the actual host.

- **Where:**
  - `installer/uninstall.sh:95-107` (units in `/etc/systemd/system`)
  - `installer/uninstall.sh:113-120` (`/usr/lib/hal0`)
  - `installer/uninstall.sh:153-160` (`/etc/hal0`, `/var/lib/hal0`)
- **What's missing:** a `--dev` (or `HAL0_PREFIX=…`) path that
  mirrors `install.sh:89–100`'s dev-mode layout so the uninstaller
  only touches the prefix.
- **Workaround the harness uses:** `harness-cleanup.sh:dev-manual-cleanup`
  does the rm-rf by hand, never invoking `uninstall.sh` in dev mode.
- **Fix:** add `--dev` to `uninstall.sh`. Compute `PREFIX`, `ETC_DIR`,
  `VAR_DIR`, `UNIT_DIR` exactly like `install.sh:89–100` and use
  those in the rm loops.
- **Impact:** dev-mode round-trip incomplete; can hurt operators
  who copy/paste guide snippets.

---

## 4. `installer/uninstall.sh` doesn't remove `hal0-caddy.service` — **bug** · ✅ RESOLVED 2026-05-16

> Closed by task #22. Confirmed by hal0-test run
> (`installer/uninstall-caddy-gap` passes). Original report below.


- **Where:** `installer/uninstall.sh:96–99`

  ```bash
  for UNIT_FILE in \
      "${UNIT_DIR}/hal0-api.service" \
      "${UNIT_DIR}/hal0-openwebui.service" \
      "${UNIT_DIR}/hal0-slot@.service"
  do
  ```

  The fourth unit installed by `--auth=basic` (`hal0-caddy.service`,
  written by `install.sh:439`) is missing from the loop.
- **Symptom:** After `install.sh --auth=basic` + `uninstall.sh`,
  `systemctl status hal0-caddy` still shows an enabled unit (failed
  to start, since the binary it points at is gone). The next
  `systemctl daemon-reload` warns about the orphan.
- **Fix:** add `"${UNIT_DIR}/hal0-caddy.service"` to the list.

---

## 5. `installer/systemd/` is dead code — **gap** · ✅ RESOLVED 2026-05-16

> Closed by task #23 (directory removed). Confirmed by hal0-test run
> (`installer/dev-installer-systemd-dir-unused` passes). Original
> report below.


- **Files shipped but unused:**
  - `installer/systemd/hal0-api.service`
  - `installer/systemd/hal0-slot@.service`
- **Evidence:**
  - `installer/install.sh:488` writes the API unit *inline* with `cat >`
  - `installer/install.sh:512` reads the slot template from
    `${REPO_ROOT}/packaging/systemd/hal0-slot@.service`, not from
    `installer/systemd/`.
- **Risk:** if someone edits `installer/systemd/hal0-slot@.service`
  intending to ship a fix, nothing changes — the installer reads
  the file in `packaging/systemd/` instead.
- **Fix options:**
  1. Delete `installer/systemd/`.
  2. Move both template units there and rewire `install.sh:512` and
     the inline cat at 488 to copy from this directory.

---

## 6. Slots created under `--dev` can't actually start — **gap** · ⚠️ STILL OPEN

> Task #24 was marked complete but the 2026-05-16 hal0-test run
> still hits this — `runtime/runtime-slot-load` is `deferred` with
> the same error message naming `install.sh:530-533`. Either the
> task #24 fix was docs-only (then the harness row is correct
> living-documentation and we can retire this finding as
> won't-fix-by-design) or the fix hasn't shipped. Verify before
> closing.


- **Where:**
  - `installer/install.sh:530-533` skips `systemctl daemon-reload` in
    `--dev` mode.
  - The units are written to `${PREFIX}/etc/systemd/system/` but the
    host's `systemctl` only consults `/etc/systemd/system` and
    `/usr/lib/systemd/system`.
- **Symptom:** `hal0 slot create … && hal0 slot load …` succeeds
  through slot create, but `slot load` fails with "Unit
  hal0-slot@<name>.service not found." The harness's `runtime-slot-load`
  row would surface this as `fail` if a toolbox image were available.
- **Fix options (rank from least invasive to most):**
  1. Document the limitation in `installer/README.md` and have
     `--dev` print a warning.
  2. Use `systemctl --user` units instead of system units in `--dev`
     mode (changes the entire deployment story for dev installs).
  3. Provide a parallel non-systemd launcher (`hal0 slot launch` is
     already a binary at `installer/bin/hal0-slot-launch`) that
     `--dev` mode wires up.
- **Impact:** The "polished one-line install for home users" goal
  is fine, but the dev-loop story has a sharp edge that the v1
  contributor docs need to call out.

---

## 7. `releases.hal0.dev` is not reachable — **env / gap** · ✅ RESOLVED 2026-05-16

> Closed by tasks #14 + #18 (CF Pages + DNS). Confirmed from
> hal0-test (`cli/cli-update-check` and `installer/dev-api-up` both
> pass; `/api/updates/check` returns placeholder manifest). Original
> report below.


- **Where:** `hal0 update --check` calls
  `GET /api/updates/check`, which fetches `https://releases.hal0.dev/stable.json`.
- **Observation:** the host can't resolve `releases.hal0.dev`
  (`[Errno -5] No address associated with hostname`). The API
  returns HTTP 500 correctly; the CLI surfaces the upstream error.
- **Fix path:** stand up `releases.hal0.dev` as part of the v1
  release ritual, OR ship the URL as configurable so home installs
  can point at a self-hosted manifest.
- **Note:** the harness marks this as `deferred`, not `fail`, since
  the CLI behaviour is correct given the missing infra.

---

## 8. `ghcr.io/hal0ai/*` toolbox images return `unauthorized` — **env / gap** · ⚠️ PARTIALLY RESOLVED 2026-05-16

> hal0-test had the image pre-pulled by the prod install, so the
> `runtime-image-check` row passes. But the original `docker pull`
> from an un-authed host (hal0-dev) has NOT been re-tested since
> task #25 was marked complete. Do an explicit `docker logout
> ghcr.io && docker pull ghcr.io/hal0ai/hal0-toolbox-vulkan:v1` on
> a clean host before declaring this fully closed.


- **Observation:** `docker pull ghcr.io/hal0ai/hal0-toolbox-vulkan:v1`
  (and pulling by digest from `manifest.json`) returns
  `Error response from daemon: error from registry: unauthorized`.
- **Memory says:** "all images are published" (per the user note).
- **Reality on hal0-dev:** can't pull without auth. Either:
  - The packages are private and `docker login ghcr.io -u <user> -t <PAT>`
    is needed (and that should be a documented installer prerequisite),
    OR
  - The image refs in `manifest.json` are wrong (mismatched org or
    tag), OR
  - The org's package visibility setting hasn't been flipped to
    public yet.
- **Impact:** every `runtime-*` row in the harness skipped on the
  dev box; release-gate γ on hal0-test LXC may also fail to pull on
  a clean reinstall.
- **Action:** verify visibility on ghcr.io/hal0ai/* packages, or
  document the login requirement in `installer/README.md` (and have
  `install.sh` warn if a docker config token isn't present).

---

## 9. host `/` filesystem at 96% on hal0-dev — **env**

`hal0 doctor` correctly fails because `/var/lib` lives on the same
74 GB root partition that's now at 3.6 GB free.

This is not a hal0 defect — it's a heads-up for the operator. The
harness's `cli-doctor` row reports it as `deferred` so it doesn't
hide a real regression.

Recommended host fix: bind-mount `/var/lib/hal0` (or set
`HAL0_PREFIX` to a path on the larger `/devpool` / `/mnt/...`
volumes) before next install.

---

# Findings from the 2026-05-16 hal0-test deep-probe round

The round added four parallel teammates beyond the δ-harness:
**team-api** (62 routes), **team-ui** (Caddy + SPA + OpenWebUI),
**team-runtime** (real inference), and **team-harness** (re-run of
δ-tier). Their per-team scratch reports remain under
`tests/harness/_in-progress/` until cleaned up.

## 10. Caddy basic_auth swallows the PUBLIC_PATHS allowlist — **critical / bug**

The dashboard `handle {}` block in `/etc/hal0/Caddyfile` applies
`basicauth` to every path that doesn't match `/chat*` or `/v1/*` —
including the entire FastAPI public-path allowlist
(`/api/health/system`, `/api/status`, `/api/metrics`,
`/api/install/state`, `/api/config/urls`, `/api/auth/status`,
`/api/docs`, ...).

- **Where:**
  - `/etc/hal0/Caddyfile` (rendered from
    `packaging/caddy/Caddyfile.template`), the default `handle {}`
    block.
  - vs. the contract in `src/hal0/api/middleware/auth.py:96-119`
    (`PUBLIC_PATHS` frozenset).
- **Impact:**
  - **First-run wizard is unbootstrappable from a browser** — the
    Vue SPA hits `/api/install/state` and `/api/config/urls` before
    any token can exist; basic_auth at the edge blocks the wizard
    from ever loading.
  - SPA can't discover whether auth is enabled (`/api/auth/status`
    gated).
  - External monitors must hold basic_auth creds to scrape
    `/api/status` / `/api/metrics`.
  - Swagger UI at `/api/docs` is gated, contradicting
    `src/hal0/api/__init__.py:284`'s intent.
- **Repro:**
  ```
  curl -ksI --resolve hal0-test.local:443:10.0.1.230 \
    https://hal0-test.local/api/install/state
  # → HTTP 401 Basic realm="restricted"
  ```
- **Fix sketch** (placed *before* the existing default `handle {}`):

  ```caddyfile
  @public path /api/health/system /api/status /api/metrics \
                /api/metrics/prometheus /api/features \
                /api/install/state /api/install/complete \
                /api/config/urls /api/auth/status /api/auth/login \
                /api/auth/logout /api/docs /api/redoc /api/openapi.json
  handle @public {
      reverse_proxy 127.0.0.1:8080
  }
  ```

  Land the fix in `packaging/caddy/Caddyfile.template` so the
  installer re-renders correctly on next deploy.

## 11. `require_token` ignores scope on every write router — **high / security bug**

Tokens minted with `scope: "read-only"` (or `v1-only`) can perform
PUT/POST/DELETE on every admin router — the scope is enforced
**only** on `/api/auth/tokens` CRUD via `require_admin`. Everything
else uses `require_token`, which accepts any valid token regardless
of scope.

- **Where:** `src/hal0/api/__init__.py:316–337` —
  `_admin_auth = [Depends(require_token)]` is applied to `slots`,
  `models`, `settings`, `hardware`, `logs`, `providers`, `updater`,
  `images` routers. Only `auth_routes.tokens_router` uses
  `require_admin`.
- **Effect:** a `read-only` token can rewrite `/etc/hal0/hal0.toml`
  via `PUT /api/settings`, swap models on prod slots via
  `POST /api/slots/{name}/swap`, change update channel, etc.
- **Repro:**
  ```
  # mint a read-only token …
  curl -sS -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
    -X POST -d '{"label":"ro-probe","scope":"read-only"}' \
    http://127.0.0.1:8080/api/auth/tokens
  # … then mutate with it:
  curl -sS -H "Authorization: Bearer $RO_TOKEN" -H 'Content-Type: application/json' \
    -X PUT -d '{}' http://127.0.0.1:8080/api/slots/primary/config
  # → HTTP 200, slot snapshot returned
  ```
- **Fix direction:** introduce `require_writer` (accepts `admin` +
  `all` only), attach to all write-shaped routes; OR split
  `_admin_auth` into `_reader_auth` / `_writer_auth` lists in
  `src/hal0/api/__init__.py`. Either way, document the scope matrix
  in `src/hal0/api/middleware/auth.py`'s module docstring.

## 12. Slot state machine reports `offline` while slots are actively serving 200s — **high / bug**

`GET /api/slots` returns `state: "offline"` for the `stt` and `tts`
slots on hal0-test, but:

- `systemctl status hal0-slot@stt` / `hal0-slot@tts` → both
  `active (running)` for 3h+.
- `POST /v1/audio/transcriptions` and `POST /v1/audio/speech`
  return 200 with valid audio/text payloads.

The dispatcher routes traffic to these slots, so the gate in the
slot-manager health-probe loop is not the same source of truth as
`/api/slots`. UI/CLI users reading `/api/slots` will conclude the
slot is broken when it is fully functional.

- **Cite:** `src/hal0/api/routes/slots.py` (state serialization),
  `src/hal0/slots/manager.py:1373` (health-probe provider list —
  both `kokoro` and `moonshine` are listed, so the probe should be
  observing them).
- **Repro:**
  ```
  curl -s http://127.0.0.1:8080/api/slots | jq '.[] | {name,state}'
  curl -X POST http://127.0.0.1:8080/v1/audio/speech \
    -H 'Authorization: Bearer $TOKEN' \
    -d '{"model":"kokoro","input":"hi","voice":"af_bella"}' \
    -o /tmp/out.mp3 -w '%{http_code}\n'
  # → 200, valid MP3 written
  ```
- **Fix direction:** trace the state-machine writer for non-llama
  providers (moonshine, kokoro); make sure the success path
  transitions to `ready` / `serving`. Task #10 in the prior session
  claimed `serving / idle / pulling` were wired; this finding shows
  the wiring missed kokoro + moonshine.

## 13. Validation errors raised as bare `Hal0Error(...)` return HTTP 500 — **medium / contract bug**

Several routes do client-side validation by raising bare
`Hal0Error("...")`, which inherits the default `status = 500` from
`src/hal0/errors.py:22`. The envelope shape is correct
(`{"error":{"code":"system.internal", "message":"..."}}`) but the
HTTP status is wrong — clients see what looks like a server failure
when they made a bad request.

- **Affected sites:**
  - `src/hal0/api/routes/auth.py:151` — "request body must be a JSON object"
  - `src/hal0/api/routes/updater.py:309–311` — channel validation
  - `src/hal0/api/routes/slots.py:165–174, 297–298, 315, 329–331, 377–381` — slot create/config/swap body validation
  - `src/hal0/api/routes/models.py:117, 122, 159, 160` — model create/update body validation
- **Repro:**
  ```
  curl -sS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -X PUT -d '{"channel":"unstable"}' \
    http://127.0.0.1:8080/api/updates/channel
  # → HTTP 500 with code="system.internal" and an arguably-4xx message
  ```
- **Fix:** subclass `Hal0Error` with `status = 400` (e.g.
  `class BadRequest(Hal0Error): code = "validation.invalid"; status = 400`)
  and use it at every cite above.

## 14. STT pipeline leaks ffmpeg subprocess argv on bad input — **medium / bug**

Sending a non-audio body to `POST /v1/audio/transcriptions` returns
HTTP 500 with the raw ffmpeg `CalledProcessError` string:

```
{"detail":"Command '['ffmpeg', ...]' returned non-zero exit status 1."}
```

The moonshine container decodes before short-circuiting on bad
input, leaking implementation detail and producing an unhelpful
error.

- **Cite:** `src/hal0/api/routes/v1.py:225-231` (forwards multipart
  unchanged) + the moonshine provider's audio decoder.
- **Fix:** catch `CalledProcessError`, return 415/422 with
  `"unsupported audio format"`. Do not echo the subprocess argv.

## 15. `utility` slot reports `state=ready` while serving zero models — **medium / UX bug**

`GET /api/slots/utility` → `{"state":"ready","models":[],"model_id":""}`.
The systemd cgroup shows `llama-server --model "" --port 8082 ...`
— the process is up but no weights are loaded. A "ready" slot that
cannot fulfil any inference request is worse than `offline`,
because callers will route to it.

- **Repro:**
  ```
  curl -s http://127.0.0.1:8080/v1/models -H "Authorization: Bearer $TOKEN" | jq
  # utility's models field is []
  curl -X POST http://127.0.0.1:8080/v1/chat/completions -H "Authorization: Bearer $TOKEN" \
    -H 'content-type: application/json' \
    -d '{"model":"utility","messages":[{"role":"user","content":"hi"}],"max_tokens":4}'
  # → 400 "model name is missing" from llama-server
  ```
- **Cite:** `src/hal0/slots/manager.py` state-machine + the
  `--model ""` argv emitted by
  `/etc/systemd/system/hal0-slot@utility.service.d/override.conf`.
- **Fix direction:** add an `idle` state for "process up, no model
  loaded" — `ready` should require both `process_up AND
  /v1/models non-empty`. Task #10's lifecycle work covered most
  cases but missed model-less containers.

## 16. basic_auth password is unrecoverable post-install — **medium / gap**

The installer renders the Caddyfile with the bcrypt hash inline and
discards the plaintext. There is no on-host record of the original
password (no `/root/.hal0-creds`, no `/etc/hal0/.basic-auth`, no
journal trace). The only recovery path is `install.sh --auth=basic
--reset-creds` (which rewrites the Caddyfile and reloads
hal0-caddy).

This blocked the 2026-05-16 ui-smoke teammate from running any
edge-authenticated probe end-to-end.

- **Cite:** `installer/install.sh` — runs `caddy hash-password
  --plaintext "${HAL0_ADMIN_PASSWORD}"` and only writes the hash.
- **Fix sketch (opt-in):** drop a chmod-600
  `/etc/hal0/.basic-auth.user` containing `admin:<plaintext>` with
  a banner in the install summary asking the operator to delete it
  after capture. Or document the unrecoverable nature + the
  `--reset-creds` recovery path in `installer/README.md`.

## 17. Deployed install at /opt/hal0 on hal0-test LXC is several commits behind main — **medium / operational**

The 2026-05-16 ui-smoke probe exposed two surfaces that look like
bugs but are artifacts of stale code on the test LXC:

- **`_mount_dashboard` is missing** —
  `/opt/hal0/src/hal0/api/__init__.py` is 354 lines vs. repo HEAD
  415. The entire `_mount_dashboard` function + `StaticFiles
  /assets` + SPA fallback are absent on the deployed install, so
  `GET /` returns `system.http_404` even though
  `/opt/hal0/ui/dist/index.html` exists on disk. Repo HEAD has
  `_mount_dashboard` at `src/hal0/api/__init__.py:359` and the call
  site at line 354. **No fix needed in main**; redeploy.
- **`/api/images` mounted without admin auth** — deployed file
  includes the router without `dependencies=_admin_auth`; repo HEAD
  already plugs this at `src/hal0/api/__init__.py:351`. Same
  redeploy fix.

**Resolution:** `ssh -i ~/.ssh/thinmint root@10.0.1.230 'cd /opt/hal0 && git pull && systemctl restart hal0-api'` — but the directory is **not a git repo** (`fatal: not a git repository`), so the recovery path is actually:

```
# from hal0-dev:
rsync -az --exclude='.git' --exclude='__pycache__' --exclude='.venv' \
  /home/halo/dev/hal0/src/ root@10.0.1.230:/opt/hal0/src/
ssh -i ~/.ssh/thinmint root@10.0.1.230 'systemctl restart hal0-api'
```

This is a deployment-process gap — the test LXC has no upgrade
path. Either ship `hal0 update --apply` working end-to-end (see
PLAN §11) or document that test LXCs are pinned to install-time
HEAD.

## 18. `/v1/audio/speech` returns 404 instead of 400 when `model` is omitted — **low / bug**

POST body `{"input":"...","voice":"..."}` with no `model` field
returns `HTTP 404 "File Not Found"` (OpenAI envelope). OpenAI's
reference returns 400 with `"you must provide a model parameter"`;
the 404 looks like the route is missing and wastes debugging time.

- **Cite:** `src/hal0/api/routes/v1.py:234-238` →
  `_dispatch_and_forward`; the dispatcher's "no model" branch
  returns 404 instead of 400.

## 19. `/api/slots/{name}/config` returns 400/`slot.config_error` for unknown slot — **low / envelope inconsistency**

- `GET /api/slots/doesntexist` → 404 `slot.not_found`
- `GET /api/slots/doesntexist/config` → 400 `slot.config_error`
  (message: "slot config /etc/hal0/slots/doesntexist.toml not
  found and no in-memory state")

A UI distinguishing "slot doesn't exist" from "slot exists but
config is bad" gets ambiguous signals.

- **Fix:** map "no config file AND no in-memory state" →
  `SlotNotFound` upstream in `SlotManager.get_config()`.

## 20. `/api/logs?unit=` missing param returns FastAPI default envelope — **low / envelope inconsistency**

`unit = Query(...)` raises `RequestValidationError`, which is NOT
in `error_codes.install()`'s handler set
(`src/hal0/api/middleware/error_codes.py:32-56` handles `Hal0Error`,
`StarletteHTTPException`, and bare `Exception` only). Response:

```
{"detail":[{"type":"missing","loc":["query","unit"],"msg":"Field required","input":null}]}
```

…instead of the `{"error":{"code","message","details"}}` contract.

- **Fix:** add `app.exception_handler(RequestValidationError)` that
  re-wraps to `{"error":{"code":"validation.invalid", "message":
  ..., "details":{"fields":[...]}}}` shape. Likely affects every
  validated query/path/body parameter across all routes.

## 21. `/api/metrics/prometheus` is in PUBLIC_PATHS but the route is unimplemented — **low / dead config**

`src/hal0/api/middleware/auth.py:103` lists
`"/api/metrics/prometheus"` in `PUBLIC_PATHS`, but
`src/hal0/api/routes/health.py` only defines `/api/metrics` (JSON).
The Prometheus path returns 404 for everyone.

- **Fix:** either implement the Prometheus exporter route, or drop
  the entry from `PUBLIC_PATHS`.

## 22. `cli-doctor` row in δ-harness false-positives on hosts with co-resident hal0 — **info / harness**

When the harness runs on a host that already has a prod hal0
install (the canonical case once the LXC story works), the
`cli-doctor` row fails because the default
`HAL0_DOCTOR_PORTS=8080 3001` collides with the live install. Not
a hal0 defect — but the row currently classifies as `fail`, which
poisons the exit code.

- **Cite:**
  `installer/lib/preflight.sh:140-156` (default port list);
  `src/hal0/cli/doctor_commands.py:76-82` (--ports flag);
  `tests/harness/cli-test.sh` (does not override).
- **Fix (one-line, harness-side):** set `HAL0_DOCTOR_PORTS="${HAL0_DOCTOR_PORTS:-18080 13001}"` near the top of `tests/harness/cli-test.sh`. The dev install binds 18080, so check that.

## 23. Spec drift — there is no `/api/slots/{name}/events` route — **info / drift**

The 2026-05-15 task brief mentioned `/api/slots/{name}/events`;
no such route exists. SSE for slot state is
`/api/slots/{name}/state/stream`. SSE for slot logs is
`/api/slots/{name}/logs/stream`. Update internal docs / future
task briefs.

- **Cite:** `src/hal0/api/routes/slots.py:519–575`.

## 24. `POST /api/updates/apply` returns 200, not 202 — **trivial / drift**

Most accepted-async routes use `status_code=202` (e.g.
`/api/models/{id}/pull`). `/api/updates/apply` doesn't, returning
the queued-job snapshot with 200. Inconsistent, not breaking.

- **Cite:** `src/hal0/api/routes/updater.py:190`.

## 25. v1 proxied 4xx leak upstream's OpenAI envelope (vs hal0 envelope) — **info / by-design**

`/v1/*` proxies upstream errors verbatim, producing the OpenAI
shape `{"error":{"message","type","code"}}` rather than the hal0
shape `{"error":{"code","message","details"}}`. OpenAI SDK clients
prefer this; hal0-internal callers expecting the hal0 shape see a
different schema for proxied vs. originated errors.

- **Status:** likely **won't-fix** — rewriting upstream envelopes
  breaks OpenAI-compat. Document that `/api/*` returns hal0
  envelope and `/v1/*` returns OpenAI envelope.

---

## What the harness *didn't* try (and why)

| Path | Reason | How to enable |
|---|---|---|
| `install.sh` prod install (touches `/etc`, `/var/lib`, `/usr/lib`) | mutates the real host | `HAL0_HARNESS_PROD=1 bash scripts/harness.sh` |
| `--auth=basic` Caddy install | needs prod mode + caddy installable | `HAL0_HARNESS_AUTH=1 HAL0_HARNESS_PROD=1 …` |
| ROCm / FLM-NPU / Moonshine / Kokoro real-model rounds | `hal0-test` LXC owns these | `make release-test` (existing γ tier) |
| Settings GET/PUT round-trip | route exists but not driven; planned next harness iteration | extend `cli-test.sh` |
| First-run wizard endpoints | currently mostly stub (PLAN §7) | wait for Team-B model-pull integration |
| Update --apply / --rollback | needs working releases.hal0.dev | unblock #7 above |

---

## How to re-run

```
# full harness (no prod, no auth):
bash scripts/harness.sh

# include sudo /opt/hal0 install + uninstall:
HAL0_HARNESS_PROD=1 bash scripts/harness.sh

# include --auth=basic install path too:
HAL0_HARNESS_AUTH=1 HAL0_HARNESS_PROD=1 bash scripts/harness.sh
```

The aggregate JSON lands at `tests/harness/reports/harness.json` and
the pretty-printed table is dumped to stdout at the end of every
run.
