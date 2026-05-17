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

- `code` — dotted namespace (`auth.*`, `slot.*`, `model.*`, `dispatch.*`, `image.*`, `config.*`, `system.*`).  Stable across releases — safe to pattern-match.
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

`/api/*` and `/v1/*` (hal0 envelope — auth runs before forwarding):

```json
{
  "error": {
    "code": "auth.required",
    "message": "no bearer token presented",
    "details": {}
  }
}
```

Same status, hal0 envelope on both surfaces, because the auth
middleware refuses the request before it can reach an upstream.

Per [ADR-0001](./adr/0001-collapse-edge-auth-into-fastapi.md) (PR #58),
the auth surface is a single FastAPI layer — `POST /api/auth/login`
issues a `hal0_session` cookie, `POST /api/auth/logout` clears it, and
`POST /api/auth/password` sets or rotates the owner password (public
when no password is yet set; writer-scoped otherwise). The middleware
accepts either the session cookie or a Bearer token against the same
`require_token` / `require_writer` deps, so 401 envelopes are identical
across both auth paths.

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

## Guidance for clients

- **hal0-internal callers** (CLI, dashboard, integration tests): pin
  to the hal0 envelope and match on `error.code` (stable).
- **OpenAI-SDK callers**: hit `/v1/*` only.  Be defensive — a
  pre-upstream failure (auth refused, no route, slot not ready)
  surfaces the hal0 envelope on a `/v1` URL.  Treat the response as
  "either OpenAI envelope or hal0 envelope" and fall back to HTTP
  status code + `error.message` when `error.type` is absent.
