"""ComfyUI's editor through hal0 (#3049): an authenticated reverse proxy under ``/comfyui/``.

ComfyUI listens on loopback only: under host networking the slot's ``--listen 0.0.0.0``
is flipped to ``127.0.0.1`` (:func:`hal0.providers.container._loopback_fence_command`),
so its web editor could not be opened from any other machine. This module serves the
editor on hal0's own origin:

* ``GET /comfyui`` redirects to ``/comfyui/``. The editor derives its API base from
  ``location.pathname`` (everything before the last ``/``), so the trailing slash is what
  puts its API calls and its websocket under ``/comfyui``.
* ``/comfyui/{path}`` (GET, HEAD, POST, PUT, PATCH, DELETE) relays to ComfyUI's
  ``/{path}``, streaming both bodies. A prompt from the editor is therefore still
  ``POST /api/prompt`` when it reaches ComfyUI, the request its GPU gate node holds while
  hal0 switches the GPU: the proxy changes nothing about the gate.
* ``/comfyui/ws`` relays the editor's websocket to ComfyUI's ``/ws``.
* ``GET /extensions/{path}`` redirects to ``/comfyui/extensions/{path}``. A node's help
  page fetches its markdown under the API base, but gives the images inside it the
  root-absolute base ``/extensions/<node>/docs/``.

**Who gets in.** ``/comfyui`` and ``/extensions`` are ADMIN in
:mod:`hal0.security.exposure`, so :class:`hal0.api.auth.AuthEnforcementMiddleware`
admits a caller exactly as it does for every other admin route: the dashboard session
cookie or the admin key while auth is enforced, the posture-coupled LAN gate while it is
off, and an admitted websocket is closed by :mod:`hal0.api.live_sockets` once its
credential stops passing. The editor queues any workflow on the GPU and can install
custom nodes, so a client key is not enough.

**Cross-site requests.** ComfyUI refuses a request whose ``Origin`` differs from its
``Host`` while that host is loopback (``origin_only_middleware`` in ComfyUI's
``server.py``): that is what stops a web page from queueing prompts on 127.0.0.1. The
proxy has to rewrite ``Origin`` to ComfyUI's own for the editor to work at all, which
would switch that check off, so the proxy applies hal0's Origin defence
(:func:`hal0.api.auth._origin_allowed`) itself, before anything is forwarded or any
websocket accepted: a request or upgrade carrying an ``Origin`` hal0 does not allow is
refused, whatever its method and whatever the auth posture. Only an Origin that passed
is rewritten. ``Sec-Fetch-Site`` is forwarded, so ComfyUI's own refusal of a
``cross-site`` request still applies.

**What crosses.** Request headers are the shared allowlist in
:mod:`hal0.forwarded_headers` plus the few the editor needs
(:data:`_EXTRA_REQUEST_HEADERS`), so hal0's session cookie, ``Authorization``,
``X-API-Key``, ``X-hal0-*``, ``X-Forwarded-*`` and the hop-by-hop headers never reach
ComfyUI, and ``?api_key=`` is dropped from the query. ``Host`` comes from the upstream
URL. On the way back, hop-by-hop headers, ``Set-Cookie`` and ``Access-Control-*`` are
dropped: ComfyUI cannot set a cookie on hal0's origin or widen its CORS.

**Where to.** Always ``COMFYUI_BASE_URL`` (default ``http://127.0.0.1:8188``), the
address the other ComfyUI routes use. Only the path and query come from the request, and
a path with a ``.`` or ``..`` segment (encoded or not) or a control character is refused
with 400.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import re
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any, Final
from urllib.parse import quote, unquote, unquote_plus

import anyio
import httpx
import structlog
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.requests import ClientDisconnect
from websockets.asyncio.client import connect as _websocket_connect
from websockets.exceptions import WebSocketException

# The middleware's own Origin predicate and refusal text: one policy, two callers.
from hal0.api.auth import _ORIGIN_FORBIDDEN_MSG, _origin_allowed
from hal0.api.routes.comfyui import _comfyui_base_url
from hal0.forwarded_headers import forwardable_request_headers
from hal0.http_client import async_client

log = structlog.get_logger(__name__)

router = APIRouter()

#: Where the editor opens on hal0's origin. The trailing slash matters (module docstring).
EDITOR_PATH: Final[str] = "/comfyui/"
_PREFIX: Final[str] = "comfyui"

_PROXIED_METHODS: Final[list[str]] = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]

#: Connect is short: ComfyUI is on loopback, and a stopped engine should answer 502 at
#: once. The read budget covers a prompt that ComfyUI's GPU gate holds while hal0
#: switches the GPU (up to 180 s by default, ``HAL0_COMFYUI_SWITCH_TIMEOUT_S`` in the gate).
_TIMEOUT: Final = httpx.Timeout(connect=2.0, read=300.0, write=60.0, pool=10.0)
#: A browser loads the editor's script chunks six at a time per tab.
_LIMITS: Final = httpx.Limits(max_connections=32, max_keepalive_connections=8)

#: Largest request body relayed (an upload: image, mask, video, workflow). Above
#: ComfyUI's own default (``--max-upload-size`` 100 MB), so the limit a user meets is
#: ComfyUI's, while one request still cannot push an unbounded stream through hal0.
MAX_BODY_BYTES: int = 512 * 1024 * 1024

_WS_OPEN_TIMEOUT_S: Final[float] = 5.0
_WS_CLOSE_TIMEOUT_S: Final[float] = 2.0
#: Largest frame relayed from ComfyUI: its binary preview frames carry an image.
_WS_MAX_FRAME_BYTES: Final[int] = 32 * 1024 * 1024

#: Request headers the editor needs on top of :data:`hal0.forwarded_headers.FORWARDABLE_REQUEST_HEADERS`:
#: content negotiation and conditional/range GETs for its assets and media,
#: ``Comfy-User`` (ComfyUI's ``--multi-user`` identity) and ``Sec-Fetch-Site`` (ComfyUI
#: refuses ``cross-site`` itself). None of them is a hal0 credential.
_EXTRA_REQUEST_HEADERS: Final[tuple[str, ...]] = (
    "accept-encoding",
    "if-none-match",
    "if-modified-since",
    "range",
    "if-range",
    "comfy-user",
    "sec-fetch-site",
)
_WS_EXTRA_HEADERS: Final[tuple[str, ...]] = ("comfy-user", "sec-fetch-site")

_HOP_BY_HOP: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

#: The protocol-range close codes an endpoint may send (RFC 6455 §7.4, IANA registry).
_SENDABLE_CLOSE_CODES: Final[frozenset[int]] = frozenset(
    {1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014}
)

_CONTROL_CHARS: Final = re.compile(r"[\x00-\x1f\x7f]")
_PATH_SEPARATORS: Final = re.compile(r"[/\\]")

_client: httpx.AsyncClient | None = None


def _build_client() -> httpx.AsyncClient:
    return async_client(timeout=_TIMEOUT, limits=_LIMITS)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


async def aclose_client() -> None:
    """Close the shared client on app shutdown. Idempotent."""
    global _client
    if _client is not None:
        with contextlib.suppress(Exception):
            await _client.aclose()
        _client = None


# websockets 15 reads HTTP(S)_PROXY from the environment for ``connect`` unless told not
# to, which would carry a loopback socket through a proxy (#2585). 13 and 14 have no
# proxy support, and no ``proxy`` argument either.
_WS_NO_PROXY: Final[dict[str, Any]] = (
    {"proxy": None} if "proxy" in inspect.signature(_websocket_connect).parameters else {}
)


def _ws_connect(url: str, *, origin: str | None, headers: dict[str, str]) -> Any:
    """Open ComfyUI's websocket; ``await`` it for the connection. The seam tests replace.

    Keepalive pings are off, as for hal0's own progress listener
    (:func:`hal0.providers.comfyui._ws_connect`): ComfyUI can starve its event loop for
    longer than a ping timeout while it loads a checkpoint. Compression is off: loopback.
    """
    return _websocket_connect(
        url,
        origin=origin,  # type: ignore[arg-type]  # websockets' Origin is a NewType of str
        additional_headers=headers or None,
        compression=None,
        ping_interval=None,
        open_timeout=_WS_OPEN_TIMEOUT_S,
        close_timeout=_WS_CLOSE_TIMEOUT_S,
        max_size=_WS_MAX_FRAME_BYTES,
        **_WS_NO_PROXY,
    )


# ---------------------------------------------------------------------------
# Request → upstream


def _error(status: int, code: str, message: str) -> JSONResponse:
    """hal0's error envelope, built here rather than raised.

    Raising a 5xx :class:`hal0.errors.Hal0Error` reports it to Sentry, and a stopped
    engine is not hal0 failing.
    """
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message, "details": {}}}
    )


def _path_refused() -> JSONResponse:
    return _error(
        400,
        "comfyui.editor_path_invalid",
        "the path has a '.' or '..' segment, a control character or a byte outside ASCII",
    )


def _origin_refused(scope: Any) -> bool:
    """True iff this request carries an ``Origin`` hal0 does not allow (logged).

    No ``Origin`` passes, as in the middleware: a same-origin navigation or a client that
    is not a browser.
    """
    if _origin_allowed(scope):
        return False
    origin = ""
    for name, value in scope.get("headers") or ():
        if name.lower() == b"origin":
            origin = value.decode("latin-1")
            break
    log.info(
        "hal0.auth.origin_rejected",
        path=scope.get("path", ""),
        method=scope.get("method", "GET"),
        scope_type=scope.get("type"),
        origin=origin[:200],
        via="comfyui_proxy",
    )
    return True


def _tail(scope: Mapping[str, Any], prefix: str) -> list[str] | None:
    """The raw (still percent-encoded) path segments after ``/<prefix>/``, or ``None``.

    The raw path is what is forwarded, so an encoded ``/`` inside one segment (the
    editor's userdata calls send ``workflows%2Fname.json``) reaches ComfyUI as sent. It
    must decode to the path the router matched, and its first segment must be the
    prefix as written. ``None`` refuses: a segment that decodes to ``.`` or ``..``, or
    contains one between ``/`` or ``\\`` separators, a control character, or a byte
    outside ASCII. Empty leading segments (``/comfyui//x``) are dropped, so the
    forwarded path is always ``/`` plus a relative path.
    """
    path = scope.get("path", "")
    raw = scope.get("raw_path")
    raw_text = raw.decode("latin-1") if raw else quote(path)
    if unquote(raw_text) != path or not raw_text.isascii():
        return None
    segments = raw_text.split("/")
    if len(segments) < 2 or segments[0] != "" or segments[1] != prefix:
        return None
    tail = segments[2:]
    for segment in tail:
        decoded = unquote(segment)
        if _CONTROL_CHARS.search(decoded):
            return None
        if any(part in (".", "..") for part in _PATH_SEPARATORS.split(decoded)):
            return None
    while tail and tail[0] == "":
        tail.pop(0)
    return tail


def _upstream_query(scope: Mapping[str, Any]) -> str | None:
    """The query string without its ``api_key`` parameters, the rest byte for byte.

    Split and decoded the way :func:`hal0.api.auth.resolve_principal` reads it
    (``parse_qs``: ``&`` separators, ``+`` and ``%`` escapes), so any parameter it would
    read as ``api_key`` is dropped. ``None`` for a query with raw bytes outside ASCII,
    which no browser sends.
    """
    query = (scope.get("query_string") or b"").decode("latin-1")
    if not query.isascii():
        return None
    kept = [part for part in query.split("&") if unquote_plus(part.partition("=")[0]) != "api_key"]
    return "&".join(kept)


def _upstream_base() -> httpx.URL:
    return httpx.URL(_comfyui_base_url())


def _upstream_origin(base: httpx.URL) -> str:
    """The ``Origin`` ComfyUI accepts: its own scheme and authority, as ``Host`` will be."""
    return f"{base.scheme}://{base.netloc.decode('ascii')}"


def _base_path(base: httpx.URL) -> str:
    return base.raw_path.decode("ascii").split("?", 1)[0].rstrip("/")


def _upstream_url(base: httpx.URL, tail: list[str], query: str) -> httpx.URL:
    raw = _base_path(base) + "/" + "/".join(tail)
    if query:
        raw += "?" + query
    return base.copy_with(raw_path=raw.encode("ascii"))


def _request_headers(request: Request, base: httpx.URL) -> dict[str, str]:
    headers = forwardable_request_headers(request.headers, extra=_EXTRA_REQUEST_HEADERS)
    if not headers.get("accept-encoding"):
        # Without one httpx asks for gzip itself; the body is relayed raw, so only an
        # encoding the browser named may come back.
        headers["accept-encoding"] = "identity"
    if "origin" in request.headers:  # already allowed by _origin_refused
        headers["origin"] = _upstream_origin(base)
    return headers


class _BodyTooLarge(Exception):
    """The request body passed :data:`MAX_BODY_BYTES` while it streamed."""


async def _limited_body(request: Request, limit: int) -> AsyncIterator[bytes]:
    seen = 0
    async for chunk in request.stream():
        seen += len(chunk)
        if seen > limit:
            raise _BodyTooLarge
        if chunk:
            yield chunk


def _declared_length(request: Request) -> int | None:
    """The request's ``Content-Length``, ``None`` without one. ``ValueError`` if malformed."""
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    value = raw.strip()
    if not (value.isascii() and value.isdigit()):
        raise ValueError(raw)
    return int(value)


# ---------------------------------------------------------------------------
# Upstream → response


def _response_headers(headers: httpx.Headers) -> list[tuple[bytes, bytes]]:
    """ComfyUI's response headers for the browser: duplicates and order kept.

    ``Content-Length`` and ``Content-Encoding`` stay: the body is relayed raw.
    """
    named = {
        token.strip().lower()
        for value in headers.get_list("connection")
        for token in value.split(",")
        if token.strip()
    }
    out: list[tuple[bytes, bytes]] = []
    for name, value in headers.multi_items():
        lower = name.lower()
        if lower in _HOP_BY_HOP or lower in named or lower == "set-cookie":
            continue
        if lower.startswith("access-control-"):
            continue
        out.append((lower.encode("latin-1"), value.encode("latin-1")))
    return out


async def _relay(upstream: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in upstream.aiter_raw():
            yield chunk
    except httpx.HTTPError as exc:
        log.warning("comfyui_proxy.upstream_body_failed", error=type(exc).__name__)
    finally:
        with anyio.CancelScope(shield=True):
            await upstream.aclose()


# ---------------------------------------------------------------------------
# Routes


def _redirect(target: str, scope: Mapping[str, Any]) -> RedirectResponse:
    query = (scope.get("query_string") or b"").decode("latin-1")
    return RedirectResponse(target + (f"?{query}" if query else ""), status_code=307)


@router.get("/comfyui", include_in_schema=False)
async def comfyui_editor_root(request: Request) -> Response:
    """Send the bare prefix to the editor's own address (see :data:`EDITOR_PATH`)."""
    return _redirect(EDITOR_PATH, request.scope)


@router.get("/extensions/{path:path}", include_in_schema=False)
async def comfyui_extension_files(request: Request, path: str) -> Response:
    """Send ComfyUI's ``/extensions/...`` files to their place under the prefix."""
    tail = _tail(request.scope, "extensions")
    if tail is None:
        return _path_refused()
    return _redirect(EDITOR_PATH + "extensions/" + "/".join(tail), request.scope)


@router.api_route("/comfyui/{path:path}", methods=_PROXIED_METHODS, include_in_schema=False)
async def comfyui_editor(request: Request, path: str) -> Response:
    """Relay one editor request to ComfyUI and stream its answer back."""
    if _origin_refused(request.scope):
        return _error(403, "auth.origin_forbidden", _ORIGIN_FORBIDDEN_MSG)
    tail = _tail(request.scope, _PREFIX)
    query = _upstream_query(request.scope)
    if tail is None or query is None:
        return _path_refused()
    try:
        length = _declared_length(request)
    except ValueError:
        return _error(400, "validation.invalid", "Content-Length is not a byte count")
    if length is not None and length > MAX_BODY_BYTES:
        return _too_large()

    base = _upstream_base()
    headers = _request_headers(request, base)
    content: AsyncIterator[bytes] | None = None
    if length:
        headers["content-length"] = str(length)
        content = _limited_body(request, MAX_BODY_BYTES)
    elif length is None and "chunked" in request.headers.get("transfer-encoding", "").lower():
        content = _limited_body(request, MAX_BODY_BYTES)

    client = _get_client()
    upstream_request = client.build_request(
        request.method,
        _upstream_url(base, tail, query),
        headers=headers,
        content=content,
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except _BodyTooLarge:
        return _too_large()
    except ClientDisconnect:
        return Response(status_code=400)
    except httpx.TimeoutException:
        return _error(504, "comfyui.editor_timeout", "ComfyUI did not answer in time")
    except httpx.HTTPError as exc:
        log.info("comfyui_proxy.upstream_unreachable", error=type(exc).__name__)
        return _error(502, "comfyui.editor_unreachable", "ComfyUI is not reachable")

    response = StreamingResponse(_relay(upstream), status_code=upstream.status_code)
    response.raw_headers = _response_headers(upstream.headers)
    return response


def _too_large() -> JSONResponse:
    return _error(
        413,
        "comfyui.editor_body_too_large",
        f"the request body is larger than {MAX_BODY_BYTES} bytes",
    )


# ---------------------------------------------------------------------------
# WebSocket


def _ws_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {name: value for name, value in headers if name in _WS_EXTRA_HEADERS}


def _upstream_ws_url(base: httpx.URL, query: str) -> str:
    scheme = "wss" if base.scheme == "https" else "ws"
    url = f"{scheme}://{base.netloc.decode('ascii')}{_base_path(base)}/ws"
    return f"{url}?{query}" if query else url


def _client_close_code(code: int | None) -> int:
    """The code to close the browser with when ComfyUI closed with ``code``.

    ComfyUI's own code when an endpoint may send it; 1011 otherwise (an abnormal end
    reads 1006, which no endpoint may send).
    """
    if code is not None and (code in _SENDABLE_CLOSE_CODES or 3000 <= code <= 4999):
        return code
    return 1011


async def _refuse_upstream_down(websocket: WebSocket) -> None:
    if "websocket.http.response" in websocket.scope.get("extensions", {}):
        await websocket.send_denial_response(
            _error(502, "comfyui.editor_unreachable", "ComfyUI is not reachable")
        )
    else:
        await websocket.close(code=1011)


@router.websocket("/comfyui/ws")
async def comfyui_editor_socket(websocket: WebSocket) -> None:
    """Relay the editor's websocket to ComfyUI's ``/ws``, both ways, until either side closes."""
    if _origin_refused(websocket.scope):
        await websocket.close(code=4403)  # before accept: the handshake is refused
        return
    query = _upstream_query(websocket.scope)
    if query is None:
        await websocket.close(code=1008)
        return
    base = _upstream_base()
    origin = _upstream_origin(base) if "origin" in websocket.headers else None
    try:
        upstream = await _ws_connect(
            _upstream_ws_url(base, query),
            origin=origin,
            headers=_ws_headers(websocket.headers.items()),
        )
    except (OSError, TimeoutError, WebSocketException) as exc:
        log.info("comfyui_proxy.ws_upstream_unreachable", error=type(exc).__name__)
        await _refuse_upstream_down(websocket)
        return

    try:
        await websocket.accept()
        await _pump(websocket, upstream)
    finally:
        with anyio.CancelScope(shield=True), contextlib.suppress(Exception):
            await upstream.close()


async def _pump(websocket: WebSocket, upstream: Any) -> None:
    """Carry frames both ways; when one side ends, close the other."""

    async def browser_to_comfyui() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            await upstream.send(text if text is not None else message.get("bytes") or b"")

    async def comfyui_to_browser() -> None:
        async for frame in upstream:
            if isinstance(frame, str):
                await websocket.send_text(frame)
            else:
                await websocket.send_bytes(frame)

    from_browser = asyncio.ensure_future(browser_to_comfyui())
    from_comfyui = asyncio.ensure_future(comfyui_to_browser())
    try:
        done, _pending = await asyncio.wait(
            {from_browser, from_comfyui}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (from_browser, from_comfyui):
            task.cancel()
        for task in (from_browser, from_comfyui):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    browser_left = (
        from_browser in done and not from_browser.cancelled() and from_browser.exception() is None
    )
    if not browser_left:
        # ComfyUI ended the socket, or it broke under a send: tell the browser, which
        # reconnects. (A send to a browser that is gone fails quietly here.)
        with contextlib.suppress(Exception):
            await websocket.close(code=_client_close_code(getattr(upstream, "close_code", None)))
    # Either way the caller closes the upstream socket.


__all__ = ["EDITOR_PATH", "MAX_BODY_BYTES", "aclose_client", "router"]
