"""Agent-driven OAuth passthrough for hal0-bundled skills (study 3.3).

Ports ODS's OAuth passthrough
(``extensions/services/dashboard-api/routers/oauth_passthrough.py``,
Osmantic/ODS, Apache-2.0; permission to copy granted by its author): a
Hermes skill that needs OAuth (Google Calendar, Spotify, GitHub, ...) is
connected by sending the operator a consent link and having the provider's
redirect land back on hal0-api directly, instead of the operator having to
copy an authorization code out of their browser's address bar.

Endpoints::

    GET    /api/oauth/providers                  — registry + connection status
    POST   /api/oauth/{provider_id}/start         — mint a consent URL
    GET    /api/oauth/{provider_id}/callback      — provider redirect target (OPEN)
    GET    /api/oauth/{provider_id}/status        — connected? expiry? (no token)
    DELETE /api/oauth/{provider_id}                — disconnect (best-effort revoke)
    POST   /api/oauth/{provider_id}/client-secret — store a provider's client secret

Where hal0 differs from ODS: ODS hands the raw authorization code to the
agent over a shared file and lets the agent's own ``setup.py`` do the token
exchange. hal0 does the exchange itself, server-side, and persists the
result through the secrets store (:mod:`hal0.oauth.store` — never in TOML,
never logged) — the agent never handles a raw code or a client secret, it
only ever sees the already-exchanged token via its own env (see
:func:`hal0.agents.hermes_provision.refresh_driver_env`).

Security model (mirrors ODS's, translated to hal0's exposure model):
  * Every route except ``callback`` is ADMIN (``security/exposure.py``).
  * ``callback`` is OPEN (it's a redirect target; a provider carries none
    of hal0's own auth) but requires a valid, single-use, server-issued
    ``state`` nonce (:mod:`hal0.oauth.state`) — an unknown, expired, or
    replayed ``state`` is refused before anything is written.
  * ``provider_id`` is resolved from the nonce, never trusted from the
    callback's own query string — closes the "attacker names the
    provider" class of bug.
  * Every outbound URL (authorize/token/revoke) is SSRF-guarded via
    :func:`hal0.mcp.manifest.enforce_safe_url` before hal0 ever makes a
    request to it — one owner of the deny-list.
"""

from __future__ import annotations

import time

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from hal0.api.middleware.error_codes import Hal0Error
from hal0.mcp.manifest import enforce_safe_url
from hal0.oauth import providers as provider_registry
from hal0.oauth import store as oauth_store
from hal0.oauth.pkce import generate_pkce_pair
from hal0.oauth.state import OAuthStateStore

_audit_log = structlog.get_logger("hal0.audit")
_log = structlog.get_logger(__name__)

router = APIRouter()

# Outbound token-exchange request budget. Mirrors the manifest resolver's
# fetch timeout (hal0.mcp.manifest._FETCH_TIMEOUT) — short connect, bounded
# total, so a slow/unresponsive provider can't hang the request handler.
_TOKEN_EXCHANGE_TIMEOUT = httpx.Timeout(connect=4.0, read=8.0, write=4.0, pool=8.0)


class OAuthProviderNotFound(Hal0Error):
    code = "oauth.provider_not_found"
    status = 404


class OAuthNotConfigured(Hal0Error):
    """Provider is registered but missing a client_id / required client secret."""

    code = "oauth.not_configured"
    status = 400


class OAuthStateInvalid(Hal0Error):
    code = "oauth.state_invalid"
    status = 400


class OAuthExchangeFailed(Hal0Error):
    code = "oauth.exchange_failed"
    status = 502


class OAuthClientSecretInvalid(Hal0Error):
    code = "oauth.client_secret_invalid"
    status = 400


def _state_store(request: Request) -> OAuthStateStore:
    return request.app.state.oauth_state


def _base_url(request: Request) -> str:
    """hal0-api's own externally-reachable base URL, for building redirect_uri.

    Mirrors the dashboard_url fallback already used by hermes_provision's
    context-link phase (HAL0_DASHBOARD_URL, then HAL0_API_URL, then a
    loopback default) rather than trusting the incoming Host header, which
    an operator's reverse proxy could rewrite in a way that doesn't match
    what's actually registered in the provider's developer console.
    """
    import os

    return os.environ.get(
        "HAL0_DASHBOARD_URL", os.environ.get("HAL0_API_URL", "http://hal0.local:8080")
    ).rstrip("/")


def _redirect_uri(request: Request, provider_id: str) -> str:
    return f"{_base_url(request)}/api/oauth/{provider_id}/callback"


def _require_provider(provider_id: str) -> provider_registry.OAuthProvider:
    provider = provider_registry.get_provider(provider_id)
    if provider is None:
        raise OAuthProviderNotFound(f"oauth provider {provider_id!r} not registered", {"provider_id": provider_id})
    return provider


def _serialize_provider(provider: provider_registry.OAuthProvider) -> dict:
    connected = oauth_store.is_connected(provider.id)
    token = oauth_store.load_token(provider.id) if connected else None
    return {
        "id": provider.id,
        "name": provider.name,
        "skill_id": provider.skill_id,
        "scopes": provider.scopes,
        "pkce": provider.pkce,
        "configured": bool(provider.client_id)
        and (not provider.requires_client_secret or oauth_store.has_client_secret(provider.id)),
        "requires_client_secret": provider.requires_client_secret,
        "has_client_secret": oauth_store.has_client_secret(provider.id),
        "connected": connected,
        "expires_at": token.expires_at if token else None,
        "expired": token.is_expired if token else None,
        "notes": provider.notes,
    }


@router.get("/oauth/providers")
async def list_oauth_providers() -> dict:
    """Registry entries + connection status (never a token value)."""
    return {"providers": [_serialize_provider(p) for p in provider_registry.load_providers()]}


class ClientSecretBody(BaseModel):
    value: str = Field(..., min_length=1)


@router.post("/oauth/{provider_id}/client-secret", status_code=204)
async def set_oauth_client_secret(provider_id: str, body: ClientSecretBody) -> None:
    """Store a provider's client secret through the secrets store.

    Never returned by any endpoint, never logged — mirrors ``/api/secrets``'s
    write-only posture.
    """
    _require_provider(provider_id)
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in body.value):
        raise OAuthClientSecretInvalid(
            "control characters not allowed in a client secret", {"provider_id": provider_id}
        )
    oauth_store.save_client_secret(provider_id, body.value)
    _audit_log.info("oauth.client_secret_set", provider_id=provider_id)


@router.post("/oauth/{provider_id}/start")
async def start_oauth(provider_id: str, request: Request) -> dict:
    """Mint a consent URL for ``provider_id``.

    The dashboard's Connect button and ``hal0 oauth connect`` both call
    this and open/print the returned ``authorize_url`` — never construct
    one client-side (the state nonce + PKCE challenge must come from here).
    """
    provider = _require_provider(provider_id)
    if not provider.client_id:
        raise OAuthNotConfigured(
            f"oauth provider {provider_id!r} has no client_id set — edit "
            f"{provider_registry.registry_path()} or your provider's console registration",
            {"provider_id": provider_id},
        )
    if provider.requires_client_secret and not oauth_store.has_client_secret(provider_id):
        raise OAuthNotConfigured(
            f"oauth provider {provider_id!r} requires a client secret — "
            f"set one with `hal0 oauth set-client-secret {provider_id}` first",
            {"provider_id": provider_id},
        )

    enforce_safe_url(provider.authorize_url)

    code_verifier: str | None = None
    params = {
        "client_id": provider.client_id,
        "redirect_uri": _redirect_uri(request, provider_id),
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "access_type": "offline",
        "prompt": "consent",
    }
    if provider.pkce:
        pair = generate_pkce_pair()
        code_verifier = pair.verifier
        params["code_challenge"] = pair.challenge
        params["code_challenge_method"] = pair.method

    state = _state_store(request).issue(provider_id, code_verifier=code_verifier)
    params["state"] = state

    authorize_url = f"{provider.authorize_url}?{httpx.QueryParams(params)}"
    _audit_log.info("oauth.start", provider_id=provider_id)
    return {"authorize_url": authorize_url, "state": state, "provider_id": provider_id}


def _success_page(provider_name: str) -> HTMLResponse:
    from html import escape

    safe = escape(provider_name)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>hal0 — connected</title>
<style>:root{{color-scheme:light dark}}body{{font:16px/1.5 system-ui,sans-serif;
max-width:32rem;margin:4rem auto;padding:0 1.5rem;text-align:center}}
.check{{font-size:2.5rem}}</style></head>
<body><div class="check">&#10003;</div><h1>Connected</h1>
<p>hal0 just got access to your {safe} account. You can close this tab —
your agent has picked it up.</p></body></html>"""
    )


def _error_page(reason: str, *, status_code: int) -> HTMLResponse:
    from html import escape

    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>hal0 — authorization failed</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:4rem auto;
padding:0 1.5rem;text-align:center}}</style></head>
<body><h1>Authorization failed</h1><p>{escape(reason)}</p></body></html>""",
        status_code=status_code,
    )


async def _exchange_code(
    provider: provider_registry.OAuthProvider,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str | None,
) -> oauth_store.OAuthToken:
    enforce_safe_url(provider.token_url)
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": provider.client_id,
    }
    if code_verifier:
        body["code_verifier"] = code_verifier
    if provider.requires_client_secret:
        secret = oauth_store.load_client_secret(provider.id)
        if not secret:
            raise OAuthNotConfigured(
                f"oauth provider {provider.id!r} requires a client secret but none is stored",
                {"provider_id": provider.id},
            )
        body["client_secret"] = secret

    try:
        async with httpx.AsyncClient(timeout=_TOKEN_EXCHANGE_TIMEOUT) as client:
            resp = await client.post(
                provider.token_url, data=body, headers={"Accept": "application/json"}
            )
    except httpx.HTTPError as exc:
        raise OAuthExchangeFailed(
            f"token exchange request to {provider.id!r} failed: {exc}", {"provider_id": provider.id}
        ) from exc

    if resp.status_code >= 400:
        # Never log the response body — a provider's error payload can echo
        # request parameters back, and we can't rule out a code fragment.
        raise OAuthExchangeFailed(
            f"provider {provider.id!r} rejected the token exchange (HTTP {resp.status_code})",
            {"provider_id": provider.id, "status_code": resp.status_code},
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise OAuthExchangeFailed(
            f"provider {provider.id!r} returned a non-JSON token response", {"provider_id": provider.id}
        ) from exc

    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthExchangeFailed(
            f"provider {provider.id!r} token response carried no access_token",
            {"provider_id": provider.id},
        )
    expires_in = payload.get("expires_in")
    expires_at = time.time() + float(expires_in) if isinstance(expires_in, (int, float)) else None
    return oauth_store.OAuthToken(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_at=expires_at,
        scope=payload.get("scope") or " ".join(provider.scopes),
        token_type=payload.get("token_type") or "Bearer",
    )


def _refresh_hermes_driver_env() -> None:
    """Best-effort: push the freshly connected token into the agent's env.

    Never raises — a running hal0-agent@hermes doesn't pick up the new
    token until its next restart regardless (same caveat as `hal0 auth
    rotate`); a failure here just means that refresh has to happen by hand
    (`hal0 agent bootstrap hermes --repair`) instead of automatically.
    """
    try:
        from hal0.agents.hermes_provision import refresh_driver_env

        refresh_driver_env()
    except Exception:  # pragma: no cover — best-effort, see docstring
        _log.warning("oauth.driver_env_refresh_failed", exc_info=True)


@router.get("/oauth/{provider_id}/callback")
async def oauth_callback(provider_id: str, request: Request) -> HTMLResponse:
    """Provider redirect target — OPEN (security/exposure.py), state-gated.

    ``provider_id`` in the path is validated against the registry, but the
    SECURITY boundary is ``state``: it must match a nonce this hal0-api
    process issued via ``start`` for the SAME provider, and it is consumed
    (single-use) before anything is written, closing the replay window a
    race between two callbacks could otherwise open.
    """
    params = request.query_params
    error = params.get("error", "")
    code = params.get("code", "")
    state = params.get("state", "")

    nonce = _state_store(request).pop(state) if state else None
    if error:
        _log.warning("oauth.callback_provider_error", provider_id=provider_id, error=error[:200])
        return _error_page(f"The provider sent back an error: {error}", status_code=400)
    if nonce is None:
        _log.warning("oauth.callback_bad_state", provider_id=provider_id)
        return _error_page(
            "This authorization link is not recognized. It may have already "
            "been used, or the flow needs to be restarted.",
            status_code=400,
        )
    if nonce.provider_id != provider_id:
        # The nonce is authoritative; a path/nonce mismatch means the
        # redirect_uri was reused across providers or tampered with.
        _log.warning(
            "oauth.callback_provider_mismatch", path_provider=provider_id, nonce_provider=nonce.provider_id
        )
        return _error_page("This authorization link does not match the provider it was issued for.", status_code=400)
    if not code:
        return _error_page(
            "No authorization code was returned. You may have denied the request.",
            status_code=400,
        )

    provider = _require_provider(provider_id)
    token = await _exchange_code(
        provider,
        code=code,
        redirect_uri=_redirect_uri(request, provider_id),
        code_verifier=nonce.code_verifier,
    )
    oauth_store.save_token(provider_id, token)
    _audit_log.info("oauth.connected", provider_id=provider_id)
    _refresh_hermes_driver_env()
    return _success_page(provider.name)


@router.get("/oauth/{provider_id}/status")
async def oauth_status(provider_id: str) -> dict:
    provider = _require_provider(provider_id)
    return _serialize_provider(provider)


@router.delete("/oauth/{provider_id}", status_code=204)
async def disconnect_oauth(provider_id: str) -> None:
    """Disconnect a provider: best-effort revoke, then delete the token.

    Deletion always happens even if the provider's revoke call fails —
    an operator disconnecting a compromised or unwanted grant should not
    be blocked by the provider being unreachable; the token is gone from
    hal0 either way, and the operator can revoke it from the provider's
    own account settings if the API call didn't land.
    """
    provider = _require_provider(provider_id)
    token = oauth_store.load_token(provider_id)
    if token is not None and provider.revoke_url:
        try:
            enforce_safe_url(provider.revoke_url)
            async with httpx.AsyncClient(timeout=_TOKEN_EXCHANGE_TIMEOUT) as client:
                await client.post(provider.revoke_url, data={"token": token.access_token})
        except httpx.HTTPError:
            _log.warning("oauth.revoke_failed", provider_id=provider_id, exc_info=True)

    oauth_store.delete_token(provider_id)
    _audit_log.info("oauth.disconnected", provider_id=provider_id)
    _refresh_hermes_driver_env()
