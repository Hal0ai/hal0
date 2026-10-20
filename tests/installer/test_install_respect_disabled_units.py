"""A re-run must not re-enable units the operator turned off (#2807).

Slices the bench-unit block and the Hermes provision + gateway block out of
install.sh and runs them under ``bash -euo pipefail`` against stateful stubs:

* ``systemctl`` keeps each unit's enablement state and active flag in files,
  so a stub that enables a unit is visible to a later ``is-enabled``;
* ``hal0`` stands in for ``hal0 agent install hermes``: it creates the venv
  and, unless given ``--no-gateway``, enables + starts the gateway exactly as
  ``_install_hermes_gateway`` does (src/hal0/cli/agent_commands.py);
* ``hermes`` stands in for the upstream ``hermes gateway install`` generator:
  it writes the unit file, then enables and starts it unless told not to.

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
"$VENV/bin/hermes" gateway install --system --run-as-user hal0
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
        self, body: str, *, venv: str = "/usr/lib/hal0/venv"
    ) -> subprocess.CompletedProcess[str]:
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


def test_gateway_disabled_generator_never_starts_it(tmp_path: Path) -> None:
    box = Box(tmp_path)
    box.provisioned()
    box.set_state("hermes-gateway.service", "disabled")
    (box.unit_dir / "hermes-gateway.service").write_text("[Unit]\n")
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    gen = [c for c in box.log() if c.startswith("hermes gateway install")]
    assert gen and "--no-start-now" in gen[0] and "--no-start-on-login" in gen[0]
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
    assert (
        "(run 'systemctl unmask hermes-gateway.service && systemctl enable --now "
        "hermes-gateway.service'" in res.stdout
    )


def test_gateway_fresh_install_enables(tmp_path: Path) -> None:
    box = Box(tmp_path)
    res = box.run(HERMES_BLOCK)
    assert res.returncode == 0, res.stderr
    assert "hal0 agent install hermes" in box.log()
    assert box.state_of("hermes-gateway.service") == "enabled"
    assert box.active("hermes-gateway.service")
