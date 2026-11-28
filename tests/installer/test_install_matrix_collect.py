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
import time
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
printf '%s\n' "$*" >> "$d/curl.args"
printf '%s\n' "$url" >> "$d/curl.urls"
want_code=0
hdr=""
prev=""
for a in "$@"; do
  [[ "$a" == -w ]] && want_code=1
  [[ "$prev" == -H && "$a" == @- ]] && hdr="$(cat)"
  prev="$a"
done
case "$url" in */v1/runs*)
  [[ "$hdr" == *"Authorization: Bearer $STUB_KEY"* ]] || { printf '%s' '{"error":"unauthorized"}'; exit 0; } ;;
esac
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
    for a in "$@"; do [[ "$a" == \{* ]] && printf '%s' "$a" > "$d/memory.body"; done
    code 200 ;;
  */v1/runs)
    [[ -e "$d/hermes_fail" ]] && exit 7
    printf '%s' '{"run_id":"run_1","status":"queued"}' ;;
  */v1/runs/run_1)
    printf '%s' "$(cat "$d/run.json")" ;;
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
    if os.path.exists(os.environ["STUB_DIR"] + "/recall_empty"):
        print('{{"items":[]}}')
    else:
        body = open(os.environ["STUB_DIR"] + "/memory.body").read()
        print('{{"items":[{{"content":' + repr(body) + '}}]}}')
    sys.exit(0)
sys.exit(3)
"""

KEY = "0123456789abcdef0123"

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
        # (a wrapper script, so the venv and its PyYAML stay visible).
        py = tmp_path / "py" / "python3"
        py.parent.mkdir()
        py.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n')
        py.chmod(0o755)
        self.write_bin("hal0", HAL0_STUB.format(python=py))
        (self.stubs / "chat.sse").write_text(SSE)
        self.set_run('{"run_id":"run_1","status":"completed","output":"ready"}')
        (self.stubs / "doctor.json").write_text(DOCTOR)
        self.net("lo", 999)
        self.net("eth0", 1000)
        self.net("eth1", 234)

    def set_run(self, body: str) -> None:
        (self.stubs / "run.json").write_text(body)

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
            "STUB_KEY": KEY,
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
        (h / ".env").write_text(f"API_SERVER_KEY={KEY}\n")


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


def test_probe_hermes_completed_with_empty_output_is_not_a_reply(guest: Guest):
    guest.hermes_env()
    for body in (
        '{"run_id":"run_1","status":"completed","output":""}',
        '{"run_id":"run_1","status":"completed","output":null}',
        '{"run_id":"run_1","status":"completed","error":"boom"}',
    ):
        guest.set_run(body)
        proc, kv = guest.run("probe")
        assert proc.returncode == 0
        assert (kv["hermes_reply_ok"], kv["hermes_reply_s"]) == ("0", "-1"), body


def test_probe_hermes_failed_run(guest: Guest):
    guest.hermes_env()
    guest.set_run('{"run_id":"run_1","status":"failed","output":"partial"}')
    proc, kv = guest.run("probe")
    assert proc.returncode == 0
    assert (kv["hermes_reply_ok"], kv["hermes_reply_s"]) == ("0", "-1")


def test_probe_hermes_never_completes_times_out(guest: Guest):
    guest.hermes_env()
    guest.set_run('{"run_id":"run_1","status":"running","output":"thinking"}')
    t0 = time.monotonic()
    proc, kv = guest.run("probe", {"HAL0_COLLECT_HERMES_TIMEOUT": "1"})
    assert time.monotonic() - t0 < 10
    assert proc.returncode == 0
    assert (kv["hermes_reply_ok"], kv["hermes_reply_s"]) == ("0", "-1")


def test_probe_memory_never_recalled_ends_at_timeout(guest: Guest):
    (guest.stubs / "recall_empty").write_text("")
    t0 = time.monotonic()
    proc, kv = guest.run("probe", {"HAL0_COLLECT_MEMORY_TIMEOUT": "1"})
    assert time.monotonic() - t0 < 10
    assert proc.returncode == 0
    assert (kv["memory_write_ok"], kv["memory_read_ok"]) == ("1", "0")


def test_memory_probe_stores_a_named_fact(guest: Guest):
    guest.run("probe")
    body = (guest.stubs / "memory.body").read_text()
    assert "install check code is hal0check" in body


def _hermes_urls(guest: Guest) -> list[str]:
    return [u for u in (guest.stubs / "curl.urls").read_text().split() if "/v1/runs" in u]


def test_hermes_endpoint_from_env_file(guest: Guest):
    guest.hermes_env()
    env = guest.root / "hermes" / ".env"
    env.write_text(env.read_text() + "API_SERVER_HOST=10.9.8.7\nAPI_SERVER_PORT=8650\n")
    _, kv = guest.run("probe")
    assert kv["hermes_reply_ok"] == "1"
    assert _hermes_urls(guest)[0] == "http://10.9.8.7:8650/v1/runs"


def test_hermes_endpoint_and_key_from_config_yaml(guest: Guest):
    h = guest.root / "hermes"
    h.mkdir()
    (h / "config.yaml").write_text(
        "gateway:\n  platforms:\n    api_server:\n"
        f"      host: 127.0.0.2\n      port: 9191\n      key: {KEY}\n"
    )
    (h / ".env").write_text("API_SERVER_PORT=8650\nAPI_SERVER_KEY=short\n")
    _, kv = guest.run("probe")
    assert kv["hermes_reply_ok"] == "1"
    assert _hermes_urls(guest)[0] == "http://127.0.0.2:9191/v1/runs"


def test_hermes_config_yaml_ignored_without_python(guest: Guest):
    guest.hermes_env()
    (guest.root / "hermes" / "config.yaml").write_text(
        "gateway:\n  platforms:\n    api_server:\n      port: 9191\n"
    )
    guest.write_bin("hal0", "#!/usr/bin/env bash\nexit 1\n")
    _, kv = guest.run("probe")
    assert kv["hermes_reply_ok"] == "1"
    assert _hermes_urls(guest)[0] == "http://127.0.0.1:8642/v1/runs"


def test_hermes_key_stays_out_of_arguments_and_output(guest: Guest):
    guest.hermes_env()
    proc, kv = guest.run("probe")
    assert kv["hermes_reply_ok"] == "1"
    assert KEY not in proc.stdout + proc.stderr
    assert KEY not in (guest.stubs / "curl.args").read_text()


@pytest.mark.parametrize("mode", ["post", "probe"])
def test_no_stray_stderr(guest: Guest, mode: str):
    guest.hermes_env()
    proc, _ = guest.run(mode)
    assert proc.stderr == ""


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
