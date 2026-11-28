"""The wizard as a program: arguments, headless path, screens on a pseudo-terminal."""

from __future__ import annotations

import contextlib
import json
import os
import select
import signal
import stat
import subprocess
import sys
import termios
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import FrameType

import pytest

from hal0.install.wizard import cli
from hal0.install.wizard.catalog import Catalog
from hal0.install.wizard.document import DocumentError
from hal0.install.wizard.facts import Facts
from hal0.install.wizard.render import strip_ansi
from hal0.install.wizard.session import Inputs, Session

from .conftest import read_all, wait_exit, wait_for_output

MAIN = Path(__file__).resolve().parents[3] / "src/hal0/install/wizard/__main__.py"
DOWN = b"\x1b[B"
ENTER = b"\r"


@pytest.fixture(autouse=True)
def _deadline() -> Iterator[None]:
    """No test here may hang the suite."""

    def expired(signum: int, frame: FrameType | None) -> None:
        raise TimeoutError("wizard test exceeded its deadline")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(90)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _env(extra: dict[str, str] | None) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", ""), "TERM": "xterm-256color"}
    env.update(extra or {})
    return env


@pytest.fixture
def spawn() -> Iterator[Callable[..., subprocess.Popen[str]]]:
    """Start the wizard by file path; every process is killed and reaped at the end."""
    started: list[subprocess.Popen[str]] = []

    def start(
        *args: object, env: dict[str, str] | None = None, capture: bool = False
    ) -> subprocess.Popen[str]:
        pipe = subprocess.PIPE if capture else subprocess.DEVNULL
        proc = subprocess.Popen(
            [sys.executable, "-I", str(MAIN), *map(str, args)],
            stdin=subprocess.DEVNULL,
            stdout=pipe,
            stderr=pipe,
            text=True,
            cwd="/",
            env=_env(env),
        )
        started.append(proc)
        return proc

    yield start
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()


def wizard(
    *args: object, env: dict[str, str] | None = None, tty: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the wizard to the end; with ``tty`` the terminal is that pty slave."""
    extra = ["--tty", str(tty)] if tty is not None else []
    return subprocess.run(
        [sys.executable, "-I", str(MAIN), *map(str, args), *extra],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd="/",
        env=_env(env),
        timeout=30,
    )


def _write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data))
    return path


def strip_meta(doc: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in doc.items() if k != "meta"}


def _facts_file(tmp_path: Path, name: str, **sections: object) -> Path:
    base: dict[str, object] = {
        "version": 1,
        "podman": {"runs": True},
        "memory_python": True,
        "npu": {"device": False},
    }
    return _write_json(tmp_path / name, {**base, **sections})


@pytest.fixture
def consent_facts_path(tmp_path: Path) -> Path:
    return _facts_file(tmp_path, "consent.json", gpu={"outcome": "cpu_only_needs_consent"})


@pytest.fixture
def upgrade_facts_path(tmp_path: Path) -> Path:
    return _facts_file(tmp_path, "upgrade.json", install={"state": "upgrade", "version": "1.0"})


@pytest.fixture
def big_pool_facts_path(tmp_path: Path) -> Path:
    return _facts_file(tmp_path, "big.json", gpu={"outcome": "usable", "pool_gb": 96})


@pytest.fixture
def saved_path(tmp_path: Path) -> Path:
    return _write_json(
        tmp_path / "saved.json",
        {
            "version": 1,
            "install": {"type": "recommended"},
            "network": {"port": 9000},
            "model_store": {"path": str(tmp_path)},
            "meta": {"saved_at": "2026-01-02T03:04:05Z"},
        },
    )


@pytest.fixture
def saved_gone_folder_path(tmp_path: Path) -> Path:
    gone = tmp_path / "gone" / "models"
    return _write_json(tmp_path / "saved-gone.json", {"model_store": {"path": str(gone)}})


# ------------------------------------------------------------------ headless


def test_headless_writes_the_recommended_document(catalog_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "a.json"
    r = wizard("--catalog", catalog_path, "--out", out, "--headless")
    doc = json.loads(out.read_text())
    assert r.returncode == 0 and r.stdout == "" and doc["install"]["type"] == "recommended"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_noninteractive_variable_wins_over_a_terminal(
    catalog_path: Path, tmp_path: Path, pty_pair: tuple[int, str]
) -> None:
    r = wizard(
        "--catalog",
        catalog_path,
        "--out",
        tmp_path / "a.json",
        env={"HAL0_NONINTERACTIVE": "1"},
        tty=pty_pair[1],
    )
    assert r.returncode == 0 and read_all(pty_pair[0]) == b""


def test_no_terminal_falls_back_to_headless(catalog_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "a.json"
    r = wizard("--catalog", catalog_path, "--out", out, "--tty", tmp_path / "absent")
    assert r.returncode == 0 and out.exists()


def test_skip_variable_never_asks_and_never_fails(catalog_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "a.json"
    r = wizard(
        "--catalog",
        catalog_path,
        "--out",
        out,
        env={"HAL0_NONINTERACTIVE": "1", "HAL0_SKIP_HERMES": "1"},
    )
    assert r.returncode == 0
    assert json.loads(out.read_text())["apps"]["hermes"]["enabled"] is False


def test_headless_without_cpu_consent_stops(
    catalog_path: Path, tmp_path: Path, consent_facts_path: Path
) -> None:
    r = wizard(
        "--catalog",
        catalog_path,
        "--facts",
        consent_facts_path,
        "--out",
        tmp_path / "a.json",
        "--headless",
    )
    assert r.returncode == 11 and "HAL0_ALLOW_CPU_ONLY=1" in r.stderr
    assert not (tmp_path / "a.json").exists()


def test_headless_stop_outcome_gives_the_reason_alone(catalog_path: Path, tmp_path: Path) -> None:
    facts = _facts_file(tmp_path, "stop.json", gpu={"outcome": "stop", "reason": "broken_group"})
    r = wizard(
        "--catalog", catalog_path, "--facts", facts, "--out", tmp_path / "a.json", "--headless"
    )
    assert r.returncode == 11 and "not allowed to use it" in r.stderr
    assert "HAL0_ALLOW_CPU_ONLY" not in r.stderr


def test_headless_with_cpu_consent_continues(
    catalog_path: Path, tmp_path: Path, consent_facts_path: Path
) -> None:
    r = wizard(
        "--catalog",
        catalog_path,
        "--facts",
        consent_facts_path,
        "--out",
        tmp_path / "a.json",
        "--headless",
        env={"HAL0_ALLOW_CPU_ONLY": "1"},
    )
    assert r.returncode == 0


def test_headless_with_a_saved_folder_that_is_gone_stops(
    catalog_path: Path, tmp_path: Path, saved_gone_folder_path: Path
) -> None:
    r = wizard(
        "--catalog",
        catalog_path,
        "--saved",
        saved_gone_folder_path,
        "--out",
        tmp_path / "a.json",
        "--headless",
    )
    assert r.returncode == 2 and "no longer exists" in r.stderr


def test_a_folder_named_by_a_variable_is_not_checked_against_the_saved_one(
    catalog_path: Path, tmp_path: Path, saved_gone_folder_path: Path
) -> None:
    r = wizard(
        "--catalog",
        catalog_path,
        "--saved",
        saved_gone_folder_path,
        "--out",
        tmp_path / "a.json",
        "--headless",
        env={"HAL0_MODELS_DIR": str(tmp_path / "elsewhere")},
    )
    assert r.returncode == 0


@pytest.mark.parametrize("bad", ["missing", "not-json", "newer-version"])
@pytest.mark.parametrize("which", ["--catalog", "--facts"])
def test_bad_input_file_is_one_line_on_stderr_and_exit_2(
    which: str, bad: str, catalog_path: Path, tmp_path: Path
) -> None:
    path = tmp_path / "bad.json"
    if bad == "not-json":
        path.write_text("{ nope")
    elif bad == "newer-version":
        path.write_text(json.dumps({"version": 99}))
    args: dict[str, object] = {"--catalog": catalog_path, "--facts": None}
    args[which] = path
    argv: list[object] = ["--out", tmp_path / "a.json", "--headless"]
    for name, value in args.items():
        if value is not None:
            argv += [name, value]
    r = wizard(*argv)
    assert r.returncode == 2 and len(r.stderr.strip().splitlines()) == 1
    assert "Traceback" not in r.stderr and r.stderr.startswith("hal0 setup: ")


def test_a_deeply_nested_catalog_is_still_one_line(catalog_path: Path, tmp_path: Path) -> None:
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 200000)
    r = wizard("--catalog", deep, "--out", tmp_path / "a.json", "--headless")
    assert r.returncode == 2 and len(r.stderr.strip().splitlines()) == 1
    assert "Traceback" not in r.stderr


def test_unknown_installer_flag_is_exit_2(catalog_path: Path, tmp_path: Path) -> None:
    r = wizard(
        "--catalog", catalog_path, "--out", tmp_path / "a.json", "--headless", "--", "--no-tls"
    )
    assert r.returncode == 2 and "--no-tls" in r.stderr


def test_installer_flags_after_the_separator_are_applied(
    catalog_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "a.json"
    r = wizard("--catalog", catalog_path, "--out", out, "--headless", "--", "--no-hermes")
    assert r.returncode == 0
    assert json.loads(out.read_text())["apps"]["hermes"]["enabled"] is False


def test_missing_required_arguments_are_a_usage_error(tmp_path: Path) -> None:
    r = wizard("--headless")
    assert r.returncode == 2 and "Traceback" not in r.stderr and r.stdout == ""


def test_a_given_answers_document_is_layered(catalog_path: Path, tmp_path: Path) -> None:
    given = _write_json(tmp_path / "given.json", {"network": {"port": 9123}})
    out = tmp_path / "a.json"
    r = wizard("--catalog", catalog_path, "--answers", given, "--out", out, "--headless")
    assert r.returncode == 0 and json.loads(out.read_text())["network"]["port"] == 9123


def test_an_unwritable_out_path_is_one_line_exit_2(catalog_path: Path, tmp_path: Path) -> None:
    r = wizard(
        "--catalog", catalog_path, "--out", tmp_path / "no" / "such" / "a.json", "--headless"
    )
    assert r.returncode == 2 and len(r.stderr.strip().splitlines()) == 1


# --------------------------------------------------------------- upgrade


def test_upgrade_reuses_saved_answers_without_asking(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    upgrade_facts_path: Path,
    saved_path: Path,
) -> None:
    out = tmp_path / "a.json"
    r = wizard(
        "--catalog",
        catalog_path,
        "--facts",
        upgrade_facts_path,
        "--saved",
        saved_path,
        "--out",
        out,
        tty=pty_pair[1],
    )
    assert r.returncode == 0 and read_all(pty_pair[0]) == b""
    assert json.loads(out.read_text())["network"]["port"] == 9000


def test_reconfigure_asks_again_on_an_upgrade(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    upgrade_facts_path: Path,
    saved_path: Path,
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    p = spawn(
        "--catalog",
        catalog_path,
        "--facts",
        upgrade_facts_path,
        "--saved",
        saved_path,
        "--out",
        tmp_path / "a.json",
        "--tty",
        pty_pair[1],
        "--reconfigure",
    )
    assert b"hal0 setup" in wait_for_output(pty_pair[0])
    os.write(pty_pair[0], b"q")
    assert wait_exit(p, pty_pair[0])[0] == 10


def test_fresh_install_with_saved_answers_shows_screens_prefilled(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    saved_path: Path,
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    spawn(
        "--catalog",
        catalog_path,
        "--saved",
        saved_path,
        "--out",
        tmp_path / "a.json",
        "--tty",
        pty_pair[1],
    )
    text = strip_ansi(wait_for_output(pty_pair[0]).decode())
    assert "answers from an earlier install on 2026-01-02" in text


# ------------------------------------------------------------ the screens


def test_typing_enter_three_times_writes_the_same_document_as_headless(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    wizard("--catalog", catalog_path, "--out", a, "--headless", "--hal0-version", "v")
    p = spawn("--catalog", catalog_path, "--out", b, "--tty", slave, "--hal0-version", "v")
    wait_for_output(master)
    os.write(master, ENTER * 3)
    assert wait_exit(p, master)[0] == 0
    assert strip_meta(json.loads(a.read_text())) == strip_meta(json.loads(b.read_text()))
    assert stat.S_IMODE(b.stat().st_mode) == 0o600


def test_quit_writes_nothing(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out = tmp_path / "a.json"
    p = spawn("--catalog", catalog_path, "--out", out, "--tty", pty_pair[1])
    wait_for_output(pty_pair[0])
    os.write(pty_pair[0], b"q")
    assert wait_exit(p, pty_pair[0])[0] == 10 and not out.exists()


def test_a_stopped_box_exits_11(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    facts = _facts_file(tmp_path, "stop.json", gpu={"outcome": "stop", "reason": "no_device"})
    out = tmp_path / "a.json"
    p = spawn("--catalog", catalog_path, "--facts", facts, "--out", out, "--tty", pty_pair[1])
    wait_for_output(pty_pair[0])
    os.write(pty_pair[0], ENTER)
    assert wait_exit(p, pty_pair[0])[0] == 11 and not out.exists()


def _tty_attrs(path: str) -> list[object]:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    try:
        return termios.tcgetattr(fd)
    finally:
        os.close(fd)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_a_signal_restores_the_terminal_and_writes_nothing(
    sig: signal.Signals,
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out = tmp_path / "a.json"
    before = _tty_attrs(slave)
    p = spawn("--catalog", catalog_path, "--out", out, "--tty", slave)
    wait_for_output(master)
    p.send_signal(sig)
    assert wait_exit(p, master)[0] == 130 and not out.exists()
    assert _tty_attrs(slave) == before


def test_control_c_on_the_terminal_is_an_interrupt(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out = tmp_path / "a.json"
    before = _tty_attrs(slave)
    p = spawn("--catalog", catalog_path, "--out", out, "--tty", slave)
    wait_for_output(master)
    os.write(master, b"\x03")
    assert wait_exit(p, master)[0] == 130 and not out.exists()
    assert _tty_attrs(slave) == before


def test_control_d_on_the_terminal_is_an_interrupt(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out = tmp_path / "a.json"
    p = spawn("--catalog", catalog_path, "--out", out, "--tty", pty_pair[1])
    wait_for_output(pty_pair[0])
    os.write(pty_pair[0], b"\x04")
    assert wait_exit(p, pty_pair[0])[0] == 130 and not out.exists()


def test_in_process_signal_returns_130_and_restores_the_handlers(
    catalog_path: Path, tmp_path: Path, pty_pair: tuple[int, str]
) -> None:
    master, slave = pty_pair
    handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    before = _tty_attrs(slave)

    failures: list[BaseException] = []

    def interrupt_once_drawn() -> None:
        try:
            wait_for_output(master)
        except BaseException as exc:
            failures.append(exc)
        finally:
            os.kill(os.getpid(), signal.SIGTERM)  # always end the run, then report the cause

    thread = threading.Thread(target=interrupt_once_drawn)
    thread.start()
    try:
        code = cli.main(
            ["--catalog", str(catalog_path), "--out", str(tmp_path / "a.json"), "--tty", slave],
            environ={"TERM": "xterm"},
        )
    finally:
        thread.join(timeout=10)
    assert not thread.is_alive()
    if failures:
        raise failures[0]
    assert code == 130 and not (tmp_path / "a.json").exists()
    assert {s: signal.getsignal(s) for s in handlers} == handlers
    assert _tty_attrs(slave) == before


def test_typed_token_goes_only_to_the_secret_file(
    catalog_path: Path,
    ladder_models_path: Path,
    big_pool_facts_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out, sec = tmp_path / "a.json", tmp_path / "s"
    p = spawn(
        "--catalog",
        catalog_path,
        "--models",
        ladder_models_path,
        "--facts",
        big_pool_facts_path,
        "--out",
        out,
        "--secret-out",
        sec,
        "--tty",
        slave,
        capture=True,
    )
    wait_for_output(master)
    customize = DOWN + DOWN + ENTER  # Recommended, Minimal, Customize
    jobs = ENTER
    model = DOWN + DOWN + ENTER  # auto, small-1, big-1 (needs an account)
    token = b"hf_SECRET" + ENTER
    rest = ENTER * 4  # storage, network, assistant, review
    os.write(master, customize + jobs + model + token + rest)
    code, shown = wait_exit(p, master)
    assert code == 0
    assert sec.read_text() == "HF_TOKEN=hf_SECRET\n"
    assert stat.S_IMODE(sec.stat().st_mode) == 0o600
    assert json.loads(out.read_text())["models"]["starter"] == "big-1"
    assert p.stdout is not None and p.stderr is not None
    assert "hf_SECRET" not in out.read_text()
    assert "hf_SECRET" not in p.stdout.read() + p.stderr.read()
    assert b"hf_SECRET" not in shown  # never echoed, even to the terminal


def test_a_token_without_a_secret_file_is_dropped_with_one_line(
    catalog_path: Path,
    ladder_models_path: Path,
    big_pool_facts_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out = tmp_path / "a.json"
    p = spawn(
        "--catalog",
        catalog_path,
        "--models",
        ladder_models_path,
        "--facts",
        big_pool_facts_path,
        "--out",
        out,
        "--tty",
        slave,
        capture=True,
    )
    wait_for_output(master)
    os.write(
        master,
        DOWN + DOWN + ENTER + ENTER + DOWN + DOWN + ENTER + b"hf_SECRET" + ENTER + ENTER * 4,
    )
    code, shown = wait_exit(p, master)
    assert code == 0
    assert p.stdout is not None and p.stderr is not None
    err = p.stderr.read()
    assert err.strip() == "hal0 setup: no place to keep the token; add it later"
    assert "hf_SECRET" not in out.read_text() + err + p.stdout.read()
    assert b"hf_SECRET" not in shown


def test_save_a_copy_from_the_review_screen(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out, copy = tmp_path / "a.json", tmp_path / "copy.json"
    p = spawn("--catalog", catalog_path, "--out", out, "--tty", slave)
    wait_for_output(master)
    os.write(master, ENTER * 2)  # type, folder: now on the review
    os.write(master, b"s" + str(copy).encode() + ENTER + ENTER)
    assert wait_exit(p, master)[0] == 0
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert strip_meta(json.loads(copy.read_text())) == strip_meta(json.loads(out.read_text()))
    assert "_save_copy_to" not in out.read_text() + copy.read_text()


# ------------------------------------------------------------- plain form


def test_plain_form_is_used_when_asked(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    p = spawn(
        "--catalog",
        catalog_path,
        "--out",
        tmp_path / "a.json",
        "--tty",
        pty_pair[1],
        env={"HAL0_PLAIN": "1"},
    )
    assert b"  1) " in wait_for_output(pty_pair[0])
    assert b"\x1b[" not in read_all(pty_pair[0])
    p.kill()


@pytest.mark.parametrize("env", [{"NO_COLOR": "1"}, {"TERM": "dumb"}, {"TERM": ""}])
def test_plain_form_is_also_used_for_no_color_and_a_dumb_terminal(
    env: dict[str, str],
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    spawn("--catalog", catalog_path, "--out", tmp_path / "a.json", "--tty", pty_pair[1], env=env)
    assert b"  1) " in wait_for_output(pty_pair[0])


def test_a_plain_form_run_writes_a_minimal_document(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out = tmp_path / "a.json"
    p = spawn("--catalog", catalog_path, "--out", out, "--tty", slave, env={"HAL0_PLAIN": "1"})
    wait_for_output(master)
    os.write(master, b"2\n\n\n")  # Minimal, the folder, the review
    assert wait_exit(p, master)[0] == 0
    doc = json.loads(out.read_text())
    assert doc["install"]["type"] == "minimal" and doc["apps"]["hermes"]["enabled"] is False


def test_plain_form_end_of_input_is_an_interrupt(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out = tmp_path / "a.json"
    p = spawn(
        "--catalog", catalog_path, "--out", out, "--tty", pty_pair[1], env={"HAL0_PLAIN": "1"}
    )
    wait_for_output(pty_pair[0])
    os.write(pty_pair[0], b"\x04")  # end of input on an empty line in cooked mode
    assert wait_exit(p, pty_pair[0])[0] == 130 and not out.exists()


def test_plain_form_can_ask_about_a_numbered_line(
    catalog: Catalog,
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out = tmp_path / "a.json"
    p = spawn("--catalog", catalog_path, "--out", out, "--tty", slave, env={"HAL0_PLAIN": "1"})
    wait_for_output(master)
    os.write(master, b"3\n")  # Customize: the first of its screens lists the jobs
    wait_for_output(master)
    os.write(master, b"?2\n")  # the cursor is on line 1; ask about line 2
    shown = " ".join(strip_ansi(wait_for_output(master).decode()).split())
    os.write(master, b"q\n")
    code, _ = wait_exit(p, master)
    second = [c for c in catalog.components if c.id in _second_job_ids(catalog)]
    assert code == 10 and not out.exists()
    assert second and " ".join(second[0].help.split()) in shown


def _second_job_ids(catalog: Catalog) -> list[str]:
    """The id of the second job on the Customize screen: the assistant."""
    return [c.id for c in catalog.components if "assistant that carries out" in c.label]


# ------------------------------------------------- amendments before review


@pytest.fixture
def consent_upgrade_facts_path(tmp_path: Path) -> Path:
    return _facts_file(
        tmp_path,
        "consent-upgrade.json",
        gpu={"outcome": "cpu_only_needs_consent"},
        install={"state": "upgrade", "version": "1.0"},
    )


def test_saved_cpu_only_consent_lets_an_upgrade_continue(
    catalog_path: Path, tmp_path: Path, consent_upgrade_facts_path: Path
) -> None:
    saved = _write_json(tmp_path / "saved-consent.json", {"hardware": {"allow_cpu_only": True}})
    out = tmp_path / "a.json"
    r = wizard(
        "--catalog",
        catalog_path,
        "--facts",
        consent_upgrade_facts_path,
        "--saved",
        saved,
        "--out",
        out,
    )
    assert r.returncode == 0 and r.stderr == ""
    assert json.loads(out.read_text())["hardware"]["allow_cpu_only"] is True


def test_given_cpu_only_consent_lets_a_headless_run_continue(
    catalog_path: Path, tmp_path: Path, consent_facts_path: Path
) -> None:
    given = _write_json(tmp_path / "given-consent.json", {"hardware": {"allow_cpu_only": True}})
    r = wizard(
        "--catalog",
        catalog_path,
        "--facts",
        consent_facts_path,
        "--answers",
        given,
        "--out",
        tmp_path / "a.json",
        "--headless",
    )
    assert r.returncode == 0


def test_a_saved_refusal_is_not_consent(
    catalog_path: Path, tmp_path: Path, consent_upgrade_facts_path: Path
) -> None:
    saved = _write_json(tmp_path / "saved-no.json", {"hardware": {"allow_cpu_only": False}})
    r = wizard(
        "--catalog",
        catalog_path,
        "--facts",
        consent_upgrade_facts_path,
        "--saved",
        saved,
        "--out",
        tmp_path / "a.json",
    )
    assert r.returncode == 11 and "HAL0_ALLOW_CPU_ONLY=1" in r.stderr


def test_no_consent_anywhere_still_stops_an_upgrade(
    catalog_path: Path, tmp_path: Path, consent_upgrade_facts_path: Path, saved_path: Path
) -> None:
    r = wizard(
        "--catalog",
        catalog_path,
        "--facts",
        consent_upgrade_facts_path,
        "--saved",
        saved_path,
        "--out",
        tmp_path / "a.json",
    )
    assert r.returncode == 11


def test_a_saved_copy_is_written_again_with_the_final_answers(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out, copy = tmp_path / "a.json", tmp_path / "copy.json"
    p = spawn("--catalog", catalog_path, "--out", out, "--tty", slave)
    wait_for_output(master)
    os.write(master, ENTER * 2 + b"s" + str(copy).encode() + ENTER)  # review, copy path set
    os.write(master, b"bb" + DOWN + ENTER + ENTER * 2)  # back to the type, pick Minimal, review
    assert wait_exit(p, master)[0] == 0
    final = json.loads(out.read_text())
    assert final["install"]["type"] == "minimal"
    assert strip_meta(json.loads(copy.read_text())) == strip_meta(final)


def test_quitting_after_saving_a_copy_keeps_the_copy(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out, copy = tmp_path / "a.json", tmp_path / "copy.json"
    p = spawn("--catalog", catalog_path, "--out", out, "--tty", slave)
    wait_for_output(master)
    os.write(master, ENTER * 2 + b"s" + str(copy).encode() + ENTER)
    os.write(master, b"q")
    assert wait_exit(p, master)[0] == 10
    assert copy.exists() and not out.exists()


def test_no_facts_file_adds_one_line_to_the_first_screen(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    spawn("--catalog", catalog_path, "--out", tmp_path / "a.json", "--tty", pty_pair[1])
    text = " ".join(strip_ansi(wait_for_output(pty_pair[0]).decode()).split())
    assert "This box could not be checked, so some choices may not suit it." in text


def test_a_facts_file_leaves_that_line_out(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    big_pool_facts_path: Path,
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    spawn(
        "--catalog",
        catalog_path,
        "--facts",
        big_pool_facts_path,
        "--out",
        tmp_path / "a.json",
        "--tty",
        pty_pair[1],
    )
    text = " ".join(strip_ansi(wait_for_output(pty_pair[0]).decode()).split())
    assert "could not be checked" not in text


def test_saving_to_the_same_path_again_writes_the_changed_answers(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out, copy = tmp_path / "a.json", tmp_path / "copy.json"
    p = spawn("--catalog", catalog_path, "--out", out, "--tty", slave)
    wait_for_output(master)
    os.write(master, ENTER * 2 + b"s" + str(copy).encode() + ENTER)  # review, copy saved
    os.write(master, b"bb" + DOWN + ENTER * 2)  # type: pick Minimal, folder, back on the review
    os.write(master, b"s" + str(copy).encode() + ENTER)  # the same path again
    os.write(master, b"q")
    assert wait_exit(p, master)[0] == 10 and not out.exists()
    assert json.loads(copy.read_text())["install"]["type"] == "minimal"


class _ScriptedTerminal:
    """Keys from a list; every screen drawn is kept as one string."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = list(keys)
        self.frames: list[str] = []

    def width(self) -> int:
        return 100

    def draw(self, lines: list[str]) -> None:
        self.frames.append("\n".join(lines))

    def read_key(self) -> str:
        assert self.keys, "the screens asked for a key the test did not script"
        return self.keys.pop(0)


def test_a_failed_save_shows_its_notice_once(
    catalog: Catalog, usable_facts: Facts, tmp_path: Path
) -> None:
    def failing(chosen: object) -> dict[str, object]:
        raise DocumentError("the copy could not be written")

    keys = ["ENTER", "ENTER", "s", *str(tmp_path / "copy.json"), "ENTER", "b", "ENTER", "q"]
    term = _ScriptedTerminal(keys)
    inp = Inputs(catalog, usable_facts, None, None, (), "v")
    Session(inp, term, failing, plain=False).run()  # type: ignore[arg-type]
    shown = ["the copy could not be written" in frame for frame in term.frames]
    first = shown.index(True)
    assert shown.count(True) == 1
    assert first == len(keys) - 3  # drawn right after the path was confirmed


def test_a_skipped_token_creates_no_secret_file(
    catalog_path: Path,
    ladder_models_path: Path,
    big_pool_facts_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    sec = tmp_path / "s"
    p = spawn(
        "--catalog",
        catalog_path,
        "--models",
        ladder_models_path,
        "--facts",
        big_pool_facts_path,
        "--out",
        tmp_path / "a.json",
        "--secret-out",
        sec,
        "--tty",
        slave,
    )
    wait_for_output(master)
    os.write(master, DOWN + DOWN + ENTER + ENTER + DOWN + DOWN + ENTER + ENTER + ENTER * 4)
    assert wait_exit(p, master)[0] == 0
    assert (tmp_path / "a.json").exists()
    assert not sec.exists()


class _Screens:
    """Read what the plain form draws, a screen at a time, with every wait bounded."""

    def __init__(self, master: int) -> None:
        self.master = master
        self.seen = b""
        self.mark = 0

    def _pull(self) -> None:
        ready, _, _ = select.select([self.master], [], [], 0.2)
        if ready:
            with contextlib.suppress(OSError):
                self.seen += os.read(self.master, 4096)

    def wait(self, text: str, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        needle = text.encode()
        while needle not in self.seen[self.mark :]:
            assert time.monotonic() < deadline, f"never saw {text!r}"
            self._pull()
        self.mark = self.seen.index(needle, self.mark) + len(needle)

    def answer(self, title: str, line: str) -> None:
        """Wait for a screen and its prompt, then type one line."""
        self.wait(title)
        self.wait("> ")
        os.write(self.master, line.encode() + b"\n")


def test_a_plain_form_token_is_read_with_echo_off_and_lands_only_in_the_secret_file(
    catalog: Catalog,
    catalog_path: Path,
    ladder_models_path: Path,
    big_pool_facts_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out, sec = tmp_path / "a.json", tmp_path / "s"
    p = spawn(
        "--catalog",
        catalog_path,
        "--models",
        ladder_models_path,
        "--facts",
        big_pool_facts_path,
        "--out",
        out,
        "--secret-out",
        sec,
        "--tty",
        slave,
        env={"HAL0_PLAIN": "1"},
        capture=True,
    )
    questions = catalog.questions
    screens = _Screens(master)
    screens.answer(questions["setup_type"].title, "3")  # Customize
    screens.answer("What this box does", "")
    screens.answer(questions["starter_model"].title, "3")  # auto, small-1, big-1: needs an account
    screens.answer(questions["hf_token"].title, "hf_SECRET")
    for title in ("Storage", "Network and access", "Assistant and memory", "Ready to install"):
        screens.answer(title, "")
    code, rest = wait_exit(p, master)
    assert code == 0
    assert sec.read_text() == "HF_TOKEN=hf_SECRET\n"
    assert stat.S_IMODE(sec.stat().st_mode) == 0o600
    assert p.stdout is not None and p.stderr is not None
    everything = screens.seen + rest
    assert b"hf_SECRET" not in everything
    assert "hf_SECRET" not in out.read_text() + p.stdout.read() + p.stderr.read()


def test_the_graphics_card_wording_lives_in_one_place() -> None:
    from hal0.install.wizard import flow_basic

    assert cli.GPU_REASON_OTHER is flow_basic.GPU_REASON_OTHER
    assert flow_basic.GPU_REASON_OTHER not in Path(cli.__file__).read_text()
