"""Shared access to the packaged OpenWebUI systemd unit.

One owner per fact: the path of the shipped unit
(``packaging/systemd/hal0-openwebui.service``) lives here, and every test
that reads it imports it from here rather than re-derying the path. The
shipped unit is the source of truth for what a box installs —
``installer/install.sh`` copies it verbatim into ``/etc/systemd/system``.

``unit_sections`` is a small systemd-unit parser: it returns the directive
lines of each section, with comments and blank lines dropped, so a test can
ask which section a directive landed in. That is the question #3524 turned
on — ``StartLimitIntervalSec`` is a ``[Unit]`` directive
(``systemd.unit(5)``), and under ``[Service]`` systemd logs "Unknown key"
and silently drops it.
"""

from __future__ import annotations

from pathlib import Path

# tests/openwebui/<file> → repo root is parents[2].
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The packaged unit — the file ``installer/install.sh`` copies to
#: ``/etc/systemd/system/hal0-openwebui.service``.
PACKAGED_UNIT = REPO_ROOT / "packaging" / "systemd" / "hal0-openwebui.service"


def unit_sections(text: str) -> dict[str, list[str]]:
    """Return ``{section name: [directive lines]}`` for a systemd unit file.

    Directives before the first section header land under the empty-string
    key (systemd treats them as belonging to no section). Comment and blank
    lines are dropped; the text of each directive is kept verbatim so a test
    can assert on the exact ``Key=Value`` pair.
    """
    sections: dict[str, list[str]] = {}
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections
