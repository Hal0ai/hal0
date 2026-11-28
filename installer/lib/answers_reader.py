#!/usr/bin/env python3
"""Read an installer answers document and print allow-listed NAME=value lines.

Usage:  python3 -I answers_reader.py --origin wizard|file [--] DOCUMENT.json

Runs as root, before hal0's package is installed: standard library only, one
file, Python 3.10 or newer. The document is data. This program never evaluates
any of it; it validates every value against a fixed pattern, then prints one
`NAME=value` line per variable to set, in ALLOWED_NAMES order, and nothing else.

Exit 0: lines printed. Exit 2: nothing on standard output, one line per problem
on standard error. Validation finishes before the first byte is printed.
"""

from __future__ import annotations

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
ORIGINS = ("wizard", "file")

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
# A models directory: absolute, and only characters that are inert in a shell
# and in a systemd unit or env file. Spaces and '=' are fine (they are data);
# $ ` " ' \\ ; & | < > ( ) * ? ! # { } [ ] are not.
_FS_PATH = re.compile(r"^/[\w.+@,:~= /-]*$")
_URL_PATH = re.compile(r"^[A-Za-z0-9._~/%-]*$")

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
    }
)
_SECTION_KEYS = {
    "model_store": frozenset({"path", "flm_path"}),
    "memory": frozenset({"enabled"}),
    "gen": frozenset({"mode", "models"}),
    "models": frozenset({"brain", "starter"}),
    "hardware": frozenset({"allow_cpu_only"}),
    "network": frozenset({"port", "bind_host", "hostname", "public_url", "allowed_origins"}),
    "maintenance": frozenset({"weekly_benchmark"}),
    "npu": frozenset({"setup"}),
}
_APP_KEYS = {
    "openwebui": frozenset({"enabled"}),
    "hermes": frozenset({"enabled", "terminal_tool"}),
    "pi": frozenset({"enabled"}),
    "comfyui": frozenset({"enabled"}),
}
_GEN_MODES = ("off", "scaffold_only", "scaffold_and_download")

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
    return "".join(
        ch if 0x20 <= ord(ch) < 0x7F else _esc(ch) for ch in msg
    )


class _Problems:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        msg = _scrub(msg)
        if msg not in self.errors:
            self.errors.append(msg)

    def warn(self, msg: str) -> None:
        msg = _scrub(msg)
        if msg not in self.warnings:
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


def _load(path: str, problems: _Problems) -> dict[str, Any] | None:
    try:
        info = os.stat(path)
        if not stat.S_ISREG(info.st_mode):
            problems.error(f"{_q(path)}: not a regular file")
            return None
        with open(path, "rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
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


def _unknown(obj: dict[str, Any], known: frozenset[str], where: str, strict: bool,
             problems: _Problems) -> None:
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


def _abs_path(obj: dict[str, Any], key: str, where: str, problems: _Problems) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    full = f"{where}.{key}"
    if not isinstance(value, str) or not value.startswith("/"):
        problems.error(f"{full}: must be an absolute path (start with /)")
        return None
    if len(value) > MAX_PATH:
        problems.error(f"{full}: is longer than {MAX_PATH} characters")
        return None
    if not _FS_PATH.match(value):
        problems.error(
            f"{full}: {_q(value)} has characters other than letters, digits, space "
            "and . _ + @ , : ~ = / -"
        )
        return None
    if ".." in value.split("/"):
        problems.error(f"{full}: must not contain a '..' segment")
        return None
    return value


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


def _http_url(raw: str, full: str, problems: _Problems, *, allow_path: bool,
              bare_ok: bool) -> str | None:
    """Validate like hal0.config.schema NetworkConfig.public_url / extra_origins."""
    text = raw.strip()
    if not text:
        problems.error(f"{full}: must not be empty")
        return None
    if "://" not in text:
        if not bare_ok:
            problems.error(f"{full}: {_q(text)} must start with http:// or https://")
            return None
        text = f"https://{text}"
    if any(c.isspace() for c in text) or not text.isascii():
        problems.error(f"{full}: {_q(raw)} must be plain ASCII without whitespace")
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        problems.error(f"{full}: {_q(raw)} is not a valid URL")
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        problems.error(f"{full}: {_q(raw)} must use http or https")
        return None
    if "@" in parts.netloc:
        problems.error(f"{full}: {_q(raw)} must not contain a user name or password")
        return None
    if not parts.hostname:
        problems.error(f"{full}: {_q(raw)} has no host")
        return None
    host = _host_part(f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname)
    if host is None:
        problems.error(
            f"{full}: {_q(raw)} is not a valid host: use a DNS name, an IPv4 address "
            "or a bracketed IPv6 address"
        )
        return None
    if port == 0:
        problems.error(f"{full}: {_q(raw)} has an invalid port")
        return None
    if parts.query or parts.fragment or "?" in text or "#" in text:
        problems.error(f"{full}: {_q(raw)} must not carry a query or fragment")
        return None
    path = parts.path.rstrip("/")
    if path and not allow_path:
        problems.error(f"{full}: {_q(raw)} must be an origin (scheme://host[:port]) with no path")
        return None
    if not _URL_PATH.match(path):
        problems.error(f"{full}: {_q(raw)} has a path with characters other than A-Za-z0-9._~/%-")
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
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise OSError("not a regular file")
        with open(path, "rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
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


def _a(word: str) -> str:
    return ("an " if word[:1] in "aeiou" else "a ") + word


def _model_id(value: Any, full: str, role: str, manifest: dict[str, set[str]] | None,
              problems: _Problems, *, require_rows: bool = True) -> str | None:
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


def _build(doc: dict[str, Any], origin: str, problems: _Problems,
           manifest_path: str = _MANIFEST) -> dict[str, str]:
    out: dict[str, str] = {"HAL0_NONINTERACTIVE": "1"}

    strict_raw = doc.get("strict", False)
    if not isinstance(strict_raw, bool):
        problems.error("strict: must be true or false")
        strict_raw = False
    strict = strict_raw
    manifest = _load_manifest(manifest_path, problems)

    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        problems.error(f"version: must be 1 (got {_q(version)})")

    _unknown(doc, _TOP_KEYS, "", strict, problems)
    if doc.get("slots") is not None and not isinstance(doc["slots"], list):
        problems.error("slots: must be a list")
    if doc.get("huggingface") is not None:
        if not isinstance(doc["huggingface"], dict):
            problems.error("huggingface: must be a mapping")
        else:
            problems.warn(
                "huggingface settings are not read from an answers document; "
                "set HF_TOKEN/HUGGING_FACE_HUB_TOKEN in the environment instead"
            )
    for name, known in _SECTION_KEYS.items():
        _unknown(_section(doc, name, problems), known, name, strict, problems)

    # model_store
    store = _section(doc, "model_store", problems)
    path = _abs_path(store, "path", "model_store", problems)
    if path is not None:
        out["HAL0_MODELS_DIR"] = path
    flm = _abs_path(store, "flm_path", "model_store", problems)
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
            problems.error(f"gen.mode: must be one of {', '.join(_GEN_MODES)} (got {_q(mode)})")
        else:
            ids: list[str] = []
            if models is not None and not isinstance(models, list):
                problems.error("gen.models: must be a list of image model ids")
            elif models:
                if mode != "scaffold_and_download":
                    problems.error(f"gen.models: cannot be given with gen.mode '{mode}'")
                else:
                    for i, item in enumerate(models):
                        ident = _model_id(item, f"gen.models[{i}]", "image", manifest, problems,
                                          require_rows=False)
                        if ident is not None:
                            ids.append(ident)
            if mode != "scaffold_and_download":
                out["HAL0_IMAGE_MODELS"] = "none"
            elif ids:
                out["HAL0_IMAGE_MODELS"] = ",".join(ids)
            elif not models:
                out["HAL0_IMAGE_MODELS"] = "recommended"

    # models
    mdl = _section(doc, "models", problems)
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
        # binds 0.0.0.0): a warning in every document, never an error.
        problems.warn(
            "network.bind_host is not applied at install; change it afterwards with hal0 network"
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
        elif pub.strip():
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

    # access: only the word `create` or `none`; nothing else, ever.
    access = doc.get("access")
    if access is not None:
        if not isinstance(access, dict):
            problems.error("access: must be an object")
        else:
            for key in sorted(set(access) - {"admin_key"}):
                problems.error(f"access.{_esc(key)}: not allowed (a document carries no key material)")
            mode_word = access.get("admin_key")
            if "admin_key" in access:
                if mode_word in ("create", "none") and isinstance(mode_word, str):
                    out["HAL0_ADMIN_KEY_MODE"] = mode_word
                else:
                    problems.error("access.admin_key: must be the word 'create' or 'none'")

    # maintenance
    bench = _bool(_section(doc, "maintenance", problems), "weekly_benchmark",
                  "maintenance", problems)
    if bench is not None:
        out["HAL0_ENABLE_BENCH_TIMER"] = "1" if bench else "0"

    # npu
    setup = _section(doc, "npu", problems).get("setup")
    if setup is not None:
        if setup in ("auto", "off"):
            out["HAL0_NPU_SETUP"] = setup
        else:
            problems.error("npu.setup: must be 'auto' or 'off'")

    return out


def _parse_args(argv: list[str]) -> tuple[str, str, str] | None:
    origin = None
    path = None
    manifest = _MANIFEST
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--origin" and args and origin is None:
            origin = args.pop(0)
        elif arg == "--models-manifest" and args:
            manifest = args.pop(0)
        elif arg == "--":
            if len(args) == 1 and path is None:
                path = args.pop(0)
            else:
                return None
        elif not arg.startswith("-") and path is None:
            path = arg
        else:
            return None
    if origin not in ORIGINS or not path:
        return None
    return origin, path, manifest


def _saved_document(doc: dict[str, Any]) -> dict[str, Any]:
    """The document as it is kept after an install: only keys the format knows.

    Unknown keys are dropped at every level that has a key table (a pasted
    secret would sit in one); `huggingface` and `apps.hermes.terminal_tool`
    are known but never kept. `slots` is kept whole. Document key order holds.
    """
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
        elif key in tables and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k in tables[key]}
        saved[key] = value
    return saved


def _emit_saved(argv: list[str]) -> int:
    """`--emit-saved DOCUMENT`: validate as a file-origin run, print the saved form."""
    path = None
    manifest = _MANIFEST
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--emit-saved" and args and path is None:
            path = args.pop(0)
        elif arg == "--origin" and args:
            args.pop(0)  # not needed in this mode
        elif arg == "--models-manifest" and args:
            manifest = args.pop(0)
        else:
            path = None
            break
    if not path:
        sys.stderr.write("usage: answers_reader.py --emit-saved DOCUMENT.json\n")
        return 2
    problems = _Problems()
    doc = _load(path, problems)
    if doc is not None:
        _walk_strings(doc, "", problems)
        _build(doc, "file", problems, manifest)
    # Warnings are not repeated here: the apply run already printed them.
    if problems.errors or doc is None:
        for error in problems.errors:
            sys.stderr.write(f"error: {error}\n")
        return 2
    text = json.dumps(_saved_document(doc), indent=2, ensure_ascii=False) + "\n"
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


def main(argv: list[str]) -> int:
    if "--emit-saved" in argv:
        return _emit_saved(argv)
    parsed = _parse_args(argv)
    if parsed is None:
        sys.stderr.write("usage: answers_reader.py --origin wizard|file [--models-manifest PATH] [--] DOCUMENT.json\n")
        return 2
    origin, path, manifest_path = parsed
    problems = _Problems()
    doc = _load(path, problems)
    lines: list[str] = []
    if doc is not None:
        _walk_strings(doc, "", problems)
        values = _build(doc, origin, problems, manifest_path)
        for name in ALLOWED_NAMES:
            if name in values:
                lines.append(f"{name}={values[name]}")
    for warning in problems.warnings:
        sys.stderr.write(f"warning: {warning}\n")
    if problems.errors or doc is None:
        for error in problems.errors:
            sys.stderr.write(f"error: {error}\n")
        return 2
    sys.stdout.buffer.write("".join(f"{line}\n" for line in lines).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
