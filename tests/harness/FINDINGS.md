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

## 10. Caddy basic_auth swallows the PUBLIC_PATHS allowlist — **critical / bug** · ✅ FIXED BY ARCHITECTURE REMOVAL (ADR-0001)

> **FIXED BY ARCHITECTURE REMOVAL (ADR-0001).** The original fix in PR
> #49 (issue #28) is now historical. Per
> [ADR-0001](../../docs/internal/adr/0001-collapse-edge-auth-into-fastapi.md), the
> Caddyfile no longer carries `basicauth` or a `@public path` matcher
> — Caddy is a dumb TLS terminator + reverse proxy (PR #59), and all
> auth lives in FastAPI (PR #58). The ordering bug cannot recur because
> there is no edge-auth layer to mis-order. Original report preserved
> below.


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

## 16. basic_auth password is unrecoverable post-install — **medium / gap** · ✅ FIXED BY ARCHITECTURE REMOVAL (ADR-0001)

> **FIXED BY ARCHITECTURE REMOVAL (ADR-0001).** This was the source of
> issue #43 (the HITL credential-capture decision). The installer no
> longer prompts for or renders a basic_auth password — credential
> capture moved into the dashboard wizard's password-setup step
> (`POST /api/auth/password`, PR #58 + #59). Password rotation is a
> wizard interaction, not a file rewrite + Caddy reload. Original
> report preserved below for history.


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

**Resolution:** `ssh -i $HAL0_TEST_SSH_KEY root@$HAL0_TEST_HOST 'cd /opt/hal0 && git pull && systemctl restart hal0-api'` — but the directory is **not a git repo** (`fatal: not a git repository`), so the recovery path is actually:

```
# from your dev host, with HAL0_TEST_HOST + HAL0_TEST_SSH_KEY set:
rsync -az --exclude='.git' --exclude='__pycache__' --exclude='.venv' \
  ./src/ root@$HAL0_TEST_HOST:/opt/hal0/src/
ssh -i $HAL0_TEST_SSH_KEY root@$HAL0_TEST_HOST 'systemctl restart hal0-api'
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

## 20. `/api/logs?unit=` missing param returns FastAPI default envelope — **low / envelope inconsistency** · ✅ RESOLVED 2026-05-21

> Closed by `fix/validation-envelope-2026-05-21`. A
> `RequestValidationError` handler now lives in
> `src/hal0/api/middleware/error_codes.py` and reshapes pydantic-driven
> failures into `{"error":{"code":"validation.invalid", "details":
> {"fields":[{"loc","msg","type"}]}}}` at the FastAPI-default 422 status.
> The `input` field is intentionally stripped from each entry to avoid
> echoing caller-supplied payloads (potential secret leak on body
> validation). Original report preserved below.


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

## 21. `/api/metrics/prometheus` is in PUBLIC_PATHS but the route is unimplemented — **low / dead config** · ✅ FIXED BY ARCHITECTURE REMOVAL (ADR-0001)

> **FIXED BY ARCHITECTURE REMOVAL (ADR-0001).** The `PUBLIC_PATHS`
> frozenset was deleted by PR #59 — every route's auth requirement is
> now declared in code via `dependencies=[Depends(require_token)]` (or
> by omitting the dep for public routes). The `/api/metrics/prometheus`
> orphan is documented in `src/hal0/api/routes/health.py`'s module
> docstring as a placeholder until a real exporter ships; the
> allowlist-vs-route drift cannot recur because the allowlist no longer
> exists. Original report preserved below.


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

# Security review — v1.0 auth surface (2026-05-21)

First focused review of the post-ADR-0001 auth surface ahead of the
public v1.0 release. The codebase was treated as unfamiliar; cite
`file:line` on every entry. Findings §26 onwards.

## 26. `X-Forwarded-Email` auth bypass — Caddy does NOT strip inbound copies — **critical**

`src/hal0/api/middleware/auth.py:254-265` (helper) plus the third
auth precedence step at `auth.py:335-342` accept any value of
`X-Forwarded-Email` as a fully-authenticated **admin-scoped**
identity when no Bearer / cookie is presented. The docstring
asserts the header is "only trusted because Caddy strips inbound
copies before forwarding (see Caddyfile template)" — but the
Caddyfile template (`packaging/caddy/Caddyfile.template:34-38`)
does NOT strip the header. It is a bare `reverse_proxy
127.0.0.1:8080` with no `header_up -X-Forwarded-Email`, no
`request_header -X-Forwarded-Email`, nothing.

Repro (with `HAL0_AUTH_ENABLED=1`, password set, no token cookie):

```
curl -X POST https://hal0.local/api/auth/tokens \
  -H "X-Forwarded-Email: attacker@example.com" \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Content-Type: application/json" \
  -d '{"label":"pwn","scope":"admin"}'
```

This succeeds — `require_admin` resolves identity via
`_resolve_forwarded_email`, gets scope=`admin` from the constant
at `auth.py:134`, the admin gate passes, and a new admin token is
minted. The same trick passes `require_writer` on every admin
router. Anyone on the LAN can mint admin Bearer tokens, change
slot configs, trigger updates, etc.

ADR-0001 Child B explicitly removed Caddy's basic_auth — there is
no longer ANY legitimate path that sets `X-Forwarded-Email` from
hal0's own Caddy. The header should default to **rejected**, with
an opt-in env var (`HAL0_TRUST_FORWARDED_EMAIL=1`) for operators
fronting hal0 with their own SSO proxy (Authelia, Authentik,
Cloudflare Access, etc.). Those operators already configure their
proxy to set + strip the header; the default install must not.

- **Where:**
  - `src/hal0/api/middleware/auth.py:254-265` — helper that reads the header without checking trust.
  - `src/hal0/api/middleware/auth.py:335-342` — precedence step 3 promotes to admin scope.
  - `packaging/caddy/Caddyfile.template:34-38` — no header strip.
  - `installer/install.sh` — has no `Caddyfile.local` template that would strip either.
- **Fix:** require an opt-in env var (e.g. `HAL0_TRUST_FORWARDED_EMAIL`) before honouring the header; the default install must reject `X-Forwarded-Email` and fall through to 401. Separately, document the `header_up -X-Forwarded-Email` directive operators must add when they DO front hal0 with their own SSO proxy.
- **Linked fix PR:** [`fix/sec-review-forwarded-email-2026-05-21`](#)

## 27. CSRF token compared with `==` — timing-leak — **high**

`src/hal0/api/middleware/auth.py:413` compares the
`X-CSRF-Token` header against the bound 16-char prefix using
Python's `==` operator. Because the prefix is derived
deterministically from the session cookie value (first 16 chars
of the JWT), a network attacker who can observe response timing
across many requests can in principle reconstruct the prefix
byte-by-byte. The token space is 16 base64url chars (~96 bits)
so this is theoretical against a healthy network, but the fix
is one line of `hmac.compare_digest` and there is no reason to
ship the timing-sensitive form.

The exact line:

```python
if csrf and expected and csrf == expected:
    return
```

- **Where:** `src/hal0/api/middleware/auth.py:413`.
- **Fix (5-line):** replace `csrf == expected` with
  `hmac.compare_digest(csrf, expected)` and `import hmac` at the
  top of the file. Bonus: also use `compare_digest` in any other
  secret-equality check.
- **Linked fix PR:** [`fix/sec-review-csrf-compare-2026-05-21`](#)

## 28. First-run `POST /api/auth/password` race — LAN attacker can claim ownership — **high**

`src/hal0/api/routes/auth.py:256-316` (`set_password`) is
intentionally callable without auth when no password is yet set
("first-run claim ownership"). This is necessary on a fresh
install because the wizard runs before any credential exists.
The problem is the window: between the moment `hal0-api.service`
starts and the moment the legitimate operator opens
`http://hal0.local:8080/` in their browser and types a password,
ANY peer on the LAN can `POST /api/auth/password` first and
take ownership of the install.

This is exacerbated by:

- `installer/install.sh:121-125` — `--no-tls` binds the API on
  `0.0.0.0:8080`. The dev install (which is what
  `curl install | bash` does) defaults to `--no-tls`.
- `hal0-openwebui.service:45` binds `0.0.0.0:3001` too — broad
  LAN exposure is the default.
- The wizard sentinel `/var/lib/hal0/.first_run_done` is also
  written by an UNAUTHENTICATED route (`POST /api/install/complete`,
  see §29), so the attacker can mark first-run done after
  claiming the password.

Mitigations to consider, in increasing order of disruption:

1. Require a one-time setup token printed to the installer
   transcript (`install.sh` emits `Setup token: hal0_xxx`; the
   first POST /api/auth/password must present it as a Bearer
   header). Closest to current UX; one extra paste during the
   wizard.
2. Refuse `set_password` from any peer other than `127.0.0.1`
   until first-run sentinel is written. Wizard runs on the host
   anyway; operators reaching the dashboard from another machine
   would SSH in first, generate a setup token via CLI, and use
   it. Slight UX hit but kills the LAN race.
3. Bind the API to `127.0.0.1` only by default and surface a
   later `hal0 expose` command after a password is set. Most
   secure; biggest UX change.

- **Where:**
  - `src/hal0/api/routes/auth.py:256-316` — endpoint.
  - `installer/install.sh:121-125` — bind host.
  - `src/hal0/api/__init__.py:378` — router mounted without auth dep.

## 29. `/api/install/*` router is wholly unauthenticated and mutating — **critical**

`src/hal0/api/__init__.py:395` mounts the installer router under
`/api/install` with NO auth dependency, on the grounds that the
first-run wizard runs before any credential exists. Reading
`src/hal0/api/routes/installer.py` shows the router exposes
mutating endpoints that should NEVER be available after first-run:

- `POST /api/install/probe` (line 148-166) — re-runs the hardware
  probe and **rewrites `/etc/hal0/hardware.json`**. A LAN
  attacker can race the file write, replace it with attacker
  content, or simply DoS by pegging it repeatedly.
- `POST /api/install/complete` (line 169-210) — **writes the
  first-run sentinel `/var/lib/hal0/.first_run_done`**. Marking
  first-run done is the gate that hides the wizard; an attacker
  can use this to forge "already done" and skip the password
  prompt entirely.
- `POST /api/install/pick-default` (line 353-430) — **starts a
  HuggingFace download** (potentially many GB), assigns a model
  to a slot, and writes `/etc/hal0/slots/<slot>.toml`.
  Combined with §30 below this becomes path traversal.
- `PUT /api/install/slots/{slot}/model` (line 433-469) — sets
  `model.default` on an arbitrary slot. No auth, no rate-limit.

Even discounting the path-traversal in §30, an attacker on the LAN
can wedge ANY hal0 install indefinitely by:

```
while true; do
  curl -sX POST http://hal0.local:8080/api/install/probe >/dev/null
  curl -sX POST http://hal0.local:8080/api/install/pick-default \
    -H "Content-Type: application/json" \
    -d '{"model_id":"qwen3-72b","slot":"primary"}'
done
```

- **Where:**
  - `src/hal0/api/__init__.py:395` — mount.
  - `src/hal0/api/routes/installer.py:148, 169, 353, 433` — endpoints.
- **Fix:** split the router. The read-only state probe
  (`GET /api/install/state`, `GET /api/install/curated-models`)
  stays public so the wizard can render. Every mutating endpoint
  declares `require_writer` AS WELL AS short-circuiting to
  `127.0.0.1`-only when the first-run sentinel does not yet exist
  (so the wizard works locally, no remote attack window).

## 30. Path traversal in `_assign_to_slot(slot, ...)` — **critical**

`src/hal0/api/routes/installer.py:298-350` builds the slot config
path with `slot_path = paths.slots_config_dir() / f"{slot}.toml"`
from the user-supplied `slot` body field — both in
`POST /api/install/pick-default` and `PUT /api/install/slots/{slot}/model`.
`slot` is not validated. `slot = "../../tmp/pwn"` resolves to
`/tmp/pwn.toml` on a default install. Combined with §29's lack of
auth, ANY LAN peer can write attacker-controlled TOML content to
any path the `hal0` service user can reach (typically root, since
`install.sh` runs `hal0-api.service` as root). Read the existing
"file" first (line 313-323) opens it via `tomllib.load`, which
will surface a parse error if the path exists and isn't TOML —
useful for an attacker probing the FS.

Repro (post-§29 fix this becomes auth'd-only, but the path
traversal stays a real defect):

```
curl -X POST http://hal0.local:8080/api/install/pick-default \
  -H "Content-Type: application/json" \
  -d '{"model_id":"qwen3-4b","slot":"../../tmp/pwn"}'
# Creates /var/lib/hal0/slots/../../tmp/pwn.toml = /tmp/pwn.toml
```

- **Where:**
  - `src/hal0/api/routes/installer.py:298-350` — `_assign_to_slot`.
  - `src/hal0/api/routes/installer.py:387-396, 444-463` — callers.
- **Fix:** validate `slot` against `^[A-Za-z0-9_-]{1,32}$` (the
  same shape used in slot create/update elsewhere) and reject
  anything else with a 400 envelope `validation.invalid` BEFORE
  building the path. Same fix for any other route that f-strings
  a user-supplied `slot` into a path.

## 31. `_admin_auth` router-level dep is `require_token`, not `require_writer` — **medium**

`src/hal0/api/__init__.py:399-462` declares
`_admin_auth = [Depends(require_token)]` and applies it to the
admin routers (`/api/slots`, `/api/models`, `/api/settings`,
`/api/hardware`, `/api/logs`, `/api/providers`, `/api/updates`,
`/api/capabilities`, `/api/backends`, `/api/images`). That's
just "any valid token". The scope-enforcement
(`require_writer`/`require_admin`) is added per-route inside
each routes module.

Today every mutating route does declare `_writer` (verified by
grep), so this is correct in practice. But it is a structural
landmine: a new admin route written in slots.py or models.py
that forgets to add `dependencies=_writer` is silently mutable
by any `read-only`-scoped token (or `v1-only`-scoped token).
There is no "deny by default" backstop.

Two compounding observations:

- `src/hal0/api/__init__.py:386-387` — `_v1_auth =
  [Depends(require_token)]` on `/v1/router`, which IS mutating
  (POST /v1/chat/completions etc.) but uses only the token
  gate. The auth middleware docstring explicitly says "POST /
  PUT / PATCH / DELETE on admin routers should reject
  read-only" — that contract does not hold for `/v1/*`.
  Read-only scoped tokens can fire chat completions, embeddings,
  TTS, image gen, etc. Likely intentional (read-only means
  "can't change the box config", not "can't burn GPU time") but
  the docstring needs to match.

- **Fix:** either (a) change `_admin_auth` to
  `[Depends(require_writer)]` for the routers that are
  primarily mutating, and add `dependencies=[Depends(require_token)]`
  per-route for the read-only endpoints in those routers; or
  (b) add a CI lint that asserts every non-GET handler in
  admin routes declares `require_writer`. (b) is cheaper.
- **Where:** `src/hal0/api/__init__.py:386, 399, 400, 402, 404, 405, 407, 413, 416, 430, 441, 461`.

## 32. No login rate-limit / lockout — **high**

`src/hal0/api/routes/auth.py:180-229` (`POST /api/auth/login`)
runs the bcrypt verify and returns 401 on failure but has no
rate-limit, no exponential backoff, no IP lockout. With bcrypt
cost 12 the legitimate path costs ~250ms per attempt, but a
distributed attack from the LAN can still mount a meaningful
dictionary attack against the owner password. The `_MIN_PASSWORD_LEN
= 8` (line 88) is a low ceiling.

The session-cookie path (`POST /api/auth/login`) and the
password-set rotation path
(`POST /api/auth/password` — when password is already set)
both share this — neither is throttled.

- **Where:** `src/hal0/api/routes/auth.py:180-229`, `:256-316`.
- **Fix:** in-process token bucket keyed by source IP, e.g. 5
  failed attempts per minute then 429 for 60s. starlette
  middleware or a lightweight in-mem dict suffice. Bind to
  `app.state` so a process restart resets the counter — that's
  fine, the attacker still pays the bcrypt cost.

## 33. Session JWT cannot be revoked server-side — **medium**

`src/hal0/api/auth/password.py:232-255` (`verify_session_token`)
trusts the JWT's `exp` claim. There is no server-side session
store. The module docstring (lines 35-41) acknowledges this and
calls out keyring rotation as the "sign everyone out" escape
hatch — but the keyring rotation:

- Is not exposed via API or CLI (no `hal0 auth rotate-keyring`
  subcommand exists in `src/hal0/cli/`).
- Is silently destructive in the OSError-on-read path: see §34.

Practical impact: if the owner password is rotated via
`POST /api/auth/password`, every existing session cookie remains
valid for up to 7 days (the default TTL at line 76). A stolen
cookie cannot be invalidated without manually `rm
/etc/hal0/keyring` and restarting the service, which also
invalidates ALL other sessions including the rotator's.

- **Where:**
  - `src/hal0/api/auth/password.py:232-255` — verify.
  - `src/hal0/api/routes/auth.py:298-316` — `set_password` does not bump the keyring.
  - `src/hal0/api/routes/auth.py:232-253` — `/logout` is purely client-side cookie deletion.
- **Fix (post-v1):** add a `jti` claim per minted token and a
  small revocation list (set of revoked `jti`s persisted to
  tokens.toml under a new `[[revoked_sessions]]` table).
  Password rotation revokes all live `jti`s. Pre-v1: at minimum,
  document the limitation in the deployment guide.

## 34. `_load_or_create_signing_key` silently rotates key on read-failure — **medium**

`src/hal0/api/auth/password.py:172-199` reads the keyring file;
if `read_text` fails with any `OSError` other than
`FileNotFoundError` (line 188-195), the function silently mints
a fresh key and writes it to disk via `_atomic_write_bytes`. The
in-line comment treats this as a feature ("we'd rather mint a
fresh key than crash the API on startup") — but the
consequences are:

- An OS-level fault (transient FS error, permissions glitch, EIO,
  ENOSPC at read time) will invalidate every live session cookie
  on the box, silently. No log line at WARNING or above is
  emitted at the rotation site; the `pass` swallows even the
  exception class.
- If the read fails but the subsequent `_atomic_write_bytes`
  succeeds, the on-disk key file now has new content with mode
  0600 owned by the service user — overwriting whatever was there
  before. Worst case the prior contents were correct and we just
  destroyed the only copy.

- **Where:** `src/hal0/api/auth/password.py:182-198`.
- **Fix:** distinguish `FileNotFoundError` from other `OSError`
  subclasses. On the latter, log a WARNING with the exception
  details and raise — let the service crash and have systemd
  restart it, rather than silently rotating keys. This is the
  pattern the tokens.toml loader (`auth/tokens.py:262-270`)
  already uses for unreadable-but-present files.

## 35. OpenWebUI exposed on `0.0.0.0:3001` by default — **medium**

`packaging/systemd/hal0-openwebui.service:45` binds the
OpenWebUI container to `0.0.0.0:3001`. OpenWebUI has its own
auth (`WEBUI_AUTH` env in `/etc/hal0/openwebui.env`) but it is
a separate trust boundary from hal0's: stealing OpenWebUI
admin does not directly steal hal0 admin, but it DOES allow
running arbitrary chat completions against any configured
upstream (including paid providers like OpenAI/Anthropic if
the operator wired them up under `/api/upstreams`). That's a
financial exposure independent of hal0's own auth.

The bind is intentional per PLAN §2 "public tier" — but
documenting it as "public" is a different decision from
defaulting it that way on every fresh install.

- **Where:** `packaging/systemd/hal0-openwebui.service:37-48`.
- **Fix (post-v1):** bind `127.0.0.1:3001` by default and
  surface a `hal0 expose openwebui` CLI command that re-renders
  the unit file with `0.0.0.0`. Same pattern as the API
  bind-host decision in install.sh.

## 36. `HAL0_AUTH_ENABLED` defaults to FALSE — open by default — **high**

`src/hal0/auth/tokens.py:143-151` reads `HAL0_AUTH_ENABLED`. If
unset (or `0`/`false`), `auth_enabled()` returns `False`, and
every dependency (`require_token`, `require_writer`, `require_admin`)
short-circuits to a pass-through that returns
`identity=anonymous, scope=all` (see `auth.py:295-301, 366-370,
445-446`). Combined with §28 (default `0.0.0.0` bind) this means
the **default v1 install is wide-open**: anyone on the LAN gets
admin-equivalent access to slots, models, upstreams, updater,
token CRUD, settings, hardware probe, and `/v1/*` inference.

The README claims "anyone running `curl … | bash` gets … a
FastAPI server on :8080 with a dashboard, OpenWebUI on :3001,
and a `/v1/*` OpenAI-compatible API". It does NOT claim that
server requires authentication. The auth surface is fully
implemented but the env var that activates it is not flipped by
the installer.

- **Where:**
  - `src/hal0/auth/tokens.py:143-151` — defaults to off.
  - `installer/install.sh` — does not set `HAL0_AUTH_ENABLED=1`
    anywhere in the generated `/etc/hal0/api.env`.
- **Fix:** make `HAL0_AUTH_ENABLED=1` the default. Invert the
  semantics: introduce `HAL0_AUTH_DISABLED=1` for the
  pre-existing test scaffolding that depends on pass-through.
  Then the installer can omit the env var entirely and ship
  locked-by-default. Combined with §28's first-run flow, the
  user is forced through the wizard's "set a password" before
  any state-changing API call succeeds.

## 37. No security response headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options) — **low**

`src/hal0/api/__init__.py:353-466` and `packaging/caddy/Caddyfile.template`
neither emit any of the standard browser security headers. The
dashboard at `/` is a Vue SPA that consumes `/api/*` from the
same origin, so the missing CORS allowlist is "secure by absence"
(no `Access-Control-Allow-Origin: *` to abuse). But the missing
HSTS / CSP / X-Frame-Options surfaces real risks:

- Without HSTS, a downgrade attack on first connect to a TLS
  install is possible.
- Without `X-Frame-Options: DENY` or
  `frame-ancestors 'none'` in CSP, the dashboard can be iframed
  by any other origin for clickjacking against logged-in
  sessions. Combined with the SameSite=Lax cookie (auth.py:219)
  this is exploitable for state-changing GETs (none today, but
  the contract is brittle).
- Without `X-Content-Type-Options: nosniff`, served assets can
  be MIME-sniffed.

- **Where:** `src/hal0/api/__init__.py:353-466` (no middleware),
  `packaging/caddy/Caddyfile.template:34-38` (no `header`
  directive).
- **Fix:** install a small Starlette middleware that sets
  `Strict-Transport-Security: max-age=31536000`,
  `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`,
  `Content-Security-Policy: default-src 'self'; img-src 'self' data:; ...`.
  For Caddy, add a global `header` block in the template.

## 38. Session cookie has no `Max-Age` — relies on JWT exp — **info**

`src/hal0/api/routes/auth.py:215-225` sets the session cookie
WITHOUT `max_age` / `expires`, making it a session-scoped
cookie that dies on browser close. Meanwhile the JWT payload's
`exp` is 7 days (`src/hal0/api/auth/password.py:76`). The result
is: the cookie's intended client-side lifetime (browser session)
and the server-validated lifetime (7 days) disagree.

Practical impact is minor (the shorter one wins on any given
browser, but if the cookie store survives a browser restart
it'll be valid for the full 7 days). Worth aligning so the
operator's mental model matches reality.

- **Where:** `src/hal0/api/routes/auth.py:215-225`,
  `src/hal0/api/auth/password.py:76`.
- **Fix:** set `max_age=DEFAULT_SESSION_TTL_SECONDS` on
  `response.set_cookie`. Make the wire and the claim agree.

## 39. `/api/install/probe` triggers heavy subprocess fanout without auth — **medium**

Subset of §29 worth calling out separately: the hardware probe
shells out to `rocminfo`, `lspci`, `nvidia-smi`, etc., each
invocation taking ~hundreds of ms. A LAN attacker can drive 100
concurrent probes with `xargs -P 100`, exhausting fork
bandwidth and pinning the API. Even after the §29 auth fix this
warrants an in-process semaphore (one probe at a time).

- **Where:** `src/hal0/api/routes/installer.py:148-166` →
  `src/hal0/hardware/probe.py:413+` (subprocess fanout).
- **Fix:** `asyncio.Semaphore(1)` around the probe; rejected
  concurrent requests get a 429.

## 40. SSE journal-stream `--since` is forwarded unvalidated — **low**

`src/hal0/api/routes/logs.py:134-135, 200-201` pass the
client-supplied `since` query string to `journalctl --since`
without validation. journalctl rejects unparseable timestamps
with a non-zero exit (the SSE generator surfaces that as a
disconnect), so this is not RCE — but a malformed value can
make the journalctl subprocess hang in some configurations
(observed historically with `--since=invalid` on certain
systemd versions). The SSE stream stalls until the 8-second
timeout elapses.

Lower-impact than the unit-name validation already present
(line 58-83). Cheap to fix by light validation: parse as ISO
timestamp or one of journalctl's well-known relative formats
("5min ago", "yesterday", etc.).

- **Where:** `src/hal0/api/routes/logs.py:134, 200`.
- **Fix:** allow only ISO-8601 timestamps and a small allowlist
  of relative-time tokens; reject everything else with a 400
  envelope.

## 41. `slot` parameter in `/api/slots/{name}/logs` not validated before journalctl invocation — **info / defense-in-depth**

`src/hal0/api/routes/slots.py:725-731, 771-780` interpolates the
`name` path param into the unit name
(`hal0-slot@{name}.service`) and passes it to
`journalctl -u <unit>`. There's no shell, so no command
injection, but the unit-name validator from
`src/hal0/api/routes/logs.py:58-83` is not reused — meaning a
`name` like `foo --output=json` would be passed as a single argv
element (which journalctl would silently reject as an unknown
unit, fine) but `name = "foo\nNotAfter=..."` would be a
correctness footgun.

Lower priority because the route auth (require_token via
_admin_auth) catches the obvious unauthenticated probe, and
SlotManager.status() raises slot.not_found on unknown names
before journalctl runs.

- **Where:** `src/hal0/api/routes/slots.py:704-744, 754-799`.
- **Fix:** call `logs._validate_unit(f"hal0-slot@{name}.service")`
  (or extract the validator into a shared helper) before
  building the journalctl argv.

## 42. `HAL0_UPDATE_SKIP_COSIGN=1` env-var bypass is mostly safe but uses string compare — **info**

`src/hal0/updater/updater.py:440-458` (`_cosign_skip`) gates the
skip on `__version__` not being a stable v1+ tag, then warns and
no-ops the cosign verify. The version check
(`_is_pre_release`, line 431-437) is `version.startswith("0.")
or "-" in version`. This is correct for `0.x.y` and
`1.0.0-rc1`, but `1.0.0+dev` (PEP 440 local-version) does NOT
match — the skip would be honored on a stable build that was
locally tagged. Edge case but worth tightening when the v1 tag
lands.

- **Where:** `src/hal0/updater/updater.py:431-458`.
- **Fix (v1+):** use `packaging.version.Version(v).is_prerelease`
  (already a runtime dep via pydantic). One-liner.

---

## Security review — severity summary

| Severity   | Count | IDs |
|------------|------:|-----|
| critical   | 3     | §26 (X-Forwarded-Email bypass), §29 (open /api/install/*), §30 (slot path traversal) |
| high       | 4     | §27 (CSRF `==`), §28 (first-run password race), §32 (no login rate-limit), §36 (auth off by default) |
| medium     | 5     | §31 (router-level dep is reader-only), §33 (no session revoke), §34 (silent keyring rotation), §35 (OpenWebUI on 0.0.0.0), §39 (unauthed heavy probe) |
| low        | 2     | §37 (no security headers), §40 (journalctl --since validation) |
| info       | 3     | §38 (cookie Max-Age vs JWT exp), §41 (slot-name validation), §42 (cosign-skip version check) |

Fix PRs opened separately for the trivial-fix criticals/highs:

- `fix/sec-review-csrf-compare-2026-05-21` — §27.
- `fix/sec-review-forwarded-email-2026-05-21` — §26.

The remaining critical/high items (§28, §29, §30, §32, §36) are
larger design changes and should be triaged before v1.0 cuts.

---

## What the harness *didn't* try (and why)

| Path | Reason | How to enable |
|---|---|---|
| `install.sh` prod install (touches `/etc`, `/var/lib`, `/usr/lib`) | mutates the real host | `HAL0_HARNESS_PROD=1 bash scripts/harness.sh` |
| TLS-default Caddy install | needs prod mode + caddy installable | `HAL0_HARNESS_TLS=1 HAL0_HARNESS_PROD=1 …` (renamed from `HAL0_HARNESS_AUTH` per ADR-0001) |
| ROCm / FLM-NPU / Moonshine / Kokoro real-model rounds | `hal0-test` LXC owns these | `make release-test` (existing γ tier) |
| Settings GET/PUT round-trip | route exists but not driven; planned next harness iteration | extend `cli-test.sh` |
| First-run wizard endpoints | currently mostly stub (PLAN §7) | wait for Team-B model-pull integration |
| Update --apply / --rollback | needs working releases.hal0.dev | unblock #7 above |

---

## How to re-run

```
# full harness (no prod, no TLS):
bash scripts/harness.sh

# include sudo /opt/hal0 install + uninstall:
HAL0_HARNESS_PROD=1 bash scripts/harness.sh

# include the TLS-default install path too (installs Caddy per ADR-0001):
HAL0_HARNESS_TLS=1 HAL0_HARNESS_PROD=1 bash scripts/harness.sh
```

The aggregate JSON lands at `tests/harness/reports/harness.json` and
the pretty-printed table is dumped to stdout at the end of every
run.

---

# v0.3 Hermes integration δ-harness (2026-05-28)

The δ-tier grew a Python `tests/harness/integration/` subdir in PR-11
to drive the v0.3 chat + persona round-trip through the hal0-api WS
proxy against a `FakeWsServer` mock hermes. Rows are in pytest rather
than the shell harness because the surface needs live WebSocket frames
+ JSON-RPC envelopes; the existing `agents-test.sh` rows still cover
the systemd unit + installer plumbing.

| Row                                                                   | Tier | Outcome  | Notes |
|-----------------------------------------------------------------------|------|----------|-------|
| `v0_3_chat_roundtrip / emits_delta_and_complete`                       | δ    | pass     | message.delta + message.complete arrive byte-identical |
| `v0_3_chat_roundtrip / origin_allowlist_rejects_unknown_origin`        | δ    | pass     | DA-sec-ops MUST-FIX #2 pinned end-to-end |
| `v0_3_chat_roundtrip / unauthenticated_ws_is_rejected`                 | δ    | pass     | HMAC cookie required even with allowlisted Origin |
| `v0_3_chat_roundtrip / progress_then_complete_ordering_preserved`      | δ    | pass     | coalescer flushes pending progress before complete |
| `v0_3_persona_activate / list_reflects_seeded_personas`                | δ    | pass     | hermes + coder + active=hermes |
| `v0_3_persona_activate / writes_active_txt`                            | δ    | pass     | activate persists active.txt on disk |
| `v0_3_persona_activate / with_reload_calls_hermes`                     | δ    | pass     | reload nudge fires upstream (best-effort, may be a no-op when fake hermes proto differs) |
| `v0_3_persona_activate / unknown_persona_returns_404`                  | δ    | pass     | typed envelope `persona.not_found` |
| `v0_3_persona_activate / unknown_agent_returns_404`                    | δ    | pass     | typed envelope `agent.unknown` |
| `v0_3_persona_activate / detail_after_activate_shows_new_active`       | δ    | pass     | swap + lookup round-trip |

Drive via:

```
.venv/bin/pytest tests/harness/integration/ --no-cov
```

Findings catalogued below as section §43 forward.

## 43. v0.3 chat WS round-trip pinned — **info / regression-guard**

The new δ row covers the full PR-9 chat-proxy → mock hermes →
PR-10 composer envelope path. Any change to the WS upgrade gate
(Origin allowlist + HMAC session cookie) OR the JSON-RPC frame shape
fails this test in CI before reaching the dashboard.

- **Cite:** `tests/harness/integration/test_v0_3_chat_roundtrip.py`,
  `src/hal0/api/agents/chat_proxy.py`.
- **Status:** ✅ landed in PR-11 (2026-05-28).

## 44. v0.3 persona-activate round-trip pinned — **info / regression-guard**

The new δ row covers the PR-4 persona endpoints over a real FastAPI
mount + a mock hermes for the hot-reload nudge. Catches regressions
where the activate handler writes `active.txt` but skips the JSON-RPC
helper (or vice versa).

- **Cite:** `tests/harness/integration/test_v0_3_persona_activate.py`,
  `src/hal0/api/agents/personas.py`, `src/hal0/agents/personas.py`.
- **Status:** ✅ landed in PR-11 (2026-05-28).

## 45. Three new v0.3 backend endpoints pinned (`restart` / `skills` / `memory/stats`) — **info / regression-guard**

PR-11 added unit-test coverage for the three missing endpoints
flagged during PR-6 / PR-8 / PR-10 integration. Each maps to a
dashboard surface (SidebarAgentBlock service chip, skills sidebar,
memory chip) that previously rendered stale static data.

- **Cite:** `tests/agents/test_agent_restart_endpoint.py`,
  `tests/agents/test_agent_skills_endpoint.py`,
  `tests/agents/test_agent_memory_stats_endpoint.py`.
- **Status:** ✅ landed in PR-11 (2026-05-28).

## 46. δ-harness `delegate_task` 3-backend dispatch coverage — **gap / regression-guard + DA finding**

DA must-fix #2 from the OpenRouter integration analysis
(`openrouter-research-2026-05-28/notes/da-or.md` line 39 + PLANNING.md
§4 #2) flagged that R7's "Hermes already ships 7 spawn backends"
claim was unverified marketing.  The Phase 0 delegate harness
addresses that with one parametrised dispatch test plus four
per-backend test files exercising local / docker / modal at the
δ-tier.  Coverage uses mocked backends so CI never spends Modal
credits or pulls docker images.

Findings from the upstream audit (pin `0554ef1a`):

- **R7's "7 backends" is partial marketing.** Upstream actually ships
  **6 execution-environment backends**: local, docker, singularity,
  modal, daytona, ssh.  Vercel Sandbox does NOT exist in upstream as
  a `BaseEnvironment` subclass.  Cite:
  `~/src/hermes-agent/tools/terminal_tool.py:1039-1178`
  (`_create_environment` factory),
  `~/src/hermes-agent/tools/environments/__init__.py:1-12`
  (docstring enumerates the six).
- **Modal has two sub-modes** (`direct` + `managed`) selected via
  `terminal.modal_mode`, plus an "auto" fallback.  Our fake covers
  the direct mode + the credentials-missing degraded path.
- **`BaseEnvironment` is the actual ABC**, not a per-backend "spawn
  adapter" — every concrete backend subclasses it.  The DA framing
  "delegate_task w/ Modal/Daytona path is not exercised in
  tests/agents/" is accurate: upstream tests cover individual
  environments in isolation but nothing exercises the
  `delegate_task → backend dispatch` hop.

The drift gate
(`test_upstream_base_environment_still_has_expected_methods`) skips
on machines without `~/src/hermes-agent` (CI, fresh laptops) and
asserts the four-method contract on machines where the checkout is
present.  When the weekly `hermes-sdk-diff` workflow (ADR-0018)
bumps the pin, that gate fires first if upstream renamed a method.

| Row                                                                         | Tier | Outcome | Notes |
|-----------------------------------------------------------------------------|------|---------|-------|
| `test_delegate_task_local / round_trips_simple_echo`                         | δ    | pass    | happy path; output reaches assistant response |
| `test_delegate_task_local / records_invocation_count_and_payload`            | δ    | pass    | command + cwd captured verbatim |
| `test_delegate_task_local / error_envelope_does_not_crash_parent`            | δ    | pass    | RuntimeError surfaces as per-task error |
| `test_delegate_task_local / empty_goal_rejected_before_dispatch`             | δ    | pass    | mirrors upstream `tools/delegate_tool.py:2034` |
| `test_delegate_task_docker / round_trips_with_image_kwargs`                  | δ    | pass    | image kwarg reaches the fake; output round-trips |
| `test_delegate_task_docker / unavailable_degrades_gracefully`                | δ    | pass    | init_session raise → per-task error, parent intact |
| `test_delegate_task_docker / payload_includes_container_kwargs`              | δ    | pass    | image / cpu / memory / disk / volumes / env captured |
| `test_delegate_task_docker / nonzero_returncode_surfaces_as_error`           | δ    | pass    | exit 127 surfaces as inline error; output preserved |
| `test_delegate_task_modal / round_trips_with_sandbox_kwargs`                 | δ    | pass    | sandbox_kwargs (cpu/memory/ephemeral_disk) captured |
| `test_delegate_task_modal / token_missing_degrades_gracefully`               | δ    | pass    | MODAL_TOKEN missing → per-task error |
| `test_delegate_task_modal / cold_start_latency_visible_in_duration`          | δ    | pass    | 200 ms simulated cold-start shows up in duration_ms |
| `test_delegate_task_modal / multiple_commands_share_one_sandbox`             | δ    | pass    | init_session called once, execute called twice |
| `test_delegate_task_dispatch_matrix / per_backend_round_trips[local]`        | δ    | pass    | dispatch matrix L1 |
| `test_delegate_task_dispatch_matrix / per_backend_round_trips[docker]`       | δ    | pass    | dispatch matrix L2 |
| `test_delegate_task_dispatch_matrix / per_backend_round_trips[modal]`        | δ    | pass    | dispatch matrix L3 |
| `test_delegate_task_dispatch_matrix / fans_out_to_three_backends_in_one_call`| δ    | pass    | upstream batch-mode shape — three backends, one call |
| `test_delegate_task_dispatch_matrix / unknown_backend_raises_keyerror`       | δ    | pass    | unregistered backend name fails loud, no silent local fallback |
| `test_delegate_task_dispatch_matrix / upstream_base_environment_methods`     | δ    | skipped on CI / passes on dev | drift gate against `tools.environments.base.BaseEnvironment` |
| `test_delegate_task_dispatch_matrix / all_fakes_implement_backend_contract`  | δ    | pass    | every fake implements `_BackendContract` (`init_session`/`execute`/`cleanup`) |

- **Cite:** `tests/harness/integration/_delegate_fakes.py`,
  `tests/harness/integration/_delegate_runner.py`,
  `tests/harness/integration/test_delegate_task_local.py`,
  `tests/harness/integration/test_delegate_task_docker.py`,
  `tests/harness/integration/test_delegate_task_modal.py`,
  `tests/harness/integration/test_delegate_task_dispatch_matrix.py`,
  upstream `tools/environments/base.py:288` + `terminal_tool.py:1039`.
- **Status:** ✅ landed in the Phase 0 delegate-harness PR (2026-05-29).
  Gates V3a Hermes-observability per
  `openrouter-research-2026-05-28/PLANNING.md` §3 Phase 0.

### V3a observability scope decision

Three backends (local + docker + modal) round-trip cleanly through
the dispatch hop — V3a Hermes-observability survives intact.  The
three remaining upstream backends (singularity / daytona / ssh) are
out of Phase 0 scope but can be added incrementally without
rescoping V3a: §14 of the README walks through the per-backend
add procedure.

If the upstream-drift gate fires in a future
`scripts/hermes-sdk-diff.sh --bump` run, the appropriate response is
to re-shape `_delegate_fakes.py::_BackendContract` to match (or, if
upstream rolls back to a smaller surface, scope down V3a's display).

