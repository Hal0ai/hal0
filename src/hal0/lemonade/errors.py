"""Exception types raised by ``LemonadeClient``.

Callers in ``SlotManager`` (later PR) catch the specific subclasses to
distinguish "lemond unreachable" (retry the whole control plane) from
"lemond returned 4xx on a /v1/load" (surface to the slot as an error
state, do NOT retry — see ADR-0007 §4).

The base ``LemonadeError`` is the catch-all. Subclasses carry the
discriminator. Avoid leaking httpx types into the rest of hal0 — the
LemonadeClient wraps and re-raises.
"""

from __future__ import annotations


class LemonadeError(Exception):
    """Base class for every error raised by ``LemonadeClient``."""


class LemonadeUnavailableError(LemonadeError):
    """lemond couldn't be reached at all — connect-refused, DNS fail,
    socket closed before response. Distinct from HTTP 5xx because the
    daemon may be down (vs. serving but erroring).

    SlotManager should treat this as "control plane offline" and not
    use it to mark individual slots errored.
    """


class LemonadeTimeoutError(LemonadeError):
    """A request exceeded the client's timeout budget. Distinct from
    ``LemonadeUnavailableError`` because the connection succeeded — the
    server is either slow or has accepted a request that's blocking on
    its serialized load queue (see ADR-0006 §Operational risks).

    ADR-0007 §5 mandates a hard timeout on ``/v1/load`` specifically.
    """


class LemonadeHTTPError(LemonadeError):
    """lemond returned a non-2xx HTTP response. ``status_code`` carries
    the raw code; ``body`` carries the parsed JSON body (if any) so
    callers can match on Lemonade's error envelope without re-parsing.
    """

    def __init__(
        self, status_code: int, body: object | None = None, msg: str | None = None
    ) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(msg or f"lemonade returned HTTP {status_code}")


class LemonadeLoadError(LemonadeHTTPError):
    """Specialisation of ``LemonadeHTTPError`` for ``/v1/load`` failures.

    Worth its own class because the nuclear-evict-all policy (ADR-0007)
    means a /v1/load 5xx has caller-visible consequences other 5xx
    don't — every loaded slot on the pool has just been blasted.
    SlotManager catches this and refreshes its view of /v1/health
    before reporting state, instead of trusting cached state.
    """
