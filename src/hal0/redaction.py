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
from collections.abc import Callable, Iterator
from typing import Any, Final, NamedTuple

# Plain sentinel for masked values. Exposed so a caller can grep logs /
# fixtures for the exact token.
MASK: Final[str] = "***REDACTED***"

# Where a ``*_KEY=`` name may start in LOG_SECRET_RE: after anything that is no
# part of a name run. The run is the ASCII word characters, with the four
# non-ASCII letters that ``re.I`` folds onto them (U+0130, U+0131, U+017F and
# U+212A, the ones ``[A-Za-z]`` matches under it), the same class the other
# name rules use
# (#2599). Not ``\b``: that reads every Unicode letter and digit as part of
# the word, which ui/src/sentry.ts cannot reproduce with its ASCII ``\b``.
_NAME_START: Final[str] = r"(?<![A-Za-z0-9_])"

# Compiled once at import time. Each alternative ends where the secret
# token starts, or with a ``(?P<...>...)`` capture of it; ``redact_log_line``
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
#
# The ``Authorization: Bearer``, ``HAL0_BEARER_TOKEN=`` and ``*_KEY=``
# alternatives match up to their value only (a lookahead checks there is
# one): the value runs to the next whitespace except where a second secret
# is packed after a ``;`` or ``,``, which :func:`_bare_value_end` decides
# (#2529).
LOG_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix_auth>Authorization:\s*Bearer\s+)(?=\S)"
    r"|(?P<prefix_env>HAL0_BEARER_TOKEN=)(?=\S)"
    r"|(?P<prefix_bearer>Bearer\s+)(?P<bearer_token>[A-Za-z0-9_\-\.]+)"
    r"|(?P<prefix_client_id>client_id=)(?P<client_id_token>[A-Za-z0-9_\-\.]{16,})"
    rf"|(?P<prefix_key>{_NAME_START}(?:[A-Za-z][A-Za-z0-9_]*_KEY|KEY)=)(?=\S)",
    re.IGNORECASE,
)
# A LOG_SECRET_RE bare value: its characters other than a packed separator,
# and the separators (see _ValueClass).
_LOG_VALUE: Final[tuple[re.Pattern[str], re.Pattern[str]]] = (
    re.compile(r"[^\s;,]*"),
    re.compile(r"[;,]"),
)
# A LOG_SECRET_RE Bearer token or client_id: no separator ends it early.
_LOG_TOKEN: Final[tuple[re.Pattern[str], re.Pattern[str]]] = (
    re.compile(r"[A-Za-z0-9_\-\.]*", re.IGNORECASE),
    re.compile(r"(?!)"),
)
# Where a LOG_SECRET_RE secret with a value that may follow whitespace starts,
# up to that value (Bearer tokens and long client_ids hold neither whitespace
# nor ``;``/``,``, so a value that swallows one still masks it).
_LOG_HEAD_RE: Final[re.Pattern[str]] = re.compile(
    r"Authorization:\s*Bearer\s+(?=\S)|HAL0_BEARER_TOKEN=(?=\S)"
    rf"|{_NAME_START}(?:[A-Za-z][A-Za-z0-9_]*_KEY|KEY)=(?=\S)",
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

    A second secret packed after a ``;`` or ``,`` (``X_KEY=a;Bearer b``)
    is masked on its own and keeps its name (#2529). One that a value takes
    in some other way, after an empty value (``KEY=  X_KEY= v``), glued on
    (``Bearer a.Authorization: Token b``) or past a quote that does not
    close where it should (``--api-key="v, db-password": w``), is masked
    with that value (#2593-#2595).
    """
    out: list[str] = []
    pos = at = 0
    while (match := LOG_SECRET_RE.search(line, at)) is not None:
        token = match.lastgroup
        if token in ("bearer_token", "client_id_token"):
            # These tokens hold no ``;`` or ``,``: the match ends with them,
            # or past a secret they swallow (#2595).
            start, end = match.span(token)
            end = _swallowing_end(line, start, end, _LOG_TOKEN, log=True)
        else:
            start = match.end()  # the lookahead saw a character there
            if _delimiter(line, start):
                end = _quoted_log_value_end(line, start)
            else:
                end = _bare_value_end(line, start, _LOG_VALUE, log=True)
        if end > start:
            out += (line[pos:start], MASK)
            pos = end
        at = end
    out.append(line[pos:])
    return _redact_shapes("".join(out), log=True)


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
# (``max_tokens``, ``tokenizer``, ``token_count``, ``passed``; plural
# ``tokens`` only with a count qualifier, #2466) or that names
# where a secret lives (``*_env``, ``*_file``, ``*_path``, ``*_dir``) is left
# alone: :func:`redact_log_line` runs this on every live log line.
#
# A name runs over ``-`` as over ``_``, before and after the secret word, so
# ``api-token-prod``, ``github-token-ci`` and ``x-api-key-v2`` are names, and
# ``max-tokens`` or ``api-token-file`` are judged whole (#2488).
_SECRET_WORD: Final[str] = (
    r"(?:SECRET|TOKEN|PASSWORD|PASS|API[_-]?KEY|ACCESS[_-]?KEY"
    r"|PRIVATE[_-]?KEY|ENCRYPTION[_-]?KEY|SALT)"
)
_SECRET_NAME: Final[str] = (
    r"[A-Za-z0-9_-]*" + _SECRET_WORD + r"[A-Za-z0-9_-]*|(?:[A-Za-z0-9_-]*[_-])?KEY"
)
_SECRET_NAME_RE: Final[re.Pattern[str]] = re.compile(_SECRET_NAME, re.IGNORECASE)
# Plural ``tokens`` is a count only with a count qualifier (#2466):
#
# * a count word right before it (``max_tokens``, ``extraction_max_tokens``,
#   ``prompt_tokens``, ``maxTokens``, ``HAL0_MAX_TOKENS``);
# * a weaker word that also names credential stores (``new``, ``cached``,
#   ``tool``, ``draft``, ``content``, ``thinking``, ...) only at the start of
#   the name or right after a count word (``cached_tokens``, ``draft_tokens``,
#   ``max_new_tokens``, ``max_thinking_tokens``), so ``github_new_tokens``,
#   ``oauth_cached_tokens``, ``mcp_tool_tokens``, ``cms_draft_tokens`` and
#   ``api_content_tokens`` stay secret (#2488);
# * a count suffix right after it: ``tokens_count``, or ``tokens_per_<unit>``
#   for a time or count unit only (``api_tokens_per_host`` stays secret);
# * the whole name ``tokens_in``, ``tokens_out``, ``tokens_completed``, ...
#   (``api_tokens_in`` stays secret).
#
# Unqualified, ``api_tokens``, ``auth_tokens``, ``tokens_by_host`` or bare
# ``tokens`` name a list of secrets. A word starts at the name's start, after
# ``_`` or at a camelCase hump (a lowercase letter or digit, then a capital;
# in ``API_TOKENS_PER_KEY`` the ``K`` is not a word end). ``tokens`` itself
# is ``tokens``, ``Tokens`` or ``TOKENS``, so ``apiTokenSCount`` is a token
# name, as the installer reads it. Only the qualified part is removed, so any
# other secret word left in the name (``max_tokens_secret``) still counts.
_TOKENS_COUNT_WORD: Final[str] = (
    r"(?:max|min|num|n|total|prompt|completion|context|ctx|input|output|text|image"
    r"|audio|video|budget|requested|expected|generated|reasoning|remaining"
    r"|floor|prediction|predicted)"
)
_TOKENS_WEAK_COUNT_WORD: Final[str] = (
    r"(?:new|cache|cached|tool_?call|tool_?response|tool|used|extra|draft|content"
    r"|thinking|generation)"
)
_TOKENS_RATE_UNIT: Final[str] = (
    r"(?:s|sec|second|ms|min|minute|hour|request|req|iteration|iter|step|1k|k)"
)
_TOKENS_WHOLE_NAME_SUFFIX: Final[str] = r"(?:in|out|completed|predicted|evaluated|cached|used)"
_TOKENS_WORD: Final[str] = r"(?-i:[Tt]okens|TOKENS)"
_WORD_START: Final[str] = r"(?:(?<![a-z0-9])|(?-i:(?<=[a-z0-9])(?=[A-Z])))"
_WORD_END: Final[str] = r"(?:(?![a-z0-9])|(?-i:(?<=[a-z0-9])(?=[A-Z])))"
_BENIGN_NAME_PART_RE: Final[re.Pattern[str]] = re.compile(
    rf"{_WORD_START}{_TOKENS_COUNT_WORD}_?(?:{_TOKENS_WEAK_COUNT_WORD}_?)?{_TOKENS_WORD}{_WORD_END}"
    rf"|^{_TOKENS_WEAK_COUNT_WORD}_?{_TOKENS_WORD}{_WORD_END}"
    rf"|{_WORD_START}{_TOKENS_WORD}_?(?:count|per_?{_TOKENS_RATE_UNIT}){_WORD_END}"
    rf"|^{_TOKENS_WORD}_?{_TOKENS_WHOLE_NAME_SUFFIX}$"
    r"|tokenizer|token_?count|pass(?=[a-z])(?!w(?:or)?d|phrase)",
    re.IGNORECASE,
)
_SECRET_REF_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"_(?:env|file|path|dir)$", re.IGNORECASE
)
# A name longer than this is judged as written, with no count part removed,
# as in the installer, whose strip is quadratic in bash: such a name is
# masked whenever it holds a secret word at all.
_NAME_JUDGED_WHOLE_MAX: Final[int] = 128

# Linear time on any line (#2501). The rules below never backtrack over a
# run of name characters, because on a long run with no match that is
# quadratic or worse: 80 KB of ``api_token_`` took 21 s, and ``--token-x-``
# far longer. Two devices keep each rule's matches exactly as before:
#
# * a run is scanned possessively (``*+``) when the character that must
#   follow it cannot be part of the run, so giving characters back could
#   never help;
# * a rule that may start anywhere inside a run tries only at the run's
#   start. Which characters it then needs depends only on where the run
#   ends, so the leftmost start the old rule would have taken is the only
#   one worth trying: the new rule skips to it inside its first group,
#   which the replacement keeps.
_NAME_RUN: Final[str] = r"[A-Za-z0-9_-]"

# ``NAME`` + separator, up to the value: double-quoted, single-quoted (closed
# or not, #2593) or bare, which a lookahead checks for and
# :func:`_name_values` reads. The name starts at a word start, so a
# hyphenated name is matched whole. The separator cannot start with a name
# character, so the name is the whole run, and _SECRET_NAME is tested once
# on it by a lookahead (#2501).
_NAME_SEP_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?<!{_NAME_RUN})(?P<name>(?={_NAME_RUN}*?{_SECRET_WORD}"
    rf"|(?:{_NAME_RUN}*[_-])?KEY(?!{_NAME_RUN})){_NAME_RUN}*+)"
    r"(?P<sep>(?:\\*+[\"'])?\s*[=:]\s*)"
    r"(?=[^\s,}&])",
    re.IGNORECASE,
)
# A bare NAME[=:]value value: its characters other than a packed ``;``, and
# that separator (see _ValueClass).
_NAME_VALUE: Final[tuple[re.Pattern[str], re.Pattern[str]]] = (
    re.compile(r"[^\"'\s,}&;]*"),
    re.compile(";"),
)

# HTTP auth schemes whose name is kept when an ``Authorization`` value is
# masked; mirrors _HAL0_REPORT_AUTH_SCHEMES in installer/lib/failure-report.sh.
_AUTH_SCHEMES: Final[str] = (
    r"(?:basic|bearer|token|apikey|api-key|key|bot|ssws|negotiate|digest|ntlm"
    r"|oauth|hawk|dpop|aws4-hmac-sha256)"
)

# Opens group 1 at the start of a run of scheme characters and ends after
# ``://``; the run's leading non-letters are kept in the group (#2501).
_URL_SCHEME: Final[str] = r"((?<![a-z0-9+.-])[0-9+.-]*+[a-z][a-z0-9+.-]*+://"

# ``--token=`` / ``--api-key ``: up to a ``--flag`` value. The flag is the
# run's first ``--``, and the match starts at the run's start (#2501).
_FLAG_PREFIX: Final[str] = (
    r"(?<![A-Za-z0-9-])(?:[A-Za-z0-9]|-(?!-))*+"
    r"--(?=[A-Za-z0-9-]*?(?:secret|token|password|pass|api-key|apikey|private-key))"
    r"[A-Za-z0-9-]*+(?:=|\s++)"
)

# ── Packed secrets (#2529) ──────────────────────────────────────────────────
#
# A bare value runs to the next whitespace, so in ``X_KEY=abc;Bearer tok`` or
# ``A_TOKEN=x,HF_TOKEN= y`` the first value used to swallow the second
# secret's name, and the second value, after a space, was left visible. A
# bare value now ends before a ``;`` or ``,`` that starts another secret: one
# of the rules here whose value may come after whitespace (``Bearer``,
# ``Authorization``, a ``--flag``, a secret ``NAME[=:]``), maybe behind
# opening quotes or brackets. That secret is masked by its own rule, keeping
# its name. A separator followed by anything else stays in the value and is
# masked with it: a cut only ever leaves a secret's NAME outside a mask, and
# only where that secret's value is masked in turn (fail closed).
#
# A value class is a pair of patterns: ``chunk`` matches a run of the value's
# characters that never end it, ``sep`` one character that ends it only
# where a packed secret starts right after it.
_ValueClass = tuple[re.Pattern[str], re.Pattern[str]]
_PACKED_OPENERS_RE: Final[re.Pattern[str]] = re.compile(r"[\"'(\[{<]*")


def _value_class(chunk: str, sep: str = "[;,]") -> _ValueClass:
    return re.compile(chunk, re.I), re.compile(sep)


# A Bearer token: no separator ends it early.
_BEARER_TOKEN: Final[_ValueClass] = _value_class(r"[A-Za-z0-9._~+/=-]*", "(?!)")

# Whitespace that is no line break: a quoted value never starts on the next line.
_HSPACE: Final[str] = r"(?:(?!\n)\s)"
# A quote around an ``Authorization`` name or scheme: plain or escaped (#2616).
_AQ: Final[str] = r"(?:\\*+[\"'])?"
# Not at the start of a quoted value, plain or escaped: a bare value starts
# anywhere else, and the quoted-value rules read the rest (#2616).
_NOT_OPENER: Final[str] = r"(?!\\*+[\"'])"
# Up to the value of a secret that starts with a ``Bearer``, ``Authorization``
# or ``--flag`` keyword; a lookahead checks for the value's first character.
_PACKED_HEAD_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<bearer>bearer\s+(?=[A-Za-z0-9._~+/=-]|" + re.escape(MASK) + r"))"
    rf"|(?P<authorization>authorization{_AQ}\s*:\s*{_AQ}"
    + _AUTH_SCHEMES
    + rf"\s+{_NOT_OPENER}(?=[^\s\"',])"
    # A quoted value is read on one line (#2605), headers and all; a bare one may
    # follow a line break.
    + rf"|authorization{_AQ}{_HSPACE}*:{_HSPACE}*{_AQ}"
    + _AUTH_SCHEMES
    + rf"{_HSPACE}+(?=\\*+[\"']))"
    rf"|(?P<authorization_raw>authorization{_AQ}\s*:\s*{_AQ}"
    r"(?!" + _AUTH_SCHEMES + rf"(?:\s|$)){_NOT_OPENER}(?=[^\s\"',]{{8}}))"
    r"|(?P<flag>" + _FLAG_PREFIX + r"(?=[^-\s]))",
    re.I,
)
# The class each such secret's value is read with when another value has
# swallowed it (#2595): its own rule's, or a wider one. A Bearer token may
# already be masked; a raw Authorization value may hold spaces.
_AUTH_RAW_VALUE: Final[_ValueClass] = _value_class(r"[^\n\"',;]*", ";")
_SWALLOWED_VALUE: Final[dict[str | None, _ValueClass]] = {
    "bearer": _value_class(r"[A-Za-z0-9._~+/=*-]*", "(?!)"),
    "authorization": _value_class(r"[^\s'\";,]*"),
    "authorization_raw": _AUTH_RAW_VALUE,
    "flag": _LOG_VALUE,
}


def _head(line: str, pos: int, *, log: bool = False) -> tuple[int, _ValueClass] | None:
    """Where the value starts of a secret whose name or keyword starts at
    ``line[pos]``, and the class its value is read with, or None if no
    secret starts there. The secret is one the shape rules mask (or have
    masked) or, with ``log``, one that LOG_SECRET_RE masks."""
    head = _PACKED_HEAD_RE.match(line, pos)
    if head is not None:
        return head.end(), _SWALLOWED_VALUE[head.lastgroup]
    if log and (head := _LOG_HEAD_RE.match(line, pos)) is not None:
        return head.end(), _LOG_VALUE
    named = _NAME_SEP_RE.match(line, pos)
    if named is not None and _is_secret_name(named.group("name")):
        return named.end(), _NAME_VALUE
    return None


def _starts_secret(line: str, pos: int, *, log: bool = False) -> bool:
    """True if a secret that the shape rules mask (or have masked) starts at
    ``line[pos]``, after any opening quotes or brackets (#2529). With
    ``log``, also one that LOG_SECRET_RE masks: only its own pass may cut
    there, as the shape pass after it would not mask all of them."""
    pos = _PACKED_OPENERS_RE.match(line, pos).end()  # type: ignore[union-attr]
    return _head(line, pos, log=log) is not None


def _packed_value_end(line: str, start: int, value: _ValueClass, log: bool) -> int:
    """Where the bare value of class ``value`` starting at ``line[start]``
    ends before :func:`_bare_value_end` looks for a swallowed secret: at the
    first character that is neither in a chunk nor a separator with no
    secret starting after it (:func:`_starts_secret`, #2529). Every step
    moves forward and tests one separator, so this is linear in the value
    (#2501)."""
    chunk, sep = value
    end = start
    while True:
        end = chunk.match(line, end).end()  # type: ignore[union-attr]
        if sep.match(line, end) and not _starts_secret(line, end + 1, log=log):
            end += 1
            continue
        return end


# ── Swallowed secrets (#2593, #2594, #2595) ─────────────────────────────────
#
# A value can take in the start of another secret whose own value lies past
# the first value's end, so the second value was left visible:
#
# * an empty value then whitespace (#2594): in ``KEY=  X_KEY= v`` the value
#   of ``KEY`` runs to the next whitespace, so it is ``X_KEY=``;
# * no separator at all (#2595): in ``Bearer a.Authorization: Token b`` the
#   token runs on over ``Authorization``;
# * a quote that does not close where it should (#2593): in
#   ``--api-key="v, db-password": w`` the quoted value closes at the quote
#   after the next name.
#
# Cutting the first value before the second secret would not be safe: a name
# is a run of name characters and the redactor cannot tell where it begins
# (``KEY= abcX_KEY= v`` may be the value ``abc`` glued to the name
# ``X_KEY``), and a quoted value may really hold ``, db-password``. So the
# first mask is extended over the second secret's value instead, and nothing
# either reading takes for a value is left visible (fail closed). Only a
# benign NAME[=:]value (``max_tokens``), whose value is not masked, is cut
# before the secret, which then keeps its name.
#
# The swallowed value is read as its own rule reads it (:func:`_head`), or to
# its closing quote, and is extended in turn over a secret it swallows.

# Where a swallowed secret may start inside a value: a name, flag or word at
# the start of its run, or a keyword anywhere. :func:`_swallowed_head`
# decides.
_SWALLOW_AT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9_-])|(?=bearer|authorization|hal0_bearer_token|client_id)",
    re.I,
)
# A long ``client_id=``, which LOG_SECRET_RE and the shape rules mask. It
# holds no ``;``, ``,`` or whitespace, so it is no packed secret (#2529), but
# a value can swallow its name.
_CLIENT_ID_HEAD_RE: Final[re.Pattern[str]] = re.compile(r"client_id=(?=[A-Za-z0-9_.-]{16})", re.I)


def _swallowed_head(line: str, pos: int, *, log: bool) -> tuple[int, _ValueClass] | None:
    """:func:`_head`, or a long ``client_id=``."""
    client_id = _CLIENT_ID_HEAD_RE.match(line, pos)
    if client_id is not None:
        return client_id.end(), _LOG_TOKEN
    return _head(line, pos, log=log)


# What a quoted value that opens with each quote holds up to its closing quote
# or the end of its line: a run of characters that end neither, in which a
# backslash and the character after it (a ``\"``, a ``\\``) are one unit and
# end nothing. Either quote honours the escape: a shell's single quotes do not,
# but a JSON-ish dump or a log of an escaped value does, and reading ``\'`` as
# an escape can only mask more of the line, never less. A backslash right
# before the end of its line is taken too, so it is masked with the value. The
# two parts of each loop cover different characters, so the run is matched
# without backtracking.
_QUOTED_RUN: Final[dict[str, re.Pattern[str]]] = {
    '"': re.compile(r'[^"\\\n]*(?:\\[^\n]?[^"\\\n]*)*'),
    "'": re.compile(r"[^'\\\n]*(?:\\[^\n]?[^'\\\n]*)*"),
}
# A quoted value's opening delimiter: a quote, or, in text that was escaped to
# sit inside a string (a JSON value in a log message, a command line in a
# JSON field), backslashes and a quote. The closing delimiter is the same
# text, so ``\"a b\"`` and ``\\"a b\\"`` are values, each closed by its own kind
# (#2616). The backslashes are possessive: a run is read once.
_OPENER_RE: Final[re.Pattern[str]] = re.compile(r"\\*+[\"']")
# Where a delimiter of ``k`` backslashes and the quote ``q`` ends an escaped
# value: a quote after a run of exactly ``k`` backslashes, or a line break.
# The lookbehind lets a run be matched from its start only.
_ESCAPED_CLOSE: Final[dict[str, re.Pattern[str]]] = {
    '"': re.compile(r'(?<!\\)(\\*+)"|\n'),
    "'": re.compile(r"(?<!\\)(\\*+)'|\n"),
}


def _delimiter(line: str, pos: int) -> str:
    """The opening delimiter of the quoted value that starts at ``line[pos]``
    (a quote, or backslashes and a quote), or ``""`` if none does."""
    match = _OPENER_RE.match(line, pos)
    return match.group() if match else ""


def _quote_close(line: str, start: int) -> tuple[int, bool]:
    """Where the quoted value that opens at ``line[start]`` (see
    :func:`_delimiter`; it must open one) ends, and whether it closes there: at
    its closing delimiter on the same line or, if it has none, at the end of
    its line. A plain quote is closed by a quote that no backslash escapes
    (#2604); an escaped one by the same delimiter, a quote after exactly as
    many backslashes, so a longer run inside it does not close it (#2616). An
    unclosed value masks the rest of its line (#2593), and never pairs with a
    delimiter on a later one. The scan stops at the value's end, which the
    caller then moves past, so it is linear (#2501)."""
    delim = _delimiter(line, start)
    quote = delim[-1]
    if len(delim) == 1:
        end = _QUOTED_RUN[quote].match(line, start + 1).end()  # type: ignore[union-attr]
        return end, end < len(line) and line[end] == quote
    for match in _ESCAPED_CLOSE[quote].finditer(line, start + len(delim)):
        if match.group() == "\n":
            return match.start(), False
        if match.end() - match.start() == len(delim):
            return match.start(), True
    return len(line), False


def _value_stop(line: str, start: int, value: _ValueClass, log: bool) -> int:
    """:func:`_packed_value_end`, less trailing whitespace (only a raw
    Authorization value holds any)."""
    end = _packed_value_end(line, start, value, log)
    return start + len(line[start:end].rstrip())


_Swallowed = tuple[int, int, _ValueClass]
# A part of a masked value: start, end, and whether the literal harvest
# takes it (:func:`_past_swallowed`).
_Span = tuple[int, int, bool]


def _swallowed(
    line: str, start: int, end: int, *, log: bool, secret: bool = True
) -> _Swallowed | None:
    """``(pos, value_start, value_class)`` (:func:`_head`) of a secret that
    starts at ``line[pos]`` inside the value ``line[start:end]`` and whose
    own value does not end inside it: it starts at or after ``end``, or,
    read as its own rule reads it, runs on past ``end``. Of those, the one
    whose value reaches furthest, then the first: names can nest
    (``-X_KEY=`` inside ``a-X_KEY=``), and the mask must cover the longest
    reading. In a value that is not ``secret``, and so is not masked, the
    first secret counts, whatever its value.

    Linear in the value (#2501): each candidate is tested once, and a value
    read from inside one already read ends where that one does, so each
    class reads each character at most once."""
    read: dict[int, tuple[int, int]] = {}  # id(class) -> the last value read
    found: _Swallowed | None = None
    reach = -1
    for at in _SWALLOW_AT_RE.finditer(line, start, end):
        head = _swallowed_head(line, at.start(), log=log)
        if head is None:
            continue
        value_start, value = head
        if not secret:
            return at.start(), value_start, value
        if _delimiter(line, value_start):
            stop = _quote_close(line, value_start)[0]
            if stop < end:
                continue
        else:
            last_start, last_end = read.get(id(value), (-1, -1))
            if not last_start <= value_start <= last_end:
                last_start, last_end = value_start, _value_stop(line, value_start, value, log)
                read[id(value)] = (last_start, last_end)
            stop = last_end
            if value_start < end and stop <= end:
                continue
        if stop > reach:
            found, reach = (at.start(), value_start, value), stop
    return found


def _past_swallowed(
    line: str,
    start: int,
    swallowed: _Swallowed,
    outer: _ValueClass | None,
    *,
    log: bool,
    secret: bool,
    spans: list[_Span],
) -> int:
    """Where a value starting at ``line[start]``, of class ``outer`` (None
    when quoted), that swallowed a secret ends. If it is not ``secret`` that
    is where the secret starts. Otherwise it runs past the secret's value,
    read as its own rule reads it, and then on as each value it was inside
    would run, so the end is one that redacting the masked text again keeps
    (idempotence). A secret swallowed on the way is passed over in turn.

    Each part of the mask is added to ``spans``. The literal harvest takes
    the parts of a swallowed ``NAME[=:]value`` value, but not the first
    value's own part: that may end in a name (``KEY= HF_TOKEN=a.b_token=
    v``), and masking it elsewhere would hide that name."""
    pos, value_start, value = swallowed
    if not secret:
        return pos
    spans.append((start, pos, False))
    # The values to run on with once the current one ends, innermost last,
    # each with whether it is a swallowed NAME[=:]value value.
    enclosing: list[tuple[_ValueClass, bool]] = [] if outer is None else [(outer, False)]
    harvest = value is _NAME_VALUE
    while True:
        if delim := _delimiter(line, value_start):
            close, closed = _quote_close(line, value_start)
            end = close + len(delim) * closed
            nested = _swallowed(line, value_start + len(delim), close, log=log)
        else:
            end = _value_stop(line, value_start, value, log)
            nested = _swallowed(line, value_start, end, log=log)
            if nested is not None:
                enclosing.append((value, harvest))
        while nested is None and enclosing:
            if harvest:
                spans.append((value_start, end, True))
            value_start, (value, harvest) = end, enclosing.pop()
            end = _value_stop(line, value_start, value, log)
            nested = _swallowed(line, value_start, end, log=log)
            if nested is not None:
                enclosing.append((value, harvest))
        if nested is None:
            if harvest:
                spans.append((value_start, end, True))
            return end
        if harvest:
            spans.append((value_start, nested[0], True))
        _pos, value_start, value = nested
        harvest = value is _NAME_VALUE


def _bare_value_end(
    line: str,
    start: int,
    value: _ValueClass,
    *,
    log: bool = False,
    secret: bool = True,
    spans: list[_Span] | None = None,
) -> int:
    """Where the bare value of class ``value`` starting at ``line[start]``
    ends: before a packed secret (:func:`_packed_value_end`), or past the
    value of a secret it swallows (:func:`_past_swallowed`)."""
    end = _packed_value_end(line, start, value, log)
    return _swallowing_end(line, start, end, value, log=log, secret=secret, spans=spans)


def _swallowing_end(
    line: str,
    start: int,
    end: int,
    value: _ValueClass | None,
    *,
    log: bool,
    secret: bool = True,
    spans: list[_Span] | None = None,
) -> int:
    """``end``, or past the value of a secret that the value
    ``line[start:end]``, of class ``value``, swallows
    (:func:`_past_swallowed`)."""
    swallowed = _swallowed(line, start, end, log=log, secret=secret)
    if swallowed is None:
        return end
    return _past_swallowed(
        line,
        start,
        swallowed,
        value,
        log=log,
        secret=secret,
        spans=[] if spans is None else spans,
    )


def _quoted_value_end(
    line: str,
    start: int,
    *,
    log: bool = False,
    secret: bool = True,
    spans: list[_Span] | None = None,
) -> tuple[int, bool]:
    """Where the quoted value that opens at ``line[start]`` ends, and whether
    its delimiter closes: past its closing delimiter or, if it has none, at the
    end of its line, and past the value of a secret it swallows
    (:func:`_past_swallowed`, #2593). The quoted text is masked whole, spaces
    and all (#2598)."""
    delim = _delimiter(line, start)
    close, closed = _quote_close(line, start)
    end = close + len(delim) * closed
    swallowed = _swallowed(line, start + len(delim), close, log=log, secret=secret)
    if swallowed is not None:
        end = _past_swallowed(
            line,
            start,
            swallowed,
            None,
            log=log,
            secret=secret,
            spans=[] if spans is None else spans,
        )
    return end, closed


def _quoted_log_value_end(line: str, start: int, *, log: bool = True) -> int:
    """:func:`_quoted_value_end` for a bare-style value that opens with a quote,
    then on as a bare value runs on from there: over characters glued to the
    closing quote (``api_key='a b'c`` masks ``'a b'c``) and a ``,`` or ``;``
    that starts no secret, so that masking the result again changes nothing."""
    end, _closed = _quoted_value_end(line, start, log=log)
    return _bare_value_end(line, end, _LOG_VALUE, log=log)


def _mask_bare_values(
    pattern: re.Pattern[str],
    value: _ValueClass,
    spaces: bool,
    line: str,
    log: bool,
    quoted: bool = False,
) -> str:
    """``pattern`` matches up to a bare value; mask each such value, as
    :func:`_bare_value_end` bounds it, and keep the match. With ``spaces``
    the value may hold whitespace, but does not end with any. With ``quoted``
    a value that opens with a quote runs to its closing quote, quotes and all
    (:func:`_quoted_log_value_end`)."""
    out: list[str] = []
    pos = at = 0
    while (match := pattern.search(line, at)) is not None:
        start = match.end()
        if quoted and _delimiter(line, start):
            end = at = _quoted_log_value_end(line, start, log=log)
        else:
            end = at = _bare_value_end(line, start, value, log=log)
        if spaces:
            end = start + len(line[start:end].rstrip())
        if end > start:
            out += (line[pos:start], MASK)
            pos = end
    out.append(line[pos:])
    return "".join(out)


def _mask_quoted_values(pattern: re.Pattern[str], line: str, log: bool) -> str:
    """``pattern`` matches up to a value that opens with a quote; mask each
    such value as :func:`_quoted_value_end` bounds it, and keep the match and
    the value's quotes (#2605)."""
    out: list[str] = []
    pos = at = 0
    while (match := pattern.search(line, at)) is not None:
        start = match.end()
        end, closed = _quoted_value_end(line, start, log=log)
        delim = _delimiter(line, start)
        out += (line[pos:start], delim, MASK, delim if closed else "")
        pos = at = end
    out.append(line[pos:])
    return "".join(out)


# A shape rule: ``rule(line, log)``, ``log`` as in :func:`_starts_secret`.
_Rule = Callable[[str, bool], str]


def _sub_rule(pattern: re.Pattern[str], repl: str) -> _Rule:
    return lambda line, log: pattern.sub(repl, line)


def _bare_value_rule(
    prefix: str, value: _ValueClass, *, spaces: bool = False, quoted: bool = False
) -> _Rule:
    """A rule that masks the value after ``prefix`` (a regex ending in a
    lookahead for the value's first character), up to a packed secret."""
    pattern = re.compile(prefix, re.I)
    return lambda line, log: _mask_bare_values(pattern, value, spaces, line, log, quoted)


# Up to a quoted value after an ``Authorization`` scheme (the bare-value rules
# below need a first character that is no quote). The whitespace is horizontal,
# so a header that ends a line never takes the quoted text on the next one.
_AUTH_QUOTED_RE: Final[re.Pattern[str]] = re.compile(
    rf"authorization{_AQ}{_HSPACE}*:{_HSPACE}*{_AQ}" + _AUTH_SCHEMES + rf"{_HSPACE}+(?=\\*+[\"'])",
    re.I,
)

# Ordered rules, as in _hal0_report_mask_patterns: these run before the
# NAME[=:]value pass, _SHAPE_RULES_AFTER after it.
_SHAPE_RULES: Final[tuple[_Rule, ...]] = (
    # An ``Authorization`` value that opens with a quote, after a known
    # scheme (#2605): the quoted text is the credential, spaces and all.
    lambda line, log: _mask_quoted_values(_AUTH_QUOTED_RE, line, log),
    _bare_value_rule(
        r"authorization:\s*(?:basic|token)\s+" + _NOT_OPENER + r"(?=[^\s'\"])",
        _value_class(r"[^\s'\";,]*"),
    ),
    _bare_value_rule(r"bearer\s+(?=[A-Za-z0-9._~+/=-])", _BEARER_TOKEN),
    # Any other ``Authorization`` value (#2410). A known scheme is kept and
    # the credential after it masked. Anything else of 8+ characters is
    # treated as a raw credential and masked to the end of the header value,
    # so a raw key followed by more words is never taken for a scheme; a
    # short word such as ``denied`` is left alone. Already-masked values
    # re-mask to the same text. A raw value holds spaces, so a secret after
    # one (``rawkey HF_TOKEN"=v``) is swallowed, and masked with it (#2595).
    _bare_value_rule(
        rf"authorization{_AQ}\s*:\s*{_AQ}" + _AUTH_SCHEMES + rf"\s+{_NOT_OPENER}(?=[^\s\"',])",
        _value_class(r"[^\s\"',;]*", ";"),
    ),
    _bare_value_rule(
        rf"authorization{_AQ}\s*:\s*{_AQ}"
        r"(?!" + _AUTH_SCHEMES + rf"(?:\s|$)){_NOT_OPENER}(?=[^\s\"',]{{8,}})",
        _AUTH_RAW_VALUE,
        spaces=True,
    ),
    # URL userinfo. The scheme starts at the first letter of its run.
    _sub_rule(re.compile(_URL_SCHEME + r"[^/@\s:]*+):[^/@\s]++@", re.I), rf"\g<1>:{MASK}@"),
    _sub_rule(re.compile(_URL_SCHEME + r")[^/@\s:]{16,}+@", re.I), rf"\g<1>{MASK}@"),
)
# Up to the escaped quoted value of a ``--secret`` flag.
_FLAG_ESCAPED_RE: Final[re.Pattern[str]] = re.compile(_FLAG_PREFIX + r"(?=\\+[\"'])", re.I)
_SHAPE_RULES_AFTER: Final[tuple[_Rule, ...]] = (
    # ``--token=value`` / ``--api-key value``; what comes before the flag in
    # its run is kept.
    # An escaped quoted value (#2616); a plain quoted one reads as a bare value.
    lambda line, log: _mask_quoted_values(_FLAG_ESCAPED_RE, line, log),
    _bare_value_rule(
        _FLAG_PREFIX + r"(?!\\+[\"'])(?=[^-\s])", _value_class(r"[^\s;,]*"), quoted=True
    ),
    _sub_rule(re.compile(r"(client_id=)[A-Za-z0-9_.-]{16,}", re.I), rf"\g<1>{MASK}"),
    # A JWT starts at the first ``eyJ`` of its run; group 1 keeps what
    # comes before it.
    _sub_rule(
        re.compile(
            r"((?<![A-Za-z0-9_-])(?:(?!eyJ)[A-Za-z0-9_-])*+)"
            r"eyJ[A-Za-z0-9_-]{10,}+\.[A-Za-z0-9_-]{10,}+\.[A-Za-z0-9_-]{10,}"
        ),
        rf"\g<1>{MASK}",
    ),
    _sub_rule(
        re.compile(r"(?:hf_|sk-|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{20,}"), MASK
    ),
    _sub_rule(re.compile(r"AKIA[0-9A-Z]{16}"), MASK),
)


def _is_secret_name(name: str) -> bool:
    """True if ``name`` (as matched by ``_SECRET_NAME``) names a secret value
    rather than a count, a tokenizer, or where a secret is stored.

    A ``-`` counts as ``_``, so header-style names (``x-api-key``,
    ``auth-token``) are as secret as their ``_`` spelling, as with
    :func:`hal0.api._redact.is_sensitive_key` (#2384). A name longer than
    ``_NAME_JUDGED_WHOLE_MAX`` keeps its count parts (#2488)."""
    name = name.lstrip("-").replace("-", "_")
    if _SECRET_REF_SUFFIX_RE.search(name):
        return False
    if len(name) <= _NAME_JUDGED_WHOLE_MAX:
        name = _BENIGN_NAME_PART_RE.sub("", name)
    return bool(_SECRET_NAME_RE.fullmatch(name))


class _NameValue(NamedTuple):
    """A ``NAME[=:]value`` found by :func:`_name_values`."""

    match: re.Match[str]  # the ``_NAME_SEP_RE`` match, up to the value
    secret: bool  # :func:`_is_secret_name` on the whole name
    end: int  # where the value ends
    closing: str  # the quote that closes a quoted value, if any
    # When a secret's value swallowed another secret, the parts of its mask
    # (:func:`_past_swallowed`). Empty otherwise.
    spans: list[_Span]


def _name_values(line: str, *, log: bool = False) -> Iterator[_NameValue]:
    """Each ``NAME[=:]value`` in ``line``. A quoted value ends at its closing
    quote; a bare one at whitespace, ``,``, ``}``, ``&``, a quote, or a ``;``
    that starts another secret (#2529). A secret's value that swallows
    another secret runs on over that secret's value; a benign one ends
    before it (:func:`_past_swallowed`). A match is consumed whole, benign
    name or not."""
    at = 0
    while (match := _NAME_SEP_RE.search(line, at)) is not None:
        start = match.end()
        secret = _is_secret_name(match.group("name"))
        spans: list[_Span] = []
        closing = ""
        if delim := _delimiter(line, start):
            end, closed = _quoted_value_end(line, start, log=log, secret=secret, spans=spans)
            if closed:
                closing = delim
        else:
            end = _bare_value_end(line, start, _NAME_VALUE, log=log, secret=secret, spans=spans)
        yield _NameValue(match, secret, end, closing, spans)
        at = end


def _mask_name_values(line: str, log: bool) -> str:
    """Mask the value of every secret-named ``NAME[=:]value`` in ``line``,
    keeping a quoted value's quotes. :func:`_is_secret_name` judges the whole
    name, so ``max-tokens`` is judged as ``max_tokens`` (#2466) and
    ``api-token-prod`` as ``api_token_prod`` (#2488)."""
    out: list[str] = []
    pos = 0
    for match, secret, end, closing, _spans in _name_values(line, log=log):
        start = match.end()
        if end == start or not secret:
            continue
        quote = _delimiter(line, start)
        out += (line[pos:start], quote, MASK, closing)
        pos = end
    out.append(line[pos:])
    return "".join(out)


def redact_secret_named_values(value: Any) -> Any:
    """Mask the value of every secret-NAMED dict key inside ``value`` (#2434).

    The structured counterpart of the ``NAME=value`` shape pass: a key is
    secret by the same test (``HF_TOKEN``, ``apiKey``, ``password``,
    ``*_KEY``, ``key``), and names that only look secret (``max_tokens``,
    ``tokenizer``, ``token_env``) are left alone, unlike
    :func:`hal0.api._redact.redact_config`'s broader key test. A masked
    value becomes :data:`MASK` whatever its type; anything else is walked
    (dicts, lists, tuples) and returned with its shape unchanged. Pure.
    """
    if isinstance(value, dict):
        return {
            k: (
                MASK if isinstance(k, str) and _is_secret_name(k) else redact_secret_named_values(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_secret_named_values(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_secret_named_values(v) for v in value)
    return value


# ── MCP audit rows (#2434) ──────────────────────────────────────────────────
#
#: MCP tool arguments that ARE a secret although their name does not say so.
#: ``provider_credential_write``'s ``value`` is the API key itself (its
#: ``key`` is only the env-var name). ``mcp_server_config_write``'s ``env``
#: maps names to literals that :meth:`hal0.mcp.installed.InstalledServer.
#: header_value_keys` sends as HTTP headers, so every value is a credential
#: (``Authorization: Basic …``, an opaque ``X-Auth``) and only the names are
#: kept. Owned here so the audit writer (:mod:`hal0.mcp.admin`) and the
#: doctor bundle's export pass read the same list.
AUDIT_SECRET_ARGS: Final[dict[str, frozenset[str]]] = {
    "provider_credential_write": frozenset({"value"}),
    "mcp_server_config_write": frozenset({"env"}),
}


def mask_audit_secret_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Return ``args`` with ``tool``'s :data:`AUDIT_SECRET_ARGS` masked.

    A mapping arg keeps its keys and has every value masked; any other value
    becomes :data:`MASK`. Pure: the caller's dict is not changed.
    """
    secret = AUDIT_SECRET_ARGS.get(tool, frozenset())
    return {
        k: (({kk: MASK for kk in v} if isinstance(v, dict) else MASK) if k in secret else v)
        for k, v in args.items()
    }


def redact_audit_args(tool: str, args: Any) -> Any:
    """Mask every secret in an MCP audit row's ``args`` (#2434, #2435).

    Three layers: ``tool``'s own :data:`AUDIT_SECRET_ARGS`, the value of every
    secret-NAMED key at any depth (:func:`redact_secret_named_values`), and
    secret SHAPES inside any string value (:func:`redact_text_tree`). The
    audit writer (:mod:`hal0.mcp.admin`) applies it at write time; the API
    routes that read audit rows back from journald apply it again on read,
    for rows logged before the write-time pass. Pure and idempotent.
    """
    if isinstance(args, dict):
        args = mask_audit_secret_args(tool, args)
    return redact_text_tree(redact_secret_named_values(args))


_AUDIT_ROW_EVENT: Final[str] = "mcp.tool.invoked"
_AUDIT_ROW_TOOL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])[\"']?tool[\"']?\s*[=:]\s*[\"']?(?P<tool>[A-Za-z0-9_]+)"
)
_QUOTES: Final[str] = "\"'"


class _Unparsed(ValueError):
    """The value at an offset is not a complete JSON / Python-repr literal."""


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _end_of_string(s: str, i: int) -> int:
    """Index just past the quoted string starting at ``s[i]``."""
    quote, i = s[i], i + 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
        elif s[i] == quote:
            return i + 1
        else:
            i += 1
    raise _Unparsed(s)


def _masked_literal(s: str, i: int) -> tuple[str, int]:
    """Masked rendering of the literal at ``s[i]`` and the index past it.

    A string becomes a quoted :data:`MASK`; a dict keeps its keys and masks
    every value (recursively); a list masks each item; any other scalar
    becomes a bare :data:`MASK`.
    """
    if i >= len(s):
        raise _Unparsed(s)
    ch = s[i]
    if ch in _QUOTES:
        return f"{ch}{MASK}{ch}", _end_of_string(s, i)
    if ch in "{[":
        close, parts, i = ("}" if ch == "{" else "]"), [ch], i + 1
        while True:
            j = _skip_ws(s, i)
            parts.append(s[i:j])
            i = j
            if i < len(s) and s[i] == close:
                parts.append(close)
                return "".join(parts), i + 1
            if ch == "{":
                if i >= len(s) or s[i] not in _QUOTES:
                    raise _Unparsed(s)
                j = _end_of_string(s, i)
                k = _skip_ws(s, j)
                if k >= len(s) or s[k] != ":":
                    raise _Unparsed(s)
                k = _skip_ws(s, k + 1)
                parts.append(s[i:k])
                i = k
            value, i = _masked_literal(s, i)
            parts.append(value)
            j = _skip_ws(s, i)
            if j < len(s) and s[j] == ",":
                parts.append(s[i : j + 1])
                i = j + 1
            elif j < len(s) and s[j] == close:
                parts.append(s[i:j])
                i = j
            else:
                raise _Unparsed(s)
    j = i
    while j < len(s) and s[j] not in ",}] \t\n":
        j += 1
    if j == i:
        raise _Unparsed(s)
    return MASK, j


def redact_audit_row_secret_args(line: str) -> str:
    """Mask :data:`AUDIT_SECRET_ARGS` inside one rendered audit log line.

    For ``mcp.tool.invoked`` rows logged before the write-time masking
    (#2434), in the structlog console rendering (``args={'value': '…'}``)
    or JSON (``"args": {"value": "…"}``). The tool is read from the row's
    ``tool`` field; each of its secret args is masked as
    :func:`mask_audit_secret_args` does. A value that does not parse (a
    truncated line) is masked to the end of the line. Other lines are
    returned unchanged. Idempotent.
    """
    if _AUDIT_ROW_EVENT not in line:
        return line
    tools = {m.group("tool") for m in _AUDIT_ROW_TOOL_RE.finditer(line)}
    names = set().union(*(AUDIT_SECRET_ARGS.get(t, frozenset()) for t in tools))
    for name in sorted(names):
        arg_re = re.compile(r"([\"'])" + re.escape(name) + r"\1\s*:\s*")
        out, pos = [], 0
        for match in arg_re.finditer(line):
            if match.start() < pos:
                continue
            out.append(line[pos : match.end()])
            try:
                masked, pos = _masked_literal(line, match.end())
            except _Unparsed:
                out.append(MASK)
                pos = len(line)
                break
            out.append(masked)
        out.append(line[pos:])
        line = "".join(out)
    return line


def redact_secret_shapes(line: str) -> str:
    """Mask secret SHAPES in one line of free text (no literal known in
    advance). Keeps each key's name, quoting and separator so a reader still
    sees a secret WAS present. Idempotent.

    Change together with ``installer/lib/failure-report.sh`` and, for
    :func:`redact_log_line`, the browser-side port in ``ui/src/sentry.ts``
    (#2413): the cases in ``tests/fixtures/redaction_shapes.json`` are checked
    against both, so a rule changed here needs the port and the fixture
    updated in the same commit."""
    return _redact_shapes(line, log=False)


def _redact_shapes(line: str, *, log: bool) -> str:
    """:func:`redact_secret_shapes`; ``log`` when LOG_SECRET_RE has already
    run on ``line``, so a value may also end before one of its secrets."""
    for rule in _SHAPE_RULES:
        line = rule(line, log)
    line = _mask_name_values(line, log)
    for rule in _SHAPE_RULES_AFTER:
        line = rule(line, log)
    return line


_ENV_NAME_VALUE_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
# A value that ends in a name and its separator (``api_key=`` in
# ``Y_KEY=   api_key= ``): masked wherever it appears, it would hide that
# name, and so the value after it (#2594).
_NAME_TAIL_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?<!{_NAME_RUN})({_NAME_RUN}++)[\"']?\s*[=:]$", re.IGNORECASE
)
_NOT_SECRET_WORDS: Final[frozenset[str]] = frozenset({"true", "false", "none", "null", "unset"})


def _harvest_secret_literals(text: str) -> list[str]:
    """Plausible secret values of ``NAME[=:]value`` in ``text``, longest
    first. Same filter as failure-report.sh's
    _hal0_report_text_value_is_secret: at least 8 characters, not purely
    numeric, not an env-var name, and not under the bare field name ``key``
    (in log text it names a setting, not a credential). A secret value that
    another one swallowed (#2594) is harvested on its own as well."""
    found: set[str] = set()
    for match, secret, end, _closing, swallowed in _name_values(text):
        if not secret:
            continue
        if swallowed:
            spans = [(start, stop) for start, stop, harvest in swallowed if harvest]
        elif match.group("name").lstrip("-").lower() != "key":
            spans = [(match.end(), end)]
        else:
            continue
        for start, stop in spans:
            value = text[start:stop]
            # An escaped quoted value is read without its delimiters (#2616); a
            # plain one without its quotes.
            if len(delim := _delimiter(value, 0)) > 1:
                value = value[len(delim) :].removesuffix(delim)
            value = value.strip("\"'").strip()
            if (
                len(value) < 8
                or value.isdigit()
                or _ENV_NAME_VALUE_RE.fullmatch(value)
                or value.lower() in _NOT_SECRET_WORDS
                or value in MASK
                or ((tail := _NAME_TAIL_RE.search(value)) and _is_secret_name(tail.group(1)))
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
    text = _mask_literals(text, _harvest_secret_literals(text))
    return "\n".join(redact_secret_shapes(line) for line in text.split("\n"))


# Past these, _mask_literals falls back to one str.replace per literal: a
# trie this deeply nested is past what ``re`` compiles, and this many
# occurrences per character of text means literals that are prefixes of
# each other, many times over.
_LITERAL_TRIE_MAX_NESTING: Final[int] = 100
_LITERAL_OCCURRENCES_PER_CHAR_MAX: Final[int] = 4
# A literal longer than this is masked by ``str.replace`` ahead of the scan
# (#2600). The scan tries every literal at every position, and where the text
# repeats a literal's own prefix (a value of glued ``a.`` units, an open quote
# over a long run of ``a``) each try runs far into it: quadratic in the text
# when the literal is as long as the line. ``str.replace`` is linear in the
# text for one literal. Shorter literals bound each try, so the scan stays
# linear with this as the constant.
_LITERAL_SCAN_MAX: Final[int] = 128
# The literals past it take one ``str.replace`` each, over the whole text, the
# longest first and only as many as fit in this many characters (about a tenth
# of a second at ``str.replace``'s speed): a bundle with thousands of long
# distinct secrets scans the rest in one pass instead (#2533).
_LITERAL_REPLACE_BUDGET: Final[int] = 500_000_000


def _may_match_a_mask(literal: str) -> bool:
    """True if ``literal`` could match text that includes part of an
    inserted :data:`MASK`: lie inside one, contain one, or start or end
    partway through one."""
    return (
        literal in MASK
        or MASK in literal
        or any(
            literal.startswith(MASK[i:]) or literal.endswith(MASK[:i]) for i in range(1, len(MASK))
        )
    )


def _replace_each(text: str, literals: list[str]) -> str:
    for literal in literals:
        text = text.replace(literal, MASK)
    return text


def _literal_trie_regex(trie: dict[str, Any], nesting: int = 0) -> str | None:
    """The alternation of every literal in ``trie``, factored as a trie so
    ``re`` walks it in time bounded by the longest literal, not their number.
    At each position it matches the longest literal starting there. ``None``
    if the trie nests deeper than _LITERAL_TRIE_MAX_NESTING."""
    if nesting > _LITERAL_TRIE_MAX_NESTING:
        return None
    alts = []
    for char in sorted(k for k in trie if k):
        run, node = char, trie[char]
        while "" not in node and len(node) == 1:
            ((char, node),) = node.items()
            run += char
        rest = _literal_trie_regex(node, nesting + 1)
        if rest is None:
            return None
        alts.append(re.escape(run) + rest)
    if not alts:
        return ""
    body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
    return f"(?:{body})?" if "" in trie else body


def _mask_literals(text: str, literals: list[str]) -> str:
    """``text`` with every literal replaced by :data:`MASK`, byte for byte as
    ``for lit in literals: text = text.replace(lit, MASK)`` would, in one
    scan of ``text`` (#2533). That loop rescans the whole text per literal,
    so a bundle with many distinct secrets took quadratic time (18 s at
    800 KB).

    The loop's result is the original text with some occurrences masked: a
    literal masks its occurrences left to right, skipping one that overlaps
    an earlier mask of its own or of an earlier literal. Its occurrences in
    the partly masked text are exactly the original ones that overlap no
    mask, unless the literal could match part of a :data:`MASK`
    (:func:`_may_match_a_mask`). So every occurrence is found once, then
    masked in the loop's order. Such a literal, or a pathological set of
    literals, takes the loop itself.
    """
    # The longest literals come first, and the loop masks them first: do the same,
    # with str.replace, so the scan only meets literals whose tries are short.
    # What it scans is then a text with those masks in it, which it handles as
    # any text that holds a MASK (below).
    long_run = 0
    while long_run < len(literals) and len(literals[long_run]) > _LITERAL_SCAN_MAX:
        long_run += 1
    # The longest ones that fit the budget; those are the ones whose tries run
    # furthest, and the rest still go in the order the loop would take them.
    long_run = min(long_run, _LITERAL_REPLACE_BUDGET // max(len(text), 1))
    text = _replace_each(text, literals[:long_run])
    literals = literals[long_run:]
    if not literals or any(_may_match_a_mask(literal) for literal in literals):
        return _replace_each(text, literals)
    # A trie of the literals; key "" marks where one ends and holds its id.
    # A repeated literal masks nothing the second time, so its first id owns
    # every occurrence.
    trie: dict[str, Any] = {}
    for i, literal in enumerate(literals):
        node = trie
        for char in literal:
            node = node.setdefault(char, {})
        node.setdefault("", i)
    lit_id: dict[str, int] = {}
    ends: list[list[int]] = []  # per literal, the ids of literals that are its prefixes
    for literal in literals:
        node, prefixes = trie, []
        for char in literal:
            node = node[char]
            if "" in node:
                prefixes.append(node[""])
        lit_id[literal] = prefixes[-1]
        ends.append(prefixes)
    body = _literal_trie_regex(trie)
    if body is None:
        return _replace_each(text, literals)
    try:
        scan = re.compile(f"(?=({body}))")
    except (re.error, RecursionError, OverflowError):
        # Fail closed onto the loop: never let a pattern ``re`` cannot
        # compile cost a secret its mask.
        return _replace_each(text, literals)

    occurrences: list[list[int]] = [[] for _ in literals]
    budget = _LITERAL_OCCURRENCES_PER_CHAR_MAX * len(text) + len(literals)
    for match in scan.finditer(text):
        start, prefixes = match.start(), ends[lit_id[match.group(1)]]
        budget -= len(prefixes)
        if budget < 0:
            return _replace_each(text, literals)
        for i in prefixes:
            occurrences[i].append(start)

    masked = bytearray(len(text))
    spans: list[tuple[int, int]] = []
    for i, literal in enumerate(literals):
        size, free_from = len(literal), 0
        for start in occurrences[i]:
            end = start + size
            if start >= free_from and masked.find(1, start, end) < 0:
                masked[start:end] = b"\x01" * size
                spans.append((start, end))
                free_from = end
    out: list[str] = []
    pos = 0
    for start, end in sorted(spans):
        out += (text[pos:start], MASK)
        pos = end
    out.append(text[pos:])
    return "".join(out)


__all__ = [
    "AUDIT_SECRET_ARGS",
    "LOG_SECRET_RE",
    "MASK",
    "mask_audit_secret_args",
    "redact_audit_args",
    "redact_audit_row_secret_args",
    "redact_log_line",
    "redact_secret_named_values",
    "redact_secret_shapes",
    "redact_shareable_text",
    "redact_text_tree",
]
