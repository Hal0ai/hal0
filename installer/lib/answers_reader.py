#!/usr/bin/env python3
"""Read an installer answers document and print allow-listed NAME=value lines.

Usage:  python3 -I answers_reader.py --origin wizard|file [--default-store PATH] [--] DOCUMENT.json

Runs as root, before hal0's package is installed: standard library only, one
file, Python 3.10 or newer. The document is data. This program never evaluates
any of it; it validates every value against a fixed pattern, then prints one
`NAME=value` line per variable to set, in ALLOWED_NAMES order.

The apply run ends with exactly one more line, `#saved <base64>`, and prints
nothing else: the saved
form of the document (what is kept after the install), standard base64 on one
line, produced from the same read as the variables. `--emit-saved DOCUMENT`
prints that saved form alone, as JSON (for tests and tools).

The two folder paths (model_store.path, model_store.flm_path) are checked by
their text alone: this program never looks at the filesystem for them, and
everything it prints is text it validated, never something read from disk.
Whether root may create or use a folder there is decided by the installer's
sinks (installer/install.sh), through the `nofollow` helper.

Exit 0: lines printed. Exit 2: nothing on standard output, one line per problem
on standard error (at most MAX_MESSAGES of each kind, then a count).
Validation finishes before the first byte is printed.
"""

from __future__ import annotations

import base64
import errno
import ipaddress
import json
import os
import re
import stat
import sys
import unicodedata
from typing import Any
from urllib.parse import urlsplit

# The allow-list. installer/lib/answers.sh holds a second copy; a test keeps
# the two equal. Order here is output order.
ALLOWED_NAMES = (
    "HAL0_NONINTERACTIVE",
    "HAL0_MODELS_DIR",
    "HAL0_FLM_MODELS_DIR",
    "HAL0_SKIP_OPENWEBUI",
    "HAL0_SKIP_HERMES",
    "HAL0_HERMES_TERMINAL",
    "HAL0_INSTALL_PI",
    "HAL0_SKIP_PI",
    "HAL0_SKIP_HINDSIGHT",
    "HAL0_IMAGE_MODELS",
    "HAL0_SKIP_BRAIN_MODEL",
    "HAL0_BRAIN_MODEL",
    "HAL0_PULL_AGENT_MODEL",
    "HAL0_AGENT_MODEL",
    "HAL0_ALLOW_CPU_ONLY",
    "HAL0_PORT",
    "HAL0_HOSTNAME",
    "HAL0_PUBLIC_URL",
    "HAL0_ALLOWED_ORIGINS",
    "HAL0_ADMIN_KEY_MODE",
    "HAL0_ENABLE_BENCH_TIMER",
    "HAL0_NPU_SETUP",
)

MAX_BYTES = 1024 * 1024
MAX_PATH = 4096
MAX_MESSAGES = 20  # per kind (warnings, errors); the rest are counted
ORIGINS = ("wizard", "file")

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
# A models directory: absolute, and only characters that are inert in a shell
# and in a systemd unit or env file. Spaces and '=' are fine (they are data);
# $ ` " ' \\ ; & | < > ( ) * ? ! # { } [ ] are not. ASCII only (re.ASCII: \w
# is A-Za-z0-9_), and always used with fullmatch, so a trailing newline cannot
# slip past a `$`. installer/lib/answers.sh holds the same set as a backstop.
_FS_PATH = re.compile(r"/[\w.+@,:~= /-]*", re.ASCII)
_URL_PATH = re.compile(r"^[A-Za-z0-9._~/%-]*$")

# Trees a model store may never be, or sit inside, because the installer (as
# root) creates it and hands it to the hal0 service account. A store there
# would make a system tree writable by that account. Compared by NAME only, on
# the normalised text: a link or a second mount is the installer's sink's
# business (it refuses anything outside folders only root can change).
DENIED_TREES = (
    "/etc",  # system configuration: root-owned files that run as root
    "/usr",
    "/bin",
    "/sbin",  # programs and libraries root executes
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",  # the same, and the loader
    "/boot",  # kernel and boot loader
    "/root",  # root's own home
    "/proc",
    "/sys",
    "/dev",  # kernel interfaces, not storage
    "/run",
    "/var/run",  # runtime state; /var/run is the same tree as /run
    "/var/log",  # logs other services own
    "/var/lib",  # service state (hal0's own models directory is the exception below)
)
# The one place under a denied tree that is a model store by design, unless the
# installer passes its own (--default-store, for a --dev prefix). Taken from the
# command line only, never from the document.
DEFAULT_STORE = "/var/lib/hal0/models"

# Keys the document may carry, per object. Anything else is "unknown".
_TOP_KEYS = frozenset(
    {
        "version",
        "strict",
        "model_store",
        "apps",
        "memory",
        "gen",
        "models",
        "hardware",
        "network",
        "access",
        "maintenance",
        "npu",
        "huggingface",
        "slots",
        "install",
        "meta",
    }
)
_SECTION_KEYS = {
    "model_store": frozenset({"path", "flm_path"}),
    "memory": frozenset({"enabled"}),
    "gen": frozenset({"mode", "models"}),
    "models": frozenset({"brain", "starter", "memory"}),
    "hardware": frozenset({"allow_cpu_only"}),
    "network": frozenset({"port", "bind_host", "hostname", "public_url", "allowed_origins"}),
    "maintenance": frozenset({"weekly_benchmark"}),
    "npu": frozenset({"opt_in"}),
    "install": frozenset({"type"}),
    "meta": frozenset({"saved_at", "saved_by"}),
}
_INSTALL_TYPES = ("recommended", "minimal", "custom")
# Written by the wizard; fullmatch so a trailing newline cannot slip through.
_META_SAVED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_META_SAVED_BY = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+~_-]{0,63}")
_HF_TOKEN_ENV = "HF_TOKEN"
_APP_KEYS = {
    "openwebui": frozenset({"enabled"}),
    "hermes": frozenset({"enabled", "terminal_tool"}),
    "pi": frozenset({"enabled"}),
    "comfyui": frozenset({"enabled"}),
}
_GEN_MODES = ("off", "scaffold_only", "scaffold_and_download")

# What a `slots` entry may carry: the keys src/hal0/install/answers.py
# (_resolve_slots) reads. Each value is a flat scalar checked below.
_SLOT_KEYS = frozenset(
    {
        "capability",
        "name",
        "port",
        "model_id",
        "device",
        "profile",
        "context_size",
        "enabled_on_pull",
    }
)
_SLOT_CAPABILITIES = ("chat", "coder")  # _VALID_SLOT_CAPABILITIES in the loader
_SLOT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")  # slots/routing.py slot-name policy
_SLOT_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,199}$")  # no '/': not a path
_SCOPE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,16}$")  # an IPv6 scope id (interface name or number)
_SLOT_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")  # device and profile

# Generated beside the reader (scripts/gen_setup_models.py); located from this
# file, never from the working directory.
_MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "manifests", "setup-models.json"
)


MAX_ECHO = 80


def _esc(value: Any) -> str:
    """Text safe to print: printable ASCII kept, everything else escaped, capped.

    Anything that came from the document and goes into a message passes through
    here, so a key or value holding a terminal escape sequence can never reach
    the root user's terminal. Non-strings are shown by repr.
    """
    if isinstance(value, str):
        text = value
    else:
        try:
            text = repr(value)
        except ValueError:  # an int past the interpreter's digit limit
            text = "<number too large to show>"
    clipped = len(text) > MAX_ECHO
    out: list[str] = []
    for ch in text[:MAX_ECHO]:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif 0x20 <= code < 0x7F:
            out.append(ch)
        elif code < 0x100:
            out.append(f"\\x{code:02x}")
        elif code < 0x10000:
            out.append(f"\\u{code:04x}")
        else:
            out.append(f"\\U{code:08x}")
    return "".join(out) + ("..." if clipped else "")


def _q(value: Any) -> str:
    return "'" + _esc(value) + "'"


def _scrub(msg: str) -> str:
    """Last line of defence: no byte outside printable ASCII leaves in a message."""
    return "".join(ch if 0x20 <= ord(ch) < 0x7F else _esc(ch) for ch in msg)


class _Problems:
    """Errors and warnings in first-seen order, each shown once.

    The ``_seen_*`` sets make the repeat check constant-time: a list search
    made a document flooded with unknown keys quadratic.
    """

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._seen_errors: set[str] = set()
        self._seen_warnings: set[str] = set()

    def error(self, msg: str) -> None:
        msg = _scrub(msg)
        if msg not in self._seen_errors:
            self._seen_errors.add(msg)
            self.errors.append(msg)

    def warn(self, msg: str) -> None:
        msg = _scrub(msg)
        if msg not in self._seen_warnings:
            self._seen_warnings.add(msg)
            self.warnings.append(msg)


class _DuplicateKey(ValueError):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKey(key)
        out[key] = value
    return out


def _bad_constant(token: str) -> Any:
    raise ValueError(f"the value {_esc(token)} is not allowed")


def _has_control(text: str) -> bool:
    for ch in text:
        cat = unicodedata.category(ch)
        if cat[0] == "C" or cat in ("Zl", "Zp"):
            return True
    return False


def _where(text: str) -> str:
    """Cap a location path so a long or deep key cannot flood a message."""
    return text if len(text) <= 160 else text[:160] + "..."


def _walk_strings(node: Any, where: str, problems: _Problems) -> None:
    """Reject control characters and unencodable text anywhere in the document."""
    if isinstance(node, str):
        if _has_control(node):
            problems.error(f"{where}: contains a control character")
        else:
            try:
                node.encode("utf-8")
            except UnicodeEncodeError:
                problems.error(f"{where}: is not valid text")
    elif isinstance(node, dict):
        for key, value in node.items():
            _walk_strings(key, _where(f"{where} key {_q(key)}"), problems)
            _walk_strings(value, _where(f"{where}.{_esc(key)}" if where else _esc(key)), problems)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _walk_strings(value, f"{where}[{index}]", problems)


class _NotRegular(OSError):
    pass


def _read_capped(path: str) -> bytes:
    """Read a regular file through ONE descriptor, never blocking on a pipe.

    The descriptor is opened O_NONBLOCK (a FIFO swapped in after any earlier
    look opens at once instead of hanging the root installer) and O_NOCTTY,
    then fstat-ed: what is checked is what is read. At most MAX_BYTES + 1 bytes
    come back, so the caller can tell "too large" from "exactly the cap".
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _NotRegular(errno.EINVAL, "not a regular file")
        if info.st_size > MAX_BYTES:
            return b"x" * (MAX_BYTES + 1)
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_BYTES:
            chunk = os.read(fd, MAX_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _load(path: str, problems: _Problems) -> dict[str, Any] | None:
    try:
        raw = _read_capped(path)
    except _NotRegular:
        problems.error(f"{_q(path)}: not a regular file")
        return None
    except OSError as exc:
        problems.error(f"cannot read the answers document: {exc.strerror or exc}")
        return None
    if len(raw) > MAX_BYTES:
        problems.error("the answers document is larger than 1 MiB")
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        problems.error("the answers document is not valid UTF-8")
        return None
    try:
        doc = json.loads(text, object_pairs_hook=_pairs, parse_constant=_bad_constant)
    except _DuplicateKey as exc:
        problems.error(f"the answers document repeats the key {_q(exc.args[0])}")
        return None
    except RecursionError:
        problems.error("the answers document is nested too deeply")
        return None
    except ValueError as exc:
        problems.error(f"the answers document is not valid JSON: {exc}")
        return None
    if not isinstance(doc, dict):
        problems.error("the answers document must be a JSON object at the top level")
        return None
    return doc


# ── value checkers: each returns the value to print, or None after reporting ──


def _section(doc: dict[str, Any], name: str, problems: _Problems) -> dict[str, Any]:
    value = doc.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        problems.error(f"{name}: must be an object")
        return {}
    return value


def _unknown(
    obj: dict[str, Any], known: frozenset[str], where: str, strict: bool, problems: _Problems
) -> None:
    for key in sorted(set(obj) - known):
        full = f"{where}.{_esc(key)}" if where else _esc(key)
        if strict:
            problems.error(f"unknown key '{full}' (strict is true)")
        else:
            problems.warn(f"answers key '{full}' is not recognized and was ignored")


def _bool(obj: dict[str, Any], key: str, where: str, problems: _Problems) -> bool | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        problems.error(f"{where}.{key}: must be true or false")
        return None
    return value


def _normal(path: str) -> str:
    """The lexical normal form of an absolute path: no filesystem access.

    os.path.normpath removes doubled slashes, `.` parts and a trailing slash
    (callers have already refused `..`). POSIX lets it keep exactly two leading
    slashes; they are folded to one, or `//etc` would not compare equal to /etc.
    """
    norm = os.path.normpath(path)
    if norm.startswith("//"):
        norm = "/" + norm.lstrip("/")
    return norm


def _parts(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


def _inside(path: str, tree: str, *, strictly: bool = False) -> bool:
    """`path` is `tree` or below it, comparing normalised components."""
    have, want = _parts(path), _parts(tree)
    if strictly and len(have) <= len(want):
        return False
    return have[: len(want)] == want


def _path_text_ok(value: str) -> bool:
    return (
        value.startswith("/")
        and len(value) <= MAX_PATH
        and _FS_PATH.fullmatch(value) is not None
        and ".." not in value.split("/")
    )


def _path_text(
    obj: dict[str, Any], key: str, where: str, problems: _Problems
) -> tuple[bool, str | None]:
    """(given, normalised text) of a folder path; the text is None after an error.

    Purely lexical: the value is checked as text, normalised, and the normal
    form is checked again by the same rules. Nothing on disk is looked at.
    """
    value = obj.get(key)
    if value is None:
        return False, None
    full = f"{where}.{key}"
    if not isinstance(value, str) or not value.startswith("/"):
        problems.error(f"{full}: must be an absolute path (start with /)")
        return True, None
    if len(value) > MAX_PATH:
        problems.error(f"{full}: is longer than {MAX_PATH} characters")
        return True, None
    if _FS_PATH.fullmatch(value) is None:
        problems.error(
            f"{full}: {_q(value)} has characters other than ASCII letters, digits, space "
            "and . _ + @ , : ~ = / -"
        )
        return True, None
    if ".." in value.split("/"):
        problems.error(f"{full}: must not contain a '..' segment")
        return True, None
    norm = _normal(value)
    if not _path_text_ok(norm):  # cannot happen for a value that passed above
        problems.error(f"{full}: {_q(value)} is not a usable path")
        return True, None
    return True, norm


def _store_path(
    store: dict[str, Any], problems: _Problems, default_store: str
) -> tuple[bool, str | None]:
    """model_store.path: (given, normalised text or None after an error)."""
    given, norm = _path_text(store, "path", "model_store", problems)
    if norm is None:
        return given, None
    shown = _q(store["path"])
    if not _inside(norm, default_store):
        for tree in DENIED_TREES:
            if _inside(norm, tree):
                problems.error(f"model_store.path: {shown} is inside {tree}, a system location")
                return True, None
    if len(_parts(norm)) < 2:
        problems.error(
            f"model_store.path: {shown} is too close to the root; use a folder like /mnt/models"
        )
        return True, None
    return True, norm


def _flm_path(
    store: dict[str, Any],
    problems: _Problems,
    default_store: str,
    store_given: bool,
    store_norm: str | None,
) -> str | None:
    """model_store.flm_path: strictly inside the model store, or None after an error.

    The model store is the document's own model_store.path when it gives one,
    otherwise the default store. A folder anywhere else can only be chosen in
    root's environment (HAL0_FLM_MODELS_DIR), never by a document.
    """
    _given, norm = _path_text(store, "flm_path", "model_store", problems)
    if norm is None:
        return None
    if store_given and store_norm is None:
        return None  # model_store.path was refused: already one error for it
    base = store_norm if store_given and store_norm is not None else default_store
    if not _inside(norm, base, strictly=True):
        problems.error(
            f"model_store.flm_path: {_q(store['flm_path'])}: the NPU model folder from an "
            "answers document must be a folder inside the model store; to use a folder "
            "elsewhere, set HAL0_FLM_MODELS_DIR in the environment when running the installer"
        )
        return None
    return norm


def _ident(value: Any, full: str, problems: _Problems) -> str | None:
    if not isinstance(value, str) or not _ID.match(value) or len(value) > 200:
        problems.error(f"{full}: must be an id like 'name-1.0' (a-z, 0-9, '.', '_', '-')")
        return None
    return value


def _dns_name(value: str) -> bool:
    if not value or len(value) > 253 or not value.isascii():
        return False
    return all(_DNS_LABEL.match(label) for label in value.rstrip(".").split("."))


def _ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _host_part(host: str) -> str | None:
    """Canonical host for a URL: DNS name, IPv4, or bracketed IPv6; else None."""
    if host.startswith("[") and host.endswith("]"):
        if "%" in host:  # a scope id is not part of a URL host (and is free text)
            return None
        try:
            return f"[{ipaddress.IPv6Address(host[1:-1])}]"
        except ValueError:
            return None
    if _ip_literal(host):
        return host if ":" not in host else None
    if _dns_name(host):
        # A last label the URL parser reads as a number is not a DNS name.
        last = host.rstrip(".").rsplit(".", 1)[-1].lower()
        if re.fullmatch(r"[0-9]+|0x[0-9a-f]*", last):
            return None
        return host.lower()
    return None


def _authority(text: str) -> str:
    """The part of a URL between '://' (if any) and the first '/', '?' or '#'."""
    rest = text.split("://", 1)[1] if "://" in text else text
    return re.split(r"[/?#]", rest, maxsplit=1)[0]


def _shown(raw: str) -> str:
    """What a URL message may echo: scheme and host[:port], never more.

    Messages end up in a log other users can read, so a user name, a password,
    a path or a query that was pasted into a URL must never be printed. The
    caller has already refused any value holding '@'.
    """
    text = raw.strip()
    scheme = text.split("://", 1)[0] + "://" if "://" in text else ""
    return _q(scheme + _authority(text))


def _http_url(
    raw: str, full: str, problems: _Problems, *, allow_path: bool, bare_ok: bool
) -> str | None:
    """Validate like hal0.config.schema NetworkConfig.public_url / extra_origins."""
    text = raw.strip()
    if not text:
        problems.error(f"{full}: must not be empty")
        return None
    # First, before any message can echo the value: credentials in the URL. An
    # '@' anywhere is refused (a password may hold '/', '?' or '#', so the
    # authority cannot be found reliably), and the message echoes nothing.
    if "@" in text:
        problems.error(f"{full}: must not contain a user name or password")
        return None
    shown = _shown(text)
    if "://" not in text:
        if not bare_ok:
            problems.error(f"{full}: {shown} must start with http:// or https://")
            return None
        text = f"https://{text}"
    if any(c.isspace() for c in text) or not text.isascii():
        problems.error(f"{full}: {shown} must be plain ASCII without whitespace")
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        problems.error(f"{full}: {shown} is not a valid URL")
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        problems.error(f"{full}: {shown} must use http or https")
        return None
    if "@" in parts.netloc:
        problems.error(f"{full}: must not contain a user name or password")
        return None
    if not parts.hostname:
        problems.error(f"{full}: {shown} has no host")
        return None
    host = _host_part(f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname)
    if host is None:
        problems.error(
            f"{full}: {shown} is not a valid host: use a DNS name, an IPv4 address "
            "or a bracketed IPv6 address"
        )
        return None
    if port == 0:
        problems.error(f"{full}: {shown} has an invalid port")
        return None
    if parts.query or parts.fragment or "?" in text or "#" in text:
        problems.error(f"{full}: {shown} must not carry a query or fragment")
        return None
    path = parts.path.rstrip("/")
    if path and not allow_path:
        problems.error(f"{full}: {shown} must be an origin (scheme://host[:port]) with no path")
        return None
    if not _URL_PATH.match(path):
        problems.error(f"{full}: {shown} has a path with characters other than A-Za-z0-9._~/%-")
        return None
    default = 80 if scheme == "http" else 443
    netloc = host if port is None or port == default else f"{host}:{port}"
    return f"{scheme}://{netloc}{path}"


def _load_manifest(path: str, problems: _Problems) -> dict[str, set[str]] | None:
    """Model ids per role from the generated list, or None (with one warning).

    Shape: {"model_ladder": [{"role": "starter|brain|image", "model": ID, ...}]};
    other row keys are ignored. A row without role or model, or a file without
    model_ladder, counts as unreadable.
    """
    try:
        raw = _read_capped(path)
        if len(raw) > MAX_BYTES:
            raise ValueError("too large")
        data = json.loads(raw.decode("utf-8"))
        rows = data["model_ladder"]
        if not isinstance(rows, list):
            raise TypeError("model_ladder is not a list")
        roles: dict[str, set[str]] = {}
        for row in rows:
            role, model = row["role"], row["model"]
            if not isinstance(role, str) or not isinstance(model, str):
                raise TypeError("role and model must be strings")
            roles.setdefault(role, set()).add(model)
        return roles
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        problems.warn(
            "the installer's model list (manifests/setup-models.json) could not be read; "
            "model ids are checked by pattern only"
        )
        return None


def _q_flat(value: Any) -> str:
    """Like _q, but a list or object is named, never shown: it may hold a pasted secret."""
    if isinstance(value, dict):
        return "an object"
    if isinstance(value, list):
        return "a list"
    return _q(value)


def _slot_text(
    entry: dict[str, Any],
    key: str,
    pattern: re.Pattern[str],
    full: str,
    problems: _Problems,
    *,
    nullable: bool,
) -> None:
    if key not in entry:
        return
    value = entry[key]
    if value is None and nullable:
        return
    if not isinstance(value, str) or not pattern.match(value):
        shape = (
            "a name like 'chat-1' (a-z, 0-9, '_', '-')"
            if pattern is _SLOT_NAME
            else ("plain text (letters, digits, '.', '_', '-', ':', '+', '/')")
        )
        problems.error(f"{full}.{key}: must be {shape} (got {_q_flat(value)})")


def _check_slots(slots: Any, strict: bool, problems: _Problems) -> None:
    """Validate `slots` the way the loader reads it; nothing nested, nothing unknown kept."""
    if slots is None:
        return
    if not isinstance(slots, list):
        problems.error("slots: must be a list")
        return
    seen: dict[int, int] = {}
    for i, entry in enumerate(slots):
        full = f"slots[{i}]"
        if not isinstance(entry, dict):
            problems.error(f"{full}: must be a mapping")
            continue
        _unknown(entry, _SLOT_KEYS, full, strict, problems)
        for required in ("capability", "name", "port"):  # as the loader requires them
            if required not in entry:
                problems.error(f"{full}.{required}: is required")
        if "capability" in entry and entry["capability"] not in _SLOT_CAPABILITIES:
            problems.error(
                f"{full}.capability: must be one of {', '.join(_SLOT_CAPABILITIES)} "
                f"(got {_q_flat(entry['capability'])})"
            )
        _slot_text(entry, "name", _SLOT_NAME, full, problems, nullable=False)
        port = entry.get("port")
        if "port" in entry:
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                problems.error(f"{full}.port: must be an integer from 1 to 65535")
            elif port in seen:
                problems.error(f"{full}.port: {port} is already used by slots[{seen[port]}]")
            else:
                seen[port] = i
        _slot_text(entry, "model_id", _SLOT_MODEL, full, problems, nullable=True)
        model_id = entry.get("model_id")
        if isinstance(model_id, str) and ".." in model_id and _SLOT_MODEL.match(model_id):
            problems.error(f"{full}.model_id: must not contain '..' (got {_q_flat(model_id)})")
        _slot_text(entry, "device", _SLOT_WORD, full, problems, nullable=True)
        _slot_text(entry, "profile", _SLOT_WORD, full, problems, nullable=True)
        if "context_size" in entry:
            size = entry["context_size"]
            if size != "auto" and (isinstance(size, bool) or not isinstance(size, int) or size < 1):
                problems.error(f"{full}.context_size: must be a positive integer or 'auto'")
        if "enabled_on_pull" in entry and not isinstance(entry["enabled_on_pull"], bool):
            problems.error(f"{full}.enabled_on_pull: must be true or false")


def _bind_host_ok(value: Any) -> bool:
    """An IP address or a host name: text, no control characters, not an option."""
    if not isinstance(value, str):
        return False
    text = value
    # No whitespace of any kind at either end (str.isspace covers Unicode spaces).
    if not text or text[0].isspace() or text[-1].isspace():
        return False
    if text.startswith("-") or _has_control(text):
        return False
    if "%" in text:
        # An IPv6 scope id: ipaddress takes any text after '%', so bound it.
        scope = text.partition("%")[2]
        return bool(_SCOPE_ID.match(scope)) and _ip_literal(text)
    if _ip_literal(text):
        return True
    # A dotted name whose last label is a number (999.1.1.1) is a malformed address.
    last = text.rstrip(".").rsplit(".", 1)[-1].lower()
    return _dns_name(text) and not re.fullmatch(r"[0-9]+|0x[0-9a-f]*", last)


def _a(word: str) -> str:
    return ("an " if word[:1] in "aeiou" else "a ") + word


def _model_id(
    value: Any,
    full: str,
    role: str,
    manifest: dict[str, set[str]] | None,
    problems: _Problems,
    *,
    require_rows: bool = True,
) -> str | None:
    ident = _ident(value, full, problems)
    if ident is None:
        return None
    if ident in ("recommended", "none", "auto"):
        problems.error(f"{full}: {_q(value)} is not a model id")
        return None
    if manifest is not None and (manifest.get(role) or require_rows):
        known = manifest.get(role, set())
        if ident not in known:
            problems.error(
                f"{full}: {_q(value)} is not {_a(role)} model this installer offers"
                f" (known: {', '.join(sorted(known)) or 'none'})"
            )
            return None
    return ident


def _build(
    doc: dict[str, Any],
    origin: str,
    problems: _Problems,
    manifest_path: str = _MANIFEST,
    default_store: str = DEFAULT_STORE,
) -> dict[str, str]:
    out: dict[str, str] = {"HAL0_NONINTERACTIVE": "1"}

    strict_raw = doc.get("strict", False)
    if not isinstance(strict_raw, bool):
        problems.error("strict: must be true or false")
        strict_raw = False
    strict = strict_raw
    manifest = _load_manifest(manifest_path, problems)

    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        problems.error(f"version: must be 1 (got {_q_flat(version)})")

    _unknown(doc, _TOP_KEYS, "", strict, problems)
    _check_slots(doc.get("slots"), strict, problems)
    if doc.get("huggingface") is not None:
        if not isinstance(doc["huggingface"], dict):
            problems.error("huggingface: must be a mapping")
        else:
            hf = doc["huggingface"]
            if "token_env" in hf and hf["token_env"] != _HF_TOKEN_ENV:
                problems.error("huggingface.token_env: must be the word 'HF_TOKEN'")
        if isinstance(doc["huggingface"], dict) and set(doc["huggingface"]) - {"token_env"}:
            problems.warn(
                "huggingface settings are not read from an answers document; "
                "set HF_TOKEN/HUGGING_FACE_HUB_TOKEN in the environment instead"
            )
    for name, known in _SECTION_KEYS.items():
        _unknown(_section(doc, name, problems), known, name, strict, problems)

    # model_store: text only (see the module docstring)
    store = _section(doc, "model_store", problems)
    store_given, path = _store_path(store, problems, default_store)
    if path is not None:
        out["HAL0_MODELS_DIR"] = path
    flm = _flm_path(store, problems, default_store, store_given, path)
    if flm is not None:
        out["HAL0_FLM_MODELS_DIR"] = flm

    # apps
    apps = _section(doc, "apps", problems)
    _unknown(apps, frozenset(_APP_KEYS), "apps", strict, problems)
    entries: dict[str, dict[str, Any]] = {}
    for app, known in _APP_KEYS.items():
        entry = apps.get(app)
        if entry is None:
            entries[app] = {}
        elif not isinstance(entry, dict):
            problems.error(f"apps.{app}: must be an object")
            entries[app] = {}
        else:
            entries[app] = entry
            _unknown(entry, known, f"apps.{app}", strict, problems)
    _bool(entries["comfyui"], "enabled", "apps.comfyui", problems)  # known, emits nothing
    if _bool(entries["openwebui"], "enabled", "apps.openwebui", problems) is False:
        out["HAL0_SKIP_OPENWEBUI"] = "1"
    if _bool(entries["hermes"], "enabled", "apps.hermes", problems) is False:
        out["HAL0_SKIP_HERMES"] = "1"
    terminal = _bool(entries["hermes"], "terminal_tool", "apps.hermes", problems)
    if terminal:
        if origin == "wizard":
            out["HAL0_HERMES_TERMINAL"] = "1"
        else:
            problems.warn(
                "apps.hermes.terminal_tool is only honoured when chosen in the install "
                "wizard; ignored for an answers file (the terminal tool stays off)"
            )
    pi = _bool(entries["pi"], "enabled", "apps.pi", problems)
    if pi is True:
        out["HAL0_INSTALL_PI"] = "1"
    elif pi is False:
        out["HAL0_SKIP_PI"] = "1"

    # memory
    if _bool(_section(doc, "memory", problems), "enabled", "memory", problems) is False:
        out["HAL0_SKIP_HINDSIGHT"] = "1"

    # gen: the document format's own words (off, scaffold_only,
    # scaffold_and_download) mapped to HAL0_IMAGE_MODELS.
    if doc.get("gen") is not None:
        gen = _section(doc, "gen", problems)
        mode = gen.get("mode", "off")
        models = gen.get("models")
        if not isinstance(mode, str) or mode not in _GEN_MODES:
            problems.error(
                f"gen.mode: must be one of {', '.join(_GEN_MODES)} (got {_q_flat(mode)})"
            )
        else:
            ids: list[str] = []
            if models is not None and not isinstance(models, list):
                problems.error("gen.models: must be a list of image model ids")
            elif models:
                if mode != "scaffold_and_download":
                    problems.error(f"gen.models: cannot be given with gen.mode '{mode}'")
                else:
                    for i, item in enumerate(models):
                        ident = _model_id(
                            item,
                            f"gen.models[{i}]",
                            "image",
                            manifest,
                            problems,
                            require_rows=False,
                        )
                        if ident is not None:
                            ids.append(ident)
            if mode != "scaffold_and_download":
                out["HAL0_IMAGE_MODELS"] = "none"
            elif ids:
                out["HAL0_IMAGE_MODELS"] = ",".join(ids)
            elif not models:
                out["HAL0_IMAGE_MODELS"] = "recommended"

    # install and meta: known, validated, emit nothing; kept in the saved copy
    inst = _section(doc, "install", problems)
    if "type" in inst and not (isinstance(inst["type"], str) and inst["type"] in _INSTALL_TYPES):
        problems.error("install.type: must be 'recommended', 'minimal' or 'custom'")
    meta = _section(doc, "meta", problems)
    for key, pattern in (("saved_at", _META_SAVED_AT), ("saved_by", _META_SAVED_BY)):
        if key in meta and not (isinstance(meta[key], str) and pattern.fullmatch(meta[key])):
            problems.error(f"meta.{key}: not a value this installer writes")

    # models
    mdl = _section(doc, "models", problems)
    if "memory" in mdl and not (isinstance(mdl["memory"], str) and mdl["memory"] == "shared"):
        problems.error("models.memory: must be the word 'shared'")
    brain = mdl.get("brain")
    if brain is not None:
        if brain == "none":
            out["HAL0_SKIP_BRAIN_MODEL"] = "1"
        elif brain != "auto":
            ident = _model_id(brain, "models.brain", "brain", manifest, problems)
            if ident is not None:
                out["HAL0_BRAIN_MODEL"] = ident
    starter = mdl.get("starter")
    if starter is not None:
        if starter == "none":
            out["HAL0_PULL_AGENT_MODEL"] = "0"
        elif starter == "auto":
            out["HAL0_PULL_AGENT_MODEL"] = "1"
        else:
            ident = _model_id(starter, "models.starter", "starter", manifest, problems)
            if ident is not None:
                out["HAL0_PULL_AGENT_MODEL"] = "1"
                out["HAL0_AGENT_MODEL"] = ident

    # hardware
    if _bool(_section(doc, "hardware", problems), "allow_cpu_only", "hardware", problems):
        out["HAL0_ALLOW_CPU_ONLY"] = "1"

    # network
    net = _section(doc, "network", problems)
    port = net.get("port")
    if port is not None:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            problems.error("network.port: must be an integer from 1 to 65535")
        else:
            out["HAL0_PORT"] = str(port)
    if net.get("bind_host") is not None:
        # A key of the document format that the installer cannot honour (it
        # binds 0.0.0.0). It is still part of the document, so it is checked in
        # every mode; a valid one draws the "not applied" warning, nothing more.
        if not _bind_host_ok(net["bind_host"]):
            problems.error(
                f"network.bind_host: {_q_flat(net['bind_host'])} must be an IP address or a host name"
            )
        else:
            problems.warn(
                "network.bind_host is not applied at install; "
                "change it afterwards with hal0 network"
            )
    value = net.get("hostname")
    if value is not None:
        if not isinstance(value, str):
            problems.error("network.hostname: must be a string or null")
        else:
            text = value.strip()
            if _ip_literal(text) or not _dns_name(text):
                problems.error(f"network.hostname: {_q(value)} is not a valid host name")
            else:
                out["HAL0_HOSTNAME"] = text
    pub = net.get("public_url")
    if pub is not None:
        if not isinstance(pub, str):
            problems.error("network.public_url: must be a string or null")
        else:
            clean = _http_url(pub, "network.public_url", problems, allow_path=True, bare_ok=True)
            if clean is not None:
                out["HAL0_PUBLIC_URL"] = clean
    origins = net.get("allowed_origins")
    if origins is not None:
        if not isinstance(origins, list):
            problems.error("network.allowed_origins: must be a list of origins")
        else:
            clean_list: list[str] = []
            for i, item in enumerate(origins):
                where = f"network.allowed_origins[{i}]"
                if not isinstance(item, str):
                    problems.error(f"{where}: must be a string")
                    continue
                clean = _http_url(item, where, problems, allow_path=False, bare_ok=False)
                if clean is not None and clean not in clean_list:
                    clean_list.append(clean)
            if clean_list:
                out["HAL0_ALLOWED_ORIGINS"] = ",".join(clean_list)

    # access: only the word `generate` or `none`; nothing else, ever.
    access = doc.get("access")
    if access is not None:
        if not isinstance(access, dict):
            problems.error("access: must be an object")
        else:
            for key in sorted(set(access) - {"admin_key"}):
                problems.error(
                    f"access.{_esc(key)}: not allowed (a document carries no key material)"
                )
            mode_word = access.get("admin_key")
            if "admin_key" in access:
                if mode_word in ("generate", "none") and isinstance(mode_word, str):
                    out["HAL0_ADMIN_KEY_MODE"] = mode_word
                else:
                    problems.error("access.admin_key: must be the word 'generate' or 'none'")

    # maintenance
    bench = _bool(
        _section(doc, "maintenance", problems), "weekly_benchmark", "maintenance", problems
    )
    if bench is not None:
        out["HAL0_ENABLE_BENCH_TIMER"] = "1" if bench else "0"

    # npu
    # `opt_in` is the format's own key (src/hal0/install/answers.py
    # _resolve_npu_opt_in): the string 'auto', or a boolean.
    opt_in = _section(doc, "npu", problems).get("opt_in")
    if opt_in is not None:
        if isinstance(opt_in, bool):
            out["HAL0_NPU_SETUP"] = "on" if opt_in else "off"
        elif isinstance(opt_in, str) and opt_in == "auto":
            out["HAL0_NPU_SETUP"] = "auto"
        else:
            problems.error("npu.opt_in: must be 'auto', true or false")

    return out


class _Options:
    """What the command line gave: origin, document, manifest, default store."""

    def __init__(self) -> None:
        self.origin: str | None = None
        self.path: str | None = None
        self.manifest = _MANIFEST
        self.default_store = DEFAULT_STORE


def _valid_default_store(value: str) -> bool:
    """--default-store is a path like a document's: absolute, plain characters, no `..`."""
    return (
        value.startswith("/")
        and len(value) <= MAX_PATH
        and _FS_PATH.fullmatch(value) is not None
        and ".." not in value.split("/")
        and len(_parts(value)) >= 2
    )


def _parse_args(argv: list[str]) -> _Options | None:
    opts = _Options()
    seen_store = False
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--origin" and args and opts.origin is None:
            opts.origin = args.pop(0)
        elif arg == "--models-manifest" and args:
            opts.manifest = args.pop(0)
        elif arg == "--default-store" and args and not seen_store:
            opts.default_store = _normal(args[0]) if args[0].startswith("/") else args[0]
            args.pop(0)
            seen_store = True
        elif arg == "--":
            if len(args) == 1 and opts.path is None:
                opts.path = args.pop(0)
            else:
                return None
        elif not arg.startswith("-") and opts.path is None:
            opts.path = arg
        else:
            return None
    if opts.origin not in ORIGINS or not opts.path:
        return None
    return opts


def _saved_document(doc: dict[str, Any], stores: dict[str, str] | None = None) -> dict[str, Any]:
    """The document as it is kept after an install: only keys the format knows.

    Unknown keys are dropped at every level that has a key table (a pasted
    secret would sit in one); `huggingface` and `apps.hermes.terminal_tool`
    are known but never kept. `slots` entries keep only the keys the loader reads.
    Document key order holds. `stores` maps `path` and `flm_path` to the
    normalised text the reader exported; those are what is kept.
    """
    stores = stores or {}
    tables: dict[str, frozenset[str]] = dict(_SECTION_KEYS)
    tables["access"] = frozenset({"admin_key"})
    saved: dict[str, Any] = {}
    for key, value in doc.items():
        if key not in _TOP_KEYS or key == "huggingface":
            continue
        if key == "apps" and isinstance(value, dict):
            apps: dict[str, Any] = {}
            for app, entry in value.items():
                if app not in _APP_KEYS:
                    continue
                if isinstance(entry, dict):
                    keep = _APP_KEYS[app] - {"terminal_tool"}
                    entry = {k: v for k, v in entry.items() if k in keep}
                apps[app] = entry
            value = apps
        elif key == "slots" and isinstance(value, list):
            value = [
                {k: v for k, v in entry.items() if k in _SLOT_KEYS}
                for entry in value
                if isinstance(entry, dict)
            ]
        elif key in tables and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k in tables[key]}
            if key == "model_store":  # kept as exported: the normalised text
                value = {
                    k: stores.get(k, _normal(v)) if isinstance(v, str) else v
                    for k, v in value.items()
                }
        saved[key] = value
    return saved


def _bad_default_store(value: str) -> bool:
    """True (after one message that echoes nothing) when --default-store is not a usable path."""
    if _valid_default_store(value):
        return False
    sys.stderr.write(
        "error: --default-store must be an absolute path of plain characters with no '..' part\n"
    )
    return True


def _write_messages(kind: str, messages: list[str]) -> None:
    """At most MAX_MESSAGES lines of one kind, then one line counting the rest."""
    for message in messages[:MAX_MESSAGES]:
        sys.stderr.write(f"{kind}: {message}\n")
    if len(messages) > MAX_MESSAGES:
        sys.stderr.write(f"{kind}: ... and {len(messages) - MAX_MESSAGES} more\n")


def _saved_json(doc: dict[str, Any], values: dict[str, str]) -> str:
    """The saved form as JSON text, with the store paths as they were exported."""
    stores = {
        key: values[name]
        for key, name in (("path", "HAL0_MODELS_DIR"), ("flm_path", "HAL0_FLM_MODELS_DIR"))
        if name in values
    }
    return json.dumps(_saved_document(doc, stores), indent=2, ensure_ascii=False) + "\n"


def _emit_saved(argv: list[str]) -> int:
    """`--emit-saved DOCUMENT`: validate as a file-origin run, print the saved form."""
    path = None
    manifest = _MANIFEST
    default_store = DEFAULT_STORE
    seen_store = False
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--emit-saved" and args and path is None:
            path = args.pop(0)
        elif arg == "--origin" and args:
            args.pop(0)  # not needed in this mode
        elif arg == "--models-manifest" and args:
            manifest = args.pop(0)
        elif arg == "--default-store" and args and not seen_store:
            default_store = args.pop(0)
            seen_store = True
        else:
            path = None
            break
    if not path:
        sys.stderr.write(
            "usage: answers_reader.py [--default-store PATH] --emit-saved DOCUMENT.json\n"
        )
        return 2
    if default_store.startswith("/"):
        default_store = _normal(default_store)
    if _bad_default_store(default_store):
        return 2
    problems = _Problems()
    doc = _load(path, problems)
    text = ""
    if doc is not None:
        try:
            _walk_strings(doc, "", problems)
            values = _build(doc, "file", problems, manifest, default_store)
            if not problems.errors:
                text = _saved_json(doc, values)
        except RecursionError:
            problems.error("the answers document is nested too deeply")
    # Warnings are not printed in this mode: only the apply run reports them.
    if problems.errors or doc is None:
        _write_messages("error", problems.errors)
        return 2
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


def main(argv: list[str]) -> int:
    if "--emit-saved" in argv:
        return _emit_saved(argv)
    opts = _parse_args(argv)
    if opts is None or opts.origin is None or opts.path is None:
        sys.stderr.write(
            "usage: answers_reader.py --origin wizard|file [--models-manifest PATH] "
            "[--default-store PATH] [--] DOCUMENT.json\n"
        )
        return 2
    if _bad_default_store(opts.default_store):
        return 2
    problems = _Problems()
    doc = _load(opts.path, problems)
    lines: list[str] = []
    if doc is not None:
        try:
            _walk_strings(doc, "", problems)
            values = _build(doc, opts.origin, problems, opts.manifest, opts.default_store)
            for name in ALLOWED_NAMES:
                if name in values:
                    lines.append(f"{name}={values[name]}")
            if not problems.errors:
                # The saved form, from this same read: one line, standard base64.
                saved = base64.b64encode(_saved_json(doc, values).encode("utf-8"))
                lines.append("#saved " + saved.decode("ascii"))
        except RecursionError:
            problems.error("the answers document is nested too deeply")
    _write_messages("warning", problems.warnings)
    if problems.errors or doc is None:
        _write_messages("error", problems.errors)
        return 2
    sys.stdout.buffer.write("".join(f"{line}\n" for line in lines).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
