"""``WS /v1/realtime`` — OpenAI Realtime WebSocket surface (HP-realtime inc-1).

Thin transport shell over :mod:`hal0.realtime`: authenticate (handled upstream
by the KB-1 enforcement middleware — CLIENT tier, exposure row in
:mod:`hal0.security.exposure`), accept the upgrade, build a
:class:`~hal0.realtime.session.RealtimeSession`, then pump client text frames
into it while the session emits server events back over the same socket. The
route imports no model/provider internals — STT/TTS/chat are reached over
loopback HTTP through :class:`~hal0.realtime.backends.RealtimeBackends` (which
tests fake via ``app.state.realtime_backends``).

Keepalive: WS is long-lived; we rely on the client's own audio/append traffic
and Starlette's transport pings. A dead peer surfaces as ``WebSocketDisconnect``
on the receive loop, which tears the session down cleanly.
"""

from __future__ import annotations

import contextlib
import json
from urllib.parse import parse_qs

import structlog
from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from hal0.realtime import events
from hal0.realtime.backends import get_backends
from hal0.realtime.session import RealtimeSession

log = structlog.get_logger(__name__)

router = APIRouter()

# Close code for "realtime disabled by config" (private-use range, mirrors the
# 4403 policy-close convention used elsewhere for WS policy rejections).
_CLOSE_DISABLED = 4404


def _resolve_config(websocket: WebSocket):
    """Return the ``[realtime]`` config, defaulting if the app has none loaded."""
    from hal0.config.schema import RealtimeConfig

    cfg = getattr(getattr(websocket.app, "state", None), "hal0_config", None)
    section = getattr(cfg, "realtime", None)
    return section if isinstance(section, RealtimeConfig) else RealtimeConfig()


def _query_model(websocket: WebSocket) -> str:
    raw = websocket.scope.get("query_string") or b""
    params = parse_qs(raw.decode("latin-1")) if raw else {}
    values = params.get("model") or []
    return values[0] if values else ""


def _forwarded_auth(websocket: WebSocket) -> str | None:
    """Credential to forward on loopback STT/TTS/chat calls (so they pass the
    enforcement middleware when auth is ON). Bearer header wins; else synthesize
    one from ``?api_key=`` (the browser WS story)."""
    authz = websocket.headers.get("authorization")
    if authz:
        return authz
    raw = websocket.scope.get("query_string") or b""
    params = parse_qs(raw.decode("latin-1")) if raw else {}
    keys = params.get("api_key") or []
    return f"Bearer {keys[0]}" if keys else None


@router.websocket("/realtime")
async def realtime_ws(websocket: WebSocket) -> None:
    """OpenAI Realtime WS endpoint (query ``?model=``)."""
    cfg = _resolve_config(websocket)
    await websocket.accept()

    if not getattr(cfg, "enabled", True):
        with contextlib.suppress(Exception):
            await websocket.send_text(
                json.dumps(events.error_event("realtime endpoint is disabled", code="disabled"))
            )
            await websocket.close(code=_CLOSE_DISABLED)
        return

    async def emit(ev: dict) -> None:
        await websocket.send_text(json.dumps(ev))

    session = RealtimeSession(
        emit=emit,
        backends=get_backends(websocket.app),
        cfg=cfg,
        model=_query_model(websocket),
        auth=_forwarded_auth(websocket),
    )
    await session.start()
    try:
        while True:
            raw = await websocket.receive_text()
            await session.handle_raw(raw)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("realtime.ws_error", error=str(exc))
    finally:
        await session.aclose()


__all__ = ["router"]
