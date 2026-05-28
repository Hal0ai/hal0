# API error envelopes

hal0 exposes two HTTP surfaces and they return error payloads in
**two different shapes** by design.  The shape you get depends on
*who produced the error*, not just the path prefix:

| Producer                                   | Envelope     | When                                             |
|--------------------------------------------|--------------|--------------------------------------------------|
| hal0 itself (auth, dispatch, slot, validation, anything internal) | hal0         | Any 4xx/5xx raised inside hal0, on **any** path |
| Upstream provider (OpenAI, OpenRouter, llama-server, …) | OpenAI-shape | Only on `/v1/*` — when the dispatcher actually reached the upstream and the upstream's HTTP response was non-2xx |

The asymmetry is by-design.  `/v1/*` is the OpenAI-compatibility
proxy: when the dispatcher *does* reach the upstream, its response
body is forwarded verbatim (see `Dispatcher._forward_direct` in
`src/hal0/dispatcher/router.py`) — rewriting the envelope would
break every OpenAI SDK that pattern-matches on `error.type` and
`error.code`.  But errors hal0 raises *before* (or instead of)
forwarding always carry the hal0 envelope.

So in practice:

- All errors on `/api/*` → hal0 envelope.
- Errors on `/v1/*` that hal0 raised itself (missing auth,
  unknown model, slot not ready, upstream unreachable, payload
  validation, …) → hal0 envelope.
- Errors on `/v1/*` that came back as the upstream's response →
  OpenAI envelope.

## hal0 envelope

```json
{
  "error": {
    "code": "slot.not_ready",
    "message": "slot 'primary' is in state STARTING; retry once READY",
    "details": {"slot": "primary", "state": "STARTING"}
  }
}
```

- `code` — dotted namespace (`auth.*`, `slot.*`, `model.*`, `dispatch.*`, `image.*`, `config.*`, `system.*`, `capability.*`, `request.*`).  Stable across releases — safe to pattern-match.
- `message` — human-readable.  Not stable; do not pattern-match.
- `details` — open object with structured context (slot name, model id, etc.).  May be `{}`.

Source: `Hal0Error` subclasses raised throughout the codebase,
serialised by `hal0.api.middleware.error_codes._envelope()`.
Unhandled `HTTPException`s get auto-wrapped with
`code = "system.http_<status>"`; uncaught exceptions become
`code = "system.internal"` at HTTP 500.

## OpenAI envelope

```json
{
  "error": {
    "message": "Invalid value for 'model': 'gpt-5'.",
    "type": "invalid_request_error",
    "code": "model_not_found"
  }
}
```

- `message` — upstream provider's string.
- `type` — coarse OpenAI category (`invalid_request_error`, `authentication_error`, `rate_limit_error`, `api_error`, …).
- `code` — fine-grained OpenAI code (`model_not_found`, `context_length_exceeded`, …).  May be `null`.

Source: the upstream provider's HTTP response body, byte-for-byte.
hal0 does not synthesise this shape itself.

## Examples by status

### 400 — bad request

`/api/slots` (hal0 envelope), invalid backend:

```json
{
  "error": {
    "code": "slot.invalid_config",
    "message": "backend 'cuda' is not supported on this hardware (have: vulkan)",
    "details": {"requested": "cuda", "available": ["vulkan"]}
  }
}
```

`/v1/chat/completions` (OpenAI envelope), upstream rejected a
malformed body:

```json
{
  "error": {
    "message": "we could not parse the JSON body of your request",
    "type": "invalid_request_error",
    "code": null
  }
}
```

### 401 — unauthorized

**Post-ADR-0012 status:** there is **no built-in 401 path** on the
FastAPI surface. `hal0-api` binds open; auth is the operator's reverse
proxy. The `auth.required` envelope below is documented for two
reasons: (a) the upstream cosign-verified release endpoints and
`/mcp/*` mounts still resolve a `client_id` from request headers
(`X-hal0-Agent` post-rename — see `v0.3-state.md` §3) and may surface
identity-related errors using this envelope shape, and (b) any
reverse-proxy auth layer in front of hal0 will surface its own 401
through the proxy, not through this envelope.

When an identity-required surface does raise, the shape is:

```json
{
  "error": {
    "code": "auth.required",
    "message": "no agent identity presented",
    "details": {}
  }
}
```

History: ADR-0001 (PR #58) introduced a FastAPI-layer auth surface
(`POST /api/auth/login` cookie, `POST /api/auth/password`, Bearer
tokens). ADR-0012 (PRs #254 / #255 / #256 / #266 / #267, v0.3.0-alpha.1)
removed that surface entirely — ~6,000 lines deleted across backend,
frontend, tests, installer, and packaging. The `auth.*` envelope
code namespace is retained for any future re-introduced auth and for
the MCP identity middleware.

### 404 — not found

`/api/slots/nope` (hal0 envelope):

```json
{
  "error": {
    "code": "slot.not_found",
    "message": "no slot named 'nope'",
    "details": {"slot": "nope"}
  }
}
```

`/v1/chat/completions` with an unbound model id — dispatch fails
**before** reaching any upstream, so this is the hal0 envelope on
a `/v1` path:

```json
{
  "error": {
    "code": "dispatch.no_route",
    "message": "no upstream serves model 'gpt-5'",
    "details": {"model": "gpt-5"}
  }
}
```

If the upstream *was* reached and itself returned 404, the response
would be the OpenAI envelope verbatim.

### 422 — unprocessable entity

`/api/*` — FastAPI pydantic validation, auto-wrapped as
`system.http_422`:

```json
{
  "error": {
    "code": "system.http_422",
    "message": "[{'loc': ['body', 'name'], 'msg': 'field required', 'type': 'value_error.missing'}]",
    "details": {}
  }
}
```

`/v1/images/generations` missing `prompt` — raised as a
`Hal0Error` subclass inside the route, so this is the hal0
envelope on a `/v1` URL:

```json
{
  "error": {
    "code": "image.prompt_required",
    "message": "body.prompt is required",
    "details": {}
  }
}
```

## Capability slots envelope (`/api/capabilities/*`)

`/api/capabilities` is the dashboard overlay that maps embed / voice /
img children onto underlying slots
([ADR-0002](./adr/0002-capabilities-overlay.md)). All errors on this
prefix use the hal0 envelope. The codes are:

| Code | Status | Raised when |
|---|---|---|
| `capability.unknown_slot` | 400 | `slot` path segment not in `("embed", "voice", "img")`. `details` carries the legal list. |
| `capability.unknown_child` | 400 | `child` segment not valid for that slot (`embed/embed`, `embed/rerank`, `voice/stt`, `voice/tts`, `img/img`). |
| `capability.unknown_fields` | 400 | Request body has keys outside `{backend, provider, model, enabled}`. `details.unexpected` lists them. |
| `capability.invalid_selection` | 400 | Merged selection failed `CapabilitySelection` pydantic validation. |
| `capability.unknown_model` | 404 | Selected `model` is not advertised for this capability and isn't in the registry either. |
| `capability.illegal_backend_model_pair` | 400 | Model exists but the requested `backend` cannot serve it. `details.legal_backends` lists what can. This is the guard the model-first picker reshape was built to enforce — prevents `backend=npu` + a llama.cpp GGUF combinations that would crash the slot at start-up. |
| `capability.apply_failed` | 503 | Underlying `SlotManager.load/swap/unload/create` raised. `details.error` carries the original message. The user's selection IS persisted before the envelope is returned so a retry doesn't lose intent. |
| `request.invalid_json` | 400 | Body did not parse as JSON. |
| `request.not_an_object` | 400 | Body parsed but isn't a JSON object. |

Source: `src/hal0/api/routes/capabilities.py` + `src/hal0/capabilities/orchestrator.py`.

## Rerank — `/v1/rerankings`

`/v1/rerankings` is a llama-server-only OpenAI-compat extension served
by the `embed-rerank` slot (port 8086 by default, model
`bge-reranker-v2-m3-q4_k_m`). The route is a plain dispatch passthrough
(`src/hal0/api/routes/v1.py:260`), so error shapes follow the same
hal0-vs-upstream split as `/v1/chat/completions`:

- hal0-raised pre-dispatch errors (no route, slot not ready, auth) →
  hal0 envelope.
- llama-server's own 4xx/5xx response (e.g. the slot binary was started
  without `--reranking`, which makes `/rerank` return 404) → forwarded
  verbatim as the upstream's body.

If the upstream 404 is the symptom, the fix is on the slot side —
`[server].extra_args = "--reranking"` in `/etc/hal0/slots/embed-rerank.toml`
(see [models-slots-impl-plan.md](./models-slots-impl-plan.md#rerank-slot)).

## Guidance for clients

- **hal0-internal callers** (CLI, dashboard, integration tests): pin
  to the hal0 envelope and match on `error.code` (stable).
- **OpenAI-SDK callers**: hit `/v1/*` only.  Be defensive — a
  pre-upstream failure (auth refused, no route, slot not ready)
  surfaces the hal0 envelope on a `/v1` URL.  Treat the response as
  "either OpenAI envelope or hal0 envelope" and fall back to HTTP
  status code + `error.message` when `error.type` is absent.
