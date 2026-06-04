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

**Streaming timing (Hermes review #6).** Body injection completes **before** httpx opens the
streaming connection to lemond (`_forward_streaming` sends the already-mutated `call.body`), so SSE
frames are never delayed or buffered by normalization — the transform is on the request, not the
response stream.

### 4.2 `LiveSlotResolver`

Ports `gateway/normalize/resolve.go` semantics to Python and generalizes the single iGPU-first rule
into a **configurable resolution chain** keyed on **slot role + device**, both read from hal0 slot
config (authoritative) rather than guessed from the loaded model's name.

**How a chain step resolves a role to a live model:**

1. **Role binding (authoritative).** Each hal0 llm slot declares a `role` (new `SlotConfig` field —
   §4.4); if unset it defaults to today's **name convention** (`primary`/`utility`/`agent-hermes`).
   The resolver maps a chain step (`primary` / `utility` / `npu`) → the matching slot(s) → their
   configured `model.default`.
2. **Hardware axis is free from `device`.** iGPU vs NPU is `slot.device` (`gpu-vulkan`/`gpu-rocm` vs
   `npu`) — no name-guessing needed for the hardware split. The ported `isNPUorFLM` name-suffix
   heuristic is kept only as a *fallback* classifier for a loaded model that isn't in hal0 slot
   config.
3. **Liveness check.** Read lemond `/api/v1/health` → `all_models_loaded[]` (`type=="llm"`); a chain
   step matches if its bound slot's `model.default` is in the loaded set.

**Default chains (ordered; first loaded match wins):**

  | Virtual name | Default chain (role → role → …) |
  |---|---|
  | `hal0/primary` | `[primary]` → configured primary `model.default` |
  | `hal0/npu` | `[npu]` → `[utility]` → `[primary]` |
  | `hal0/utility` | `[utility]` → `[npu]` → `[primary]` |

- **Protect the fast primary (design intent).** NPU-/utility-intended work must NOT commandeer or
  evict the iGPU primary. The chains route lighter/instruct work to a **utility** (or npu) slot
  before ever landing on the primary, and the resolver **never triggers an eviction** of the primary
  to satisfy an `npu`/`utility` name. (Rationale: the operator's best/fastest model should not be
  bogged down — or unloaded — by grunt work a weaker/slower slot was handling.)
- **Operator-configurable.** Per-name chains are overridable in config; an operator running many
  slots tags each slot's `role` and (optionally) reorders a chain. An NPU-only deployment still works
  (the `primary`/`utility` steps simply never match a loaded slot and fall through); a primary-only
  deployment collapses every chain to the configured primary.
- **Ensure-load is opt-in, never on the primary.** If a chain's preferred role isn't loaded, the
  default is to fall through the chain (no load). An operator may opt a *non-primary* role into
  ensure-load (`SlotManager.load()` of the configured npu/utility slot) — weighed against load
  latency + lemond's serialized-load / nuclear-evict behavior (`router.cpp:238-247,374-423`).
- **Returns `(model_name, context_length)`.** The resolver returns the resolved physical model id
  **and** the bound slot's `context_size` (slot TOML `[model] context_size`), so the `/v1/models`
  advertisement (§4.3) and Hermes' context-length lookup get the right window for a virtual name
  (Hermes review #2). If the entire chain misses, falls back to the configured primary
  `model.default` + its context (never hard-fails resolution — see §7 for the load-failure terminal
  case).
- **MUST NOT add new polling.** PR #474 (`#475`, on `origin/main`) just fixed hal0-api storming
  lemond's control plane. The resolver **reuses hal0-api's already-cached lemond health/slot state**;
  if no suitable cache exists, it uses a short-TTL (≈2–5 s) memoized read. This constraint is a
  hard acceptance criterion, not a nice-to-have.

### 4.3 `/v1/models` virtual-name advertisement

Hermes' `custom` provider calls `GET /v1/models` on startup to populate its `/model` picker and
context-length lookup (Hermes review #1, #2). hal0-api's **public** `/v1/models`
(`api/routes/v1.py` `public_router`) must therefore advertise the virtual names alongside physical
model rows:

```json
{
  "id": "hal0/primary",
  "object": "model",
  "context_length": 65536,
  "_hal0": { "virtual": true, "kind": "live-resolve", "resolves_to": "qwen3.6-35b-…", "device": "gpu-vulkan" }
}
```

- One row per **enabled** virtual name. **`context_length`** carries the bound slot's `context_size`
  (verified field choice — Hermes scans `_CONTEXT_LENGTH_KEYS` and `context_length` is *first* in
  precedence: `agent/model_metadata.py:291-304`). `_hal0.resolves_to` / `_hal0.device` annotate what
  is live now (the resolver already computes these — §4.2). The `_hal0` block is additive; Hermes
  parses `/v1/models` leniently (no schema validation — `model_metadata.py:739-755`) so extra fields
  are safe.
- **Advertising `context_length` is mandatory, not cosmetic.** If a virtual row omits every
  context key, Hermes falls back to `DEFAULT_FALLBACK_CONTEXT = 256_000` (`model_metadata.py:128`),
  which would over-run a smaller local window and silently corrupt context handling.
- This `/v1/models` row is the **single source** of context for virtual names (see §6 — we
  deliberately do *not* render a `custom_providers` entry, which would out-prioritize it). Hermes
  caches the probe ~300 s, so a context change after a slot swap propagates within that window —
  acceptable.
- Physical model rows are unchanged. Virtual rows are appended. The picker enumerates virtual names
  from `data[].id` with no extra config (`discover_models` defaults `True`; the `provider: custom`
  path probes regardless — `hermes_cli/model_switch.py:1540`, `models.py:2000/3108/3186`).

### 4.4 `SlotConfig.role` (schema addition)

Add one optional field to `SlotConfig` (`src/hal0/slots/…schema`): `role: str | None`. Semantics:

- For `type=="llm"` slots, `role` names the slot's purpose for chain binding (`primary` / `utility` /
  `npu` / free string). **Default when unset:** derive from the slot **name** (so existing
  installs — `primary`, `utility`, `agent-hermes` — keep working with zero migration).
- Authoritative over the name: a slot named `coder-mini` with `role = "utility"` binds to the
  `utility` chain step; renaming the slot doesn't break the chain.
- `device` (existing) remains the authoritative hardware axis; `role` only disambiguates **same-device
  llm slots** (the one real ambiguity — heavy primary vs light utility on the iGPU).
- Validation: non-llm slot types ignore `role`. No reserved-name collision (the field is independent
  of `name`).
- *Future (out of scope, §10):* surface role binding through the capability-selection system
  (`capabilities.toml`) for a picker UI; the `role` field is the data model that path would reuse.

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
- **Idempotent (Hermes review #5):** re-applying `apply_thinking_policy` to an already-injected body
  is a no-op (the opt-out check sees the field it set). Safe even though the two call sites (§4.1b)
  are mutually exclusive in practice.
- **`no_think` passthrough:** if the caller already suppressed thinking at the prompt level (a
  `no_think` marker), pass it through untouched — never strip it, never double-inject.

---

## 6. Hermes side (minimal, upgrade-safe)

Still `provider: custom` + `base_url: http://127.0.0.1:8080/v1` — no plugin, no venv patch, no
`PROVIDER_REGISTRY` change, no `extra_body` thinking config (suppression is server-side). The
template changes are about making the virtual names **discoverable + correctly sized** in Hermes.

- **Render virtual names directly, not via override (Hermes review #8).** Add a provisioner-set
  bootstrap variable `live_resolve_enabled: bool` (true when this feature is active) and render the
  default explicitly, so the config is self-documenting rather than template-writes-X-then-override-
  patches-Y:
  ```jinja2
  {%- if live_resolve_enabled %}
    default: "hal0/primary"
  {%- elif primary %}
    default: {{ primary.model_id | tojson }}
  {%- endif %}
  ```
- **Picker discovery needs nothing beyond `/v1/models` (revised after source check — Hermes review
  #9 superseded).** Hermes' `/model` picker for `provider: custom` is built **solely** from the
  server's `/v1/models` `data[].id` (`hermes_cli/models.py:3108-3186`), not from `model_aliases`. So
  advertising the virtual name in `/v1/models` (§4.3) is **sufficient** for discovery — *no
  `model_aliases` entry is required*. `model_aliases` only provides optional request-time
  **shorthand** (e.g. `hermes` → `hal0/primary`, `model_switch.py:179-243`); add a shorthand entry
  *only if desired*, not for discovery. (This drops the original #9 requirement.)
- **Context length: `/v1/models` only — do NOT render `custom_providers` (revised, Hermes review #2).**
  Source check: Hermes resolves context in precedence order where `custom_providers[].models.<id>.
  context_length` **out-prioritizes** the live `/v1/models` probe (`config.py:3479-3541`,
  `model_metadata.py:1484+`). A `custom_providers` virtual entry would therefore **lock a stale
  value** and defeat live-follow. So the **single source** is the `context_length` field on the
  `/v1/models` virtual row (§4.3); the template renders **no** `custom_providers` entry for virtual
  names.
- All template changes survive bootstrap re-render natively (rendered from `live_resolve_enabled` +
  slot config); `overrides.yaml` remains the escape hatch, not the primary mechanism. Never hand-edit
  `config.yaml` (re-rendered on startup); restart via `hal0-agent@hermes` as the `hal0` user.

---

## 7. Error handling / "ensure loaded"

- Keep the existing proactive `SlotManager.load()` (`v1.py:359`) for configured chat slots.
- Live-follow resolves to whatever's loaded; if **nothing** is loaded, fall back to the configured
  primary and let the existing ensure-load path warm it (covers lemond's "local model 404s if not
  loaded" + idle-unload, per memories `hal0_lemonade_v1_load_schema`, `hal0_lemonade_gotchas`).
- Any `/health` fetch failure → fall back to configured primary `model.default`. **Resolution never
  hard-fails** (that was Bifrost's stated failure mode).
- **Terminal case — define it explicitly (Hermes review #11).** "Never hard-fail a turn" means
  *resolution* always yields a target; it does not mean inference can't fail. If the chain fully
  misses **and** the configured primary is not loaded **and** an ensure-load of the primary fails,
  hal0-api returns **HTTP 503 + `Retry-After`** — reusing the existing `SlotLoading` semantics
  (`router.py` `_check_slot_ready_for_dispatch`), not a hang and not a confusing 404/502. A 503 is
  the honest "model warming / unavailable, retry" signal; Hermes' `request_timeout_seconds: 300`
  tolerates the warm-up.

**Cold-start latency (Hermes review #7).** On Hermes' first request after idle-unload, the existing
`SlotManager.load()` is synchronous in the handler — a 30–90 s pause with no UI signal. v1 keeps this
(within Hermes' 300 s timeout) but **must emit a `gateway.log` line when ensure-load is triggered** so
the operator can see what's happening. Surfacing warm progress to the user (SSE preamble) is a future
enhancement (§10).

---

## 8. Testing & rollout

### 8.1 Tests

- **Unit — resolver:** port `resolve_test.go` cases (iGPU>FLM, FLM-only, empty→fallback, malformed
  health); **resolution-chain matrix** per virtual name — `hal0/primary` (iGPU; FLM not
  commandeered), `hal0/npu` (npu → utility → primary; never evicts primary), `hal0/utility` (utility
  → npu → primary); primary-only + NPU-only deployments collapsing the chains; **role binding** —
  `role`-tag wins over name; name-convention default when `role` unset; a renamed slot (`coder-mini`
  + `role=utility`) still binds; **chain exhaustion** — all roles miss → falls back to
  `model.default`; no models loaded at all → ensure-load triggers for primary; **primary load-fails →
  503 + Retry-After** (§7 terminal case). Returns `(model_name, context_length)`.
- **Unit — thinking:** opt-out matrix (none set / `enable_thinking` set / `thinking` set /
  `chat_template_kwargs.enable_thinking` set); **`no_think` passthrough** (caller already suppressed
  at prompt level — not stripped, not double-injected); **idempotency** (second apply is a no-op).
- **Integration:** dispatcher/v1 tests with mocked lemond `/health` + `/v1/models`; **`/v1/models`
  advertisement** — virtual rows present with correct `context_length` + `_hal0` annotation, physical
  rows unchanged, and **no `custom_providers` virtual entry** rendered in the Hermes config; γ-suite
  for the chat path (streaming passthrough + tool-calls untouched; body mutated before stream opens).
- **CT105 smoke:** curl `GET /v1/models` → assert `hal0/primary` present with `context_length`; curl
  `model: hal0/primary` → assert it hits the live slot and `enable_thinking:false` is on the wire
  (adapt `gateway/scripts/smoke.sh`).

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

**Hermes `/v1/models` + config consumption contract (venv `hermes_agent-0.15.2`, dual-verified):**
- `agent/model_metadata.py:291-304` — `_CONTEXT_LENGTH_KEYS` (13 keys; `context_length` first).
- `agent/model_metadata.py:128` — `DEFAULT_FALLBACK_CONTEXT = 256_000` (hazard if context unadvertised).
- `agent/model_metadata.py:739-755` — lenient `/v1/models` parse (no schema validation; `_hal0` safe).
- `agent/model_metadata.py:1484+` + `hermes_cli/config.py:3479-3541` — context precedence:
  `custom_providers` config > live `/v1/models` probe (≈300 s cache). → use `/v1/models` only.
- `hermes_cli/models.py:2000,3108-3186` — `provider: custom` picker = `/v1/models` `data[].id` only.
- `hermes_cli/model_switch.py:1540` — `discover_models` defaults `True`; `:179-243` `model_aliases`
  = request-time shorthand, not picker source.
- `tools/delegate_tool.py:2345` — `delegation.model` resolved once at spawn.
- `hermes_cli/auth.py:6353-6448` — `/model` picker renders id-only (no per-row label).
- grep (hermes_cli/agent/tools/gateway): **zero** `enable_thinking`/`chat_template_kwargs`/`no_think`
  set-sites → hal0-api injection is unopposed.

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

## 10. Out of scope / future

- Widening normalization to non-hal0-api agents (none exist today; revisit if an agent ever points
  straight at `lemond:13305`).
- Changing lemond itself, slot lifecycle FSM, or the registry.
- Memory-extraction model choice (instruct-only) — unaffected; separate path.
- **Picker live-annotation (Hermes review #3).** The `_hal0.resolves_to` / `_hal0.device` fields are
  *emitted now* (§4.3) but Hermes' `/model` picker renders **id-only** today — no per-row
  label/description (`hermes_cli/auth.py:6353-6448`; a `/v1/models` `name` field *is* read at
  `model_metadata.py:742` but the picker doesn't render it). So `hal0/primary → [now: <model>] (iGPU)`
  needs a downstream Hermes (or hal0 admin-skill) change. The annotation format is reserved here so
  the data is ready when that lands.
- **Cold-start warm progress (Hermes review #7).** Surfacing slot-warming as an SSE preamble/spinner
  during the synchronous first-token load. v1 only logs it (§7).
- **Subagent model stability (Hermes review #12).** Hermes resolves `delegation.model` **once at
  `delegate_task` spawn**, not per-turn (verified: `tools/delegate_tool.py:2345`). With a live-resolve
  virtual name, a subagent therefore keeps *sending* `hal0/primary` for its whole run, and hal0-api
  maps it to whatever slot is loaded **now** each turn — so a mid-run slot swap changes the subagent's
  effective model. That's intended: a subagent "sees whatever is loaded, same as any caller." Pinning
  a subagent to a fixed physical model for its lifetime would be a Hermes-side feature, not hal0-api.
- **Capability-UI role binding.** Promote the `SlotConfig.role` field (§4.4) into the
  `capabilities.toml` selection system so operators pick chat/utility/npu roles through the same UX as
  embed/voice/img. The `role` data model is the seam; the orchestrator/UI work is deferred.
