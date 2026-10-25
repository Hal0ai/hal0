"""Text that comes from inside the ComfyUI container, made safe to keep and to send.

A refusal's reason is written inside the img container, so hal0 treats it as
untrusted; so are the node names and prompt ids on the render websocket. They reach
``/status`` and ``/arbiter`` JSON, error envelopes, the ``hal0.error`` log and Sentry,
none of which can carry a lone surrogate (``"\\ud800"`` is valid JSON and not valid
UTF-8: encoding it raises), and none of which should hold an unbounded string.
"""

from __future__ import annotations

#: The longest reason hal0 keeps or repeats, in characters.
REASON_MAX = 500

#: The longest node name or prompt id hal0 repeats from the render websocket, in characters.
SOCKET_ID_MAX = 64

#: The longest text taken from the websocket (an error message, a frame) that goes in a
#: log line, in characters.
SOCKET_LOG_MAX = 200


def clean_reason(value: object, limit: int = REASON_MAX) -> str:
    """``value`` as UTF-8-encodable text of at most ``limit`` characters (:data:`REASON_MAX`).

    Anything that cannot be encoded (a lone surrogate) becomes ``?``.
    """
    return str(value).encode("utf-8", "replace").decode("utf-8")[:limit]


__all__ = ["REASON_MAX", "SOCKET_ID_MAX", "SOCKET_LOG_MAX", "clean_reason"]
