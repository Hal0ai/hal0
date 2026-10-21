"""Normalise the gateway unit upstream generates (ADR-0032 decision 12, ADR-0034 decision 5).

``hermes gateway install --system`` bakes the interpreter it runs under into
``/etc/systemd/system/hermes-gateway.service``. Started through
``/var/lib/hal0/venvs/hermes``, that interpreter resolves to the release it was
built in, so the unit ends up naming ``/var/lib/hal0/hermes/releases/<id>/``.
The shape differs by upstream ref:

* 3ef6bbd2: ``ExecStart=<venv>/bin/python -m hermes_cli.main … gateway run``
  plus ``Environment="VIRTUAL_ENV=<venv>"``;
* v0.22.2: ``ExecStart``, ``ExecStop`` and ``ExecStopPost`` all run
  ``"<venv>/bin/python" "-I" "-c" "…sys.path.insert(0, '<checkout>')…"``.

Both name a release path, and the prune after the next activation would delete
it. :func:`normalise_gateway_unit` replaces every
``/var/lib/hal0/hermes/releases/<id>/`` substring anywhere in the file with
``/var/lib/hal0/hermes/current/``, then reloads systemd and try-restarts the
gateway so a unit the generator already started moves onto the stable path.

Runs as root, straight after the generator: ``_install_hermes_gateway`` imports
it, and ``install.sh`` runs ``python -m hal0.agents.hermes_gateway_unit``.
Exit codes for the module form: 0 normalised (or nothing to do), 2 a release
path remains, 1 the unit could not be read or written.
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404 — fixed systemctl argv
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from hal0.agents.hermes_layout import GATEWAY_UNIT_PATH, HERMES_RELEASE_ROOT

GATEWAY_UNIT = "hermes-gateway.service"


def _release_prefix_re(root: Path) -> re.Pattern[str]:
    # A release id never contains a slash, quote or whitespace. The id may be
    # followed by a slash (a path inside the release), whitespace, a quote or
    # the end of the line (the release directory itself).
    return re.compile(re.escape(f"{root}/releases/") + r"[^/\s'\"]+(?=[/\s'\"]|$)", re.MULTILINE)


def normalise_text(text: str, *, root: Path = HERMES_RELEASE_ROOT) -> str:
    """Replace every ``<root>/releases/<id>`` path prefix with ``<root>/current``."""
    return _release_prefix_re(root).sub(f"{root}/current", text)


def remaining_release_paths(text: str, *, root: Path = HERMES_RELEASE_ROOT) -> list[str]:
    """Any ``<root>/releases/`` reference still in ``text``, one per line."""
    needle = f"{root}/releases/"
    return [line.strip() for line in text.splitlines() if needle in line]


@dataclass
class NormaliseResult:
    changed: bool = False
    remaining: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.remaining


def normalise_gateway_unit(
    unit_path: Path = GATEWAY_UNIT_PATH,
    *,
    root: Path = HERMES_RELEASE_ROOT,
    run: Callable[..., subprocess.CompletedProcess] | None = None,
    reload: bool = True,
) -> NormaliseResult:
    """Rewrite release paths in the gateway unit to ``current``; reload and try-restart.

    ``try-restart`` is a no-op on a stopped unit, so a gateway the operator
    stopped stays stopped.
    """
    run = run if run is not None else subprocess.run
    result = NormaliseResult()
    try:
        text = unit_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return result
    except (OSError, UnicodeDecodeError) as exc:
        result.error = f"cannot read {unit_path}: {exc}"
        return result
    new = normalise_text(text, root=root)
    if new != text:
        tmp = unit_path.with_name(f".{unit_path.name}.hal0-{os.getpid()}")
        try:
            tmp.write_text(new, encoding="utf-8")
            os.chmod(tmp, 0o644)
            os.replace(tmp, unit_path)
        except OSError as exc:
            result.error = f"cannot write {unit_path}: {exc}"
            return result
        result.changed = True
    result.remaining = remaining_release_paths(new, root=root)
    if result.changed and reload:
        for argv in (["systemctl", "daemon-reload"], ["systemctl", "try-restart", GATEWAY_UNIT]):
            try:
                run(argv, check=False, capture_output=True, text=True, timeout=120)  # nosec B603
            except (OSError, subprocess.SubprocessError) as exc:
                result.error = f"{' '.join(argv)}: {exc}"
                break
    return result


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    unit = Path(args[0]) if args else GATEWAY_UNIT_PATH
    result = normalise_gateway_unit(unit)
    if result.error:
        print(f"hermes-gateway unit: {result.error}", file=sys.stderr)
        return 1
    if result.remaining:
        print(
            "hermes-gateway unit still names a release path: " + "; ".join(result.remaining),
            file=sys.stderr,
        )
        return 2
    if result.changed:
        print(f"hermes-gateway unit normalised to {HERMES_RELEASE_ROOT}/current/")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised through main()
    raise SystemExit(main())
