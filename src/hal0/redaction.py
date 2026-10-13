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

# ``NAME`` + separator, then the value: double-quoted, single-quoted or bare.
# The name starts at a word start, so a hyphenated name is matched whole.
# The separator cannot start with a name character, so the name is the whole
# run, and _SECRET_NAME is tested once on it by a lookahead (#2501).
_NAME_VALUE_SHAPE_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?<!{_NAME_RUN})(?P<name>(?={_NAME_RUN}*?{_SECRET_WORD}"
    rf"|(?:{_NAME_RUN}*[_-])?KEY(?!{_NAME_RUN})){_NAME_RUN}*+)"
    r"(?P<sep>[\"']?\s*[=:]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\"'\s,}&]+)",
    re.IGNORECASE,
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

# Ordered (pattern, replacement) pairs, as in _hal0_report_mask_patterns:
# these run before the NAME[=:]value pass, _SHAPE_RULES_AFTER after it.
_SHAPE_RULES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"(authorization:\s*(?:basic|token)\s+)[^\s'\"]+", re.I), rf"\g<1>{MASK}"),
    (re.compile(r"(bearer\s+)[A-Za-z0-9._~+/=-]+", re.I), rf"\g<1>{MASK}"),
    # Any other ``Authorization`` value (#2410). A known scheme is kept and
    # the credential after it masked. Anything else of 8+ characters is
    # treated as a raw credential and masked to the end of the header value,
    # so a raw key followed by more words is never taken for a scheme; a
    # short word such as ``denied`` is left alone. Already-masked values
    # re-mask to the same text.
    (
        re.compile(
            r"(authorization[\"']?\s*:\s*[\"']?" + _AUTH_SCHEMES + r"\s+)[^\s\"',]+",
            re.I,
        ),
        rf"\g<1>{MASK}",
    ),
    (
        re.compile(
            r"(authorization[\"']?\s*:\s*[\"']?)"
            r"(?!" + _AUTH_SCHEMES + r"(?:\s|$))(?=[^\s\"',]{8,})[^\"',\n]*[^\s\"',]",
            re.I,
        ),
        rf"\g<1>{MASK}",
    ),
    # URL userinfo. The scheme starts at the first letter of its run.
    (re.compile(_URL_SCHEME + r"[^/@\s:]*+):[^/@\s]++@", re.I), rf"\g<1>:{MASK}@"),
    (re.compile(_URL_SCHEME + r")[^/@\s:]{16,}+@", re.I), rf"\g<1>{MASK}@"),
)
_SHAPE_RULES_AFTER: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # ``--token=value`` / ``--api-key value``. The flag is the run's first
    # ``--``: group 1 starts at the run's start and keeps what comes before.
    (
        re.compile(
            r"((?<![A-Za-z0-9-])(?:[A-Za-z0-9]|-(?!-))*+"
            r"--(?=[A-Za-z0-9-]*?(?:secret|token|password|pass|api-key|apikey|private-key))"
            r"[A-Za-z0-9-]*+(?:=|\s++))[^-\s]\S*",
            re.I,
        ),
        rf"\g<1>{MASK}",
    ),
    (re.compile(r"(client_id=)[A-Za-z0-9_.-]{16,}", re.I), rf"\g<1>{MASK}"),
    # A JWT starts at the first ``eyJ`` of its run; group 1 keeps what
    # comes before it.
    (
        re.compile(
            r"((?<![A-Za-z0-9_-])(?:(?!eyJ)[A-Za-z0-9_-])*+)"
            r"eyJ[A-Za-z0-9_-]{10,}+\.[A-Za-z0-9_-]{10,}+\.[A-Za-z0-9_-]{10,}"
        ),
        rf"\g<1>{MASK}",
    ),
    (re.compile(r"(?:hf_|sk-|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{20,}"), MASK),
    (re.compile(r"AKIA[0-9A-Z]{16}"), MASK),
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


def _matched_name_is_secret(match: re.Match[str]) -> bool:
    """:func:`_is_secret_name` on a ``_NAME_VALUE_SHAPE_RE`` match's whole
    name, so ``max-tokens`` is judged as ``max_tokens`` (#2466) and
    ``api-token-prod`` as ``api_token_prod`` (#2488)."""
    return _is_secret_name(match.group("name"))


def _mask_name_value(match: re.Match[str]) -> str:
    if not _matched_name_is_secret(match):
        return match.group(0)
    value = match.group("value")
    quote = value[0] if value[0] in "\"'" else ""
    return f"{match.group('name')}{match.group('sep')}{quote}{MASK}{quote}"


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
    sees a secret WAS present. Idempotent."""
    for pattern, repl in _SHAPE_RULES:
        line = pattern.sub(repl, line)
    line = _NAME_VALUE_SHAPE_RE.sub(_mask_name_value, line)
    for pattern, repl in _SHAPE_RULES_AFTER:
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
        if name.lower() == "key" or not _matched_name_is_secret(match):
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
    text = _mask_literals(text, _harvest_secret_literals(text))
    return "\n".join(redact_secret_shapes(line) for line in text.split("\n"))


# Past these, _mask_literals falls back to one str.replace per literal: a
# trie this deeply nested is past what ``re`` compiles, and this many
# occurrences per character of text means literals that are prefixes of
# each other, many times over.
_LITERAL_TRIE_MAX_NESTING: Final[int] = 100
_LITERAL_OCCURRENCES_PER_CHAR_MAX: Final[int] = 4


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
