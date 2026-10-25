"""Text that comes from the ComfyUI GPU gate, made safe to keep and to send.

A refusal's reason is written inside the img container, so hal0 treats it as
untrusted. It reaches ``/status`` and ``/arbiter`` JSON, error envelopes, the
``hal0.error`` log and Sentry, none of which can carry a lone surrogate
(``"\\ud800"`` is valid JSON and not valid UTF-8: encoding it raises), and none of which
should hold an unbounded string.
"""

from __future__ import annotations

#: The longest reason hal0 keeps or repeats, in characters.
REASON_MAX = 500


def clean_reason(value: object) -> str:
    """``value`` as UTF-8-encodable text of at most :data:`REASON_MAX` characters.

    Anything that cannot be encoded (a lone surrogate) becomes ``?``.
    """
    return str(value).encode("utf-8", "replace").decode("utf-8")[:REASON_MAX]


__all__ = ["REASON_MAX", "clean_reason"]
