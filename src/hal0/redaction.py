"""Dependency-free secret redaction primitives (issues #553, #1523).

This module is the *bottom* of the redaction stack: it imports nothing from
hal0 and may therefore be used from any layer, including ``hal0.events`` and
``hal0.activity``, which sit below the API package and cannot import
:mod:`hal0.api._redact` without a cycle.

Two complementary strategies live here:

``redact_log_line``
    TEXT scanning. For free-text where there is no key/value structure to
    walk — journald lines, exception strings, event messages. Matches on
    the *shape* of the secret (``Authorization: Bearer …``,
    ``HAL0_BEARER_TOKEN=…``, a long ``client_id=…``, ``<NAME>_KEY=…``) and
    destroys only the token body, leaving the prefix so an operator reading
    a redacted log still sees a secret WAS present.

``redact_text_tree``
    The same text scan applied recursively to every string in a structured
    value, without touching keys, types, or shape. Structured ``data``
    blobs are half text: ``{"error": "HTTPStatusError: Bearer tok…"}`` hides
    a secret under an entirely innocent key name, so key-name redaction
    alone (:func:`hal0.api._redact.redact_config`) cannot see it.

Key-NAME redaction (``is_sensitive_key`` / ``redact_config`` /
``redact_value``) stays in :mod:`hal0.api._redact`, which re-exports
everything here so existing importers keep working unchanged.
"""

from __future__ import annotations

import re
from typing import Any, Final

# Plain sentinel for masked values. Exposed so a caller can grep logs /
# fixtures for the exact token.
MASK: Final[str] = "***REDACTED***"

# Compiled once at import time. Each alternative ends with a
# ``(?P<...>...)`` capture of just the secret token; ``redact_log_line``
# rewrites that token to :data:`MASK` while leaving the surrounding
# ``Authorization:``, ``Bearer``, ``HAL0_BEARER_TOKEN=``, ``client_id=``,
# or ``<NAME>_KEY=``/``KEY=`` prefix in place so an operator reading a
# redacted log still sees a secret WAS present. Case-insensitive; the
# explicit alternatives are ordered most-to-least specific so the
# precise header form wins over the bare ``Bearer`` fallback (Python's
# ``re`` alternation is leftmost-wins inside a single match).
#
# The ``client_id=`` alternative is length-gated (16+ chars) so it
# doesn't mask the short, non-secret labels client_id legitimately takes
# (``anonymous``, the 12-hex-char hash). The ``<NAME>_KEY=``/``KEY=``
# alternative mirrors is_sensitive_key's ``_KEY$``/``^KEY$`` suffix rule
# so hal0's own admin/client keys (HAL0_ADMIN_KEY, HAL0_CLIENT_KEY, ...)
# are caught if one is ever stamped into a log line verbatim, not just
# in structured config — same conservative "over-redact" posture as
# is_sensitive_key.
LOG_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix_auth>Authorization:\s*Bearer\s+)(?P<auth_token>\S+)"
    r"|(?P<prefix_env>HAL0_BEARER_TOKEN=)(?P<env_token>\S+)"
    r"|(?P<prefix_bearer>Bearer\s+)(?P<bearer_token>[A-Za-z0-9_\-\.]+)"
    r"|(?P<prefix_client_id>client_id=)(?P<client_id_token>[A-Za-z0-9_\-\.]{16,})"
    r"|(?P<prefix_key>\b(?:[A-Za-z][A-Za-z0-9_]*_KEY|KEY)=)(?P<key_token>\S+)",
    re.IGNORECASE,
)


def redact_log_line(line: str) -> str:
    """Replace Bearer / HAL0_BEARER_TOKEN / long client_id / ``*_KEY=``
    secrets in ``line`` with :data:`MASK`, then run the shared shape pass
    (:func:`redact_secret_shapes`, #2403): secret-named ``NAME=value`` and
    ``NAME: value`` such as ``HF_TOKEN=``, ``apikey=`` and ``"apiKey":``,
    URL userinfo, ``--token`` flags and well-known token prefixes.

    The prefix is preserved so an operator reading a redacted log still
    sees that an Authorization header (or client_id / ``*_KEY`` field)
    was present — only the token body is destroyed. For free-text log
    lines; contrast with ``redact_config``, which walks structured
    dict/list trees by key name.

    Idempotent: :data:`MASK` contains no character that any alternative
    matches, so re-running over already-redacted text is a no-op. That
    matters because the same string can cross more than one seam
    (emit-time redaction, then again at the durable write).
    """

    def _sub(match: re.Match[str]) -> str:
        groups = match.groupdict()
        for prefix_group in ("prefix_auth", "prefix_env", "prefix_client_id", "prefix_key"):
            if groups[prefix_group] is not None:
                return f"{groups[prefix_group]}{MASK}"
        return f"{groups['prefix_bearer']}{MASK}"

    return redact_secret_shapes(LOG_SECRET_RE.sub(_sub, line))


def redact_text_tree(value: Any) -> Any:
    """Apply :func:`redact_log_line` to every string inside ``value``.

    Walks dicts (values only — keys are structural and never carry the
    secret body), lists, and tuples; returns scalars untouched. Shape,
    types, key names, and ordering are preserved exactly, because
    consumers route on structured fields: ``data["slot"]`` picks the slot
    a journal entry belongs to, ``data["model"]`` the activity target.
    A redaction that reshaped those would break routing rather than
    protect it.

    Pure — does not mutate the input. Complements key-name redaction
    rather than replacing it: this catches a secret hiding in the VALUE
    under an innocent key, that one catches a secret whose KEY names it.
    """
    if isinstance(value, str):
        return redact_log_line(value)
    if isinstance(value, dict):
        return {k: redact_text_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_text_tree(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_text_tree(v) for v in value)
    return value


# ── Shareable-text redaction (#2409) ────────────────────────────────────────
#
# The Python port of installer/lib/failure-report.sh's text pass, for text
# that leaves the box as a file (the doctor bundle's copy of the install log
# and failure report). Two steps, as in the shell:
#
#   1. literal harvest: the value of every secret-named ``NAME=value`` /
#      ``NAME: value`` (optionally quoted name and value) that is a
#      plausible secret is masked wherever else it appears in the text;
#   2. shape pass, line by line (:func:`redact_secret_shapes`): auth
#      schemes, URL userinfo, ``NAME[=:]value``, ``--token``-style flags,
#      long ``client_id=`` values and well-known token prefixes.
#
# _SECRET_NAME mirrors failure-report.sh's _HAL0_REPORT_TEXT_NAME_RE: a
# name containing a secret word (the KEY words with an optional ``_``/``-``
# separator, #2384), ending in ``_KEY``, or the bare word ``key``. Unlike
# the shell pass, a name whose only secret word is a benign one
# (``max_tokens``, ``tokenizer``, ``token_count``, ``passed``) or that names
# where a secret lives (``*_env``, ``*_file``, ``*_path``, ``*_dir``) is left
# alone: :func:`redact_log_line` runs this on every live log line.
_SECRET_NAME: Final[str] = (
    r"[A-Za-z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASS|API[_-]?KEY|ACCESS[_-]?KEY"
    r"|PRIVATE[_-]?KEY|ENCRYPTION[_-]?KEY|SALT)[A-Za-z0-9_]*|(?:[A-Za-z0-9_]*_)?KEY"
)
_SECRET_NAME_RE: Final[re.Pattern[str]] = re.compile(_SECRET_NAME, re.IGNORECASE)
_BENIGN_NAME_PART_RE: Final[re.Pattern[str]] = re.compile(
    r"tokens|tokenizer|token_?count|pass(?=[a-z])(?!w(?:or)?d|phrase)", re.IGNORECASE
)
_SECRET_REF_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"_(?:env|file|path|dir)$", re.IGNORECASE
)

# ``NAME`` + separator, then the value: double-quoted, single-quoted or bare.
_NAME_VALUE_SHAPE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])(?P<name>" + _SECRET_NAME + r")"
    r"(?P<sep>[\"']?\s*[=:]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\"'\s,}&]+)",
    re.IGNORECASE,
)

# Ordered (pattern, replacement) pairs, as in _hal0_report_mask_patterns.
_SHAPE_RULES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"(authorization:\s*(?:basic|token)\s+)[^\s'\"]+", re.I), rf"\g<1>{MASK}"),
    (re.compile(r"(bearer\s+)[A-Za-z0-9._~+/=-]+", re.I), rf"\g<1>{MASK}"),
    (re.compile(r"([a-z][a-z0-9+.-]*://[^/@\s:]*):[^/@\s]+@", re.I), rf"\g<1>:{MASK}@"),
    (re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s:]{16,}@", re.I), rf"\g<1>{MASK}@"),
    (
        re.compile(
            r"(--[A-Za-z0-9-]*(?:secret|token|password|pass|api-key|apikey|private-key)"
            r"[A-Za-z0-9-]*(?:=|\s+))[^-\s]\S*",
            re.I,
        ),
        rf"\g<1>{MASK}",
    ),
    (re.compile(r"(client_id=)[A-Za-z0-9_.-]{16,}", re.I), rf"\g<1>{MASK}"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), MASK),
    (re.compile(r"(?:hf_|sk-|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{20,}"), MASK),
    (re.compile(r"AKIA[0-9A-Z]{16}"), MASK),
)


def _is_secret_name(name: str) -> bool:
    """True if ``name`` (as matched by ``_SECRET_NAME``) names a secret value
    rather than a count, a tokenizer, or where a secret is stored."""
    name = name.lstrip("-")
    if _SECRET_REF_SUFFIX_RE.search(name):
        return False
    return bool(_SECRET_NAME_RE.fullmatch(_BENIGN_NAME_PART_RE.sub("", name)))


def _mask_name_value(match: re.Match[str]) -> str:
    if not _is_secret_name(match.group("name")):
        return match.group(0)
    value = match.group("value")
    quote = value[0] if value[0] in "\"'" else ""
    return f"{match.group('name')}{match.group('sep')}{quote}{MASK}{quote}"


def redact_secret_shapes(line: str) -> str:
    """Mask secret SHAPES in one line of free text (no literal known in
    advance). Keeps each key's name, quoting and separator so a reader still
    sees a secret WAS present. Idempotent."""
    for pattern, repl in _SHAPE_RULES[:4]:
        line = pattern.sub(repl, line)
    line = _NAME_VALUE_SHAPE_RE.sub(_mask_name_value, line)
    for pattern, repl in _SHAPE_RULES[4:]:
        line = pattern.sub(repl, line)
    return line


_ENV_NAME_VALUE_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
_NOT_SECRET_WORDS: Final[frozenset[str]] = frozenset({"true", "false", "none", "null", "unset"})


def _harvest_secret_literals(text: str) -> list[str]:
    """Plausible secret values of ``NAME[=:]value`` in ``text``, longest
    first. Same filter as failure-report.sh's
    _hal0_report_text_value_is_secret: at least 8 characters, not purely
    numeric, not an env-var name, and not under the bare field name ``key``
    (in log text it names a setting, not a credential)."""
    found: set[str] = set()
    for match in _NAME_VALUE_SHAPE_RE.finditer(text):
        name = match.group("name").lstrip("-")
        if name.lower() == "key" or not _is_secret_name(name):
            continue
        value = match.group("value").strip("\"'").strip()
        if (
            len(value) < 8
            or value.isdigit()
            or _ENV_NAME_VALUE_RE.fullmatch(value)
            or value.lower() in _NOT_SECRET_WORDS
            or value in MASK
        ):
            continue
        found.add(value)
    return sorted(found, key=len, reverse=True)


def redact_shareable_text(text: str) -> str:
    """Redact free text that is about to be written to a shareable file.

    Every plausible secret value seen as ``NAME=value`` / ``NAME: value`` is
    masked wherever it appears (longest first, so a secret containing
    another is masked whole), then :func:`redact_secret_shapes` runs on each
    line. The Python counterpart of installer/lib/failure-report.sh's text
    pass. Idempotent.
    """
    for literal in _harvest_secret_literals(text):
        text = text.replace(literal, MASK)
    return "\n".join(redact_secret_shapes(line) for line in text.split("\n"))


__all__ = [
    "LOG_SECRET_RE",
    "MASK",
    "redact_log_line",
    "redact_secret_shapes",
    "redact_shareable_text",
    "redact_text_tree",
]
