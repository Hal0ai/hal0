"""The answers reader: a document in, allow-listed NAME=value lines out.

The reader runs as root before hal0 is installed, so it is exercised the way
the installer runs it: ``python -I answers_reader.py --origin ... DOC``.
"""

from __future__ import annotations

import json
import os
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
        manifest: Path | None = None, env: dict[str, str] | None = None):
    path = tmp_path / "answers.json"
    path.write_bytes(raw if raw is not None else json.dumps(doc).encode())
    seam = ["--models-manifest", str(manifest)] if manifest else []
    return subprocess.run(
        [sys.executable, "-I", str(READER), "--origin", origin, *seam, "--", str(path)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **env} if env else None,
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


def test_emit_saved_drops_unknown_keys_at_every_level(tmp_path: Path) -> None:
    doc = {
        "version": 1,
        "extra_thing": "hf_SECRETSECRET",
        "memory": {"enabled": True, "note": "hf_SECRETSECRET"},
        "apps": {"pi": {"enabled": True, "x": "hf_SECRETSECRET"}, "mystery": {"a": "hf_SECRETSECRET"}},
        "access": {"admin_key": "create"},
    }
    proc = _emit(doc, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "SECRETSECRET" not in proc.stdout
    assert json.loads(proc.stdout) == {
        "version": 1,
        "memory": {"enabled": True},
        "apps": {"pi": {"enabled": True}},
        "access": {"admin_key": "create"},
    }


def test_emit_saved_known_keys_round_trip(tmp_path: Path) -> None:
    doc = {
        "version": 1,
        "strict": False,
        "model_store": {"path": "/mnt/m"},
        "apps": {"hermes": {"enabled": True, "terminal_tool": True}, "comfyui": {"enabled": False}},
        "network": {"port": 80, "bind_host": "0.0.0.0"},
        "access": {"admin_key": "none"},
        "slots": [{"name": "a", "anything": 1}],
    }
    proc = _emit(doc, tmp_path)
    assert proc.returncode == 0, proc.stderr
    expected = json.loads(json.dumps(doc))
    expected["apps"]["hermes"] = {"enabled": True}
    expected["slots"] = [{"name": "a"}]  # unknown slot-entry keys are dropped
    assert json.loads(proc.stdout) == expected
    assert proc.stderr == ""


# ── model-store locations: never a system tree, never a file ────────────────

_STORE_KEYS = ("path", "flm_path")
_DENIED = ("/etc", "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/boot",
           "/root", "/proc", "/sys", "/dev", "/run", "/var/run", "/var/log", "/var/lib")


@pytest.mark.parametrize("key", _STORE_KEYS)
@pytest.mark.parametrize("tree", _DENIED)
@pytest.mark.parametrize("suffix", ["", "/x", "/x/y"])
def test_store_path_in_a_system_tree_is_refused(tmp_path: Path, key: str, tree: str,
                                                suffix: str) -> None:
    proc = run({"version": 1, "model_store": {key: tree + suffix}}, tmp_path)
    assert proc.returncode == 2, proc.stdout
    assert proc.stdout == ""
    assert f"model_store.{key}" in proc.stderr and "system location" in proc.stderr


@pytest.mark.parametrize("key", _STORE_KEYS)
@pytest.mark.parametrize("bad", ["/", "/mnt", "/home", "//", "/./", "//mnt/", "/mnt/."])
def test_store_path_needs_two_components(tmp_path: Path, key: str, bad: str) -> None:
    proc = run({"version": 1, "model_store": {key: bad}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    assert f"model_store.{key}" in proc.stderr and "too close to the root" in proc.stderr


@pytest.mark.parametrize("bad", ["//etc", "/./etc", "/etc/", "//usr//lib/x", "/var/./lib/x"])
def test_store_path_spelled_oddly_is_still_refused(tmp_path: Path, bad: str) -> None:
    proc = run({"version": 1, "model_store": {"path": bad}}, tmp_path)
    assert proc.returncode == 2 and "system location" in proc.stderr


@pytest.mark.parametrize("key", _STORE_KEYS)
@pytest.mark.parametrize("ok", ["/var/lib/hal0/models", "/var/lib/hal0/models/flm"])
def test_hal0_models_directory_is_allowed_under_var_lib(tmp_path: Path, key: str, ok: str) -> None:
    proc = run({"version": 1, "model_store": {key: ok}}, tmp_path)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("bad", ["/var/lib/hal0", "/var/lib/hal0/other", "/var/lib/hal0/modelsx"])
def test_the_rest_of_var_lib_hal0_is_refused(tmp_path: Path, bad: str) -> None:
    assert run({"version": 1, "model_store": {"path": bad}}, tmp_path).returncode == 2


@pytest.mark.parametrize("key", _STORE_KEYS)
@pytest.mark.parametrize("ok", ["/mnt/disk/models", "/srv/hal0/models", "/home/someone/models",
                                "/mnt/my models/a=b", "/opt/hal0", "/var/data/models"])
def test_ordinary_store_paths_are_accepted(tmp_path: Path, key: str, ok: str) -> None:
    proc = run({"version": 1, "model_store": {key: ok}}, tmp_path)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("key", _STORE_KEYS)
def test_symlink_into_a_system_tree_is_refused(tmp_path: Path, key: str) -> None:
    link = tmp_path / "link"
    link.symlink_to("/etc")
    for value in (str(link), f"{link}/hal0/models"):  # the link itself, and below it
        proc = run({"version": 1, "model_store": {key: value}}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == "", value
        assert f"model_store.{key}" in proc.stderr and "/etc" in proc.stderr, value


def test_symlink_to_an_ordinary_directory_is_accepted(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    assert run({"version": 1, "model_store": {"path": str(link)}}, tmp_path).returncode == 0


@pytest.mark.parametrize("key", _STORE_KEYS)
def test_existing_regular_file_is_refused(tmp_path: Path, key: str) -> None:
    target = tmp_path / "a-file"
    target.write_text("x")
    proc = run({"version": 1, "model_store": {key: str(target)}}, tmp_path)
    assert proc.returncode == 2
    assert f"model_store.{key}" in proc.stderr and "not a directory" in proc.stderr


def test_existing_directory_and_missing_path_are_accepted(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    for value in (str(tmp_path / "d"), str(tmp_path / "not" / "yet")):
        assert run({"version": 1, "model_store": {"path": value}}, tmp_path).returncode == 0


# ── credentials and secrets never reach the (world-readable) install log ────

_SECRET = "hunter2-S3CRET"


@pytest.mark.parametrize(
    "net",
    [
        {"public_url": f"https://admin:{_SECRET}@x.example.com"},
        {"public_url": f"admin:{_SECRET}@x.example.com"},
        {"public_url": f"https://admin:{_SECRET}@x.example.com:99999/"},  # also a bad port
        {"public_url": f"https://admin:{_SECRET}@[bad/"},  # also unparsable
        {"public_url": f"https://x.example.com/p?token={_SECRET}"},
        {"public_url": f"ftp://x.example.com/{_SECRET}"},
        {"public_url": f"https://x.example.com/{_SECRET} z"},
        {"allowed_origins": [f"https://admin:{_SECRET}@x.example.com"]},
        {"allowed_origins": ["https://ok.example.com", f"https://:{_SECRET}@x.example.com"]},
        {"allowed_origins": [f"https://x.example.com/{_SECRET}"]},
    ],
)
def test_url_messages_never_echo_a_secret(tmp_path: Path, net: dict) -> None:
    proc = run({"version": 1, "network": net}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    assert _SECRET not in proc.stderr
    assert _SECRET not in proc.stdout


@pytest.mark.parametrize("key", ["public_url", "allowed_origins"])
def test_credentials_message_names_the_key_only(tmp_path: Path, key: str) -> None:
    url = f"https://admin:{_SECRET}@x.example.com"
    value = url if key == "public_url" else [url]
    proc = run({"version": 1, "network": {key: value}}, tmp_path)
    assert proc.returncode == 2
    where = "network.public_url" if key == "public_url" else "network.allowed_origins[0]"
    assert f"error: {where}: must not contain a user name or password" in proc.stderr
    assert "x.example.com" not in proc.stderr and "admin" not in proc.stderr


def test_other_url_messages_echo_scheme_and_host_only(tmp_path: Path) -> None:
    proc = run({"version": 1, "network": {"public_url": "ftp://x.example.com:21/a/b?c=d"}}, tmp_path)
    assert "ftp://x.example.com:21" in proc.stderr
    assert "/a/b" not in proc.stderr and "c=d" not in proc.stderr


# ── a hostile shape cannot crash the reader ─────────────────────────────────


@pytest.mark.parametrize("depth", [5_000, 100_000])
@pytest.mark.parametrize("shape", ["list", "dict"])
def test_deep_nesting_exits_2_with_a_message(tmp_path: Path, depth: int, shape: str) -> None:
    if shape == "list":
        raw = b'{"version": 1, "x": ' + b"[" * depth + b"]" * depth + b"}"
    else:
        raw = b'{"version": 1, "x": ' + b'{"a": ' * depth + b"1" + b"}" * depth + b"}"
    proc = run(None, tmp_path, raw=raw)
    assert proc.returncode == 2 and proc.stdout == ""
    assert "nested too deeply" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_deep_nesting_in_a_known_section_exits_2(tmp_path: Path) -> None:
    raw = b'{"version": 1, "slots": ' + b"[" * 5000 + b"]" * 5000 + b"}"
    proc = run(None, tmp_path, raw=raw)
    assert proc.returncode == 2 and "Traceback" not in proc.stderr


def test_deep_nesting_in_the_saved_form_run_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "deep.json"
    path.write_bytes(b'{"version": 1, "x": ' + b"[" * 5000 + b"]" * 5000 + b"}")
    proc = subprocess.run([sys.executable, "-I", str(READER), "--emit-saved", str(path)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 2 and "Traceback" not in proc.stderr


# ── a pipe where a file should be returns promptly ──────────────────────────


def test_fifo_document_does_not_hang(tmp_path: Path) -> None:
    import os
    import time

    fifo = tmp_path / "doc.fifo"
    os.mkfifo(fifo)
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-I", str(READER), "--origin", "file", "--", str(fifo)],
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert time.monotonic() - start < 2
    assert proc.returncode == 2 and "not a regular file" in proc.stderr


def test_the_document_is_read_through_one_nonblocking_descriptor() -> None:
    text = READER.read_text(encoding="utf-8")
    assert "os.O_NONBLOCK" in text and "os.fstat(fd)" in text
    assert text.count("_read_capped(path)") == 2  # the document and the model list
    # The one os.stat is _identity, which looks at the denied trees and the
    # store path's ancestors, never at the document or the model list.
    without_identity = text.replace("info = os.stat(path)", "")
    assert "with open(" not in without_identity and "os.stat(" not in without_identity


# ── network.bind_host: checked in every mode, kept when valid ───────────────


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("good", ["0.0.0.0", "127.0.0.1", "::1", "2001:db8::1", "box-1",
                                  "box.example.com", " 192.0.2.5 "])
def test_valid_bind_host_is_kept_and_warns_once(tmp_path: Path, strict: bool, good: str) -> None:
    proc = run({"version": 1, "strict": strict, "network": {"bind_host": good}}, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]
    assert proc.stderr.count("network.bind_host is not applied at install") == 1


@pytest.mark.parametrize("bad", ["-bad", "--help", "a b", "a;b", "$(x)", "x\ny", "x\x1b[31m", "",
                                 "   ", "-1.2.3.4", "999.1.1.1", "bad_host", 5, True, [], {}, ["1.1.1.1"],
                                 {"token": "secret"}, "a" * 300])
@pytest.mark.parametrize("strict", [False, True])
def test_invalid_bind_host_is_an_error(tmp_path: Path, strict: bool, bad: object) -> None:
    proc = run({"version": 1, "strict": strict, "network": {"bind_host": bad}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    assert "network.bind_host" in proc.stderr
    assert "\x1b" not in proc.stderr  # escaped, never raw
    assert "secret" not in proc.stderr


def test_bind_host_survives_into_the_saved_copy(tmp_path: Path) -> None:
    proc = _emit({"version": 1, "network": {"bind_host": "192.0.2.1"}}, tmp_path)
    assert json.loads(proc.stdout)["network"] == {"bind_host": "192.0.2.1"}
    proc = _emit({"version": 1, "network": {"bind_host": {"x": "tok"}}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""


# ── slots: only what the document format defines, flat, validated ───────────

_SLOT = {"capability": "coder", "name": "code-1", "port": 8201, "model_id": "qwen3-8b",
         "device": "auto", "profile": "auto", "context_size": 8192, "enabled_on_pull": True}


def test_a_full_slot_entry_is_accepted_and_kept(tmp_path: Path) -> None:
    doc = {"version": 1, "strict": True, "slots": [_SLOT, {"capability": "chat", "name": "chat",
                                                          "port": 8200, "model_id": "auto"}]}
    proc = run(doc, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]  # slots emit no variable
    saved = _emit(doc, tmp_path)
    assert json.loads(saved.stdout)["slots"] == doc["slots"]


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("capability", "embed"), ("capability", 3), ("capability", None), ("capability", ["chat"]),
        ("name", "Has Space"), ("name", "UPPER"), ("name", "x" * 40), ("name", ""), ("name", 5),
        ("name", "a\nb"), ("name", {"k": "v"}),
        ("port", 0), ("port", 65536), ("port", True), ("port", "8080"), ("port", 1.5), ("port", [1]),
        ("model_id", 5), ("model_id", "bad id"), ("model_id", "a\x00b"), ("model_id", {"x": 1}),
        ("model_id", ["a"]), ("model_id", "-x"),
        ("device", "a b"), ("device", 3), ("device", ["gpu"]), ("device", {"a": 1}),
        ("profile", "a b"), ("profile", 3), ("profile", {"a": 1}),
        ("context_size", 0), ("context_size", -1), ("context_size", True), ("context_size", "big"),
        ("context_size", [1]),
        ("enabled_on_pull", "yes"), ("enabled_on_pull", 1), ("enabled_on_pull", None),
    ],
)
def test_invalid_slot_values_are_errors(tmp_path: Path, key: str, bad: object) -> None:
    entry = {**_SLOT, key: bad}
    proc = run({"version": 1, "slots": [entry]}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    assert f"slots[0].{key}" in proc.stderr


@pytest.mark.parametrize("entry", ["x", 5, None, ["a"], True])
def test_a_slot_entry_must_be_a_mapping(tmp_path: Path, entry: object) -> None:
    proc = run({"version": 1, "slots": [entry]}, tmp_path)
    assert proc.returncode == 2 and "slots[0]: must be a mapping" in proc.stderr


def test_nesting_inside_a_slot_entry_is_refused(tmp_path: Path) -> None:
    doc = {"version": 1, "slots": [{"name": "a", "extra": {"token": "hf_nested_secret"}}]}
    for strict in (False, True):
        proc = run({**doc, "strict": strict}, tmp_path)
        assert "hf_nested_secret" not in proc.stderr
    saved = _emit(doc, tmp_path)
    assert "hf_nested_secret" not in saved.stdout


def test_duplicate_slot_ports_are_an_error(tmp_path: Path) -> None:
    proc = run({"version": 1, "slots": [{"name": "a", "port": 9000}, {"name": "b", "port": 9000}]}, tmp_path)
    assert proc.returncode == 2 and "slots[1].port" in proc.stderr and "9000" in proc.stderr


def test_unknown_slot_key_follows_strict(tmp_path: Path) -> None:
    doc = {"version": 1, "slots": [{"name": "a", "mystery": "hf_token_like"}]}
    loose = run(doc, tmp_path)
    assert lines(loose) == ["HAL0_NONINTERACTIVE=1"]
    assert "slots[0].mystery" in loose.stderr
    strict = run({**doc, "strict": True}, tmp_path)
    assert strict.returncode == 2 and "slots[0].mystery" in strict.stderr


def test_unknown_slot_keys_are_dropped_from_the_saved_copy(tmp_path: Path) -> None:
    doc = {"version": 1, "slots": [{"name": "a", "port": 9000, "token": "hf_secret_slot"}]}
    proc = _emit(doc, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "hf_secret_slot" not in proc.stdout
    assert json.loads(proc.stdout)["slots"] == [{"name": "a", "port": 9000}]


def test_slots_still_emit_no_variable(tmp_path: Path) -> None:
    proc = run({"version": 1, "slots": [_SLOT]}, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]


@pytest.mark.parametrize("key", ["capability", "name", "model_id", "device", "profile"])
def test_slot_messages_never_echo_a_nested_value(tmp_path: Path, key: str) -> None:
    proc = run({"version": 1, "slots": [{key: {"token": "hf_nested_secret"}}]}, tmp_path)
    assert proc.returncode == 2 and f"slots[0].{key}" in proc.stderr
    assert "hf_nested_secret" not in proc.stderr


# ── model_store.flm_path: the installer must be able to create it safely ────
#
# Mirrors the installer's sink rule so a person learns before the install starts.
# Tests cannot make root-owned directories: HAL0_ANSWERS_TEST_ROOT_UID names the
# uid that counts as root (default 0; the reader reads it from the environment,
# never from a document).

_ME = {"HAL0_ANSWERS_TEST_ROOT_UID": str(os.getuid())}


def _flm(tmp_path: Path, value: str, env: dict[str, str] | None = None):
    return run({"version": 1, "model_store": {"flm_path": value}}, tmp_path, env=env)


def test_flm_path_in_another_persons_directory_is_refused(tmp_path: Path) -> None:
    home = tmp_path / "home" / "alice"
    (home / ".config").mkdir(parents=True)
    for rel in (".config/systemd/user", ".local/bin", ".config/autostart"):
        value = str(home / rel)
        proc = _flm(tmp_path, value, env={"HAL0_ANSWERS_TEST_ROOT_UID": "0"})
        assert proc.returncode == 2 and proc.stdout == "", value
        assert "model_store.flm_path" in proc.stderr and "owned by root" in proc.stderr


def test_flm_path_reason_never_echoes_a_resolved_path(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    proc = _flm(tmp_path, str(tmp_path / "link" / "flm"), env=_ME)
    assert proc.returncode == 2 and "a link" in proc.stderr
    assert str(tmp_path / "real") not in proc.stderr


def test_flm_path_under_a_group_or_sticky_ancestor_is_refused(tmp_path: Path) -> None:
    for mode in (0o775, 0o1777, 0o757):
        parent = tmp_path / f"p{mode:o}"
        parent.mkdir()
        parent.chmod(mode)
        proc = _flm(tmp_path, str(parent / "flm"), env=_ME)
        assert proc.returncode == 2, oct(mode)
        assert "model_store.flm_path" in proc.stderr and "writable" in proc.stderr


def test_flm_path_under_a_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "afile").write_text("x")
    proc = _flm(tmp_path, str(tmp_path / "afile" / "flm"), env=_ME)
    assert proc.returncode == 2 and "model_store.flm_path" in proc.stderr


def test_flm_path_under_a_root_owned_ancestor_is_accepted(tmp_path: Path) -> None:
    proc = _flm(tmp_path, str(tmp_path / "a" / "b" / "flm"), env=_ME)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[-1].startswith("HAL0_FLM_MODELS_DIR=")


def test_flm_path_in_the_default_store_is_accepted_without_the_ancestor_rule(tmp_path: Path) -> None:
    proc = _flm(tmp_path, "/var/lib/hal0/models/flm", env={"HAL0_ANSWERS_TEST_ROOT_UID": "4242"})
    assert proc.returncode == 0, proc.stderr


def test_an_existing_flm_directory_is_accepted_whoever_owns_it(tmp_path: Path) -> None:
    (tmp_path / "mine").mkdir()
    proc = _flm(tmp_path, str(tmp_path / "mine"), env={"HAL0_ANSWERS_TEST_ROOT_UID": "0"})
    assert proc.returncode == 0, proc.stderr


def test_the_model_store_path_is_not_subject_to_the_ancestor_rule(tmp_path: Path) -> None:
    value = str(tmp_path / "x" / "store")
    proc = run({"version": 1, "model_store": {"path": value}}, tmp_path,
               env={"HAL0_ANSWERS_TEST_ROOT_UID": "0"})
    assert proc.returncode == 0, proc.stderr


def test_a_nonsense_seam_value_means_root_is_uid_0(tmp_path: Path) -> None:
    proc = _flm(tmp_path, str(tmp_path / "flm"), env={"HAL0_ANSWERS_TEST_ROOT_UID": "abc"})
    assert proc.returncode == 2 if os.getuid() != 0 else proc.returncode == 0


# ── a second name for a system tree: compared by device and inode ───────────
#
# os.path.realpath cannot see a bind mount or a second mount of one filesystem.
# The reader therefore also compares (st_dev, st_ino) of every existing ancestor
# of the path with each denied tree that exists on the box. A real second name
# needs a mount (root only; a hard link to a directory is impossible), so the
# test reaches the reader in-process: the real stat is exercised on real
# directories, and a fake identity function stands in for the second name.


def _reader_module():
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("answers_reader_under_test", READER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_ancestor_that_is_a_denied_tree_by_inode_is_found(tmp_path: Path) -> None:
    reader = _reader_module()
    (tmp_path / "denied" / "inner").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    trees = (str(tmp_path / "denied"), str(tmp_path / "absent"))
    assert reader._denied_by_identity(str(tmp_path / "denied" / "inner" / "x" / "y"), trees)
    assert reader._denied_by_identity(str(tmp_path / "denied"), trees)
    assert not reader._denied_by_identity(str(tmp_path / "other" / "x"), trees)
    assert not reader._denied_by_identity(str(tmp_path / "denied-not" / "x"), trees)


def test_a_second_name_for_a_denied_tree_is_refused(tmp_path: Path, monkeypatch) -> None:
    reader = _reader_module()
    seen = {"/fake/denied": (7, 99), "/mnt/alias": (7, 99), "/mnt": (7, 1), "/": (7, 0)}
    monkeypatch.setattr(reader, "DENIED_TREES", ("/fake/denied",))
    monkeypatch.setattr(reader, "_identity", lambda p: seen.get(p))
    problems = reader._Problems()
    assert not reader._safe_location("/mnt/alias/models", "model_store.path", problems)
    assert len(problems.errors) == 1
    assert "model_store.path" in problems.errors[0] and "second name" in problems.errors[0]
    assert "/fake/denied" in problems.errors[0]
    ok = reader._Problems()
    assert reader._safe_location("/mnt/else/models", "model_store.path", ok)
    assert ok.errors == []


def test_the_default_store_is_not_caught_by_the_inode_check(tmp_path: Path) -> None:
    # /var/lib is a denied tree and an ancestor of the default store.
    reader = _reader_module()
    problems = reader._Problems()
    assert reader._safe_location("/var/lib/hal0/models/flm", "model_store.path", problems)
    assert problems.errors == []


@pytest.mark.parametrize("key", _STORE_KEYS)
def test_a_real_system_tree_is_still_refused_by_name_first(tmp_path: Path, key: str) -> None:
    proc = run({"version": 1, "model_store": {key: "/etc/x"}}, tmp_path)
    assert proc.returncode == 2 and "system location" in proc.stderr
