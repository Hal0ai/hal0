"""Render ``hal0-api.service`` from the shipped template (#3160).

One source of truth for the unit body: ``installer/systemd/hal0-api.service.in``.
Both writers render it through :func:`render_api_unit` so a fresh install and
an in-place ``hal0 update`` produce the same bytes for the same inputs:

* ``installer/install.sh`` runs this file as a script
  (``python -I api_unit.py render ...``) — stdlib only, so it works before the
  venv exists.
* ``hal0.updater.updater.refresh_api_unit`` imports it root-side, in a fresh
  interpreter of the activated release, and ``hal0 doctor all`` compares the
  installed unit with it.

Only values that differ per box are substituted; ``${HAL0_PORT}`` stays a
literal systemd expansion read from ``api.env`` at start, so the port and the
bind host are never baked into the unit.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: Template path relative to a release tree / repo checkout.
TEMPLATE_RELPATH = Path("installer") / "systemd" / "hal0-api.service.in"

JOURNAL_GROUP = "systemd-journal"

_JOURNAL_LINE_PRESENT = f"SupplementaryGroups={JOURNAL_GROUP}"
_JOURNAL_LINE_ABSENT = (
    "# no systemd-journal group on this host: /api/logs cannot read the journal (#2435)"
)

_TOKEN = re.compile(r"@(HAL0_[A-Z_]+)@")

#: A line break would split a directive, as would a trailing backslash, which
#: continues it onto the next line. Values reach unquoted directives as root.
_UNSAFE_VALUE = re.compile(r"[\r\n\0]")


@dataclass(frozen=True)
class ApiUnitParams:
    """Per-box values substituted into the template."""

    bin: str  #: absolute path of the ``hal0`` console script (ExecStart)
    workdir: str  #: WorkingDirectory (``current`` symlink in prod)
    api_env: str  #: required EnvironmentFile (``<etc>/api.env``)
    etc_dir: str  #: config dir holding ``hermes-python.env``
    hf_secrets_env: str  #: optional HF token EnvironmentFile
    journal_group: bool  #: emit ``SupplementaryGroups=systemd-journal``


def host_has_journal_group() -> bool:
    try:
        import grp

        grp.getgrnam(JOURNAL_GROUP)
    except (ImportError, KeyError):
        return False
    return True


def render_api_unit(template: str, params: ApiUnitParams) -> str:
    """Substitute ``@HAL0_*@`` tokens in one pass (values are never re-scanned)."""
    values = {
        "HAL0_API_JOURNAL_GROUP_LINE": (
            _JOURNAL_LINE_PRESENT if params.journal_group else _JOURNAL_LINE_ABSENT
        ),
        "HAL0_API_WORKDIR": params.workdir,
        "HAL0_API_ENV": params.api_env,
        "HAL0_ETC_DIR": params.etc_dir,
        "HAL0_HF_SECRETS_ENV": params.hf_secrets_env,
        "HAL0_BIN": params.bin,
    }

    for value in values.values():
        if _UNSAFE_VALUE.search(value) or value.endswith("\\"):
            raise ValueError(f"unsafe value for a systemd directive: {value!r}")

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"unknown template token @{name}@")
        return values[name]

    return _TOKEN.sub(_sub, template)


_DIRECTIVES = ("WorkingDirectory", "ExecStart", "EnvironmentFile")


def recover_params(unit_text: str) -> dict[str, str]:
    """Pull the per-box values back out of an installed unit.

    Returns any of ``bin``, ``workdir``, ``api_env``, ``etc_dir``,
    ``hf_secrets_env`` the unit carries; absent keys are simply missing. The
    first ``EnvironmentFile=`` without a ``-`` prefix is ``api_env``; the
    optional ones are matched by basename (``hermes-python.env`` gives the etc
    dir, ``hal0-api.env`` the HF secrets file).
    """
    found: dict[str, str] = {}
    for raw in unit_text.splitlines():
        line = raw.strip()
        key, sep, value = line.partition("=")
        if not sep or key not in _DIRECTIVES:
            continue
        value = value.strip()
        if key == "WorkingDirectory":
            found.setdefault("workdir", value)
        elif key == "ExecStart":
            parts = value.split()
            if parts:
                found.setdefault("bin", parts[0].lstrip("-@:+!"))
        elif value.startswith("-"):
            path = value[1:]
            if path.endswith("/hermes-python.env"):
                found.setdefault("etc_dir", path[: -len("/hermes-python.env")])
            elif path.endswith("/hal0-api.env"):
                found.setdefault("hf_secrets_env", path)
        else:
            found.setdefault("api_env", value)
    return found


#: FHS defaults an installed prod box uses (installer/install.sh, non-dev branch).
DEFAULT_ETC_DIR = "/etc/hal0"
DEFAULT_HF_SECRETS_ENV = "/var/lib/hal0/secrets/hal0-api.env"


def desired_api_unit(
    template: str,
    *,
    installed: str | None,
    venv_dir: str,
    current_link: str,
    bin_exists: Callable[[str], bool] | None = None,
) -> str:
    """The unit a box should carry: the template plus values recovered from it.

    Every per-box value is taken from the ``installed`` unit when it has one,
    so an operator's non-default prefix, env-file layout or ``WorkingDirectory``
    survives a refresh. Only what the unit lacks falls back to the FHS default
    derived from the running venv (``<venv>/bin/hal0``) and the ``current``
    link. A recovered ``ExecStart`` binary that no longer exists is replaced by
    the venv's, since a unit pointing at nothing dies ``203/EXEC``.
    """
    exists = bin_exists if bin_exists is not None else (lambda p: Path(p).exists())
    got = recover_params(installed) if installed else {}
    default_bin = f"{venv_dir}/bin/hal0"
    bin_ = got.get("bin") or default_bin
    if not exists(bin_):
        bin_ = default_bin
    params = ApiUnitParams(
        bin=bin_,
        workdir=got.get("workdir") or current_link,
        api_env=got.get("api_env") or f"{got.get('etc_dir', DEFAULT_ETC_DIR)}/api.env",
        etc_dir=got.get("etc_dir") or DEFAULT_ETC_DIR,
        hf_secrets_env=got.get("hf_secrets_env") or DEFAULT_HF_SECRETS_ENV,
        journal_group=host_has_journal_group(),
    )
    return render_api_unit(template, params)


#: api.env is a small KEY=VALUE file; anything bigger is not one.
_API_ENV_MAX_BYTES = 64 * 1024


def _read_api_env(path: str) -> str:
    """Read ``api.env`` as root: no symlink, regular file only, size capped."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > _API_ENV_MAX_BYTES:
            raise OSError(f"{path} is not a regular file under {_API_ENV_MAX_BYTES} bytes")
        with os.fdopen(fd, "rb", closefd=False) as f:
            return f.read(_API_ENV_MAX_BYTES + 1).decode("utf-8")
    finally:
        os.close(fd)


def loses_bind_host(installed: str, *, read: Callable[[str], str] | None = None) -> bool:
    """Would re-rendering this unit move the API off the address it binds today?

    Units from before WS-C pass ``--host`` on ``ExecStart``; the template reads
    ``HAL0_BIND_HOST`` from ``api.env`` instead, and ``hal0 serve`` falls back to
    loopback when that is unset. Nothing adds the variable to an old ``api.env``,
    so rewriting such a unit would drop a LAN box off the network at its next
    restart. An unreadable or undecodable ``api.env`` counts as not setting it.
    """
    if not re.search(r"^\s*ExecStart=.*\s--host[\s=]", installed, re.MULTILINE):
        return False
    api_env = recover_params(installed).get("api_env") or f"{DEFAULT_ETC_DIR}/api.env"
    reader = read if read is not None else _read_api_env
    try:
        text = reader(api_env)
    except (OSError, UnicodeError):
        return True
    return not re.search(r"^\s*HAL0_BIND_HOST=\S", text, re.MULTILINE)


def foreign_user(installed: str) -> str | None:
    """The ``User=`` an installed unit runs as, when it is not ``hal0``.

    The template renders ``User=hal0``/``Group=hal0``. A unit from 0.9.3-0.9.8
    runs as ``root`` (``UMask=0002``); re-rendering it and restarting would move
    a root-run API to ``hal0`` onto root-owned trees (217/USER or EACCES), so the
    refresh is held for any account other than ``hal0``. ``None`` when the unit
    names ``hal0`` or no ``User=`` at all.
    """
    for raw in installed.splitlines():
        key, sep, value = raw.strip().partition("=")
        if sep and key == "User" and value.strip() not in ("", "hal0"):
            return value.strip()
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="render the unit to stdout")
    r.add_argument("--template", required=True, type=Path)
    r.add_argument("--bin", required=True)
    r.add_argument("--workdir", required=True)
    r.add_argument("--api-env", required=True)
    r.add_argument("--etc-dir", required=True)
    r.add_argument("--hf-secrets-env", required=True)
    args = ap.parse_args(argv)
    params = ApiUnitParams(
        bin=args.bin,
        workdir=args.workdir,
        api_env=args.api_env,
        etc_dir=args.etc_dir,
        hf_secrets_env=args.hf_secrets_env,
        journal_group=host_has_journal_group(),
    )
    sys.stdout.write(render_api_unit(args.template.read_text(encoding="utf-8"), params))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
