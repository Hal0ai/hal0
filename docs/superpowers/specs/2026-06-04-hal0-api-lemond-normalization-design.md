# Design — Fold lemond normalization into hal0-api (retire Bifrost)

**Date:** 2026-06-04
**Status:** Approved design, pending implementation plan
**Branch base:** `origin/main` @ `17367b5` (the revision CT105 `/opt/hal0` runs)
**Supersedes:** PR #469 (Bifrost gateway) — to be retired; its live-slot resolver is ported, not its process model.

---

## 1. Summary

Add a single normalization step to hal0-api's OpenAI-compatible chat path so **every** agent that
talks to lemond through hal0-api (`http://127.0.0.1:8080/v1`) automatically gets:

1. **Live-slot model resolution** — a stable virtual model name (`hal0/primary`) that resolves
   to whichever LLM slot is actually loaded right now (iGPU chat slot preferred over NPU/FLM), so
   callers never track runtime model swaps.
2. **Reasoning suppression** — top-level `enable_thinking: false` injected for lemond-bound requests
   unless the caller opted in, so local reasoning models don't emit `<think>` blocks that blow the
   request budget.

Hermes keeps its existing trivial config (`provider: custom` + `base_url: http://127.0.0.1:8080/v1`).
No native Hermes provider, no plugin, no venv patch. The Bifrost sidecar is retired.

---

## 2. Why this shape (alternatives considered & rejected)

The handoff posed three options — **native Hermes provider**, **external Bifrost gateway**, or
**hybrid/hal0-api**. Investigation (file:line evidence in §9) made the choice clear.

### 2.1 Native Hermes provider — REJECTED

- Hermes' `PROVIDER_REGISTRY` is a dict of `ProviderConfig` dataclasses in `hermes_cli/auth.py:184`.
  The only runtime extension hook (`auth.py:460-491`) registers **`auth_type=="api_key"` cloud
  providers** discovered from a `providers/` package and **explicitly skips `custom`/`openrouter`**.
  A LAN OpenAI-compatible endpoint *must* use the built-in `provider: custom` + `base_url` — which
  hal0 already does.
- hal0 **already removed** a model-provider plugin (`Hal0Profile`) for hardcoding a dead `:8000`
  base_url (`hermes_provision.py:480`, template comment `config.yaml.j2:30-38`). Re-introducing a
  model-provider plugin repeats that mistake.
- Any edit inside the Hermes venv site-packages is **wiped on `hermes-all` upgrades** — a
  native/patched provider is upgrade-fragile.

### 2.2 External Bifrost gateway — RETIRED

- Bifrost was designed to fix lemond's strict model names + reasoning timeouts **when agents point
  straight at `lemond:13305`**. But Hermes (and hal0's agents) point at **hal0-api `:8080/v1`**, which
  already does model remap + proactive ensure-load via `SlotManager.load()`
  (`api/routes/v1.py:359`) — something Bifrost cannot do (it only resolves to *already-loaded*
  slots).
- Bifrost adds a Go binary, a systemd unit, an ABI-fragile `-buildmode=plugin` `.so`, and an extra
  network hop, to re-solve ~90% of what hal0-api already solves.
- Bifrost injects `chat_template_kwargs.enable_thinking=false` (`gateway/normalize/normalize.go:35`)
  — the **wrong layer** for current lemond (SHA `1bce071`), which reads **top-level**
  `enable_thinking`/`thinking` and strips its own handled fields (`server.cpp:58-114`). Getting
  `chat_template_kwargs` through Bifrost also required fighting Bifrost's param-dropping compat layer
  (the branch's last commit, plus memory `hal0_bifrost_passthrough_flag`).
- **Keep its one genuine asset:** the live `/health` resolver with the iGPU-over-FLM discriminator
  (`gateway/normalize/resolve.go`) is **ported to Python** into hal0-api.

### 2.3 Fold into hal0-api — CHOSEN

hal0-api is already the universal chokepoint every agent uses; it already remaps models and
ensure-loads slots. We add the two missing bits **there**, in one place, with no extra process/hop,
fully under hal0's control, and upgrade-safe (no Hermes venv coupling).

---

## 3. Where it hooks in (current request path)

A `POST /v1/chat/completions` on hal0-api today (`api/routes/v1.py:550-584`):

1. `_read_json_body(request)` (v1.py:569) — parse body once.
2. `_rewrite_chat_slot_alias` (v1.py:233-281) — static alias (`primary`/`agent-hermes`/`utility`) →
   slot TOML `model.default`; overwrites `request._body` in place.
3. `_ensure_backend_for_model` (v1.py:359) — reverse-map model → chat slot, `SlotManager.load()`.
4. `Dispatcher.dispatch` → `forward` (`dispatcher/router.py:398/594`) → composite `hal0` upstream →
   `http://127.0.0.1:13305/v1/chat/completions` (`router.py:104,138`).
5. On `NoRouteFound` → fall-through `lemonade_proxy._proxy` → also `:13305/v1/...`
   (`lemonade_proxy.py:138`).

**Confirmed today hal0-api does NOT touch** `enable_thinking`/`thinking`/`chat_template_kwargs`/
`reasoning`/`no_think` anywhere (grep clean across `src/hal0`), and the model mapping is
**static/config-based** — there is no live `/health` lookup to "whatever is loaded now."

---

## 4. Components (all additive, in `src/hal0`)

Normalization is **two phases applied at two points** in the pipeline, because the model name must
be resolved *before* routing, but thinking-injection must be gated on the route target, which is only
known *after* routing.

#### 4.1a `resolve_model_name(body) -> body` — pre-dispatch (top of handler)

Applied once near `_read_json_body` (`api/routes/v1.py:569`), folding in / replacing
`_rewrite_chat_slot_alias`. Order:

1. **Virtual-name resolution.** If `body["model"]` is a registered virtual name, resolve it to a
   live-loaded LLM slot via `LiveSlotResolver` (§4.2) using that name's **resolution chain**:
   - `hal0/primary` — the iGPU chat slot.
   - `hal0/npu` (alias `hal0/flm`) — the FLM/NPU slot, for instruct-only / lighter work
     (e.g. Hermes memory-extraction, which times out on reasoning models — `[[hal0_flm_npu_llm_models]]`).
   - `hal0/utility` — the designated slack-absorber slot.

   The `hal0/` namespace marks these as hal0-owned virtual names (not lemond-native model ids).
2. **Static alias rewrite (unchanged).** Existing `primary`/`agent-hermes`/`utility` → `model.default`
   behavior is preserved for back-compat.

Re-serialises into `request._body` so both the dispatcher path and the `NoRouteFound` fall-through
proxy observe the resolved model name.

#### 4.1b `apply_thinking_policy(body) -> body` — at the lemond-bound forward boundary

Applied where the route is known to target lemond — i.e. on the composite-`hal0` forward
(`dispatcher/router.py` forward path) **and** the `lemonade_proxy._proxy` fall-through
(`lemonade_proxy.py`). NOT applied on `kind=="remote"` upstream forwards. Implements §5. This is the
only point where "is this lemond-bound?" is answerable, so the gate lives here rather than at the top.

> Note: requests reaching hal0-api `:8080/v1` are lemond-bound in practice — Hermes' cloud
> (OpenRouter) traffic uses a different provider/base_url and never transits hal0-api. The
> `kind=="remote"` exclusion is defensive for hal0-registered remote upstreams, not a path Hermes
> exercises today.

### 4.2 `LiveSlotResolver`

Ports `gateway/normalize/resolve.go` semantics to Python and generalizes the single iGPU-first rule
into a **configurable resolution chain** per virtual name.

- Reads lemond `/api/v1/health` → `all_models_loaded[]`, filters `type=="llm"`.
- **Slot classification:** the `-FLM`/`FLM` name suffix plus optional `device`/`recipe`/`backend`
  hints (`npu`/`flm`) classify a loaded slot as iGPU-chat vs NPU/FLM (the ported `isNPUorFLM`
  discriminator). The configured `utility` slot is identified by its slot name/role from slot config.
- **Resolution chain (ordered preference, first loaded match wins):** each virtual name carries an
  ordered list of slot *roles* to try against what is currently loaded. Defaults:

  | Virtual name | Default chain (loaded-slot preference) |
  |---|---|
  | `hal0/primary` | `[igpu-chat]` → configured primary `model.default` |
  | `hal0/npu` | `[npu/flm]` → `[utility]` → configured primary `model.default` |
  | `hal0/utility` | `[utility]` → `[npu/flm]` → configured primary `model.default` |

- **Protect the fast primary (design intent).** NPU-/utility-intended work must NOT commandeer or
  evict the iGPU primary. The chains above therefore route lighter/instruct work to a **utility**
  slot before ever landing on the primary, and the resolver **never triggers an eviction** of the
  primary to satisfy an `npu`/`utility` name. (Rationale: the operator's best/fastest model should
  not be bogged down — or unloaded — by grunt work a weaker/slower slot was handling.)
- **Operator-configurable.** When multiple slots are running, the operator picks per-name chains
  via slot/capability config (`capabilities.toml` / slot TOML role tags), overridable per deployment.
  An NPU-only deployment still works (the iGPU step is simply never matched); a primary-only
  deployment collapses every chain to the configured primary.
- **Ensure-load is opt-in, never on the primary.** If a chain's preferred role isn't loaded, the
  default is to fall through the chain (no load). An operator may opt a *non-primary* role into
  ensure-load (`SlotManager.load()` of the configured FLM/utility slot) — weighed against load
  latency + lemond's serialized-load / nuclear-evict behavior (`router.cpp:238-247,374-423`).
- Returns the resolved `model_name`; if the entire chain misses, falls back to the configured primary
  `model.default` (never hard-fails a turn).
- **MUST NOT add new polling.** PR #474 (`#475`, on `origin/main`) just fixed hal0-api storming
  lemond's control plane. The resolver **reuses hal0-api's already-cached lemond health/slot state**;
  if no suitable cache exists, it uses a short-TTL (≈2–5 s) memoized read. This constraint is a
  hard acceptance criterion, not a nice-to-have.

---

## 5. Thinking-suppression semantics

- **Mechanism:** inject **top-level** `enable_thinking: false` — matches lemond's
  `should_disable_thinking()` → `/no_think` injection (`server.cpp:58-114`).
- **Opt-out honored:** if the body already contains any of `enable_thinking`, `thinking`, or
  `chat_template_kwargs.enable_thinking`, do not override (caller opted in).
- **Local-only:** inject **only** when the route resolves to the lemond composite/fall-through path.
  Never inject on remote/cloud upstreams (meaningless or harmful). Living in hal0-api — which knows
  the route target — is precisely why this gate is possible (a Hermes-config `extra_body` could not
  distinguish target).

---

## 6. Hermes side (minimal, upgrade-safe)

- `src/hal0/agents/hermes_templates/config.yaml.j2`: set `model.default: hal0/primary`
  (live-follow); keep `provider: custom` + `base_url: http://127.0.0.1:8080/v1`.
- **No `extra_body` thinking config** — suppression is server-side.
- Change survives bootstrap re-render via the `overrides.yaml` deep-merge seam
  (`hermes_provision.py:818-833`); never hand-edit `config.yaml` (re-rendered on startup).
- No plugin, no venv patch, no `PROVIDER_REGISTRY` change.

---

## 7. Error handling / "ensure loaded"

- Keep the existing proactive `SlotManager.load()` (`v1.py:359`) for configured chat slots.
- Live-follow resolves to whatever's loaded; if **nothing** is loaded, fall back to the configured
  primary and let the existing ensure-load path warm it (covers lemond's "local model 404s if not
  loaded" + idle-unload, per memories `hal0_lemonade_v1_load_schema`, `hal0_lemonade_gotchas`).
- Any `/health` fetch failure → fall back to configured primary `model.default`. **Never hard-fail a
  turn on resolver error** (that was Bifrost's stated failure mode).

---

## 8. Testing & rollout

### 8.1 Tests

- **Unit:** port `resolve_test.go` cases (iGPU>FLM, FLM-only, empty→fallback, malformed health);
  **resolution-chain matrix** per virtual name — `hal0/primary` (iGPU; FLM not commandeered),
  `hal0/npu` (FLM → utility → primary; never evicts primary), `hal0/utility` (utility →
  FLM → primary), plus primary-only and NPU-only deployments collapsing the chains; thinking opt-out
  matrix (none set / `enable_thinking` set / `thinking` set / `chat_template_kwargs` set);
  virtual-name resolution + final fallback. New module gets real unit coverage.
- **Integration:** dispatcher/v1 tests with mocked lemond `/health` + `/v1/models`; γ-suite for the
  chat path (streaming passthrough + tool-calls untouched).
- **CT105 smoke:** curl `model: hal0/primary` → assert it hits the live slot and
  `enable_thinking:false` is on the wire (adapt `gateway/scripts/smoke.sh`).

### 8.2 Rollout (each step independently revertible)

1. Land hal0-api normalization (`resolve_model_name` + `apply_thinking_policy` + `LiveSlotResolver`).
   Additive; default
   behavior for existing model names is unchanged.
2. CT105 curl verification (virtual name routes to live slot; thinking suppressed; streaming + tools
   intact).
3. Flip Hermes `model.default` → `hal0/primary` via template + `overrides.yaml`; re-render
   (`hal0-agent@hermes` restart as `hal0` user — never root).
4. **Hermes OpenRouter→local cutover** — switch Hermes from the cloud model to the local
   `hal0/primary` path to test end-to-end. Operator-gated (Tier-2); real behavior change. Verify a
   full reason→tool→reason turn completes without `<think>` timeouts.
5. Retire Bifrost: stop/disable `hal0-bifrost.service` on CT105; close PR #469 (keep the branch as
   reference for the ported resolver).

---

## 9. Evidence index (file:line)

**Hermes provider machinery (venv `hermes_agent-0.15.2`):**
- `hermes_cli/auth.py:168-183` — `ProviderConfig` dataclass.
- `hermes_cli/auth.py:184-250` — `PROVIDER_REGISTRY` literal (built-ins).
- `hermes_cli/auth.py:460-491` — api-key-only auto-extend; skips `custom`/`openrouter`.
- `hermes_cli/runtime_provider.py:619-623` — `_custom_provider_request_overrides` (`extra_body`).
- `hermes_cli/model_normalize.py:326-466` — stateless per-provider name normalization (not live).

**hal0 Hermes integration (`src/hal0/agents/`):**
- `hermes_templates/config.yaml.j2:30-38` — `custom`-provider rationale; `Hal0Profile` removal.
- `hermes_provision.py:480` — legacy model-provider plugin removal.
- `hermes_provision.py:818-833` — `overrides.yaml` deep-merge seam.
- `hermes/plugins/memory_cognee/` — memory-only plugin framework (no model-provider support).

**hal0-api normalization today (`src/hal0/`):**
- `api/routes/v1.py:550-584` — chat handler; `233-281` alias rewrite; `359` ensure-load.
- `dispatcher/router.py:104,138` — lemond `:13305` composite target; `1114-1118` `_remap_model`.
- `api/routes/lemonade_proxy.py:138` — fall-through proxy target.
- grep clean: no `enable_thinking`/`chat_template_kwargs`/`no_think` anywhere in `src/hal0`.

**lemond contract (lemonade-sdk `1bce071`):**
- `server.cpp:336-368` — `/v0|/v1|/api/v0|/api/v1` route prefixes; chat at `/v1/chat/completions`.
- `server.cpp:58-114` — `should_disable_thinking()` reads top-level `enable_thinking`/`thinking`;
  `/no_think` injection; strips handled fields. `chat_template_kwargs` not read by lemond.
- `server.cpp:1378-1407` — `/health` `all_models_loaded[]` (model_name/type/device/recipe/backend).
- `server.cpp:3176-3268` — `/load`: only `model_name` required; 404 on unknown; no idle TTL.
- `router.py(cpp):238-247,374-423` — serialized loads; nuclear evict-all on non-file-not-found fail.
- `streaming_proxy.cpp:51-55` — injects `data: [DONE]` if backend omits it.

**Bifrost (ported, then retired) (`gateway/`):**
- `normalize/resolve.go` — live `/health` LLM-slot resolver + `isNPUorFLM` (port this).
- `normalize/normalize.go:35` — `chat_template_kwargs.enable_thinking=false` (do NOT port; use
  top-level instead).
- `README.md` — gateway rationale + held cutover (superseded by this design).

---

## 10. Out of scope

- Widening normalization to non-hal0-api agents (none exist today; revisit if an agent ever points
  straight at `lemond:13305`).
- Changing lemond itself, slot lifecycle FSM, or the registry.
- Memory-extraction model choice (instruct-only) — unaffected; separate path.
