"""The answers reader: a document in, allow-listed NAME=value lines out.

The reader runs as root before hal0 is installed, so it is exercised the way
the installer runs it: ``python -I answers_reader.py --origin ... DOC``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

READER = Path(__file__).resolve().parents[2] / "installer" / "lib" / "answers_reader.py"

FULL = {
    "version": 1,
    "model_store": {"path": "/mnt/my models/a=b", "flm_path": "/mnt/flm"},
    "apps": {
        "openwebui": {"enabled": False},
        "hermes": {"enabled": False, "terminal_tool": True},
        "pi": {"enabled": True},
    },
    "memory": {"enabled": False},
    "gen": {"mode": "custom", "models": ["flux-1.dev", "sdxl_turbo"]},
    "models": {"brain": "qwen3-8b", "starter": "llama-3.2-3b"},
    "hardware": {"allow_cpu_only": True},
    "network": {
        "port": 8181,
        "bind_host": "127.0.0.1",
        "hostname": "box-1",
        "public_url": "chat.example.com",
        "allowed_origins": ["https://a.example.com", "http://10.0.0.5:8080"],
    },
    "access": {"admin_key": "create"},
    "maintenance": {"weekly_benchmark": True},
    "npu": {"setup": "off"},
}


def run(doc: object, tmp_path: Path, origin: str = "wizard", raw: bytes | None = None):
    path = tmp_path / "answers.json"
    path.write_bytes(raw if raw is not None else json.dumps(doc).encode())
    return subprocess.run(
        [sys.executable, "-I", str(READER), "--origin", origin, "--", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def lines(proc: subprocess.CompletedProcess[str]) -> list[str]:
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.splitlines()


def test_full_document_maps_every_key(tmp_path: Path) -> None:
    proc = run(FULL, tmp_path)
    assert lines(proc) == [
        "HAL0_NONINTERACTIVE=1",
        "HAL0_MODELS_DIR=/mnt/my models/a=b",
        "HAL0_FLM_MODELS_DIR=/mnt/flm",
        "HAL0_SKIP_OPENWEBUI=1",
        "HAL0_SKIP_HERMES=1",
        "HAL0_HERMES_TERMINAL=1",
        "HAL0_INSTALL_PI=1",
        "HAL0_SKIP_HINDSIGHT=1",
        "HAL0_IMAGE_MODELS=flux-1.dev,sdxl_turbo",
        "HAL0_BRAIN_MODEL=qwen3-8b",
        "HAL0_PULL_AGENT_MODEL=1",
        "HAL0_AGENT_MODEL=llama-3.2-3b",
        "HAL0_ALLOW_CPU_ONLY=1",
        "HAL0_PORT=8181",
        "HAL0_BIND_HOST=127.0.0.1",
        "HAL0_HOSTNAME=box-1",
        "HAL0_PUBLIC_URL=https://chat.example.com",
        "HAL0_ALLOWED_ORIGINS=https://a.example.com,http://10.0.0.5:8080",
        "HAL0_ADMIN_KEY_MODE=create",
        "HAL0_ENABLE_BENCH_TIMER=1",
        "HAL0_NPU_SETUP=off",
    ]


def test_skip_variants(tmp_path: Path) -> None:
    doc = {
        "version": 1,
        "apps": {"pi": {"enabled": False}},
        "models": {"brain": "none", "starter": "none"},
        "gen": {"mode": "none"},
        "maintenance": {"weekly_benchmark": False},
    }
    assert lines(run(doc, tmp_path)) == [
        "HAL0_NONINTERACTIVE=1",
        "HAL0_SKIP_PI=1",
        "HAL0_IMAGE_MODELS=none",
        "HAL0_SKIP_BRAIN_MODEL=1",
        "HAL0_PULL_AGENT_MODEL=0",
        "HAL0_ENABLE_BENCH_TIMER=0",
    ]


def test_defaults_emit_only_noninteractive(tmp_path: Path) -> None:
    assert lines(run({"version": 1}, tmp_path)) == ["HAL0_NONINTERACTIVE=1"]


def test_terminal_tool_only_for_wizard_origin(tmp_path: Path) -> None:
    doc = {"version": 1, "apps": {"hermes": {"terminal_tool": True}}}
    wizard = run(doc, tmp_path, origin="wizard")
    assert "HAL0_HERMES_TERMINAL=1" in lines(wizard)
    proc = run(doc, tmp_path, origin="file")
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]
    assert "terminal_tool" in proc.stderr


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("none", ["HAL0_NONINTERACTIVE=1", "HAL0_PULL_AGENT_MODEL=0"]),
        ("auto", ["HAL0_NONINTERACTIVE=1", "HAL0_PULL_AGENT_MODEL=1"]),
        (
            "qwen3.5-9b",
            ["HAL0_NONINTERACTIVE=1", "HAL0_PULL_AGENT_MODEL=1", "HAL0_AGENT_MODEL=qwen3.5-9b"],
        ),
    ],
)
def test_starter_three_ways(tmp_path: Path, value: str, expected: list[str]) -> None:
    assert lines(run({"version": 1, "models": {"starter": value}}, tmp_path)) == expected


_BAD = ["$(reboot)", "`id`", "a;b", "a\nb", '"quoted"', "a\x00b", "a\rb", "a'b", "a\\b", "a|b"]


@pytest.mark.parametrize("bad", _BAD)
@pytest.mark.parametrize(
    "section",
    [
        {"model_store": {"path": None}},
        {"models": {"starter": None}},
        {"network": {"hostname": None}},
    ],
)
def test_rejects_shell_metacharacters(tmp_path: Path, bad: str, section: dict) -> None:
    (outer,) = section
    (inner,) = section[outer]
    value = f"/mnt/{bad}" if outer == "model_store" else bad
    doc = {"version": 1, outer: {inner: value}}
    # Raw JSON so the NUL and newline reach the reader as an escape sequence too.
    proc = run(doc, tmp_path)
    assert proc.returncode == 2, proc.stdout
    assert proc.stdout == ""
    assert proc.stderr.strip()


def test_control_characters_rejected_everywhere(tmp_path: Path) -> None:
    for text in ("a\x1b[0m", "a\x7fb", "a\u0085b", "a b"):
        doc = {"version": 1, "model_store": {"path": "/mnt/" + text}}
        proc = run(doc, tmp_path)
        assert proc.returncode == 2 and proc.stdout == ""


def test_relative_models_path_rejected(tmp_path: Path) -> None:
    for key in ("path", "flm_path"):
        proc = run({"version": 1, "model_store": {key: "models/here"}}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == ""


@pytest.mark.parametrize(
    ("port", "ok"),
    [(1, True), (65535, True), (0, False), (65536, False), (-1, False),
     ("80", False), (True, False), (80.5, False)],
)
def test_port_range(tmp_path: Path, port: object, ok: bool) -> None:
    proc = run({"version": 1, "network": {"port": port}}, tmp_path)
    if ok:
        assert f"HAL0_PORT={port}" in lines(proc)
    else:
        assert proc.returncode == 2 and proc.stdout == ""


@pytest.mark.parametrize(
    "net",
    [
        {"public_url": "ftp://x.example.com"},
        {"public_url": "https://u:p@x.example.com"},
        {"public_url": "https://x.example.com/?a=1"},
        {"public_url": "https://x.example.com/a b"},
        {"allowed_origins": ["x.example.com"]},
        {"allowed_origins": ["https://x.example.com/path"]},
        {"allowed_origins": "https://x.example.com"},
        {"bind_host": "bad host"},
        {"hostname": "-bad"},
        {"hostname": "10.0.0.1"},
    ],
)
def test_network_values_rejected(tmp_path: Path, net: dict) -> None:
    proc = run({"version": 1, "network": net}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""


def test_unknown_key_warns_or_fails_by_strict(tmp_path: Path) -> None:
    doc = {"version": 1, "mystery": 1, "network": {"extra": 2}}
    proc = run(doc, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]
    assert "mystery" in proc.stderr and "network.extra" in proc.stderr
    strict = run({**doc, "strict": True}, tmp_path)
    assert strict.returncode == 2 and strict.stdout == ""
    assert "mystery" in strict.stderr


@pytest.mark.parametrize("version", [None, 2, "1", True, 1.0])
def test_wrong_version_exits_2(tmp_path: Path, version: object) -> None:
    doc = {} if version is None else {"version": version}
    proc = run(doc, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    assert "version" in proc.stderr


def test_admin_key_mode_values(tmp_path: Path) -> None:
    for word in ("create", "none"):
        proc = run({"version": 1, "access": {"admin_key": word}}, tmp_path)
        assert f"HAL0_ADMIN_KEY_MODE={word}" in lines(proc)
    for bad in ("sk-abcdef0123456789", "", "Create", 1, True, None, ["create"]):
        proc = run({"version": 1, "access": {"admin_key": bad}}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == "", bad
    proc = run({"version": 1, "access": {"admin_key": "create", "key": "hal0-secret"}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    assert "hal0-secret" not in proc.stderr


def test_duplicate_keys_rejected(tmp_path: Path) -> None:
    raw = b'{"version": 1, "model_store": {"path": "/a", "path": "/b"}}'
    proc = run(None, tmp_path, raw=raw)
    assert proc.returncode == 2 and proc.stdout == ""
    assert "repeats" in proc.stderr


def test_oversize_invalid_and_non_object_rejected(tmp_path: Path) -> None:
    big = b'{"version": 1, "x": "' + b"a" * (1024 * 1024) + b'"}'
    for raw in (big, b"{not json", b"[1]", b'"s"', b"", b'{"version": NaN}', b"\xff\xfe"):
        proc = run(None, tmp_path, raw=raw)
        assert proc.returncode == 2 and proc.stdout == "", raw[:20]


def test_partial_output_never_happens(tmp_path: Path) -> None:
    doc = {"version": 1, "model_store": {"path": "/ok"}, "network": {"port": 0}}
    proc = run(doc, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""


def test_bad_usage_and_missing_file(tmp_path: Path) -> None:
    for argv in ([], ["--origin", "other", "x"], ["--origin", "file"]):
        proc = subprocess.run([sys.executable, "-I", str(READER), *argv],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 2 and proc.stdout == ""
    proc = subprocess.run(
        [sys.executable, "-I", str(READER), "--origin", "file", str(tmp_path / "nope.json")],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 2 and proc.stdout == ""


def test_only_allow_listed_names_are_printed(tmp_path: Path) -> None:
    names = {ln.split("=", 1)[0] for ln in lines(run(FULL, tmp_path))}
    src = READER.read_text(encoding="utf-8")
    ns: dict = {}
    exec(compile(src.split("MAX_BYTES")[0], str(READER), "exec"), ns)  # noqa: S102
    assert names <= set(ns["ALLOWED_NAMES"])
