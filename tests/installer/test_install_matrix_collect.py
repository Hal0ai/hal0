"""collect.sh: the measurements the install matrix takes inside the guest.

Real bash under subprocess. ``df``, ``systemctl``, ``curl`` and ``hal0`` are
stubs first on PATH; ``/sys/class/net`` and ``/etc/hal0/slots`` live in a temp
tree reached through ``HAL0_COLLECT_ROOT``. No network, no root.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COLLECT = REPO / "scripts" / "install-matrix" / "collect.sh"

SSE = (
    'data: {"model":"hal0-agent-q4","choices":[{"delta":{"role":"assistant"}}]}\n\n'
    'data: {"model":"hal0-agent-q4","choices":[{"delta":{"content":"ready"}}]}\n\n'
    "data: [DONE]\n\n"
)

CURL_STUB = r"""#!/usr/bin/env bash
d="$STUB_DIR"
url="${*: -1}"
want_code=0
for a in "$@"; do [[ "$a" == -w ]] && want_code=1; done
code() { if ((want_code)); then printf '%s' "$1"; fi; }
case "$url" in
  */api/health)
    [[ -e "$d/health_fail" ]] && exit 7
    code 200 ;;
  */v1/chat/completions)
    [[ -e "$d/chat_fail" ]] && exit 7
    cat "$d/chat.sse" ;;
  */api/memory/add)
    [[ -e "$d/memory_fail" ]] && exit 7
    code 200 ;;
  */v1/runs)
    [[ -e "$d/hermes_fail" ]] && exit 7
    printf '%s' '{"run_id":"run_1","status":"queued"}' ;;
  */v1/runs/run_1)
    printf '%s' '{"run_id":"run_1","status":"completed","output":"ready"}' ;;
  *) exit 22 ;;
esac
"""

HAL0_STUB = """#!{python}
import os, sys
args = sys.argv[1:]
if args[:2] == ["doctor", "--json"]:
    print(open(os.environ["STUB_DIR"] + "/doctor.json").read())
    sys.exit(int(os.environ.get("STUB_DOCTOR_EXIT", "1")))
if args[:2] == ["memory", "recall"]:
    print('{{"items":[{{"content":"note ' + args[2] + '"}}]}}')
    sys.exit(0)
sys.exit(3)
"""

DOCTOR = (
    '[{"id":"a","severity":"ok"},{"id":"b","severity":"ok"},'
    '{"id":"c","severity":"warn"},{"id":"d","severity":"critical"}]'
)

BARE_TOOLS = (
    "bash cat tr date sleep od timeout ls head tail sort readlink dirname df mkdir "
    "kill env sed"
).split()


class Guest:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "root"
        self.stubs = tmp_path / "stubs"
        self.bin = tmp_path / "bin"
        for d in (self.root, self.stubs, self.bin):
            d.mkdir()
        self.write_bin(
            "df", "#!/usr/bin/env bash\nprintf '%s\\n' '         Used' '  4242424242'\n"
        )
        self.write_bin(
            "systemctl",
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' '● hal0-api.service loaded failed failed hal0 API' "
            "'  hal0-slot@agent.service loaded failed failed slot'\n",
        )
        self.write_bin("curl", CURL_STUB)
        # A shebang cannot hold a path with spaces, so reach the interpreter
        # through a symlink in the temp tree (the launcher's shebang in a guest).
        py = tmp_path / "py" / "python3"
        py.parent.mkdir()
        py.symlink_to(sys.executable)
        self.write_bin("hal0", HAL0_STUB.format(python=py))
        (self.stubs / "chat.sse").write_text(SSE)
        (self.stubs / "doctor.json").write_text(DOCTOR)
        self.net("lo", 999)
        self.net("eth0", 1000)
        self.net("eth1", 234)

    def write_bin(self, name: str, text: str) -> None:
        p = self.bin / name
        p.write_text(text)
        p.chmod(0o755)

    def net(self, name: str, rx: int) -> None:
        d = self.root / "sys/class/net" / name / "statistics"
        d.mkdir(parents=True)
        (d / "rx_bytes").write_text(f"{rx}\n")

    def run(self, mode: str, extra_env: dict[str, str] | None = None, bare: bool = False):
        path = str(self.bin)
        if bare:
            tools = self.bin.parent / "tools"
            tools.mkdir(exist_ok=True)
            for t in BARE_TOOLS:
                src = shutil.which(t)
                if src and not (tools / t).exists():
                    (tools / t).symlink_to(src)
            path = str(tools)
        else:
            path = f"{self.bin}:{os.environ['PATH']}"
        env = {
            "PATH": path,
            "HOME": str(self.root),
            "HAL0_COLLECT_ROOT": str(self.root),
            "STUB_DIR": str(self.stubs),
            "HAL0_COLLECT_HEALTH_TRIES": "2",
            "HAL0_COLLECT_HEALTH_SLEEP": "0.05",
            "HAL0_COLLECT_POLL_SLEEP": "0.05",
            "HAL0_COLLECT_MEMORY_TIMEOUT": "2",
            "HAL0_COLLECT_HERMES_TIMEOUT": "2",
            "HAL0_COLLECT_CHAT_TIMEOUT": "5",
            "HERMES_HOME": str(self.root / "hermes"),
        }
        env.update(extra_env or {})
        bash = shutil.which("bash") or "/bin/bash"
        proc = subprocess.run(
            [bash, str(COLLECT), mode],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc, parse(proc.stdout)

    def hermes_env(self) -> None:
        h = self.root / "hermes"
        h.mkdir()
        (h / ".env").write_text("API_SERVER_KEY=0123456789abcdef0123\n")


def parse(out: str) -> dict[str, str]:
    kv: dict[str, str] = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        kv[k] = v
    return kv


@pytest.fixture
def guest(tmp_path: Path) -> Guest:
    return Guest(tmp_path)


PRE_KEYS = {"disk_used_bytes", "net_rx_bytes"}
POST_KEYS = PRE_KEYS | {"slot_count", "slots", "failed_units", "install_log", "failure_report"}
PROBE_KEYS = {
    "api_health_s", "doctor_ok", "doctor_warn", "doctor_critical", "doctor_exit",
    "chat_ok", "chat_first_token_s", "chat_model", "memory_write_ok", "memory_read_ok",
    "hermes_reply_ok", "hermes_reply_s",
}  # fmt: skip


def test_pre_prints_disk_and_rx(guest: Guest):
    proc, kv = guest.run("pre")
    assert proc.returncode == 0
    assert proc.stderr == ""
    assert kv == {"disk_used_bytes": "4242424242", "net_rx_bytes": "1234"}


def test_post_counts_slots(guest: Guest):
    slots = guest.root / "etc/hal0/slots"
    slots.mkdir(parents=True)
    for n in ("memory", "agent", "brain"):
        (slots / f"{n}.toml").write_text("x = 1\n")
    logs = guest.root / "var/log/hal0"
    logs.mkdir(parents=True)
    old = logs / "install-20260101-aaaa.log"
    new = logs / "install-20260102-bbbb.log"
    old.write_text("")
    new.write_text("")
    os.utime(old, (1, 1))
    proc, kv = guest.run("post")
    assert proc.returncode == 0
    assert set(kv) == POST_KEYS
    assert kv["slot_count"] == "3"
    assert kv["slots"] == "agent,brain,memory"
    assert kv["failed_units"] == "hal0-api.service,hal0-slot@agent.service"
    assert kv["install_log"] == str(new)
    assert kv["failure_report"] == ""


def test_post_reports_failure_report_path(guest: Guest):
    logs = guest.root / "var/log/hal0"
    logs.mkdir(parents=True)
    (logs / "install-1.log").write_text("")
    rep = logs / "hal0-install-report-20260101-xyz.txt"
    rep.write_text("")
    _, kv = guest.run("post")
    assert kv["failure_report"] == str(rep)
    assert kv["install_log"].endswith("install-1.log")


def test_probe_health_timeout_prints_minus_one(guest: Guest):
    (guest.stubs / "health_fail").write_text("")
    proc, kv = guest.run("probe")
    assert proc.returncode == 0
    assert kv["api_health_s"] == "-1"


def test_probe_health_answers(guest: Guest):
    _, kv = guest.run("probe")
    assert float(kv["api_health_s"]) >= 0


def test_probe_doctor_counts(guest: Guest):
    proc, kv = guest.run("probe")
    assert proc.returncode == 0
    assert (kv["doctor_ok"], kv["doctor_warn"], kv["doctor_critical"]) == ("2", "1", "1")
    assert kv["doctor_exit"] == "1"


def test_probe_chat_success(guest: Guest):
    _, kv = guest.run("probe")
    assert kv["chat_ok"] == "1"
    assert float(kv["chat_first_token_s"]) >= 0
    assert kv["chat_model"] == "hal0-agent-q4"


def test_probe_chat_failure_is_not_fatal(guest: Guest):
    (guest.stubs / "chat_fail").write_text("")
    proc, kv = guest.run("probe")
    assert proc.returncode == 0
    assert kv["chat_ok"] == "0"
    assert kv["chat_first_token_s"] == "-1"


def test_probe_memory_roundtrip(guest: Guest):
    _, kv = guest.run("probe")
    assert (kv["memory_write_ok"], kv["memory_read_ok"]) == ("1", "1")


def test_probe_memory_write_failure(guest: Guest):
    (guest.stubs / "memory_fail").write_text("")
    proc, kv = guest.run("probe")
    assert proc.returncode == 0
    assert (kv["memory_write_ok"], kv["memory_read_ok"]) == ("0", "0")


def test_probe_hermes_reply(guest: Guest):
    guest.hermes_env()
    _, kv = guest.run("probe")
    assert kv["hermes_reply_ok"] == "1"
    assert float(kv["hermes_reply_s"]) >= 0


def test_probe_hermes_without_key_is_not_fatal(guest: Guest):
    proc, kv = guest.run("probe")
    assert proc.returncode == 0
    assert (kv["hermes_reply_ok"], kv["hermes_reply_s"]) == ("0", "-1")


def test_values_are_single_line(guest: Guest):
    (guest.stubs / "doctor.json").write_text(DOCTOR.replace(",", ",\n"))
    guest.write_bin(
        "systemctl",
        "#!/usr/bin/env bash\nprintf '%s\\n' 'a.service loaded failed failed A' 'b.service x y z'\n",
    )
    for mode in ("pre", "post", "probe"):
        proc, _ = guest.run(mode)
        for line in proc.stdout.splitlines():
            key, sep, value = line.partition("=")
            assert sep == "=" and key and " " not in key, line
        assert proc.stdout.endswith("\n")


@pytest.mark.parametrize(
    ("mode", "keys"), [("pre", PRE_KEYS), ("post", POST_KEYS), ("probe", PROBE_KEYS)]
)
def test_every_key_prints_with_no_tools(guest: Guest, mode: str, keys: set[str]):
    """A failed install: no hal0, curl, systemctl or Python. Every key, exit 0."""
    proc, kv = guest.run(mode, bare=True)
    assert proc.returncode == 0
    assert set(kv) == keys
    if mode == "probe":
        assert kv["api_health_s"] == "-1"
        assert kv["doctor_ok"] == "-1"
        assert kv["doctor_exit"] == "-1"
        assert kv["chat_ok"] == "0"
        assert kv["memory_write_ok"] == "0"
        assert kv["hermes_reply_ok"] == "0"


def test_doctor_without_python_is_minus_one(guest: Guest):
    guest.write_bin(
        "hal0", "#!/usr/bin/env bash\ncat \"$STUB_DIR/doctor.json\"\nexit 1\n"
    )
    _, kv = guest.run("probe")
    assert (kv["doctor_ok"], kv["doctor_warn"], kv["doctor_critical"]) == ("-1", "-1", "-1")
    assert kv["doctor_exit"] == "1"


def test_unknown_mode_exits_zero(guest: Guest):
    proc, kv = guest.run("bogus")
    assert proc.returncode == 0
    assert kv == {}
