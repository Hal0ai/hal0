"""The wizard as a program: arguments, headless path, screens on a pseudo-terminal."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
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


# ------------------------------------------- the chat model of a run with no screens


def _starter_run(
    tmp_path: Path,
    catalog_path: Path,
    *args: object,
    env: dict[str, str] | None = None,
    tty: str | None = None,
) -> dict[str, dict[str, object]]:
    """Run the wizard to the end and return the document it wrote."""
    out = tmp_path / "starter.json"
    r = wizard("--catalog", catalog_path, "--out", out, *args, env=env, tty=tty)
    assert r.returncode == 0, r.stderr
    doc: dict[str, dict[str, object]] = json.loads(out.read_text())
    return doc


def _saved_with(tmp_path: Path, models: dict[str, object] | None) -> Path:
    saved: dict[str, object] = {
        "version": 1,
        "install": {"type": "recommended"},
        "model_store": {"path": str(tmp_path)},
        "meta": {"saved_at": "2026-01-02T03:04:05Z"},
    }
    if models is not None:
        saved["models"] = models
    return _write_json(tmp_path / "saved-starter.json", saved)


@pytest.mark.parametrize("how", ["headless", "noninteractive", "no_terminal"])
def test_a_fresh_run_with_no_screens_leaves_the_chat_model_out(
    how: str, catalog_path: Path, tmp_path: Path
) -> None:
    extra: dict[str, tuple[list[object], dict[str, str]]] = {
        "headless": (["--headless"], {}),
        "noninteractive": ([], {"HAL0_NONINTERACTIVE": "1"}),
        "no_terminal": (["--tty", tmp_path / "absent-terminal"], {}),
    }
    args, env = extra[how]
    doc = _starter_run(tmp_path, catalog_path, *args, env=env)
    assert "starter" not in doc["models"]
    assert doc["install"]["type"] == "recommended" and doc["models"]["brain"] == "auto"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"HAL0_PULL_AGENT_MODEL": "1"}, "auto"),
        ({"HAL0_PULL_AGENT_MODEL": "0"}, "none"),
        ({"HAL0_AGENT_MODEL": "small-1"}, "small-1"),
    ],
)
def test_a_variable_that_names_the_chat_model_keeps_it(
    env: dict[str, str], expected: str, catalog_path: Path, ladder_models_path: Path, tmp_path: Path
) -> None:
    doc = _starter_run(
        tmp_path,
        catalog_path,
        "--models",
        ladder_models_path,
        "--headless",
        env={**env, "HAL0_NONINTERACTIVE": "1"},
    )
    assert doc["models"]["starter"] == expected


@pytest.mark.parametrize(
    ("given_doc", "expected"),
    [
        ({"models": {"starter": "auto"}}, "auto"),
        ({"models": {"starter": "big-1"}}, "big-1"),
        ({"install": {"type": "recommended"}}, None),
    ],
)
def test_a_given_document_keeps_the_chat_model_only_when_it_names_one(
    given_doc: dict[str, object],
    expected: str | None,
    catalog_path: Path,
    ladder_models_path: Path,
    tmp_path: Path,
) -> None:
    given = _write_json(tmp_path / "given.json", given_doc)
    doc = _starter_run(tmp_path, catalog_path, "--models", ladder_models_path, "--answers", given)
    if expected is None:
        assert "starter" not in doc["models"]
        assert doc["models"]["brain"] == "auto"
    else:
        assert doc["models"]["starter"] == expected


@pytest.mark.parametrize(
    ("saved_models", "expected"),
    [
        ({"starter": "auto"}, "auto"),
        ({"starter": "big-1"}, "big-1"),
        ({"brain": "auto"}, None),
        (None, None),
        ({"starter": "retired-9"}, None),
    ],
)
def test_an_upgrade_keeps_a_saved_chat_model_only_when_it_is_still_offered(
    saved_models: dict[str, object] | None,
    expected: str | None,
    catalog_path: Path,
    ladder_models_path: Path,
    upgrade_facts_path: Path,
    tmp_path: Path,
) -> None:
    saved = _saved_with(tmp_path, saved_models)
    doc = _starter_run(
        tmp_path,
        catalog_path,
        "--models",
        ladder_models_path,
        "--facts",
        upgrade_facts_path,
        "--saved",
        saved,
    )
    if expected is None:
        assert "starter" not in doc["models"]
        assert doc["models"]["brain"] == "auto"
    else:
        assert doc["models"]["starter"] == expected


def test_minimal_with_no_screens_keeps_none_as_the_chat_model(
    catalog_path: Path, tmp_path: Path
) -> None:
    doc = _starter_run(tmp_path, catalog_path, "--headless", "--", "--bare")
    assert doc["models"]["starter"] == "none"
    given = _write_json(tmp_path / "given-minimal.json", {"install": {"type": "minimal"}})
    doc = _starter_run(tmp_path, catalog_path, "--answers", given)
    assert doc["models"]["starter"] == "none"


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
    headless, screens = json.loads(a.read_text()), json.loads(b.read_text())
    # The review screen lists the chat model, so a run that showed it keeps the preset's
    # `auto`; a run with no screens leaves it out (nobody saw its size).
    assert "starter" not in headless["models"] and screens["models"]["starter"] == "auto"
    headless["models"]["starter"] = "auto"
    assert strip_meta(headless) == strip_meta(screens)
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


def test_a_session_never_writes_over_an_unrelated_file(
    catalog: Catalog, usable_facts: Facts, tmp_path: Path
) -> None:
    other = tmp_path / "hosts"
    other.write_bytes(b"127.0.0.1 localhost\n")
    inp = Inputs(catalog, usable_facts, None, None, (), "v")
    erase = ["BACKSPACE"] * len(str(other))
    keys = ["ENTER", "ENTER", "s", *str(other), "ENTER", *erase, "ENTER", "q"]  # refused, cancel
    term = _ScriptedTerminal(keys)
    made: list[object] = []

    def document(chosen: object) -> dict[str, object]:
        made.append(chosen)
        return {"version": 1, "meta": {"saved_by": "v"}}

    Session(inp, term, document, plain=False).run()  # type: ignore[arg-type]
    assert other.read_bytes() == b"127.0.0.1 localhost\n"
    assert made == []
    shown = [" ".join(strip_ansi(frame).split()) for frame in term.frames]
    assert any("already exists; choose another name" in frame for frame in shown)


def test_a_session_writes_the_same_copy_again_when_it_is_confirmed_twice(
    catalog: Catalog, usable_facts: Facts, tmp_path: Path
) -> None:
    copy = tmp_path / "copy.json"
    inp = Inputs(catalog, usable_facts, None, None, (), "v")
    path = [*str(copy)]
    term = _ScriptedTerminal(["ENTER", "ENTER", "s", *path, "ENTER", "s", *path, "ENTER", "q"])
    writes: list[int] = []

    def document(chosen: object) -> dict[str, object]:
        writes.append(len(writes))
        return {"written": len(writes)}  # not shaped like an answers document

    Session(inp, term, document, plain=False).run()  # type: ignore[arg-type]
    assert json.loads(copy.read_text()) == {"written": 2}
    assert not any("already exists" in frame for frame in term.frames)


def test_a_token_typed_with_spaces_around_it_is_kept_trimmed(
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
    )
    wait_for_output(master)
    token = b"  hf_TRIMMED \t" + ENTER
    os.write(master, DOWN + DOWN + ENTER + ENTER + DOWN + DOWN + ENTER + token + ENTER * 4)
    assert wait_exit(p, master)[0] == 0
    assert sec.read_bytes() == b"HF_TOKEN=hf_TRIMMED\n"


def test_the_plain_form_can_type_another_folder(
    catalog: Catalog,
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out, folder = tmp_path / "a.json", tmp_path / "my models"
    p = spawn("--catalog", catalog_path, "--out", out, "--tty", slave, env={"HAL0_PLAIN": "1"})
    screens = _Screens(master)
    screens.answer(catalog.questions["setup_type"].title, "")  # Recommended
    screens.wait(catalog.questions["model_store"].title)
    screens.wait("> ")
    match = re.search(rb"(\d+)\) Type another folder\.\.\.", screens.seen)
    assert match is not None
    os.write(master, match.group(1) + b"\n")
    screens.wait("Type the value and press Enter")
    screens.wait("> ")
    os.write(master, str(folder).encode() + b"\n")
    screens.answer("Ready to install", "")
    assert wait_exit(p, master)[0] == 0
    assert json.loads(out.read_text())["model_store"]["path"] == str(folder)


def test_a_background_run_goes_headless_and_never_touches_the_terminal(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master, slave = pty_pair
    before = _tty_attrs(slave)
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: os.getpgrp() + 1)
    out = tmp_path / "a.json"
    code = cli.main(
        ["--catalog", str(catalog_path), "--out", str(out), "--tty", slave],
        environ={"TERM": "xterm"},
    )
    assert code == 0 and out.exists()
    assert read_all(master) == b"" and _tty_attrs(slave) == before


@pytest.mark.parametrize("plain", [False, True])
def test_an_internal_error_is_one_line_exit_70_and_writes_nothing(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    plain: bool,
) -> None:
    from hal0.install.wizard import session

    _, slave = pty_pair
    before = _tty_attrs(slave)

    def broken(*args: object, **kwargs: object) -> list[str]:
        raise LookupError("a latent bug")

    monkeypatch.setattr(session, "render", broken)
    out, sec = tmp_path / "a.json", tmp_path / "s"
    argv = ["--catalog", str(catalog_path), "--out", str(out), "--secret-out", str(sec)]
    env = {"TERM": "xterm", **({"HAL0_PLAIN": "1"} if plain else {})}
    code = cli.main([*argv, "--tty", slave], environ=env)
    captured = capsys.readouterr()
    assert code == cli.EXIT_INTERNAL == 70
    assert captured.err == "hal0 setup: internal error (LookupError)\n"
    assert captured.out == ""
    assert not out.exists() and not sec.exists()
    assert _tty_attrs(slave) == before


def test_an_oserror_that_is_a_bug_is_exit_70_not_an_interrupt(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hal0.install.wizard import flow, term

    _, slave = pty_pair
    before = _tty_attrs(slave)

    def broken(*args: object, **kwargs: object) -> object:
        raise OSError("bug")

    monkeypatch.setattr(flow, "press", broken)
    out = tmp_path / "a.json"
    argv = ["--catalog", str(catalog_path), "--out", str(out), "--tty", slave]
    # Opening the terminal discards typed-ahead input, so hand the loop its key directly.
    monkeypatch.setattr(term.Terminal, "read_key", lambda self: "ENTER")
    code = cli.main(argv, environ={"TERM": "xterm"})
    captured = capsys.readouterr()
    assert code == 70
    assert captured.err == "hal0 setup: internal error (OSError)\n"
    assert not out.exists()
    assert _tty_attrs(slave) == before


@pytest.mark.parametrize("method", ["read_key", "read_line", "width", "draw"])
def test_a_lost_terminal_is_an_interruption_exit_130(
    method: str,
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hal0.install.wizard import term

    _, slave = pty_pair
    before = _tty_attrs(slave)

    def lost(*args: object, **kwargs: object) -> object:
        raise OSError(errno.EIO, "terminal lost")

    monkeypatch.setattr(term.Terminal, method, lost)
    plain = method == "read_line"  # read_line is the plain form's call
    env = {"TERM": "xterm", **({"HAL0_PLAIN": "1"} if plain else {})}
    out = tmp_path / "a.json"
    argv = ["--catalog", str(catalog_path), "--out", str(out), "--tty", slave]
    code = cli.main(argv, environ=env)
    captured = capsys.readouterr()
    assert code == cli.EXIT_INTERRUPTED == 130
    assert captured.out == ""
    assert not out.exists()
    assert _tty_attrs(slave) == before


def test_a_keyboard_interrupt_inside_the_screens_is_still_130(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hal0.install.wizard import session

    def interrupted(*args: object, **kwargs: object) -> list[str]:
        raise KeyboardInterrupt

    monkeypatch.setattr(session, "render", interrupted)
    out = tmp_path / "a.json"
    argv = ["--catalog", str(catalog_path), "--out", str(out), "--tty", pty_pair[1]]
    assert cli.main(argv, environ={"TERM": "xterm"}) == 130 and not out.exists()


def test_a_catalog_without_the_consent_question_is_a_usage_error_on_the_headless_path(
    catalog_path: Path, tmp_path: Path, consent_facts_path: Path
) -> None:
    data = json.loads(catalog_path.read_text())
    data["questions"] = [q for q in data["questions"] if q["id"] != "cpu_only"]
    bare = _write_json(tmp_path / "bare.json", data)
    out = tmp_path / "a.json"
    r = wizard("--catalog", bare, "--facts", consent_facts_path, "--out", out, "--headless")
    assert r.returncode == 2 and not out.exists()
    assert r.stderr == f"hal0 setup: {bare}: question 'cpu_only' is missing\n"


def test_the_field_names_and_the_copy_key_have_one_owner(
    catalog: Catalog, usable_facts: Facts, tmp_path: Path
) -> None:
    from hal0.install.wizard import flow, session

    for module in (session, cli):
        text = Path(module.__file__).read_text()
        assert '"_save_copy_to"' not in text
        assert '"save"' not in text and '"token"' not in text
    review = flow.start(catalog, usable_facts)
    for key in ("ENTER", "ENTER"):
        review = flow.press(review, key)
    saving = flow.press(review, "s")
    assert flow.save_field_open(saving) and not flow.save_field_open(review)
    assert not flow.secret_field_open(saving)
    saved = saving
    for key in [*str(tmp_path / "c.json"), "ENTER"]:
        saved = flow.press(saved, key)
    assert flow.chosen(saved)[flow.SAVE_COPY_KEY] == str(tmp_path / "c.json")


def test_the_unchecked_wording_lives_in_one_place() -> None:
    package = Path(cli.__file__).parent
    owners = [
        p.name for p in package.glob("*.py") if "could not be checked on this box" in p.read_text()
    ]
    assert owners == ["flow_model.py"]


# ------------------------------------------------- the chosen-folder signal

NOT_THERE = "hal0 setup: --chosen-folder-out must name a path that does not exist"
CUSTOM_KEYS = [DOWN, DOWN, ENTER]
# With saved answers naming another folder, the second row is the preset's store.
PICK_PRESET = [ENTER, DOWN, ENTER]


def _screens_run(
    spawn: Callable[..., subprocess.Popen[str]],
    pty_pair: tuple[int, str],
    keys: list[bytes],
    *args: object,
    env: dict[str, str] | None = None,
) -> int:
    """Start the wizard on the pty, type the keys once the first screen is up, return the exit code."""
    master, slave = pty_pair
    p = spawn("--tty", slave, *args, env=env)
    wait_for_output(master)
    for key in keys:
        os.write(master, key)
    return wait_exit(p, master)[0]


def test_three_enters_on_the_short_path_record_nothing(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, mark = tmp_path / "a.json", tmp_path / "folder"
    code = _screens_run(
        spawn,
        pty_pair,
        [ENTER * 3],
        "--catalog",
        catalog_path,
        "--out",
        out,
        "--chosen-folder-out",
        mark,
    )
    assert code == 0 and out.exists() and not mark.exists()


def test_picking_another_listed_folder_records_it(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    saved_path: Path,
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, mark = tmp_path / "a.json", tmp_path / "folder"
    code = _screens_run(
        spawn,
        pty_pair,
        [*PICK_PRESET, ENTER],
        "--catalog",
        catalog_path,
        "--saved",
        saved_path,
        "--out",
        out,
        "--chosen-folder-out",
        mark,
    )
    assert code == 0
    assert mark.read_bytes() == b"/var/lib/hal0/models\n"
    assert stat.S_IMODE(mark.stat().st_mode) == 0o600


def test_a_folder_typed_on_the_folder_screen_is_recorded(
    catalog: Catalog,
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out, mark, folder = tmp_path / "a.json", tmp_path / "folder", tmp_path / "my models"
    p = spawn(
        "--catalog",
        catalog_path,
        "--out",
        out,
        "--tty",
        slave,
        "--chosen-folder-out",
        mark,
        env={"HAL0_PLAIN": "1"},
    )
    screens = _Screens(master)
    screens.answer(catalog.questions["setup_type"].title, "")
    screens.wait(catalog.questions["model_store"].title)
    screens.wait("> ")
    match = re.search(rb"(\d+)\) Type another folder\.\.\.", screens.seen)
    assert match is not None
    os.write(master, match.group(1) + b"\n")
    screens.wait("Type the value and press Enter")
    screens.wait("> ")
    os.write(master, str(folder).encode() + b"\n")
    screens.answer("Ready to install", "")
    assert wait_exit(p, master)[0] == 0
    assert mark.read_text() == f"{folder}\n"
    assert json.loads(out.read_text())["model_store"]["path"] == str(folder)


def test_customize_that_leaves_the_folder_alone_records_nothing(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, mark = tmp_path / "a.json", tmp_path / "folder"
    code = _screens_run(
        spawn,
        pty_pair,
        [*CUSTOM_KEYS, ENTER * 8],
        "--catalog",
        catalog_path,
        "--out",
        out,
        "--chosen-folder-out",
        mark,
    )
    assert code == 0 and out.exists() and not mark.exists()
    assert json.loads(out.read_text())["install"]["type"] == "custom"


def test_customize_with_the_folder_changed_records_the_new_folder(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, mark = tmp_path / "a.json", tmp_path / "folder"
    typed = [c.encode() for c in "/srv/m"]
    code = _screens_run(
        spawn,
        pty_pair,
        [*CUSTOM_KEYS, ENTER, ENTER, b" ", *typed, ENTER, ENTER * 8],
        "--catalog",
        catalog_path,
        "--out",
        out,
        "--chosen-folder-out",
        mark,
    )
    assert code == 0
    assert json.loads(out.read_text())["model_store"]["path"] == "/srv/m"
    assert mark.read_text() == "/srv/m\n"


def test_a_reinstall_pressing_enter_on_a_prefilled_folder_records_nothing(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    saved_path: Path,
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, mark = tmp_path / "a.json", tmp_path / "folder"
    code = _screens_run(
        spawn,
        pty_pair,
        [ENTER * 3],
        "--catalog",
        catalog_path,
        "--saved",
        saved_path,
        "--out",
        out,
        "--chosen-folder-out",
        mark,
    )
    assert code == 0 and out.exists() and not mark.exists()
    assert json.loads(out.read_text())["model_store"]["path"] == str(tmp_path)


def test_a_run_without_screens_records_nothing(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    upgrade_facts_path: Path,
    saved_path: Path,
) -> None:
    base = ["--catalog", catalog_path, "--chosen-folder-out"]
    cases: list[tuple[list[object], dict[str, str], str | None]] = [
        (["--headless"], {}, pty_pair[1]),
        ([], {"HAL0_NONINTERACTIVE": "1"}, pty_pair[1]),
        ([], {}, str(tmp_path / "absent-terminal")),
        (["--facts", upgrade_facts_path, "--saved", saved_path], {}, pty_pair[1]),
    ]
    for number, (extra, env, tty) in enumerate(cases):
        mark = tmp_path / f"folder{number}"
        r = wizard(*base, mark, "--out", tmp_path / f"a{number}.json", *extra, env=env, tty=tty)
        assert r.returncode == 0, (number, r.stderr)
        assert not mark.exists(), number
    assert read_all(pty_pair[0]) == b""


@pytest.mark.parametrize("how", ["quit", "interrupt", "stop"])
def test_quit_interrupt_and_stop_record_nothing(
    how: str,
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, mark = tmp_path / "a.json", tmp_path / "folder"
    facts: list[object] = []
    if how == "stop":
        facts = [
            "--facts",
            _facts_file(tmp_path, "stop.json", gpu={"outcome": "stop", "reason": "no_device"}),
        ]
    keys = {"quit": [ENTER, b"q"], "interrupt": [ENTER, b"\x03"], "stop": [ENTER]}[how]
    code = _screens_run(
        spawn, pty_pair, keys, "--catalog", catalog_path, *facts, "--out", out,
        "--chosen-folder-out", mark,
    )  # fmt: skip
    assert code == {"quit": 10, "interrupt": 130, "stop": 11}[how]
    assert not out.exists() and not mark.exists()


@pytest.mark.parametrize("how", ["quit", "interrupt"])
def test_quit_and_interrupt_on_the_review_screen_record_nothing(
    how: str,
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    saved_path: Path,
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, mark = tmp_path / "a.json", tmp_path / "folder"
    code = _screens_run(
        spawn,
        pty_pair,
        [*PICK_PRESET, b"q" if how == "quit" else b"\x03"],
        "--catalog",
        catalog_path,
        "--saved",
        saved_path,
        "--out",
        out,
        "--chosen-folder-out",
        mark,
    )
    assert code == {"quit": 10, "interrupt": 130}[how]
    assert not out.exists() and not mark.exists()


def test_the_signal_never_replaces_the_document(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    saved_path: Path,
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    same = tmp_path / "shared"
    code = _screens_run(
        spawn,
        pty_pair,
        [*PICK_PRESET, ENTER],
        "--catalog",
        catalog_path,
        "--saved",
        saved_path,
        "--out",
        same,
        "--chosen-folder-out",
        same,
    )
    assert code == 0
    assert json.loads(same.read_text())["model_store"]["path"] == "/var/lib/hal0/models"


def test_the_signal_never_replaces_the_secret_file(
    catalog_path: Path,
    ladder_models_path: Path,
    big_pool_facts_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, same = tmp_path / "a.json", tmp_path / "shared"
    typed = [c.encode() for c in "/srv/m"]
    keys = [DOWN, DOWN, ENTER, ENTER, DOWN, DOWN, ENTER, b"hf_SECRET", ENTER]
    keys += [b" ", *typed, ENTER, ENTER * 4]
    code = _screens_run(
        spawn,
        pty_pair,
        keys,
        "--catalog",
        catalog_path,
        "--models",
        ladder_models_path,
        "--facts",
        big_pool_facts_path,
        "--out",
        out,
        "--secret-out",
        same,
        "--chosen-folder-out",
        same,
    )
    assert code == 0
    assert json.loads(out.read_text())["model_store"]["path"] == "/srv/m"
    assert same.read_text() == "HF_TOKEN=hf_SECRET\n"


@pytest.mark.parametrize("fixer", ["flag", "variable"])
def test_a_folder_fixed_by_a_flag_or_variable_records_nothing(
    fixer: str,
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, mark = tmp_path / "a.json", tmp_path / "folder"
    args: list[object] = ["--catalog", catalog_path, "--out", out, "--chosen-folder-out", mark]
    env: dict[str, str] = {}
    if fixer == "flag":
        args += ["--", "--models-dir=/x/y"]
    else:
        env["HAL0_MODELS_DIR"] = "/x/y"
    code = _screens_run(spawn, pty_pair, [ENTER * 3], *args, env=env)
    assert code == 0
    assert json.loads(out.read_text())["model_store"]["path"] == "/x/y"
    assert not mark.exists()


@pytest.mark.parametrize("kind", ["file", "folder", "dangling"])
@pytest.mark.parametrize("headless", [True, False])
def test_an_existing_path_is_refused_untouched(
    kind: str, headless: bool, catalog_path: Path, tmp_path: Path, pty_pair: tuple[int, str]
) -> None:
    mark, out = tmp_path / "folder", tmp_path / "a.json"
    if kind == "file":
        mark.write_text("mine")
    elif kind == "folder":
        mark.mkdir()
    else:
        mark.symlink_to(tmp_path / "nowhere")
    r = wizard(
        "--catalog",
        catalog_path,
        "--out",
        out,
        "--chosen-folder-out",
        mark,
        *(["--headless"] if headless else []),
        tty=pty_pair[1],
    )
    assert r.returncode == 2 and r.stderr == NOT_THERE + "\n" and r.stdout == ""
    assert not out.exists() and read_all(pty_pair[0]) == b""
    if kind == "file":
        assert mark.read_text() == "mine"
    elif kind == "folder":
        assert mark.is_dir() and not list(mark.iterdir())
    else:
        assert mark.is_symlink() and not (tmp_path / "nowhere").exists()


def test_the_folder_is_nowhere_in_the_document_but_its_own_key(
    catalog: Catalog,
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    saved_path: Path,
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, mark = tmp_path / "a.json", tmp_path / "folder"
    code = _screens_run(
        spawn,
        pty_pair,
        [*PICK_PRESET, ENTER],
        "--catalog",
        catalog_path,
        "--saved",
        saved_path,
        "--out",
        out,
        "--chosen-folder-out",
        mark,
    )
    doc = json.loads(out.read_text())
    folder = mark.read_text().strip()

    def holders(node: object, path: str = "") -> list[str]:
        if isinstance(node, dict):
            return [h for k, v in node.items() for h in holders(v, f"{path}.{k}" if path else k)]
        if isinstance(node, list):
            return [h for v in node for h in holders(v, path)]
        return [path] if isinstance(node, str) and folder in node else []

    assert code == 0 and holders(doc) == ["model_store.path"]
    assert sorted(doc["meta"]) == ["saved_at", "saved_by"]
    assert "chosen" not in out.read_text()


def test_a_failed_signal_write_still_exits_0_with_the_document_and_no_partial_file(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    saved_path: Path,
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, mark = tmp_path / "a.json", tmp_path / "missing-parent" / "folder"
    master, slave = pty_pair
    p = spawn(
        "--catalog",
        catalog_path,
        "--saved",
        saved_path,
        "--out",
        out,
        "--tty",
        slave,
        "--chosen-folder-out",
        mark,
        capture=True,
    )
    wait_for_output(master)
    for key in [*PICK_PRESET, ENTER]:
        os.write(master, key)
    assert wait_exit(p, master)[0] == 0
    assert (
        p.stderr is not None
        and p.stderr.read() == "hal0 setup: could not record the chosen folder\n"
    )
    assert out.exists() and not mark.exists() and not mark.parent.exists()


# ------------------------------------------------ a given answers file: no screens


@pytest.mark.parametrize("reconfigure", [False, True])
def test_a_given_answers_file_shows_no_screens(
    reconfigure: bool, catalog_path: Path, tmp_path: Path, pty_pair: tuple[int, str]
) -> None:
    given = _write_json(tmp_path / "given.json", {"network": {"port": 9123}})
    out, mark = tmp_path / "a.json", tmp_path / "folder"
    r = wizard(
        "--catalog",
        catalog_path,
        "--answers",
        given,
        "--out",
        out,
        "--chosen-folder-out",
        mark,
        *(["--reconfigure"] if reconfigure else []),
        tty=pty_pair[1],
    )
    assert r.returncode == 0 and r.stderr == ""
    assert read_all(pty_pair[0]) == b""
    assert json.loads(out.read_text())["network"]["port"] == 9123
    assert not mark.exists()


def test_a_given_file_wins_over_saved_answers_on_an_upgrade(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    upgrade_facts_path: Path,
    saved_path: Path,
) -> None:
    given = _write_json(tmp_path / "given.json", {"network": {"port": 9123}})
    out = tmp_path / "a.json"
    r = wizard(
        "--catalog",
        catalog_path,
        "--facts",
        upgrade_facts_path,
        "--saved",
        saved_path,
        "--answers",
        given,
        "--out",
        out,
        "--reconfigure",
        tty=pty_pair[1],
    )
    assert r.returncode == 0 and read_all(pty_pair[0]) == b""
    assert json.loads(out.read_text())["network"]["port"] == 9123


def test_a_given_file_still_stops_a_box_that_cannot_run(
    catalog_path: Path, tmp_path: Path, pty_pair: tuple[int, str]
) -> None:
    given = _write_json(tmp_path / "given.json", {})
    facts = _facts_file(tmp_path, "stop.json", gpu={"outcome": "stop", "reason": "no_device"})
    out = tmp_path / "a.json"
    r = wizard(
        "--catalog", catalog_path, "--facts", facts, "--answers", given, "--out", out,
        tty=pty_pair[1],
    )  # fmt: skip
    assert r.returncode == 11 and not out.exists() and read_all(pty_pair[0]) == b""


def test_a_path_that_cannot_be_checked_is_refused_like_an_existing_one(
    catalog_path: Path, tmp_path: Path
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    out = tmp_path / "a.json"
    r = wizard(
        "--catalog", catalog_path, "--out", out, "--headless",
        "--chosen-folder-out", blocker / "folder",
    )  # fmt: skip
    assert r.returncode == 2 and r.stderr == NOT_THERE + "\n" and not out.exists()


# ------------------------------------------- the screens-shown marker

SHOWN_EXISTS = "hal0 setup: --screens-shown-out must name a path that does not exist"


def test_confirmed_screens_write_the_marker_before_the_document(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, shown = tmp_path / "a.json", tmp_path / "shown"
    code = _screens_run(
        spawn,
        pty_pair,
        [ENTER * 3],
        "--catalog",
        catalog_path,
        "--out",
        out,
        "--screens-shown-out",
        shown,
    )
    assert code == 0 and out.exists()
    assert shown.read_bytes() == b"shown\n"
    assert stat.S_IMODE(shown.stat().st_mode) == 0o600


def test_customize_that_is_confirmed_writes_the_marker_too(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, shown = tmp_path / "a.json", tmp_path / "shown"
    code = _screens_run(
        spawn,
        pty_pair,
        [*CUSTOM_KEYS, ENTER * 8],
        "--catalog",
        catalog_path,
        "--out",
        out,
        "--screens-shown-out",
        shown,
    )
    assert code == 0 and json.loads(out.read_text())["install"]["type"] == "custom"
    assert shown.read_bytes() == b"shown\n"


def test_a_run_without_screens_writes_no_marker(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    upgrade_facts_path: Path,
    saved_path: Path,
) -> None:
    given = _write_json(tmp_path / "given.json", {})
    base = ["--catalog", catalog_path, "--screens-shown-out"]
    cases: list[tuple[list[object], dict[str, str], str | None]] = [
        (["--headless"], {}, pty_pair[1]),
        ([], {"HAL0_NONINTERACTIVE": "1"}, pty_pair[1]),
        (["--answers", given], {}, pty_pair[1]),
        ([], {}, str(tmp_path / "absent-terminal")),
        (["--facts", upgrade_facts_path, "--saved", saved_path], {}, pty_pair[1]),
    ]
    for number, (extra, env, tty) in enumerate(cases):
        shown = tmp_path / f"shown{number}"
        out = tmp_path / f"a{number}.json"
        r = wizard(*base, shown, "--out", out, *extra, env=env, tty=tty)
        assert r.returncode == 0, (number, r.stderr)
        assert out.exists() and not shown.exists(), number
    assert read_all(pty_pair[0]) == b""


@pytest.mark.parametrize(
    ("keys", "code"),
    [
        ([b"q"], 10),
        ([ENTER, ENTER, b"q"], 10),
        ([ENTER, ENTER, b"\x03"], 130),
    ],
    ids=["quit-first-screen", "quit-review", "interrupt-review"],
)
def test_quit_and_interrupt_write_no_marker(
    keys: list[bytes],
    code: int,
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    out, shown = tmp_path / "a.json", tmp_path / "shown"
    got = _screens_run(
        spawn, pty_pair, keys, "--catalog", catalog_path, "--out", out,
        "--screens-shown-out", shown,
    )  # fmt: skip
    assert got == code
    assert not out.exists() and not shown.exists()


def _existing(kind: str, tmp_path: Path) -> Path:
    path = tmp_path / "shown"
    if kind == "file":
        path.write_text("mine")
    elif kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        (tmp_path / "target").write_text("mine")
        path.symlink_to(tmp_path / "target")
    else:
        path.symlink_to(tmp_path / "nowhere")
    return path


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling"])
def test_a_marker_path_that_exists_is_a_usage_error(
    kind: str, catalog_path: Path, tmp_path: Path
) -> None:
    shown = _existing(kind, tmp_path)
    before = (shown.is_symlink(), shown.is_dir(), os.readlink(shown) if shown.is_symlink() else "")
    out = tmp_path / "a.json"
    r = wizard("--catalog", catalog_path, "--out", out, "--headless", "--screens-shown-out", shown)
    assert r.returncode == 2 and r.stderr == SHOWN_EXISTS + "\n" and r.stdout == ""
    assert not out.exists()
    assert before == (
        shown.is_symlink(),
        shown.is_dir(),
        os.readlink(shown) if shown.is_symlink() else "",
    )
    if kind == "file":
        assert shown.read_text() == "mine"
    if kind == "symlink":
        assert (tmp_path / "target").read_text() == "mine"
    if kind == "dangling":
        assert not (tmp_path / "nowhere").exists()


def test_a_marker_that_cannot_be_written_stops_the_run_with_no_files(
    catalog_path: Path,
    tmp_path: Path,
    pty_pair: tuple[int, str],
    spawn: Callable[..., subprocess.Popen[str]],
) -> None:
    master, slave = pty_pair
    out, secret, folder = tmp_path / "a.json", tmp_path / "token", tmp_path / "folder"
    p = spawn(
        "--catalog", catalog_path, "--out", out, "--tty", slave,
        "--secret-out", secret, "--chosen-folder-out", folder,
        "--screens-shown-out", tmp_path / "missing" / "shown",
        capture=True,
    )  # fmt: skip
    wait_for_output(master)
    os.write(master, ENTER * 3)
    code, _ = wait_exit(p, master)
    assert p.stdout is not None and p.stderr is not None
    stdout, stderr = p.stdout.read(), p.stderr.read()
    assert code == 2  # the code an unwritable --out gives too
    assert stdout == ""
    assert len(stderr.strip().splitlines()) == 1 and stderr.startswith("hal0 setup: ")
    assert not out.exists() and not secret.exists() and not folder.exists()


def test_the_help_lists_the_marker_option() -> None:
    r = wizard("--help")
    assert r.returncode == 0
    assert "--screens-shown-out" in r.stdout
    assert "write a one-line file when the screens were shown and confirmed" in " ".join(
        r.stdout.split()
    )
