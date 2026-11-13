"""#3524: the packaged OpenWebUI unit must carry its restart cap in [Unit].

``StartLimitIntervalSec=`` and ``StartLimitBurst=`` are ``[Unit]``-section
directives (``systemd.unit(5)``). The shipped unit carried them under
``[Service]``, where systemd logs "Unknown key" and silently drops them —
so the unit fell back to systemd's 10s default window and the intended
120s/10 restart cap was never in force on the box.

Two halves of the guard:

* **placement** — the keys are in ``[Unit]`` and absent from ``[Service]``.
  This is the always-on assertion; it needs no systemd on the box.
* **effective settings** — the values are the intended 120/10, and systemd
  itself accepts the file (``systemd-analyze verify`` reports no
  ``Unknown key``). That half skips cleanly where ``systemd-analyze`` is
  not installed.

The unit path comes from :mod:`tests.openwebui._owui_unit`, the single
owner of "where the shipped OpenWebUI unit lives".
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.openwebui._owui_unit import PACKAGED_UNIT, unit_sections

#: The restart cap the unit is meant to enforce.
EXPECTED_INTERVAL = "120"
EXPECTED_BURST = "10"

_STARTLIMIT_KEYS = ("StartLimitIntervalSec", "StartLimitBurst")


@pytest.fixture(scope="module")
def sections() -> dict[str, list[str]]:
    assert PACKAGED_UNIT.is_file(), f"missing packaged unit at {PACKAGED_UNIT}"
    return unit_sections(PACKAGED_UNIT.read_text(encoding="utf-8"))


def _values(section_lines: list[str], key: str) -> list[str]:
    """Return every value assigned to ``key`` in ``section_lines``."""
    prefix = f"{key}="
    return [ln[len(prefix) :] for ln in section_lines if ln.startswith(prefix)]


def test_startlimit_keys_land_in_unit_section_not_service(
    sections: dict[str, list[str]],
) -> None:
    """The keys must sit in [Unit] — and nowhere near [Service].

    Mirrors the generated-slot-unit guard in
    ``tests/providers/test_container.py`` (``test_startlimit_keys_land_in_unit_section_not_service``):
    under [Service] systemd drops them silently.
    """
    unit_lines = sections.get("Unit", [])
    service_lines = sections.get("Service", [])
    for key in _STARTLIMIT_KEYS:
        assert _values(unit_lines, key), f"{key} is missing from the [Unit] section"
        assert not _values(service_lines, key), (
            f"{key} is in the [Service] section — systemd.unit(5) puts it in "
            "[Unit], and under [Service] systemd drops it with an "
            "'Unknown key' warning (#3524)"
        )


def test_startlimit_values_are_the_intended_cap(
    sections: dict[str, list[str]],
) -> None:
    """The cap itself must not drift while it moves sections."""
    unit_lines = sections.get("Unit", [])
    assert _values(unit_lines, "StartLimitIntervalSec") == [EXPECTED_INTERVAL]
    assert _values(unit_lines, "StartLimitBurst") == [EXPECTED_BURST]


def test_startlimit_keys_are_not_duplicated(sections: dict[str, list[str]]) -> None:
    """One value each — a second copy would be ambiguous to systemd."""
    for name, lines in sections.items():
        for key in _STARTLIMIT_KEYS:
            values = _values(lines, key)
            assert len(values) <= 1, f"{key} appears {len(values)} times in [{name}]"


@pytest.mark.skipif(
    shutil.which("systemd-analyze") is None,
    reason="systemd-analyze not installed on this box",
)
def test_systemd_accepts_the_packaged_unit() -> None:
    """systemd must not report an unknown key for the shipped unit.

    ``systemd-analyze verify`` exits non-zero for benign reasons on a dev
    box (e.g. "Command /usr/bin/podman is not executable" without podman),
    so the assertion is on the message, not the exit code: no line may
    report an unknown key.
    """
    proc = subprocess.run(
        ["systemd-analyze", "verify", str(PACKAGED_UNIT)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = proc.stdout + proc.stderr
    unknown = [ln for ln in output.splitlines() if "Unknown key" in ln]
    assert not unknown, (
        "systemd rejects a directive in the packaged OpenWebUI unit "
        f"({PACKAGED_UNIT}):\n" + "\n".join(unknown)
    )
