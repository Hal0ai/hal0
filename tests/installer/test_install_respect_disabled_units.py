"""A re-run must not re-enable units the operator turned off (#2807).

Slices the bench-unit block and the Hermes provision + gateway block out of
install.sh and runs them under ``bash -euo pipefail`` against stateful stubs:

* ``systemctl`` keeps each unit's enablement state and active flag in files,
  so a stub that enables a unit is visible to a later ``is-enabled``;
* ``hal0`` stands in for ``hal0 agent install hermes``: it creates the venv
  and, unless given ``--no-gateway``, enables + starts the gateway exactly as
  ``_install_hermes_gateway`` does (src/hal0/cli/agent_commands.py);
* ``python`` stands in for hal0's own venv: ``-m hal0.agents.hermes_gateway_regen
  install`` writes the unit file from hal0's template and never enables or
  starts it (#2943); ``-m hal0.agents.hermes_gateway_unit`` is the normaliser;
* ``hermes`` is upstream's binary. Since #2943 the installer must never run
  it as root to install the gateway; the stub records any call so a test can
  prove that.

The real ``install`` binary is used, so a /dev/null mask symlink in the unit
directory is exercised for real.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEXT = (REPO / "installer" / "install.sh").read_text(encoding="utf-8")


def _slice(start: str, end: str) -> str:
    s = TEXT.index(start)
    return TEXT[s : TEXT.index(end, s)]


HELPERS = _slice("wait_http_ready() {", "DEV_MODE=0\n")
BENCH_BLOCK = _slice(
    "        # Capture the operator's on/off choice BEFORE the unit files are",
    '        info "wrote ${ETC_DIR}/bench',
)
HERMES_BLOCK = (
    _slice(
        '            warn "${HERMES_WRAPPER_SRC} not found — hermes wrapper not pre-installed"\n'
        "        fi\n    fi\n",
        '    elif [[ -f "${AGENT_UNIT_DST}" ]]; then\n        # No venv after the provision block',
    ).split("\n", 3)[3]
    + "    fi\n"
)
VENV = "/var/lib/hal0/venvs/hermes"

SYSTEMCTL = r"""#!/bin/bash
echo "$*" >> "$CALLS"
S="$STATE_DIR"
mode="$1"; shift
now=0; quiet=0; units=()
for a in "$@"; do
    case "$a" in
        --now) now=1 ;;
        --quiet) quiet=1 ;;
        -*) ;;
        *) units+=("$a") ;;
    esac
done
u="${units[0]:-}"
st() { cat "$S/$1" 2>/dev/null; }
case "$mode" in
    is-enabled)
        [[ -f "$S/$u" ]] || exit 1
        st "$u"
        [[ "$(st "$u")" == enabled ]] ;;
    is-active) [[ -f "$S/$u.active" ]] ;;
    enable)
        [[ "$(st "$u")" == masked* ]] && { echo "Unit $u is masked" >&2; exit 1; }
        echo enabled > "$S/$u"
        [[ $now -eq 1 ]] && touch "$S/$u.active"
        exit 0 ;;
    disable)
        [[ -f "$S/$u" && "$(st "$u")" != masked* ]] && echo disabled > "$S/$u"
        [[ $now -eq 1 ]] && rm -f "$S/$u.active"
        exit 0 ;;
    start)
        [[ "$(st "$u")" == masked* ]] && exit 1
        touch "$S/$u.active" ;;
    *) exit 0 ;;
esac
"""

HAL0 = r"""#!/bin/bash
echo "hal0 $*" >> "$CALLS"
mkdir -p "$VENV/bin"
cp "$HERMES_STUB" "$VENV/bin/hermes"
for a in "$@"; do [[ "$a" == --no-gateway ]] && exit 0; done
echo "[Unit]" > "$UNIT_DIR/hermes-gateway.service"
systemctl enable --now hermes-gateway.service
"""

HERMES = r"""#!/bin/bash
echo "hermes $*" >> "$CALLS"
start_now=1; on_login=1
for a in "$@"; do
    case "$a" in
        --no-start-now) start_now=0 ;;
        --no-start-on-login) on_login=0 ;;
    esac
done
echo "[Unit]" > "$UNIT_DIR/hermes-gateway.service"
systemctl daemon-reload
if [[ $on_login -eq 1 ]]; then systemctl enable hermes-gateway.service || exit 1; fi
if [[ $start_now -eq 1 ]]; then systemctl start hermes-gateway.service || exit 1; fi
exit 0
"""


# Stands in for ``${VENV_DIR}/bin/python`` (hal0's own venv). The template
# install writes the unit and never toggles it; the normaliser exits with
# $NORMALISE_RC (default 0) so a test can simulate a unit that still names a
# release path.
HAL0_PYTHON = r"""#!/bin/bash
echo "python $*" >> "$CALLS"
if [[ "$*" == "-I -m hal0.agents.hermes_gateway_regen state" ]]; then
    echo "${EFFECTIVE_GATEWAY_STATE:-not-found 0}"
    exit 0
fi
if [[ "$*" == "-I -m hal0.agents.hermes_gateway_regen install" ]]; then
    echo "[Unit]" > "$UNIT_DIR/hermes-gateway.service"
    systemctl daemon-reload
    exit "${GATEWAY_INSTALL_RC:-0}"
fi
exit "${NORMALISE_RC:-0}"
"""


class Box:
    """A throwaway host: unit dir, stub binaries, recorded systemctl state."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.state = tmp / "state"
        self.unit_dir = tmp / "units"
        self.bin = tmp / "bin"
        self.venv = tmp / "venv"
        self.calls = tmp / "calls.log"
        for d in (self.state, self.unit_dir, self.bin):
            d.mkdir()
        for name, body in (("systemctl", SYSTEMCTL), ("hal0", HAL0), ("hermes", HERMES)):
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        self.hal0_venv = tmp / "hal0-venv"
        (self.hal0_venv / "bin").mkdir(parents=True)
        (self.hal0_venv / "bin" / "python").write_text(HAL0_PYTHON)
        (self.hal0_venv / "bin" / "python").chmod(0o755)
        self.agent_unit = self.unit_dir / "hal0-agent@.service"
        self.agent_unit.write_text("[Unit]\n")
        repo = tmp / "repo" / "installer" / "systemd"
        repo.mkdir(parents=True)
        for unit in ("hal0-bench.service", "hal0-bench.timer", "hal0-bench-worker.service"):
            (repo / unit).write_text("[Service]\nExecStart=/usr/lib/hal0/venv/bin/hal0-bench\n")

    def set_state(self, unit: str, state: str, *, active: bool = False) -> None:
        (self.state / unit).write_text(state + "\n")
        if active:
            (self.state / f"{unit}.active").touch()

    def state_of(self, unit: str) -> str | None:
        p = self.state / unit
        return p.read_text().strip() if p.exists() else None

    def active(self, unit: str) -> bool:
        return (self.state / f"{unit}.active").exists()

    def mask(self, unit: str) -> Path:
        self.set_state(unit, "masked")
        link = self.unit_dir / unit
        link.symlink_to("/dev/null")
        return link

    def provisioned(self) -> None:
        (self.venv / "bin").mkdir(parents=True)
        (self.venv / "bin" / "hermes").write_text(HERMES)
        (self.venv / "bin" / "hermes").chmod(0o755)

    def log(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def run(
        self, body: str, *, venv: str | None = None, normalise_rc: int = 0
    ) -> subprocess.CompletedProcess[str]:
        venv = venv or (str(self.hal0_venv) if body is HERMES_BLOCK else "/usr/lib/hal0/venv")
        prelude = (
            'info() { echo "INFO: $*"; }\nwarn() { echo "WARN: $*"; }\n'
            'wait_active() { systemctl is-active --quiet "$1"; }\n'
            "preflight_git() { return 0; }\n"
            f"DEV_MODE=0\nETC_DIR=/etc/hal0\nREPO_ROOT={self.tmp / 'repo'}\n"
            f"UNIT_DIR={self.unit_dir}\nVENV_DIR={venv}\n"
            "BENCH_STATE_DIR=/var/lib/hal0-bench\nPY=python3\n"
            f"HAL0_BIN={self.bin / 'hal0'}\nAGENT_UNIT_DST={self.agent_unit}\n"
        )
        script = prelude + HELPERS + body.replace(VENV, str(self.venv))
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "CALLS": str(self.calls),
            "STATE_DIR": str(self.state),
            "UNIT_DIR": str(self.unit_dir),
            "VENV": str(self.venv),
            "HERMES_STUB": str(self.bin / "hermes"),
            "NORMALISE_RC": str(normalise_rc),
        }
        env.pop("HAL0_SKIP_HERMES", None)
        return subprocess.run(
            ["bash", "-euo", "pipefail", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


# ── bench units ──────────────────────────────────────────────────────────


def test_bench_disabled_units_stay_disabled(tmp_path: Path) -> None:
    box = Box(tmp_path)
    box.set_state("hal0-bench-worker.service", "disabled")
    box.set_state("hal0-bench.timer", "disabled")
    res = box.run(BENCH_BLOCK)
    assert res.returncode == 0, res.stderr
    assert not [c for c in box.log() if c.startswith("enable")]
    assert "is disabled; left as is (run 'systemctl enable --now hal0-bench.timer'" in res.stdout


def test_bench_fresh_install_still_enables(tmp_path: Path) -> None:
    box = Box(tmp_path)
    res = box.run(BENCH_BLOCK)
    assert res.returncode == 0, res.stderr
    assert box.state_of("hal0-bench.timer") == "enabled"
    assert box.state_of("hal0-bench-worker.service") == "enabled"


def test_bench_state_is_captured_before_the_unit_copy(tmp_path: Path) -> None:
    """A unit that first appears during the copy reads `disabled` to systemd
    (it has an [Install] section and no wants link yet). Captured after the
    copy, a fresh install would be mistaken for an operator opt-out."""
    box = Box(tmp_path)
    wrapper = box.bin / "install"
    real_install = shutil.which("install")
    assert real_install
    wrapper.write_text(
        f'#!/bin/bash\n"{real_install}" "$@" || exit\n'
        'u="$(basename "${@: -1}")"\n'
        '[[ -f "$STATE_DIR/$u" ]] || echo disabled > "$STATE_DIR/$u"\n'
    )
    wrapper.chmod(0o755)
    res = box.run(BENCH_BLOCK)
    assert res.returncode == 0, res.stderr
    assert box.state_of("hal0-bench.timer") == "enabled"
    assert box.state_of("hal0-bench-worker.service") == "enabled"


def test_bench_masked_runtime_note_names_runtime_unmask(tmp_path: Path) -> None:
    box = Box(tmp_path)
    box.set_state("hal0-bench.timer", "masked-runtime")
    res = box.run(BENCH_BLOCK)
    assert res.returncode == 0, res.stderr
    assert box.state_of("hal0-bench.timer") == "masked-runtime"
    assert (
        "hal0-bench.timer is masked-runtime; left as is (run 'systemctl unmask --runtime "
        "hal0-bench.timer && systemctl enable --now hal0-bench.timer'" in res.stdout
    )


def test_bench_only_disabled_one_is_respected(tmp_path: Path) -> None:
    box = Box(tmp_path)
    box.set_state("hal0-bench.timer", "disabled")
    res = box.run(BENCH_BLOCK)
    assert res.returncode == 0, res.stderr
    assert box.state_of("hal0-bench.timer") == "disabled"
    assert box.state_of("hal0-bench-worker.service") == "enabled"


def test_bench_masked_unit_file_is_never_written_through(tmp_path: Path) -> None:
    """GNU install would replace the mask symlink (silent unmask); uutils
    install fails "invalid target" and aborts the installer under set -e."""
    box = Box(tmp_path)
    link = box.mask("hal0-bench.timer")
    # Non-default venv so the python prefix rewrite runs too.
    res = box.run(BENCH_BLOCK, venv="/opt/hal0/venv")
    assert res.returncode == 0, res.stderr
    assert link.is_symlink() and os.readlink(link) == "/dev/null"
    assert box.state_of("hal0-bench.timer") == "masked"
    assert "/opt/hal0/venv" in (box.unit_dir / "hal0-bench-worker.service").read_text()
    assert box.state_of("hal0-bench-worker.service") == "enabled"


# ── hermes gateway ───────────────────────────────────────────────────────


def test_gateway_disabled_survives_the_repair_path(tmp_path: Path) -> None:
    """Agent unit present, venv missing: `hal0 agent install hermes` runs.
    Its default --gateway enables the gateway, so the state must be captured
    before it and handed over as --no-gateway."""
    box = Box(tmp_path)
    box.set_state("hermes-gateway.service", "disabled")
    (box.unit_dir / "hermes-gateway.service").write_text("[Unit]\n")
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert "hal0 agent install hermes --no-gateway" in box.log()
    assert box.state_of("hermes-gateway.service") == "disabled"
    assert not box.active("hermes-gateway.service")


def test_gateway_disabled_template_install_never_starts_it(tmp_path: Path) -> None:
    box = Box(tmp_path)
    box.provisioned()
    box.set_state("hermes-gateway.service", "disabled")
    (box.unit_dir / "hermes-gateway.service").write_text("[Unit]\n")
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert "python -I -m hal0.agents.hermes_gateway_regen install" in box.log()
    assert not [c for c in box.log() if c.startswith(("start", "enable hermes-gateway"))]
    assert box.state_of("hermes-gateway.service") == "disabled"
    assert "hermes-gateway.service is disabled; left as is" in res.stdout


def test_gateway_disabled_but_running_is_not_stopped(tmp_path: Path) -> None:
    box = Box(tmp_path)
    box.provisioned()
    box.set_state("hermes-gateway.service", "disabled", active=True)
    (box.unit_dir / "hermes-gateway.service").write_text("[Unit]\n")
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert box.active("hermes-gateway.service")
    assert "disable --now hermes-gateway.service" not in box.log()


def test_gateway_masked_generator_is_skipped(tmp_path: Path) -> None:
    box = Box(tmp_path)
    box.provisioned()
    link = box.mask("hermes-gateway.service")
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert not [c for c in box.log() if c.startswith("hermes ")]
    assert link.is_symlink() and os.readlink(link) == "/dev/null"
    assert box.state_of("hermes-gateway.service") == "masked"
    # No unit file is left to enable under a mask: the remedy re-runs the
    # install after the unmask (#3025).
    assert (
        f"(run 'systemctl unmask hermes-gateway.service && {box.bin / 'hal0'} agent install "
        "hermes'" in res.stdout
    )
    assert "systemctl enable --now hermes-gateway.service" not in res.stdout


def test_gateway_fresh_install_enables(tmp_path: Path) -> None:
    box = Box(tmp_path)
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert "hal0 agent install hermes" in box.log()
    assert box.state_of("hermes-gateway.service") == "enabled"
    assert box.active("hermes-gateway.service")


# ── gateway unit normaliser (ADR-0032 decision 12) ─────────────────────────


def test_template_install_is_followed_by_the_normaliser_before_enable(tmp_path: Path) -> None:
    box = Box(tmp_path)
    box.provisioned()
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    log = box.log()
    gen = next(
        i for i, c in enumerate(log) if c == "python -I -m hal0.agents.hermes_gateway_regen install"
    )
    norm = next(i for i, c in enumerate(log) if c == "python -I -m hal0.agents.hermes_gateway_unit")
    enable = next(i for i, c in enumerate(log) if c == "enable --now hermes-gateway.service")
    assert gen < norm < enable
    assert box.state_of("hermes-gateway.service") == "enabled"


def test_a_unit_left_naming_a_release_path_stays_disabled(tmp_path: Path) -> None:
    box = Box(tmp_path)
    box.provisioned()
    res = box.run(HERMES_BLOCK, normalise_rc=2)
    assert res.returncode == 0, res.stderr
    assert "still names a release path" in res.stdout
    # The template never enables the unit, so it was never on to turn off.
    assert box.state_of("hermes-gateway.service") in (None, "disabled")
    assert not box.active("hermes-gateway.service")
    assert "enable --now hermes-gateway.service" not in box.log()


def test_a_disabled_but_running_gateway_survives_a_failed_normalise(tmp_path: Path) -> None:
    box = Box(tmp_path)
    box.provisioned()
    box.set_state("hermes-gateway.service", "disabled", active=True)
    (box.unit_dir / "hermes-gateway.service").write_text("[Unit]\n")
    res = box.run(HERMES_BLOCK, normalise_rc=2)
    assert res.returncode == 0, res.stderr
    assert box.active("hermes-gateway.service")
    assert "disable --now hermes-gateway.service" not in box.log()


def test_the_installer_never_runs_hermes_to_install_the_gateway(tmp_path: Path) -> None:
    """#2943: upstream's generator run as root wrote hermes-gateway-<hash>.service
    with a baked release path and created /root/.hermes. Fresh and re-run alike,
    no ``hermes`` call may appear."""
    box = Box(tmp_path)
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    (tmp_path / "rerun").mkdir()
    box2 = Box(tmp_path / "rerun")
    box2.provisioned()
    res2 = box2.run(HERMES_BLOCK)
    assert res2.returncode == 0, res2.stderr
    for b in (box, box2):
        assert not [c for c in b.log() if c.startswith("hermes ")]
        assert (b.unit_dir / "hermes-gateway.service").exists()
        assert b.state_of("hermes-gateway.service") == "enabled"


def test_a_disabled_suffixed_unit_keeps_a_rerun_from_turning_the_gateway_on(
    tmp_path: Path, monkeypatch
) -> None:
    """#2943 review: only upstream's suffixed unit exists, disabled. The
    installer must read that as the operator's off switch."""
    monkeypatch.setenv("EFFECTIVE_GATEWAY_STATE", "disabled 0")
    box = Box(tmp_path)
    box.provisioned()
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert "python -I -m hal0.agents.hermes_gateway_regen state" in box.log()
    assert "enable --now hermes-gateway.service" not in box.log()
    assert "hermes-gateway.service is disabled; left as is" in res.stdout


def test_runtime_only_enablement_stays_runtime_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EFFECTIVE_GATEWAY_STATE", "enabled-runtime 1")
    box = Box(tmp_path)
    box.provisioned()
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert "enable --runtime --now hermes-gateway.service" in box.log()


# ── #2950: mixed and interrupted states ──────────────────────────────────────


def test_an_interrupted_move_reads_the_suffixed_units_state(tmp_path: Path, monkeypatch) -> None:
    """#2950 item 1: hermes-gateway.service exists, disabled and stopped, beside
    the enabled suffixed unit a killed move left. The capture must ask the
    `state` verb (the move's ownership rule), not stop the gateway."""
    monkeypatch.setenv("EFFECTIVE_GATEWAY_STATE", "enabled 1")
    box = Box(tmp_path)
    box.provisioned()
    box.set_state("hermes-gateway.service", "disabled")
    (box.unit_dir / "hermes-gateway.service").write_text("[Unit]\n")
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert "python -I -m hal0.agents.hermes_gateway_regen state" in box.log()
    assert "disable --now hermes-gateway.service" not in box.log()
    assert "enable --now hermes-gateway.service" in box.log()


def test_an_enabled_canonical_unit_is_not_second_guessed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EFFECTIVE_GATEWAY_STATE", "disabled 0")
    box = Box(tmp_path)
    box.provisioned()
    box.set_state("hermes-gateway.service", "enabled")
    (box.unit_dir / "hermes-gateway.service").write_text("[Unit]\n")
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert "python -I -m hal0.agents.hermes_gateway_regen state" not in box.log()
    assert box.state_of("hermes-gateway.service") == "enabled"


def test_a_masked_gateway_still_moves_a_suffixed_unit_off(tmp_path: Path) -> None:
    """#2950 item 2: the masked branch runs the mask-safe `migrate` verb, so a
    suffixed gateway beside the mask is stopped and removed; nothing is
    written through the mask."""
    box = Box(tmp_path)
    box.provisioned()
    link = box.mask("hermes-gateway.service")
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert "python -I -m hal0.agents.hermes_gateway_regen migrate" in box.log()
    assert "python -I -m hal0.agents.hermes_gateway_regen install" not in box.log()
    assert link.is_symlink() and os.readlink(link) == "/dev/null"


# ── #3020 review follow-ups ──────────────────────────────────────────────────


def test_a_finished_move_with_a_leftover_file_is_not_reported_as_not_installed(
    tmp_path: Path, monkeypatch
) -> None:
    """(b) `install` exit 3: the gateway moved, only the old suffixed file is
    left. The installer says so and still enables the gateway."""
    monkeypatch.setenv("GATEWAY_INSTALL_RC", "3")
    box = Box(tmp_path)
    box.provisioned()
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    out = res.stdout + res.stderr
    assert "moved to hermes-gateway.service" in out
    assert "hermes-gateway unit not installed" not in out
    assert box.state_of("hermes-gateway.service") == "enabled"


def test_a_failed_install_still_warns_not_installed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_INSTALL_RC", "1")
    box = Box(tmp_path)
    box.provisioned()
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert "hermes-gateway unit not installed" in res.stdout + res.stderr


# ── #3024: a runtime mask with no /etc canonical file ────────────────────────


def test_a_runtime_mask_runs_the_move_and_writes_no_unit(tmp_path: Path) -> None:
    """#3024: is-enabled reports masked-runtime only while there is no /etc file.
    The installer runs the mask-safe `migrate`, never `install` (whose /etc file
    would shadow the /run mask), and enables nothing."""
    box = Box(tmp_path)
    box.provisioned()
    box.set_state("hermes-gateway.service", "masked-runtime")
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    log = box.log()
    assert "python -I -m hal0.agents.hermes_gateway_regen migrate" in log
    assert "python -I -m hal0.agents.hermes_gateway_regen install" not in log
    assert not (box.unit_dir / "hermes-gateway.service").exists()
    assert not [
        c for c in log if "hermes-gateway" in c and c.startswith(("enable", "disable", "start"))
    ]
    assert "hermes-gateway.service is masked-runtime; left as is" in res.stdout
    assert (
        f"systemctl unmask --runtime hermes-gateway.service && {box.bin / 'hal0'} agent install "
        "hermes'" in res.stdout
    )
