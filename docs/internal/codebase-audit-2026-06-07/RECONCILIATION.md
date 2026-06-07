# Cross-Agent Reconciliations — 2026-06-07 audit

Resolutions to seam questions that no single specialist fully answered.
Each section is appended by the specialist who navigated the conflict to ground.

## Reconciliation 1: omni_router routing is an orthogonal axis, not a Dispatcher tier — keep it; only `resolve_slot` (Dispatcher Tier 4) is a demote candidate

**Verdict:** The "three routing implementations" framing conflates two
different routing *axes*. `Dispatcher` and `SlotManager.route_for_request`
do not compete; they answer different questions and both are live. The only
genuine consolidation candidate is `proxy.resolve_slot`, which is a leaf used
exclusively as the Dispatcher's Tier-4 legacy fallback. A1's "demote
`route_for_request`, delete `resolve_slot`" should be split: **do NOT demote
`route_for_request`** (it is the omni-router's load-bearing primitive on a
different axis); `resolve_slot` remains a defensible delete-candidate but only
as the Dispatcher's own legacy tier, independent of omni.

### The two axes

1. **Model-id → upstream URL (the Dispatcher's 4-tier ladder).**
   `Dispatcher.dispatch` (`src/hal0/dispatcher/router.py:398`) maps an
   OpenAI `model` id to a concrete upstream HTTP target via:
   registry (`router.py:436`) → passthrough on warm `/v1/models`
   (`router.py:505`) → cold-cache prefetch (`router.py:528`) → legacy
   `resolve_slot` (`router.py:549`). Output is an `UpstreamCall` with a
   `target_url`. This is the *transport* router.

2. **slot_type(+labels) → slot NAME (`route_for_request`).**
   `SlotManager.route_for_request` (`src/hal0/slots/manager.py:1064`) takes a
   *capability type* (`llm | embedding | image | tts | transcription |
   reranking`) plus `required_labels` and returns a slot *name* by
   type-match+default (`manager.py:1102`) → label-overlay fall-through
   (`manager.py:1113`). It never produces a URL and never consults the
   registry/upstream cache. This is the *capability/multimodal selection*
   router.

These cannot be folded into one another: the Dispatcher is keyed on a
client-supplied model string; `route_for_request` is keyed on an internal
capability type the client never names.

### `route_for_request` is live and load-bearing for omni

- It is the resolution primitive of every omni tool dispatch:
  `_route_or_error` calls it at `src/hal0/omni_router/dispatch.py:127`,
  feeding all eight tool handlers (`dispatch.py:199, 217, 234, 250, 272,
  299, 313` and the `HANDLERS` table at `dispatch.py:387`).
- It is also the per-request tool-eligibility gate:
  `active_tools_for` calls it at `src/hal0/omni_router/filter.py:121`
  to decide which tools the chat LLM is even shown.
- The `SlotManagerLike` Protocol (`filter.py:31`) declares
  `route_for_request` (`filter.py:41`) as part of the narrow surface omni
  depends on — it is an intentional, tested seam (graph: `route_for_request`
  node at `filter.py:41`, community 60, reached from `active_tools_for`).
- `OmniRouter` is constructed and attached at
  `src/hal0/api/__init__.py:1100` (`app.state.omni_router`), with
  `slot_manager=slot_manager` injected — i.e. omni's routing primitive is
  the real `SlotManager.route_for_request`, not a stub, in production.

Demoting/removing `route_for_request` would break omni's tool selection and
dispatch outright. It is NOT redundant with the Dispatcher.

### Omni does NOT route through the Dispatcher (so they are not a single ladder)

The omni tool handlers POST directly to Lemonade's loopback gateway, not back
through `Dispatcher.dispatch`:

- `DispatchContext.lemonade_base_url` (`dispatch.py:91`) is set from
  `LEMONADE_BASE_URL` (default `http://127.0.0.1:13305`) at
  `api/__init__.py:1099`.
- Every handler calls `_post_json(ctx, "/v1/...", body)`
  (`dispatch.py:155`), which hits `{lemonade_base_url}{path}` directly
  (`dispatch.py:162`). There is no `dispatcher.dispatch` call anywhere in
  `omni_router/`.
- The one exception is `route_to_chat`, which delegates back via an injected
  `chat_completion` callback (`dispatch.py:326, 365`) — that callback is the
  chat loop, which *does* go through normal chat dispatch.

So omni is a **client-side tool-calling loop layered above** the chat route,
selecting *which capability slot* to invoke and calling Lemonade directly,
while the Dispatcher handles the *base chat completion* transport.

### How they compose at the chat route (the actual control flow)

In `v1.py` `chat_completions`:

1. Body is normalized; if `body["omni"] is True`, `_maybe_run_omni_loop`
   runs the omni tool-calling loop (`src/hal0/api/routes/v1.py:722-725`).
   Inside that loop, `active_tools_for` (→ `route_for_request`) gates tools
   and `dispatch_tool` (→ `route_for_request`) executes them against Lemonade.
2. If omni is absent/declined (`looped is None`) or `omni` is not set, the
   request falls through to `_dispatch_and_forward` → `dispatcher.dispatch`
   (`v1.py:735`, and the base dispatch at `v1.py:463`/`v1.py:1044`).

They are sequential layers on one request, not three competing resolvers.

### Concrete recommendation

- **Keep `SlotManager.route_for_request`** as the single capability-selection
  authority. It is correctly owned by `SlotManager` (the slot-config source of
  truth) and consumed via the `SlotManagerLike` Protocol — that is the right
  shape. No demote.
- **Do not fold `omni_router` under the Dispatcher.** They are orthogonal
  (capability-selection vs. transport). A merge would force the Dispatcher to
  grow a second, label-driven resolution mode it currently has no need for.
- **`resolve_slot` (`src/hal0/dispatcher/proxy.py:58`) is the only true
  consolidation target**, and only as the Dispatcher's Tier-4 legacy
  fallback. It is reached at exactly one production site
  (`router.py:549`), is documented as "Kept until v0.2"
  (`router.py:20-22`), and duplicates path/name heuristics the registry +
  passthrough tiers now cover. Removing it is a **single-owner change inside
  the Dispatcher** (delete Tier 4, raise `NoRouteFound` when tiers 1–3 miss),
  with no effect on omni. Confirm no remaining model bindings rely on the
  legacy path before deleting (it is the only tier that resolves without a
  registry entry or a warm `/v1/models` advertisement).

**Bottom line for the synthesis lead:** the consolidation is NOT a multi-owner
refactor. It is two independent, smaller moves — (a) leave the omni axis alone,
(b) optionally retire `resolve_slot` as a Dispatcher-internal Tier-4 cleanup.
`route_for_request` stays.

### Cross-cutting seams touched

- **A1 (routing/dispatcher):** their "demote route_for_request" item is
  withdrawn; their "delete resolve_slot" item stands but is re-scoped to a
  Dispatcher-internal Tier-4 removal.
- **A2 (slots):** `route_for_request` ownership stays on `SlotManager`
  (`manager.py:1064`); no migration needed.
- **B1 (api/omni):** the omni loop's dependency on `route_for_request` via the
  `SlotManagerLike` Protocol (`filter.py:31`, wired at `api/__init__.py:1100`)
  is confirmed intentional and must be preserved.

## Reconciliation 3: Non-lemonade providers are intentionally-retired (one live exception); systemd template renderer is orphaned

**Ruling (both questions settled):**
1. The four non-lemonade provider classes are **not part of the hot path and not
   self-managed spawners** — SlotManager dispatches 100% through
   `lemonade_provider()`. They are *intentionally retained* singletons pending a
   follow-up retirement PR (PR-10), with exactly **one genuinely-live method**:
   `ComfyUIProvider.infer()`. `SELF_MANAGED_PROVIDERS` is a **validation label
   set, not a spawn path**.
2. The `hal0-slot@.service` unit_template renderer (`render_override` /
   `render_unit`) and the `install_template_unit` installer helper are
   **orphaned**: the base template was removed in PR-9, the template file does
   not exist, and nothing in the live tree invokes the renderer or the helper.

### Q1 — SlotManager never touches `get_provider`; dispatch is lemond-only
- `_spawn_locked` → `lemonade_provider().load(cfg, model_info)`
  (`src/hal0/slots/manager.py:1239,1250`). Unload (`:1284,1295`) and status
  (`:580,582`, `:2035,2037`, `:2077,2079`) likewise go only through
  `lemonade_provider()`. There is **no `get_provider`, `render_override`, or
  subprocess spawn anywhere in `manager.py`** (the docstring at `manager.py:16`
  and `:1223-1227` states this explicitly: "No subprocess spawning… the lemond
  daemon owns process lifecycle and `/v1/load` is the single control-plane
  entry point").

### Q1 — `SELF_MANAGED_PROVIDERS` is a guard, not a spawn table
- `SELF_MANAGED_PROVIDERS = {"kokoro","moonshine","vibevoice"}`
  (`src/hal0/slots/state.py:124`). Its **sole runtime consumer** is
  `provider_requires_model()` (`state.py:127-129`), called once in
  `manager.py:348` to **suppress a spurious modelless-`READY` transition**
  (forces `→ IDLE` instead, `manager.py:359`). It spawns nothing; it encodes the
  contract "these providers serve a baked-in model so an empty `model_id` is
  legal." Proof it is a label set and not a dispatch table: `vibevoice` is in the
  frozenset but is **not even registered** in `_PROVIDERS`
  (`src/hal0/providers/__init__.py:45-52`).

### Q1 — Per-class liveness (split ruling; do NOT lump FLM in)
- **`ComfyUIProvider.infer()` — LIVE.** Driven directly by the
  `/v1/images/generations` HTTP route at `src/hal0/api/routes/v1.py:1026,1076,1082`
  against an already-running ComfyUI port (the provider is an HTTP translator
  here, not a spawner). Reachable in production via the seed slot
  `installer/etc-hal0/slots/img.toml:18` (`provider = "comfyui"`). Its
  spawn/lifecycle methods (`build_env`/`start_cmd`/`container_spec`,
  `src/hal0/providers/comfyui.py:108,129,172`) are **dead** — nothing spawns it.
- **`LlamaServerProvider` / `MoonshineProvider` / `KokoroProvider` — fully dead
  at runtime.** Only references are (a) the `_PROVIDERS` registry +
  `__all__` (`providers/__init__.py:25,47,49,50,82,87,88`), (b) `voice/`
  re-export shims with **no live importer** (`voice/__init__.py:20-25`,
  `voice/kokoro.py:18`, `voice/moonshine.py:18`), and (c) the test below.
- **`FLMProvider` — NOT dead (out of scope of this question for a reason).**
  `flm_served_models()` / `_probe_flm_catalog` are consumed by
  `api/routes/hardware.py` and `registry/pull.py` (per the `__init__` docstring,
  `providers/__init__.py:41-43`). Excluded from the dead pile.

### Q1 — Doc-vs-code discrepancy to surface in the ARCHITECTURE rewrite
- The retention rationale in `providers/__init__.py:36-44` justifies keeping
  Kokoro/Moonshine because "`voice/__init__.py` re-exports [them] for the voice
  surface." That justification points at a **dead chain** — `voice/` has no live
  consumer. The honest statement is: Kokoro/Moonshine survive only because a
  follow-up retirement PR (PR-10) and a test pin them, **not** because any voice
  surface consumes them today.

### Q2 — The systemd template renderer + installer helper are orphaned
- **Template file gone:** `packaging/systemd/` contains only
  `hal0-openwebui.service` (no `hal0-slot@.service`). `installer/install.sh:651-655`
  is a comment confirming removal in PR-9.
- **Renderer orphaned:** `render_override`/`render_unit`
  (`src/hal0/slots/unit_template.py:74,353`) — the only live in-tree call site is
  the now-unused `get_provider(...)` at `unit_template.py:102,122`; SlotManager
  does not invoke either. `render_unit` (`unit_template.py:353`) is a compat shim
  appearing only in its own `__all__` (`:372`).
- **Installer helper orphaned:** `install_template_unit`
  (`src/hal0/installer/template_unit.py:41`) is exported (`installer/__init__.py:19,25`)
  but **never called by `install.sh`** (which has no template-install step). The
  dev-mode warning at `install.sh:1880-1888` referencing
  `systemctl link …/hal0-slot@.service` is **stale text** — that path no longer
  exists. `uninstall.sh:207` still tries to `rm` the (never-installed) unit.

### Removal cost (gates the dead-code PR — removal is NOT free)
1. **`tests/providers/test_lemonade.py:644` `test_legacy_providers_still_registered`**
   explicitly asserts `llama-server/flm/moonshine/kokoro/comfyui` stay in
   `_PROVIDERS` ("Anti-scope: PR-8 must NOT remove… PR-10 owns their
   retirement"). Any removal must delete/rewrite this test.
2. **Schema enum coupling + a latent bug:**
   `_VALID_PROVIDERS = {"lemonade","llama-server","flm","moonshine","kokoro"}`
   (`src/hal0/config/schema.py:89`, enforced at `:423-428`). Note it **omits
   `comfyui`**, yet the seed slot `installer/etc-hal0/slots/img.toml:18` sets
   `provider = "comfyui"` — a pre-existing inconsistency (the live ComfyUI path
   bypasses this validator) the rewrite/removal must reconcile.
3. `_VALID_BACKENDS` (`schema.py:45`) also carries `moonshine`/`kokoro` as
   hardware-intent values — separate from the provider enum, touch with care.

### Cross-cutting seams
- **A1/A3 (providers + slots):** confirms the providers layer's only live
  runtime surfaces are `LemonadeProvider` (all lifecycle) + `ComfyUIProvider.infer`
  (image HTTP route) + `FLMProvider` catalog probes. The rest is retained-dead.
- **B1/B2 (api + installer):** the image route (`v1.py:1076`) and the `img.toml`
  seed slot are the only things keeping ComfyUI alive; the installer no longer
  installs/uses the slot template unit, so the `unit_template`/`template_unit`
  modules are removable alongside the provider retirement.
- **Doc-reconciliation (DOC-RECONCILIATION.md / architecture-seams.md):** the
  ARCHITECTURE.md providers section should describe lemond-only dispatch + the
  single ComfyUI HTTP exception, and must NOT cite the stale `voice/` rationale.

## Reconciliation 2: "Three-path auth surface" is a misread — one real gate, one identity-stasher, one phantom; thinmint.dev is hygiene not a vuln

**Verdict:** The premise is wrong. There is not a three-gate auth surface to
consolidate. Of the three cited paths, exactly **one** enforces auth, **one**
only stamps identity (and never rejects), and **one does not exist as code at
all**. The intended post-ADR-0012 model is sound and should be **documented**,
not refactored. The `thinmint.dev` default origin is a demo-default hygiene
cleanup, **not a security blocker** — it must not gate the doc rewrite.

### The honest accounting of the "three paths"

**Path 1 — `api/agents/_auth.py`: the ONE real gate (browser-facing).**
Origin allowlist + HMAC session cookie, wired live into the chat-proxy WS
routes: `chat_proxy.py:412` and `:434` call `check_ws_origin_and_cookie`
before any frame; `:519` mints the cookie via `set_session_cookie`; `:530`,
`:542`, `:560` gate the session-REST shim via `require_browser_auth`. The gate
requires Origin allowlisted **AND** a valid HMAC cookie
(`_auth.py:220-231`); the cookie is `HttpOnly` + `SameSite=Lax`
(`_auth.py:202`). This is genuine enforcement and the only one on hal0-api.

**Path 2 — `api/mcp_mount.py` bearer: NOT a gate, an identity-stasher.**
`bearer_resolver()` returns `(bearer, bearer or "anonymous")`
(`mcp_mount.py:146`) — a missing bearer falls back to `"anonymous"` and the
call proceeds. It exists only to stamp `client_id` for the Cognee
`private:<client_id>` namespace. ADR-0012 lines 92-99 explicitly bless this
("kept as a thin identity-stashing middleware — no longer enforces a bearer").
There is no rejection path here.

**Path 3 — `api/deps.py` token/writer: DOES NOT EXIST.**
The full file (`deps.py:1-74`) is DI getters (`get_slot_manager`,
`get_registry`, …) — there is no `require_token`/`require_writer`/`require_admin`
`def`, no `Depends(require_*)`, and **no `dependencies=` array on any
`include_router` call** in `api/__init__.py` (grep for `dependencies=` →
zero matches). ADR-0012 §"What was deleted" removed
`src/hal0/api/middleware/auth.py` (508 lines, the `require_token`/`require_writer`
home) and `src/hal0/api/routes/auth.py` entirely. Confirmed:
`src/hal0/api/middleware/` no longer contains `auth.py`.

**Where the phantom came from (code-hygiene finding):** stale comments
referencing the deleted `require_token`/`require_writer`/`HAL0_AUTH_ENABLED`
machinery survive across `api/__init__.py:1217,1221,1251,1332`, `routes/v1.py:42`,
`routes/approvals.py:26,54`, `routes/slots.py:39`, `routes/installer.py:47`,
`routes/lemonade_proxy.py:28`, `routes/lemonade_admin.py:22,59`,
`routes/providers.py:272`. These comments describe a live token gate that no
longer exists and actively mislead a reader into inferring "three auth paths."
**Recommend deleting/correcting these comments** (touches A2/A3 code-hygiene).

### Q1 — Is the surface the intended post-ADR-0012 model? YES — document it.

The genuine surfaces are: **one session gate** (`_auth`) + **two parallel
transport allowlists** that protect different transports:
- `HAL0_ALLOWED_ORIGINS` → browser-WS origins (`_auth.py:132`, default
  `DEFAULT_ALLOWED_ORIGINS` at `_auth.py:60`).
- `HAL0_MCP_ALLOWED_HOSTS` / `HAL0_MCP_ALLOWED_ORIGINS` → MCP streamable-http
  DNS-rebinding allowlist (`mcp_mount.py:86,93`).

These are intentionally separate (different transports) and consistently
designed — `mcp_mount.py:70` explicitly says it is "mirroring the
`HAL0_ALLOWED_ORIGINS` knob." There is **no redundant gate to fold into one**;
the only "third gate" was never a gate. Resolution: **document this model in
auth.mdx; do not refactor.**

### Q2 — Is `thinmint.dev` in DEFAULT_ALLOWED_ORIGINS a bug or a demo default?

**Demo default → move/drop it. NOT a security blocker; does not gate the doc fix.**

Discriminator: the WS gate requires Origin allowlisted **AND** a valid HMAC
cookie, and that cookie is `HttpOnly` + `SameSite=Lax` (`_auth.py:202,26`,
`check_ws_origin_and_cookie` at `_auth.py:220`). A cross-site page cannot carry
the cookie regardless of an Origin match, and Origin cannot be spoofed
cross-site. So an extra default origin an operator does not use is **inert** —
it is not an exploitable hole. Shipping the maintainer's homelab vhost
(`https://hal0.thinmint.dev`, `_auth.py:61`) in an OSS default is clutter, not
a vuln.

Recommendation (hygiene, one line, **non-blocking**): drop
`https://hal0.thinmint.dev` from `DEFAULT_ALLOWED_ORIGINS`, keeping the generic
`http://hal0.local` + localhost/Vite entries; the `HAL0_ALLOWED_ORIGINS`
override already lets any operator add their own vhost. Explicitly **not** a
security gate on the doc rewrite — the two can land independently.

### The doc fix is bigger than the question framed it (B2/B3)

`docs/operate/auth.mdx` does not merely "lack the three paths" — it documents a
**fully-deleted surface**. The entire page describes the pre-ADR-0012 ADR-0001
model: bundled Caddy `--auth=basic` (`auth.mdx:40,58-84`), `HAL0_AUTH_ENABLED`
(`auth.mdx:74,88,348`), bearer tokens + `/api/auth/tokens` + `tokens.toml` +
token scopes (`auth.mdx:113-126,237-293`), and the public-route allowlist
(`auth.mdx:296-312`). **Every one of those was removed by ADR-0012.** The
rewrite must:
1. Replace the bundled-Caddy/bearer/token model with ADR-0012's
   "reverse-proxy-at-the-edge" guidance (Traefik / nginx / Cloudflare Tunnel —
   the ADR §Decision already names these three patterns).
2. Document the ONE real in-app gate: chat-proxy WS Origin + HMAC session
   cookie (`_auth.py`), incl. the `HAL0_ALLOWED_ORIGINS` knob.
3. Document the MCP transport-security knobs (`HAL0_MCP_ALLOWED_HOSTS` /
   `HAL0_MCP_ALLOWED_ORIGINS`, `mcp_mount.py:86,93`).

**Two additional drift cites** (doc/code drift in both directions, worth fixing
alongside the rewrite):
- `src/hal0/memory/cognee_wrapper.py:217` references
  `hal0.api.middleware.auth.AuthIdentity` — a module ADR-0012 §"What was
  deleted" (line 66) removed. Stale code comment.
- ADR-0012 line 93 itself is now stale vs code: it says `MCPAuthMiddleware`
  "is kept (in `src/hal0/api/mcp_mount.py`)," but no such class exists in that
  file (confirmed — `grep "class MCP.*Middleware"` → zero). It became the
  `bearer_resolver`/`client_id_resolver`/`private_resolver` callbacks
  (`mcp_mount.py:134-165`). The ADR's own deferred-rename note never landed and
  should be corrected.

### Bottom line for the synthesis lead

- **No security blocker.** The only enforcing gate (`_auth.py`) is correctly
  built (origin + HMAC + HttpOnly/SameSite). `thinmint.dev` is inert clutter.
- **No auth consolidation work.** Two of the "three paths" are not gates; the
  third does not exist. The model is intended and coherent.
- **The real action is documentation + comment hygiene**: rewrite auth.mdx to
  the ADR-0012 model, delete the phantom `require_token` comments, fix the two
  stale `AuthIdentity` / `MCPAuthMiddleware` references. The `thinmint.dev`
  drop rides along as optional one-line hygiene — it need not block the doc PR.

### Cross-cutting seams
- **A2/A3 (API factory + routes):** stale `require_token`/`require_writer`
  comments across nine route modules + `__init__.py` — delete in a hygiene pass.
- **B2/B3 (docs):** auth.mdx full rewrite to the reverse-proxy-at-edge model;
  ADR-0012 line 93 self-correction.
- **Memory subsystem:** `cognee_wrapper.py:217` stale `AuthIdentity` ref.
