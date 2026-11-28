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

LADDER = {
    "version": 1,
    "model_ladder": [
        {"role": "starter", "model": "llama-3.2-3b", "label": "x", "download_gb": 1.0},
        {"role": "starter", "model": "qwen3.5-9b"},
        {"role": "brain", "model": "qwen3-8b"},
        {"role": "image", "model": "flux-1.dev"},
        {"role": "image", "model": "sdxl_turbo"},
    ],
}


def _manifest(tmp_path: Path, data: object = LADDER) -> Path:
    path = tmp_path / "ladder.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


FULL = {
    "version": 1,
    "model_store": {"path": "/mnt/my models/a=b", "flm_path": "/mnt/flm"},
    "apps": {
        "openwebui": {"enabled": False},
        "hermes": {"enabled": False, "terminal_tool": True},
        "pi": {"enabled": True},
    },
    "memory": {"enabled": False},
    "gen": {"mode": "scaffold_and_download", "models": ["flux-1.dev", "sdxl_turbo"]},
    "models": {"brain": "qwen3-8b", "starter": "llama-3.2-3b"},
    "hardware": {"allow_cpu_only": True},
    "network": {
        "port": 8181,
        "hostname": "box-1",
        "public_url": "chat.example.com",
        "allowed_origins": ["https://a.example.com", "http://192.0.2.5:8080"],
    },
    "access": {"admin_key": "create"},
    "maintenance": {"weekly_benchmark": True},
    "npu": {"setup": "off"},
}


def run(doc: object, tmp_path: Path, origin: str = "wizard", raw: bytes | None = None,
        manifest: Path | None = None):
    path = tmp_path / "answers.json"
    path.write_bytes(raw if raw is not None else json.dumps(doc).encode())
    seam = ["--models-manifest", str(manifest)] if manifest else []
    return subprocess.run(
        [sys.executable, "-I", str(READER), "--origin", origin, *seam, "--", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def lines(proc: subprocess.CompletedProcess[str]) -> list[str]:
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.splitlines()


def test_full_document_maps_every_key(tmp_path: Path) -> None:
    proc = run(FULL, tmp_path, manifest=_manifest(tmp_path))
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
        "HAL0_HOSTNAME=box-1",
        "HAL0_PUBLIC_URL=https://chat.example.com",
        "HAL0_ALLOWED_ORIGINS=https://a.example.com,http://192.0.2.5:8080",
        "HAL0_ADMIN_KEY_MODE=create",
        "HAL0_ENABLE_BENCH_TIMER=1",
        "HAL0_NPU_SETUP=off",
    ]


def test_skip_variants(tmp_path: Path) -> None:
    doc = {
        "version": 1,
        "apps": {"pi": {"enabled": False}},
        "models": {"brain": "none", "starter": "none"},
        "gen": {"mode": "scaffold_only"},
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


@pytest.mark.parametrize(
    ("gen", "expected"),
    [
        ({}, "none"),
        ({"mode": "off"}, "none"),
        ({"mode": "scaffold_only"}, "none"),
        ({"mode": "scaffold_and_download"}, "recommended"),
        ({"mode": "scaffold_and_download", "models": []}, "recommended"),
        ({"mode": "scaffold_and_download", "models": ["sdxl_turbo"]}, "sdxl_turbo"),
    ],
)
def test_gen_modes(tmp_path: Path, gen: dict, expected: str) -> None:
    proc = run({"version": 1, "gen": gen}, tmp_path, manifest=_manifest(tmp_path))
    assert f"HAL0_IMAGE_MODELS={expected}" in lines(proc)


@pytest.mark.parametrize(
    "gen",
    [
        {"mode": "custom"},
        {"mode": "none"},
        {"mode": "recommended"},
        {"mode": "off", "models": ["flux-1.dev"]},
        {"mode": "scaffold_only", "models": ["flux-1.dev"]},
        {"mode": "scaffold_and_download", "models": ["recommended"]},
        {"mode": "scaffold_and_download", "models": ["none"]},
        {"mode": "scaffold_and_download", "models": "flux-1.dev"},
    ],
)
def test_gen_rejected(tmp_path: Path, gen: dict) -> None:
    proc = run({"version": 1, "gen": gen}, tmp_path, manifest=_manifest(tmp_path))
    assert proc.returncode == 2 and proc.stdout == ""


def test_comfyui_app_key_is_known_and_emits_nothing(tmp_path: Path) -> None:
    doc = {"version": 1, "strict": True, "apps": {"comfyui": {"enabled": True}}}
    proc = run(doc, tmp_path, manifest=_manifest(tmp_path))
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"] and proc.stderr == ""


@pytest.mark.parametrize("strict", [False, True])
def test_bind_host_warns_never_errors_and_is_not_emitted(tmp_path: Path, strict: bool) -> None:
    doc = {"version": 1, "strict": strict, "network": {"bind_host": "127.0.0.1"}}
    proc = run(doc, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]
    assert proc.stderr.count("network.bind_host is not applied at install") == 1


def test_slots_and_huggingface_are_known_keys(tmp_path: Path) -> None:
    doc = {"version": 1, "strict": True, "slots": [{"capability": "chat"}],
           "huggingface": {"token_env": "HF_TOKEN"}}
    proc = run(doc, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]
    assert "huggingface" in proc.stderr
    for bad in ({"slots": {}}, {"huggingface": []}):
        proc = run({"version": 1, **bad}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == ""


# ── model ids against the installer's generated list ────────────────────────


@pytest.mark.parametrize(
    ("section", "key", "good", "bad"),
    [
        ("models", "starter", "llama-3.2-3b", "qwen3-8b"),
        ("models", "brain", "qwen3-8b", "llama-3.2-3b"),
    ],
)
def test_model_ids_checked_against_manifest(
    tmp_path: Path, section: str, key: str, good: str, bad: str
) -> None:
    m = _manifest(tmp_path)
    assert run({"version": 1, section: {key: good}}, tmp_path, manifest=m).returncode == 0
    for other in (bad, "unknown-model"):
        proc = run({"version": 1, section: {key: other}}, tmp_path, manifest=m)
        assert proc.returncode == 2 and proc.stdout == ""
        assert f"{section}.{key}" in proc.stderr
    for word in ("auto", "none"):
        assert run({"version": 1, section: {key: word}}, tmp_path, manifest=m).returncode == 0


def test_image_ids_checked_against_manifest(tmp_path: Path) -> None:
    m = _manifest(tmp_path)
    gen = {"mode": "scaffold_and_download", "models": ["flux-1.dev"]}
    assert run({"version": 1, "gen": gen}, tmp_path, manifest=m).returncode == 0
    gen = {"mode": "scaffold_and_download", "models": ["nope-model"]}
    proc = run({"version": 1, "gen": gen}, tmp_path, manifest=m)
    assert proc.returncode == 2 and "gen.models[0]" in proc.stderr
    # No image rows in the file: any well-formed id passes.
    only = _manifest(tmp_path, {"model_ladder": [{"role": "starter", "model": "a-1"}]})
    gen = {"mode": "scaffold_and_download", "models": ["nope-model"]}
    assert run({"version": 1, "gen": gen}, tmp_path, manifest=only).returncode == 0


@pytest.mark.parametrize(
    "data",
    [None, "missing", {"models": []}, {"model_ladder": [{"role": "starter"}]},
     {"model_ladder": [{"model": "x"}]}, {"model_ladder": "x"}],
)
def test_manifest_unreadable_falls_back_to_pattern_with_one_warning(
    tmp_path: Path, data: object
) -> None:
    if data == "missing":
        m = tmp_path / "absent.json"
    elif data is None:
        m = tmp_path / "bad.json"
        m.write_text("{not json", encoding="utf-8")
    else:
        m = _manifest(tmp_path, data)
    doc = {"version": 1, "models": {"starter": "any-id", "brain": "other-id"}}
    proc = run(doc, tmp_path, manifest=m)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1", "HAL0_BRAIN_MODEL=other-id",
                           "HAL0_PULL_AGENT_MODEL=1", "HAL0_AGENT_MODEL=any-id"]
    assert proc.stderr.count("model list") == 1
    assert run({"version": 1, "models": {"starter": "Bad Id"}}, tmp_path,
               manifest=m).returncode == 2


def test_default_manifest_is_found_beside_the_reader_not_the_cwd(tmp_path: Path) -> None:
    import shutil

    lib = tmp_path / "installer" / "lib"
    lib.mkdir(parents=True)
    shutil.copy(READER, lib / "answers_reader.py")
    (tmp_path / "installer" / "manifests").mkdir()
    (tmp_path / "installer" / "manifests" / "setup-models.json").write_text(
        json.dumps(LADDER), encoding="utf-8"
    )
    doc = tmp_path / "d.json"
    doc.write_text(json.dumps({"version": 1, "models": {"brain": "qwen3-8b"}}), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    proc = subprocess.run(
        [sys.executable, "-I", str(lib / "answers_reader.py"), "--origin", "file", str(doc)],
        capture_output=True, text=True, check=False, cwd=elsewhere,
    )
    assert proc.returncode == 0 and proc.stderr == "", proc.stderr
    doc.write_text(json.dumps({"version": 1, "models": {"brain": "nope"}}), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-I", str(lib / "answers_reader.py"), "--origin", "file", str(doc)],
        capture_output=True, text=True, check=False, cwd=elsewhere,
    )
    assert proc.returncode == 2


# ── messages never carry document bytes that move a terminal ────────────────

HOSTILE_KEY = "\x1b[2J\x1b]0;pwn\x07\x00\x7f\u0085\u202e"
HOSTILE_DOCS = [
    {"version": 1, HOSTILE_KEY: 1},
    {"version": 1, "strict": True, HOSTILE_KEY: 1},
    {"version": 1, "network": {HOSTILE_KEY: 1}},
    {"version": 1, "apps": {HOSTILE_KEY: {"enabled": True}}},
    {"version": 1, "access": {HOSTILE_KEY: "x"}},
    {"version": 1, "model_store": {"path": "/mnt/" + HOSTILE_KEY}},
    {"version": 1, "models": {"starter": HOSTILE_KEY}},
    {"version": 1, "network": {"hostname": HOSTILE_KEY, "public_url": HOSTILE_KEY,
                                "allowed_origins": [HOSTILE_KEY]}},
    {"version": 1, "gen": {"mode": HOSTILE_KEY}},
    {"version": 1, "access": {"admin_key": HOSTILE_KEY}},
    {"version": HOSTILE_KEY},
    {"version": 1, "x": {HOSTILE_KEY: {HOSTILE_KEY: HOSTILE_KEY}}},
]


@pytest.mark.parametrize("doc", HOSTILE_DOCS)
def test_stderr_has_no_control_bytes(tmp_path: Path, doc: dict) -> None:
    proc = run(doc, tmp_path, manifest=_manifest(tmp_path))
    data = proc.stderr.encode("utf-8", "surrogatepass")
    assert proc.stderr, "expected a message"
    assert all(b >= 0x20 or b == 0x0A for b in data), data
    assert b"\x7f" not in data
    assert all(b < 0x80 for b in data), data


def test_duplicate_key_message_is_escaped(tmp_path: Path) -> None:
    raw = b'{"version": 1, "\\u001b[2J": 1, "\\u001b[2J": 2}'
    proc = run(None, tmp_path, raw=raw)
    assert proc.returncode == 2
    assert b"\x1b" not in proc.stderr.encode()


def test_huge_values_are_not_echoed_whole(tmp_path: Path) -> None:
    raw = b'{"version": 1, "network": {"port": ' + b"9" * 100_000 + b"}}"
    proc = run(None, tmp_path, raw=raw)
    assert proc.returncode == 2 and proc.stdout == ""
    assert len(proc.stderr) < 1000
    long_path = "/mnt/" + "a$" * 50_000
    proc = run({"version": 1, "model_store": {"path": long_path}}, tmp_path)
    assert proc.returncode == 2 and len(proc.stderr) < 1000


def test_fifo_manifest_does_not_hang(tmp_path: Path) -> None:
    import os
    import time

    fifo = tmp_path / "ladder.fifo"
    os.mkfifo(fifo)
    start = time.monotonic()
    proc = run({"version": 1, "models": {"brain": "any-id"}}, tmp_path, manifest=fifo)
    assert time.monotonic() - start < 2
    assert lines(proc)[-1] == "HAL0_BRAIN_MODEL=any-id"
    assert proc.stderr.count("model list") == 1


def test_oversize_manifest_is_unreadable(tmp_path: Path) -> None:
    big = tmp_path / "big.json"
    big.write_bytes(b'{"model_ladder": [], "pad": "' + b"a" * (1024 * 1024) + b'"}')
    proc = run({"version": 1}, tmp_path, manifest=big)
    assert proc.returncode == 0 and proc.stderr.count("model list") == 1


def test_long_key_is_capped_in_messages(tmp_path: Path) -> None:
    key = "k" * 200_000
    doc = {"version": 1, "x": {key: {"y": "bad\x1bvalue"}}}
    proc = run(doc, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    assert len(proc.stderr.encode()) < 1000
    deep = {"version": 1, "n": {key: {key: "bad\x1b"}}}
    assert len(run(deep, tmp_path).stderr.encode()) < 1000


def test_strict_unknown_key_escaped_once(tmp_path: Path) -> None:
    proc = run({"version": 1, "strict": True, "bad\x1bkey": 1}, tmp_path)
    assert proc.returncode == 2
    assert "\\x1b" in proc.stderr and "\\\\x1b" not in proc.stderr


def test_reader_via_symlink_finds_manifest_beside_real_file(tmp_path: Path) -> None:
    import shutil

    real = tmp_path / "installer" / "lib"
    real.mkdir(parents=True)
    shutil.copy(READER, real / "answers_reader.py")
    (tmp_path / "installer" / "manifests").mkdir()
    (tmp_path / "installer" / "manifests" / "setup-models.json").write_text(
        json.dumps(LADDER), encoding="utf-8"
    )
    link = tmp_path / "elsewhere" / "reader.py"
    link.parent.mkdir()
    link.symlink_to(real / "answers_reader.py")
    doc = tmp_path / "d.json"
    doc.write_text(json.dumps({"version": 1, "models": {"brain": "nope"}}), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-I", str(link), "--origin", "file", str(doc)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 2 and "models.brain" in proc.stderr


def test_path_percent_and_dotdot_rejected(tmp_path: Path) -> None:
    for bad in ("/mnt/%h", "/mnt/../etc", "/mnt/a/../b", "/.."):
        proc = run({"version": 1, "model_store": {"path": bad}}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == "", bad
    assert run({"version": 1, "model_store": {"path": "/mnt/a..b/c"}}, tmp_path).returncode == 0


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
    proc = run({"version": 1, "models": {"starter": value}}, tmp_path,
               manifest=_manifest(tmp_path))
    assert lines(proc) == expected


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
        {"hostname": "-bad"},
        {"hostname": "192.0.2.1"},
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
    names = {ln.split("=", 1)[0] for ln in lines(run(FULL, tmp_path, manifest=_manifest(tmp_path)))}
    src = READER.read_text(encoding="utf-8")
    ns: dict = {}
    exec(compile(src.split("MAX_BYTES")[0], str(READER), "exec"), ns)  # noqa: S102
    assert names <= set(ns["ALLOWED_NAMES"])


def _emit(doc: object, tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "doc.json"
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", str(READER), *extra, "--emit-saved", str(path)],
        capture_output=True, text=True, check=False,
    )


def test_emit_saved_strips_grant_and_token_keeps_order(tmp_path: Path) -> None:
    doc = {
        "version": 1,
        "huggingface": {"token": "hf_x"},
        "apps": {"hermes": {"terminal_tool": True}, "pi": {"enabled": True}},
        "network": {"port": 80},
    }
    proc = _emit(doc, tmp_path, "--origin", "wizard")
    assert proc.returncode == 0, proc.stderr
    assert "hf_x" not in proc.stdout
    assert proc.stdout == json.dumps(
        {"version": 1, "apps": {"hermes": {}, "pi": {"enabled": True}}, "network": {"port": 80}}, indent=2
    ) + "\n"


def test_emit_saved_drops_look_alike_spellings(tmp_path: Path) -> None:
    doc = {
        "version": 1,
        "HuggingFace": {"token": "hf_y"},
        "apps": {"Hermes": {"Terminal_Tool": True}},
        "network": {"huggingface": "hf_z"},
    }
    proc = _emit(doc, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "hf_" not in proc.stdout and "erminal" not in proc.stdout


def test_emit_saved_rejects_like_a_normal_run(tmp_path: Path) -> None:
    proc = _emit({"version": 2}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == "" and "version" in proc.stderr
    proc = _emit("{not json", tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
