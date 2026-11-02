"""#3211: the podman0 ACCEPT must survive Docker or podman0 appearing later.

``hal0-podman-forward.service`` used to exit 0 when ``podman0`` did not exist yet
and then stay active through ``RemainAfterExit=yes``. netavark only creates the
bridge with the first container, so a Docker that came up first left the box with
no rule and no second run: Open WebUI's :3001 answered locally and timed out from
the LAN.

These drive the real ``ExecStart`` from the shipped unit against a fake
``iptables`` on PATH (real systemd and firewall state are not available in CI),
and slice the real install.sh block. Whether systemd and Docker do the rest is a
live check on a box with Docker.
"""

from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
UNIT = REPO / "packaging" / "systemd" / "hal0-podman-forward.service"
INSTALL_SH = REPO / "installer" / "install.sh"


def _exec_start_script() -> str:
    """The bash program in the unit's ExecStart, as systemd would hand it to bash."""
    text = UNIT.read_text(encoding="utf-8")
    match = re.search(r"^ExecStart=/usr/bin/env bash -c '(.*?)'$", text, re.DOTALL | re.MULTILINE)
    assert match, "ExecStart no longer has the `bash -c '...'` shape this test parses"
    # systemd joins a trailing backslash-newline into one line.
    return match.group(1).replace("\\\n", " ")


def _fake_iptables(bin_dir: Path, *, chain: bool, rules_present: bool) -> Path:
    """An iptables that logs its argv and answers -L and -C as the box would."""
    calls = bin_dir / "iptables.calls"
    script = bin_dir / "iptables"
    script.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{calls}"\n'
        'case " $* " in\n'
        f'  *" -L "*) {"exit 0" if chain else "exit 1"} ;;\n'
        f'  *" -C "*) {"exit 0" if rules_present else "exit 1"} ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return calls


def _run_exec_start(bin_dir: Path) -> subprocess.CompletedProcess[str]:
    # PATH holds only the fakes, so a stray `ip link show podman0` would fail
    # exactly as it does on a box with no bridge yet.
    return subprocess.run(
        ["/bin/bash", "-c", _exec_start_script()],
        env={"PATH": str(bin_dir)},
        capture_output=True,
        text=True,
        check=False,
    )


def _inserts(calls: Path) -> list[str]:
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return [line for line in lines if " -I " in f" {line} "]


def test_rule_is_added_before_the_bridge_exists(tmp_path: Path) -> None:
    """The #3211 race: Docker is up, the first container has not created podman0."""
    calls = _fake_iptables(tmp_path, chain=True, rules_present=False)

    result = _run_exec_start(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _inserts(calls) == [
        "-w 5 -I DOCKER-USER -i podman0 -j ACCEPT",
        "-w 5 -I DOCKER-USER -o podman0 -j ACCEPT",
    ]


def test_unit_does_not_consult_the_link() -> None:
    assert "ip link" not in _exec_start_script()


def test_existing_rules_are_not_duplicated(tmp_path: Path) -> None:
    calls = _fake_iptables(tmp_path, chain=True, rules_present=True)

    assert _run_exec_start(tmp_path).returncode == 0
    assert _inserts(calls) == []


def test_no_docker_chain_is_a_clean_noop(tmp_path: Path) -> None:
    calls = _fake_iptables(tmp_path, chain=False, rules_present=False)

    assert _run_exec_start(tmp_path).returncode == 0
    assert _inserts(calls) == []


def test_no_iptables_is_a_clean_noop(tmp_path: Path) -> None:
    assert _run_exec_start(tmp_path).returncode == 0


def test_every_iptables_call_waits_for_the_lock() -> None:
    """A lost xtables lock on the -L probe must not read as "no Docker"."""
    script = _exec_start_script()
    calls = re.findall(r"iptables(?! >)\s+(\S+)", script.replace("command -v iptables", ""))
    assert calls
    assert set(calls) == {"-w"}


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("RemainAfterExit=", "an active unit makes the next Docker start a no-op for it"),
        ("PartOf=", "only useful to stop a unit that stays active"),
        ("multi-user.target", "a boot run with no Docker would do nothing"),
    ],
)
def test_unit_runs_on_every_docker_start(line: str, why: str) -> None:
    directives = [
        ln for ln in UNIT.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")
    ]
    assert not any(line in ln for ln in directives), why


def test_unit_is_ordered_after_and_installed_into_docker() -> None:
    text = UNIT.read_text(encoding="utf-8")
    assert re.search(r"^After=docker\.service$", text, re.MULTILINE)
    assert re.search(r"^WantedBy=docker\.service$", text, re.MULTILINE)


def _install_block() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index('if [[ -f "${PODMAN_FWD_UNIT_DST}" ]]')
    return text[start : text.index("\n    fi\n", start)]


def test_installer_enables_without_requiring_docker_today() -> None:
    """A Docker installed after hal0 must still trigger the unit."""
    block = _install_block()
    assert "command -v docker" not in block
    assert "systemctl reenable hal0-podman-forward" in block


def test_installer_reruns_a_unit_left_active_by_an_older_release() -> None:
    block = _install_block()
    assert "systemctl restart hal0-podman-forward" in block
    assert "--now" not in block
