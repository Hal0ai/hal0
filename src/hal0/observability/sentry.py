"""Optional Sentry error reporting.

hal0 is a self-hosted appliance: by default it phones home to nobody, and
that default does not change here. Sentry is **inert unless a DSN is
configured** — no DSN, no SDK import, no network egress, no behaviour
change. That makes this module safe to call unconditionally from every
entry point (API, CLI, agent shim) without gating each call site on an
"is telemetry on" check.

Enabling it
-----------
1. ``pip install 'hal0ai[sentry]'`` (or ``uv pip install sentry-sdk``)
2. Set ``HAL0_SENTRY_DSN`` in the unit's environment file
   (``/etc/hal0/api.env`` for ``hal0-api.service``).

Environment
-----------
``HAL0_SENTRY_DSN``
    The DSN. Falls back to the SDK-conventional ``SENTRY_DSN``. Empty or
    unset ⇒ hard off. This is the ONLY switch; there is no separate
    enable flag to drift out of sync with it.
``HAL0_SENTRY_ENVIRONMENT``
    Sentry "environment" tag. Default ``development`` — deliberately not
    ``production``, so an operator who copies a DSN around without
    thinking does not pollute a production stream.
``HAL0_SENTRY_TRACES_SAMPLE_RATE`` / ``HAL0_SENTRY_PROFILES_SAMPLE_RATE``
    Floats in ``[0, 1]``. Both default to ``0.0`` (errors only). hal0
    serves long-lived streaming requests; sampling every transaction on
    an inference box is expensive, so tracing is opt-in per install.
``HAL0_SENTRY_SERVER_NAME``
    Overrides the reported hostname. Defaults to ``socket.gethostname()``.
``HAL0_SENTRY_DEBUG``
    ``1``/``true``/``yes`` prints the SDK's own transport logging. For
    diagnosing "why did my event not arrive".

Privacy posture (strict scrub)
------------------------------
``send_default_pii=False`` and ``max_request_body_size="never"`` are the
SDK-level floor; on top of that :func:`scrub_event` runs on every event
and transaction and:

* drops the ``user`` block entirely (no id / ip / email leaves the box),
* drops request ``data`` / ``cookies`` / ``query_string`` / ``env``, and
  truncates the request URL at ``?`` — hal0 accepts ``?api_key=`` on WS
  and SSE upgrades (browsers cannot set headers there), so a query string
  is assumed to be credential-bearing,
* masks headers in :data:`SENSITIVE_HEADERS`,
* walks the whole remaining event and applies hal0's OWN redaction
  helpers — :func:`hal0.api._redact.is_sensitive_key` by key name and
  :func:`hal0.api._redact.redact_log_line` on every string — so the
  Sentry surface inherits exactly the scrubbing rules already proven on
  ``/api/logs`` and the config-echo endpoints rather than inventing a
  second, weaker pattern list that would drift.

If scrubbing itself raises, the event is **dropped**, not sent raw.

The dashboard has a twin of this policy in ``ui/src/sentry.ts``, including a
hand-port of ``LOG_SECRET_RE``. Changing the pattern list here means changing
it there too — a browser-side leak is just as bad as a server-side one.

Prompt/completion content never reaches Sentry unless it appears inside an
exception message; that residue is what ``redact_log_line`` is for. Do not
add ``extra={...}`` payloads carrying user text at capture sites.

Failure posture
---------------
Every public function swallows exceptions and returns a bool/None. A
broken or unreachable Sentry must never take down an inference box.
"""

from __future__ import annotations

import os
import socket
from typing import Any, Final

# Env var names, in resolution order. HAL0_-prefixed first so hal0's own
# config surface wins over anything ambient in the unit environment.
DSN_ENV_VARS: Final[tuple[str, ...]] = ("HAL0_SENTRY_DSN", "SENTRY_DSN")

# Request headers masked wholesale. Compared lower-cased. Anything NOT in
# this list still passes through the key-name scrubber below, which
# catches ``X-Foo-Token``-shaped names generically; this set exists for
# the names that carry a credential without a matching key-name token.
SENSITIVE_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-hal0-admin-key",
        "x-hal0-client-key",
    }
)

# Request sub-keys dropped outright. ``env`` is Sentry's copy of the WSGI
# environ; ``data`` is the parsed body (prompts live there).
DROPPED_REQUEST_KEYS: Final[tuple[str, ...]] = ("data", "cookies", "query_string", "env")

# Recursion guard for the event walker. Sentry events nest a handful of
# levels; anything deeper is pathological and not worth the stack.
_MAX_SCRUB_DEPTH: Final[int] = 12

# Component name of the entry point that won the init race, or None when
# Sentry is off / not yet initialised. Module-global because sentry_sdk's
# own client is global too — a second init() would silently replace the
# first one's config.
_initialised_component: str | None = None


def dsn_from_env() -> str:
    """Return the configured DSN, or ``""`` when Sentry is off.

    Whitespace-only values count as unset — an env file with
    ``HAL0_SENTRY_DSN=`` (the shape a commented-out line degrades into)
    must mean "off", not "misconfigured".
    """
    for name in DSN_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _float_env(name: str, default: float) -> float:
    """Parse a ``[0, 1]`` sample rate from the environment.

    Anything unparseable or out of range falls back to ``default`` rather
    than raising — a typo in a sample rate must not stop a service from
    starting.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not 0.0 <= value <= 1.0:
        return default
    return value


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _scrub(value: Any, depth: int = 0) -> Any:
    """Recursively redact a decoded-JSON-shaped structure.

    Two independent rules, both borrowed from ``hal0.api._redact`` so this
    module never becomes a second source of truth for "what is a secret":

    * key name matches :func:`is_sensitive_key` ⇒ value replaced by
      ``MASK``, without ever inspecting the value;
    * any surviving ``str`` ⇒ passed through :func:`redact_log_line`,
      which strips ``Bearer``/``*_KEY=``/``client_id=`` token bodies while
      preserving the prefix.

    Imported lazily: ``hal0.api._redact`` is pure-stdlib, but keeping the
    import inside the call preserves this module's "importable from the
    stdlib-only agent shim" property no matter what that module grows.
    """
    from hal0.api._redact import MASK, is_sensitive_key, redact_log_line

    if depth > _MAX_SCRUB_DEPTH:
        return MASK
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_key(key):
                out[key] = MASK
            else:
                out[key] = _scrub(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        scrubbed = [_scrub(item, depth + 1) for item in value]
        return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
    if isinstance(value, str):
        return redact_log_line(value)
    return value


def scrub_event(event: dict[str, Any], hint: Any = None) -> dict[str, Any] | None:
    """``before_send`` / ``before_send_transaction`` hook.

    Returns the scrubbed event, or ``None`` to drop it. See the module
    docstring for the full policy. ``hint`` is accepted (and ignored) to
    match the SDK's callback signature.
    """
    del hint  # SDK signature parity; hal0 does not branch on the hint
    try:
        from hal0.api._redact import MASK

        event.pop("user", None)

        request = event.get("request")
        if isinstance(request, dict):
            for key in DROPPED_REQUEST_KEYS:
                request.pop(key, None)
            url = request.get("url")
            if isinstance(url, str) and "?" in url:
                request["url"] = url.split("?", 1)[0]
            headers = request.get("headers")
            if isinstance(headers, dict):
                request["headers"] = {
                    name: (MASK if str(name).lower() in SENSITIVE_HEADERS else value)
                    for name, value in headers.items()
                }

        scrubbed = _scrub(event)
        return scrubbed if isinstance(scrubbed, dict) else None
    except Exception:
        # Fail CLOSED. An event we could not scrub is an event we do not
        # send — the opposite choice would ship raw prompts on any future
        # shape change in the SDK's event schema.
        return None


def init_sentry(component: str) -> bool:
    """Initialise the SDK for ``component`` (``api`` / ``cli`` / ``agent``).

    Returns True when Sentry is live afterwards, False when it is off for
    any reason (no DSN, SDK not installed, init raised). Idempotent: the
    first caller in a process wins and later calls are no-ops, so an
    in-process CLI that also builds the FastAPI app does not re-init and
    clobber the first client's config.
    """
    global _initialised_component

    if _initialised_component is not None:
        return True

    dsn = dsn_from_env()
    if not dsn:
        return False

    try:
        import sentry_sdk
    except ImportError:
        # Extra not installed. Silent: a DSN in the environment of a box
        # without the extra is a config leftover, not an error worth
        # printing on every CLI invocation.
        return False

    try:
        from hal0 import __version__

        sentry_sdk.init(
            dsn=dsn,
            release=f"hal0@{__version__}",
            environment=os.environ.get("HAL0_SENTRY_ENVIRONMENT", "").strip() or "development",
            server_name=os.environ.get("HAL0_SENTRY_SERVER_NAME", "").strip()
            or socket.gethostname(),
            # Privacy floor — see module docstring. These three are the
            # SDK-level guarantees; scrub_event is the hal0-level one.
            send_default_pii=False,
            max_request_body_size="never",
            attach_stacktrace=True,
            traces_sample_rate=_float_env("HAL0_SENTRY_TRACES_SAMPLE_RATE", 0.0),
            profiles_sample_rate=_float_env("HAL0_SENTRY_PROFILES_SAMPLE_RATE", 0.0),
            before_send=scrub_event,
            before_send_transaction=scrub_event,
            debug=_bool_env("HAL0_SENTRY_DEBUG"),
        )
        sentry_sdk.set_tag("hal0.component", component)
        _initialised_component = component
        return True
    except Exception:
        return False


def capture_exception(exc: BaseException, *, component: str | None = None) -> None:
    """Report ``exc`` to Sentry if it is live; otherwise do nothing.

    Needed because hal0 installs a catch-all ``@app.exception_handler(Exception)``
    (``hal0.api.middleware.error_codes``). A handled exception is invisible
    to Sentry's Starlette integration, so unhandled-in-spirit 500s have to
    be reported explicitly at that handler.

    ``component`` tags the event when the caller knows a narrower scope
    than the process-wide tag set at init.
    """
    if _initialised_component is None:
        return
    try:
        import sentry_sdk

        if component:
            with sentry_sdk.new_scope() as scope:
                scope.set_tag("hal0.component", component)
                sentry_sdk.capture_exception(exc)
        else:
            sentry_sdk.capture_exception(exc)
    except Exception:
        return


def active_component() -> str | None:
    """Return the component name Sentry was initialised for, else None.

    Read-only view of module state for tests and ``hal0 doctor``-style
    reporting; never use it to gate behaviour that must work with Sentry
    off.
    """
    return _initialised_component


def _reset_for_tests() -> None:
    """Clear the init latch. Tests only — not part of the public surface."""
    global _initialised_component
    _initialised_component = None


__all__ = [
    "DROPPED_REQUEST_KEYS",
    "DSN_ENV_VARS",
    "SENSITIVE_HEADERS",
    "active_component",
    "capture_exception",
    "dsn_from_env",
    "init_sentry",
    "scrub_event",
]
