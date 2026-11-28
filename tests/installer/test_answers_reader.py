"""The answers reader: a document in, allow-listed NAME=value lines out.

The reader runs as root before hal0 is installed, so it is exercised the way
the installer runs it: ``python -I answers_reader.py --origin ... DOC``.
"""

from __future__ import annotations

import base64
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
    "model_store": {"path": "/mnt/my models/a=b", "flm_path": "/mnt/my models/a=b/flm"},
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
    "access": {"admin_key": "generate"},
    "maintenance": {"weekly_benchmark": True},
    "npu": {"opt_in": False},
}


def run(
    doc: object,
    tmp_path: Path,
    origin: str = "wizard",
    raw: bytes | None = None,
    manifest: Path | None = None,
    env: dict[str, str] | None = None,
):
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
    """The NAME=value lines of a successful run (its last line, `#saved ...`, checked apart)."""
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.splitlines()
    assert out and out[-1].startswith("#saved "), proc.stdout
    assert [x for x in out if x.startswith("#")] == [out[-1]], proc.stdout
    return out[:-1]


def saved_of(proc: subprocess.CompletedProcess[str]) -> dict:
    """The saved form carried by the apply run's `#saved <base64>` line."""
    assert proc.returncode == 0, proc.stderr
    last = proc.stdout.splitlines()[-1]
    assert last.startswith("#saved ")
    return json.loads(base64.b64decode(last[len("#saved ") :], validate=True).decode("utf-8"))


def test_full_document_maps_every_key(tmp_path: Path) -> None:
    proc = run(FULL, tmp_path, manifest=_manifest(tmp_path))
    assert lines(proc) == [
        "HAL0_NONINTERACTIVE=1",
        "HAL0_MODELS_DIR=/mnt/my models/a=b",
        "HAL0_FLM_MODELS_DIR=/mnt/my models/a=b/flm",
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
        "HAL0_ADMIN_KEY_MODE=generate",
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
    doc = {
        "version": 1,
        "strict": True,
        "slots": [{"capability": "chat", "name": "c", "port": 8200}],
        "huggingface": {"token_env": "HF_TOKEN"},
    }
    proc = run(doc, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]
    assert "huggingface" not in proc.stderr  # token_env alone is accepted silently
    proc = run({**doc, "huggingface": {"token_env": "HF_TOKEN", "token": "hf_x"}}, tmp_path)
    assert proc.returncode == 0 and proc.stderr.count("huggingface settings are not read") == 1
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
    [
        None,
        "missing",
        {"models": []},
        {"model_ladder": [{"role": "starter"}]},
        {"model_ladder": [{"model": "x"}]},
        {"model_ladder": "x"},
    ],
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
    assert lines(proc) == [
        "HAL0_NONINTERACTIVE=1",
        "HAL0_BRAIN_MODEL=other-id",
        "HAL0_PULL_AGENT_MODEL=1",
        "HAL0_AGENT_MODEL=any-id",
    ]
    assert proc.stderr.count("model list") == 1
    assert (
        run({"version": 1, "models": {"starter": "Bad Id"}}, tmp_path, manifest=m).returncode == 2
    )


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
        capture_output=True,
        text=True,
        check=False,
        cwd=elsewhere,
    )
    assert proc.returncode == 0 and proc.stderr == "", proc.stderr
    doc.write_text(json.dumps({"version": 1, "models": {"brain": "nope"}}), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-I", str(lib / "answers_reader.py"), "--origin", "file", str(doc)],
        capture_output=True,
        text=True,
        check=False,
        cwd=elsewhere,
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
    {
        "version": 1,
        "network": {
            "hostname": HOSTILE_KEY,
            "public_url": HOSTILE_KEY,
            "allowed_origins": [HOSTILE_KEY],
        },
    },
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
        capture_output=True,
        text=True,
        check=False,
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
    proc = run({"version": 1, "models": {"starter": value}}, tmp_path, manifest=_manifest(tmp_path))
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
    [
        (1, True),
        (65535, True),
        (0, False),
        (65536, False),
        (-1, False),
        ("80", False),
        (True, False),
        (80.5, False),
    ],
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
    for word in ("generate", "none"):
        proc = run({"version": 1, "access": {"admin_key": word}}, tmp_path)
        assert f"HAL0_ADMIN_KEY_MODE={word}" in lines(proc)
    for bad in ("sk-abcdef0123456789", "", "Generate", "create", 1, True, None, ["generate"]):
        proc = run({"version": 1, "access": {"admin_key": bad}}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == "", bad
    proc = run({"version": 1, "access": {"admin_key": "generate", "key": "hal0-secret"}}, tmp_path)
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
        proc = subprocess.run(
            [sys.executable, "-I", str(READER), *argv], capture_output=True, text=True, check=False
        )
        assert proc.returncode == 2 and proc.stdout == ""
    proc = subprocess.run(
        [sys.executable, "-I", str(READER), "--origin", "file", str(tmp_path / "nope.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2 and proc.stdout == ""


def test_only_allow_listed_names_are_printed(tmp_path: Path) -> None:
    names = {ln.split("=", 1)[0] for ln in lines(run(FULL, tmp_path, manifest=_manifest(tmp_path)))}
    src = READER.read_text(encoding="utf-8")
    ns: dict = {}
    exec(compile(src.split("MAX_BYTES")[0], str(READER), "exec"), ns)  # noqa: S102
    assert names <= set(ns["ALLOWED_NAMES"])


def _emit(
    doc: object, tmp_path: Path, *extra: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "doc.json"
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", str(READER), *extra, "--emit-saved", str(path)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **env} if env else None,
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
    assert (
        proc.stdout
        == json.dumps(
            {
                "version": 1,
                "apps": {"hermes": {}, "pi": {"enabled": True}},
                "network": {"port": 80},
            },
            indent=2,
        )
        + "\n"
    )


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
        "apps": {
            "pi": {"enabled": True, "x": "hf_SECRETSECRET"},
            "mystery": {"a": "hf_SECRETSECRET"},
        },
        "access": {"admin_key": "generate"},
    }
    proc = _emit(doc, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "SECRETSECRET" not in proc.stdout
    assert json.loads(proc.stdout) == {
        "version": 1,
        "memory": {"enabled": True},
        "apps": {"pi": {"enabled": True}},
        "access": {"admin_key": "generate"},
    }


def test_emit_saved_known_keys_round_trip(tmp_path: Path) -> None:
    doc = {
        "version": 1,
        "strict": False,
        "model_store": {"path": "/mnt/m"},
        "apps": {"hermes": {"enabled": True, "terminal_tool": True}, "comfyui": {"enabled": False}},
        "network": {"port": 80, "bind_host": "0.0.0.0"},
        "access": {"admin_key": "none"},
        "slots": [{"capability": "chat", "name": "a", "port": 9000, "anything": 1}],
    }
    proc = _emit(doc, tmp_path)
    assert proc.returncode == 0, proc.stderr
    expected = json.loads(json.dumps(doc))
    expected["apps"]["hermes"] = {"enabled": True}
    expected["slots"] = [
        {"capability": "chat", "name": "a", "port": 9000}
    ]  # unknown slot-entry keys are dropped
    assert json.loads(proc.stdout) == expected
    assert proc.stderr == ""


# ── model-store locations: never a system tree, never a file ────────────────

_STORE_KEYS = ("path", "flm_path")
_DENIED = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",
    "/boot",
    "/root",
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/var/run",
    "/var/log",
    "/var/lib",
)


_OUTSIDE_STORE = "must be a folder inside the model store"


@pytest.mark.parametrize("tree", _DENIED)
@pytest.mark.parametrize("suffix", ["", "/x", "/x/y"])
def test_store_path_in_a_system_tree_is_refused(tmp_path: Path, tree: str, suffix: str) -> None:
    proc = run({"version": 1, "model_store": {"path": tree + suffix}}, tmp_path)
    assert proc.returncode == 2, proc.stdout
    assert proc.stdout == ""
    assert (
        "model_store.path" in proc.stderr and f"is inside {tree}, a system location" in proc.stderr
    )


@pytest.mark.parametrize("tree", _DENIED)
@pytest.mark.parametrize("suffix", ["", "/x", "/x/y"])
def test_flm_path_in_a_system_tree_is_outside_the_store(
    tmp_path: Path, tree: str, suffix: str
) -> None:
    proc = run({"version": 1, "model_store": {"flm_path": tree + suffix}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    assert "model_store.flm_path" in proc.stderr and _OUTSIDE_STORE in proc.stderr


@pytest.mark.parametrize("bad", ["/", "/mnt", "/home", "//", "/./", "//mnt/", "/mnt/."])
def test_store_path_needs_two_components(tmp_path: Path, bad: str) -> None:
    proc = run({"version": 1, "model_store": {"path": bad}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    assert "model_store.path" in proc.stderr and "too close to the root" in proc.stderr


@pytest.mark.parametrize("bad", ["//etc", "/./etc", "/etc/", "//usr//lib/x", "/var/./lib/x"])
def test_store_path_spelled_oddly_is_still_refused(tmp_path: Path, bad: str) -> None:
    proc = run({"version": 1, "model_store": {"path": bad}}, tmp_path)
    assert proc.returncode == 2 and "system location" in proc.stderr


@pytest.mark.parametrize("ok", ["/var/lib/hal0/models", "/var/lib/hal0/models/flm"])
def test_hal0_models_directory_is_allowed_under_var_lib(tmp_path: Path, ok: str) -> None:
    proc = run({"version": 1, "model_store": {"path": ok}}, tmp_path)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("bad", ["/var/lib/hal0", "/var/lib/hal0/other", "/var/lib/hal0/modelsx"])
def test_the_rest_of_var_lib_hal0_is_refused(tmp_path: Path, bad: str) -> None:
    assert run({"version": 1, "model_store": {"path": bad}}, tmp_path).returncode == 2


@pytest.mark.parametrize(
    "ok",
    [
        "/mnt/disk/models",
        "/srv/hal0/models",
        "/home/someone/models",
        "/mnt/my models/a=b",
        "/opt/hal0",
        "/var/data/models",
    ],
)
def test_ordinary_store_paths_are_accepted(tmp_path: Path, ok: str) -> None:
    proc = run({"version": 1, "model_store": {"path": ok, "flm_path": f"{ok}/flm"}}, tmp_path)
    assert lines(proc) == [
        "HAL0_NONINTERACTIVE=1",
        f"HAL0_MODELS_DIR={ok}",
        f"HAL0_FLM_MODELS_DIR={ok}/flm",
    ]


def test_what_is_on_disk_at_the_path_does_not_matter_to_the_reader(tmp_path: Path) -> None:
    # A link into /etc, a regular file, a dangling link: the reader judges the
    # text only, and passes the document's own text on; the installer's sink
    # decides what may be created or used there.
    (tmp_path / "etc-link").symlink_to("/etc")
    (tmp_path / "a-file").write_text("x")
    (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
    for value in (
        f"{tmp_path}/etc-link",
        f"{tmp_path}/etc-link/hal0/models",
        f"{tmp_path}/a-file",
        f"{tmp_path}/dangling/models",
    ):
        proc = run({"version": 1, "model_store": {"path": value}}, tmp_path)
        assert lines(proc) == ["HAL0_NONINTERACTIVE=1", f"HAL0_MODELS_DIR={value}"], value
        assert proc.stderr == ""
        assert saved_of(proc)["model_store"] == {"path": value}


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
    proc = run(
        {"version": 1, "network": {"public_url": "ftp://x.example.com:21/a/b?c=d"}}, tmp_path
    )
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
    proc = subprocess.run(
        [sys.executable, "-I", str(READER), "--emit-saved", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
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
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert time.monotonic() - start < 2
    assert proc.returncode == 2 and "not a regular file" in proc.stderr


def test_the_document_is_read_through_one_nonblocking_descriptor() -> None:
    text = READER.read_text(encoding="utf-8")
    assert "os.O_NONBLOCK" in text and "os.fstat(fd)" in text
    assert text.count("_read_capped(path)") == 2  # the document and the model list
    assert "with open(" not in text and "os.stat(" not in text


# ── network.bind_host: checked in every mode, kept when valid ───────────────


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "good",
    [
        "0.0.0.0",
        "127.0.0.1",
        "::1",
        "2001:db8::1",
        "box-1",
        "box.example.com",
        "fe80::1%eth0",
        "fe80::1%a_b.c-9",
    ],
)
def test_valid_bind_host_is_kept_and_warns_once(tmp_path: Path, strict: bool, good: str) -> None:
    proc = run({"version": 1, "strict": strict, "network": {"bind_host": good}}, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]
    assert proc.stderr.count("network.bind_host is not applied at install") == 1


@pytest.mark.parametrize(
    "bad",
    [
        "-bad",
        "--help",
        "a b",
        "a;b",
        "$(x)",
        "x\ny",
        "x\x1b[31m",
        "",
        "   ",
        "-1.2.3.4",
        "999.1.1.1",
        "bad_host",
        5,
        True,
        [],
        {},
        ["1.1.1.1"],
        {"token": "secret"},
        "a" * 300,
        " 192.0.2.5 ",
        "192.0.2.5 ",
        " box-1",
        "192.0.2.5\u00a0",
        "\u2003box-1",
        "box-1\u3000",
        "\u200bbox-1",
        "fe80::1%$(id)",
        "fe80::1%",
        "fe80::1%" + "a" * 17,
        "fe80::1%a b",
        "fe80::1%eth0%x",
        "fe80::1%a;b",
        "fe80::1%a/b",
        "fe80::1%`id`",
        "fe80::1%\u00e9th0",
    ],
)
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

_SLOT = {
    "capability": "coder",
    "name": "code-1",
    "port": 8201,
    "model_id": "qwen3-8b",
    "device": "auto",
    "profile": "auto",
    "context_size": 8192,
    "enabled_on_pull": True,
}


_REQUIRED = {"capability": "chat", "name": "a", "port": 9000}


def test_a_full_slot_entry_is_accepted_and_kept(tmp_path: Path) -> None:
    doc = {
        "version": 1,
        "strict": True,
        "slots": [_SLOT, {"capability": "chat", "name": "chat", "port": 8200, "model_id": "auto"}],
    }
    proc = run(doc, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]  # slots emit no variable
    saved = _emit(doc, tmp_path)
    assert json.loads(saved.stdout)["slots"] == doc["slots"]


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("capability", "embed"),
        ("capability", 3),
        ("capability", None),
        ("capability", ["chat"]),
        ("name", "Has Space"),
        ("name", "UPPER"),
        ("name", "x" * 40),
        ("name", ""),
        ("name", 5),
        ("name", "a\nb"),
        ("name", {"k": "v"}),
        ("port", 0),
        ("port", 65536),
        ("port", True),
        ("port", "8080"),
        ("port", 1.5),
        ("port", [1]),
        ("model_id", 5),
        ("model_id", "bad id"),
        ("model_id", "a\x00b"),
        ("model_id", {"x": 1}),
        ("model_id", ["a"]),
        ("model_id", "-x"),
        ("device", "a b"),
        ("device", 3),
        ("device", ["gpu"]),
        ("device", {"a": 1}),
        ("profile", "a b"),
        ("profile", 3),
        ("profile", {"a": 1}),
        ("context_size", 0),
        ("context_size", -1),
        ("context_size", True),
        ("context_size", "big"),
        ("context_size", [1]),
        ("enabled_on_pull", "yes"),
        ("enabled_on_pull", 1),
        ("enabled_on_pull", None),
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
    doc = {"version": 1, "slots": [{**_REQUIRED, "extra": {"token": "hf_nested_secret"}}]}
    for strict in (False, True):
        proc = run({**doc, "strict": strict}, tmp_path)
        assert "hf_nested_secret" not in proc.stderr
    saved = _emit(doc, tmp_path)
    assert "hf_nested_secret" not in saved.stdout


def test_duplicate_slot_ports_are_an_error(tmp_path: Path) -> None:
    proc = run({"version": 1, "slots": [_REQUIRED, {**_REQUIRED, "name": "b"}]}, tmp_path)
    assert proc.returncode == 2 and "slots[1].port" in proc.stderr and "9000" in proc.stderr


def test_unknown_slot_key_follows_strict(tmp_path: Path) -> None:
    doc = {"version": 1, "slots": [{**_REQUIRED, "mystery": "hf_token_like"}]}
    loose = run(doc, tmp_path)
    assert lines(loose) == ["HAL0_NONINTERACTIVE=1"]
    assert "slots[0].mystery" in loose.stderr
    strict = run({**doc, "strict": True}, tmp_path)
    assert strict.returncode == 2 and "slots[0].mystery" in strict.stderr


def test_unknown_slot_keys_are_dropped_from_the_saved_copy(tmp_path: Path) -> None:
    doc = {"version": 1, "slots": [{**_REQUIRED, "token": "hf_secret_slot"}]}
    proc = _emit(doc, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "hf_secret_slot" not in proc.stdout
    assert json.loads(proc.stdout)["slots"] == [_REQUIRED]


def test_slots_still_emit_no_variable(tmp_path: Path) -> None:
    proc = run({"version": 1, "slots": [_SLOT]}, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]


@pytest.mark.parametrize("key", ["capability", "name", "model_id", "device", "profile"])
def test_slot_messages_never_echo_a_nested_value(tmp_path: Path, key: str) -> None:
    proc = run({"version": 1, "slots": [{key: {"token": "hf_nested_secret"}}]}, tmp_path)
    assert proc.returncode == 2 and f"slots[0].{key}" in proc.stderr
    assert "hf_nested_secret" not in proc.stderr


def _reader_module():
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("answers_reader_under_test", READER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── a credential anywhere in a URL: rejected, nothing of it echoed ──────────

_PASSWORDS = [
    "Sup3r/Secret",
    "Sup3r?Secret",
    "Sup3r#Secret",
    "Sup3r@Secret",
    "Sup3r Secret",
    "Sup3rSecret/xyz",
]


@pytest.mark.parametrize("pw", _PASSWORDS)
@pytest.mark.parametrize("key", ["public_url", "allowed_origins"])
@pytest.mark.parametrize(
    "shape",
    [
        "{scheme}://admin:{pw}@host.example",
        "{scheme}://admin:{pw}/x@host.example",
        "{scheme}://host.example/{pw}@x",
        "admin:{pw}@host.example",
    ],
)
def test_a_url_with_an_at_sign_anywhere_echoes_nothing_of_it(
    tmp_path: Path, pw: str, key: str, shape: str
) -> None:
    url = shape.format(scheme="https", pw=pw)
    value = url if key == "public_url" else [url]
    proc = run({"version": 1, "network": {key: value}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    assert "Sup3r" not in proc.stderr and "host.example" not in proc.stderr
    where = "network.public_url" if key == "public_url" else "network.allowed_origins[0]"
    assert f"error: {where}: must not contain a user name or password" in proc.stderr


def test_the_motivating_url_is_rejected_without_its_password(tmp_path: Path) -> None:
    proc = run(
        {"version": 1, "network": {"public_url": "https://admin:Sup3rSecret/xyz@host.example"}},
        tmp_path,
    )
    assert proc.returncode == 2 and "Sup3rSecret" not in proc.stderr


# ── a scope id on an IPv6 address is bounded text, and so is a URL host ─────


def test_a_scoped_address_is_not_a_url_host(tmp_path: Path) -> None:
    for url in ("https://[fe80::1%eth0]/", "https://[fe80::1%25eth0]/", "https://[fe80::1%$(id)]"):
        proc = run({"version": 1, "network": {"public_url": url}}, tmp_path)
        assert proc.returncode == 2, url
        assert "$(id)" not in proc.stdout


# ── slots: the loader's required keys, and no path-like model id ────────────


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"capability": "chat"},
        {"name": "a", "port": 9000},
        {"capability": "chat", "port": 9000},
        {"capability": "chat", "name": "a"},
    ],
)
def test_a_slot_entry_needs_capability_name_and_port(tmp_path: Path, entry: dict) -> None:
    proc = run({"version": 1, "slots": [entry]}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    for key in ("capability", "name", "port"):
        if key not in entry:
            assert f"slots[0].{key}: is required" in proc.stderr


@pytest.mark.parametrize("bad", ["../x", "a/b", "a..b", "..", "/abs", "org/model", "x/../y"])
def test_a_slot_model_id_has_no_dotdot_or_slash(tmp_path: Path, bad: str) -> None:
    proc = run({"version": 1, "slots": [{**_REQUIRED, "model_id": bad}]}, tmp_path)
    assert proc.returncode == 2 and "slots[0].model_id" in proc.stderr


@pytest.mark.parametrize("good", ["auto", "qwen3-8b", "llama-3.2-3b", "q4_k_m", "name:tag", "a+b"])
def test_a_plain_slot_model_id_is_accepted(tmp_path: Path, good: str) -> None:
    assert run({"version": 1, "slots": [{**_REQUIRED, "model_id": good}]}, tmp_path).returncode == 0


# ── a value of unknown type is never echoed in full ─────────────────────────

_MARK = "SECRETMARK" + "x" * 190


@pytest.mark.parametrize("bad", [{"token": "hf_nested_secret"}, ["hf_nested_secret"]])
def test_version_and_gen_mode_never_echo_a_container(tmp_path: Path, bad: object) -> None:
    for doc in ({"version": bad}, {"version": 1, "gen": {"mode": bad}}):
        proc = run(doc, tmp_path)
        assert proc.returncode == 2
        assert "hf_nested_secret" not in proc.stderr and "token" not in proc.stderr


def _every_key() -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = [(key,) for key in sorted(_reader_module()._TOP_KEYS)]
    reader = _reader_module()
    for section, keys in reader._SECTION_KEYS.items():
        paths += [(section, key) for key in sorted(keys)]
    paths += [("apps", app) for app in reader._APP_KEYS]
    paths += [("apps", app, key) for app, keys in reader._APP_KEYS.items() for key in sorted(keys)]
    paths += [("access", "admin_key"), ("access", "other"), ("strict",)]
    paths += [("slots", 0, key) for key in sorted(reader._SLOT_KEYS)]  # type: ignore[misc]
    paths += [("slots", 0), ("gen", "models", 0), ("network", "allowed_origins", 0)]  # type: ignore[misc]
    return paths


def _doc_with(path: tuple, value: object) -> dict:
    doc: dict = {"version": 1}
    node: object = doc
    for step, nxt in zip(path, [*path[1:], None]):
        container: object = [] if isinstance(nxt, int) else {}
        if isinstance(node, dict):
            if nxt is None:
                node[step] = value
            else:
                node = node.setdefault(step, container)
        else:
            assert isinstance(node, list)
            if nxt is None:
                node.append(value)
            else:
                node.append(container)
                node = node[-1]
    return doc


@pytest.mark.parametrize("path", _every_key(), ids=lambda p: ".".join(map(str, p)))
@pytest.mark.parametrize("strict", [False, True])
def test_a_nested_value_in_any_key_is_never_echoed(
    tmp_path: Path, path: tuple, strict: bool
) -> None:
    doc = _doc_with(path, {"k": {"deeper": _MARK}, "m": [_MARK]})
    doc["strict"] = strict if path != ("strict",) else doc["strict"]
    proc = run(doc, tmp_path, manifest=_manifest(tmp_path))
    assert _MARK not in proc.stderr, proc.stderr
    assert "SECRETMARK" not in proc.stderr, proc.stderr
    assert "Traceback" not in proc.stderr


def test_a_long_scalar_in_any_key_is_capped(tmp_path: Path) -> None:
    for path in _every_key():
        proc = run(_doc_with(path, _MARK), tmp_path, manifest=_manifest(tmp_path))
        assert _MARK not in proc.stderr, path
        assert "Traceback" not in proc.stderr, path


# ── values are exported and saved in normalised form ────────────────────────


@pytest.mark.parametrize(
    ("key", "given", "normal"),
    [
        ("path", "/mnt//x/./", "/mnt/x"),
        ("path", "//mnt/x///y/", "/mnt/x/y"),
        ("path", "/mnt/./x", "/mnt/x"),
        ("flm_path", "/var/lib/hal0/models//flm/./", "/var/lib/hal0/models/flm"),
    ],
)
def test_store_paths_are_exported_and_saved_normalised(
    tmp_path: Path, key: str, given: str, normal: str
) -> None:
    doc = {"version": 1, "model_store": {key: given}}
    proc = run(doc, tmp_path)
    name = "HAL0_MODELS_DIR" if key == "path" else "HAL0_FLM_MODELS_DIR"
    assert f"{name}={normal}" in lines(proc)
    assert saved_of(proc)["model_store"] == {key: normal}
    saved = _emit(doc, tmp_path)
    assert saved.returncode == 0, saved.stderr
    assert json.loads(saved.stdout)["model_store"] == {key: normal}


def test_the_normalised_path_is_checked_too(tmp_path: Path) -> None:
    for bad in ("/mnt/../etc/x", "/./usr/./lib", "//etc//x", "///etc", "//var//lib/x"):
        proc = run({"version": 1, "model_store": {"path": bad}}, tmp_path)
        assert proc.returncode == 2, bad
    proc = run({"version": 1, "model_store": {"path": "/./etc/./x"}}, tmp_path)
    assert "system location" in proc.stderr


@pytest.mark.parametrize("blank", ["", " ", "  ", "\t", " ", "  "])
def test_a_blank_public_url_is_an_error(tmp_path: Path, blank: str) -> None:
    proc = run({"version": 1, "network": {"public_url": blank}}, tmp_path)
    # \t and the Unicode spaces are control/format text as well; any error will do
    assert proc.returncode == 2 and proc.stdout == ""
    assert "network.public_url" in proc.stderr


def test_a_null_public_url_is_still_fine(tmp_path: Path) -> None:
    assert lines(run({"version": 1, "network": {"public_url": None}}, tmp_path)) == [
        "HAL0_NONINTERACTIVE=1"
    ]


# ── the two folder paths are text: the reader never looks at the disk ───────
#
# Round 3 resolved the path with realpath and printed the result; a folder whose
# NAME held a newline then injected NAME=value lines. The reader now checks and
# prints the document's own text only, and makes no filesystem call for either
# path; the installer's sinks hold every rule about what is on disk.


def _store(tmp_path: Path, key: str, value: str, *extra: str):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"version": 1, "model_store": {key: value}}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", str(READER), "--origin", "wizard", *extra, "--", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


_HOSTILE_NAMES = [
    "x\nHAL0_HERMES_TERMINAL=1",
    "x'\"$(id)`id`",
    "x\x1b]0;owned\x07\x1b[31m",
    "x\\nHAL0_PORT=1",
    "#saved AAAA",
]


@pytest.mark.parametrize("hostile", _HOSTILE_NAMES)
@pytest.mark.parametrize("origin", ["wizard", "file"])
def test_a_hostile_folder_behind_a_link_never_reaches_the_output(
    tmp_path: Path, hostile: str, origin: str
) -> None:
    # The third pass's attack: the document's text is clean, the folder the link
    # leads to on disk has a newline, a quote or an escape in its NAME.
    real = tmp_path / "real" / hostile
    real.mkdir(parents=True)
    (tmp_path / "link").symlink_to(real)
    store = f"{tmp_path}/link"
    doc = {
        "version": 1,
        "model_store": {"path": f"{store}/models", "flm_path": f"{store}/models/flm"},
    }
    proc = run(doc, tmp_path, origin=origin)
    assert lines(proc) == [
        "HAL0_NONINTERACTIVE=1",
        f"HAL0_MODELS_DIR={store}/models",
        f"HAL0_FLM_MODELS_DIR={store}/models/flm",
    ]
    assert len(proc.stdout.splitlines()) == 4  # nothing more than the three lines and #saved
    assert saved_of(proc)["model_store"] == doc["model_store"]
    assert proc.stderr == ""
    assert hostile not in proc.stdout and "real" not in proc.stdout
    # the store itself being the link: the same
    proc = run({"version": 1, "model_store": {"path": store}}, tmp_path, origin=origin)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1", f"HAL0_MODELS_DIR={store}"]
    saved = _emit(doc, tmp_path)
    assert saved.returncode == 0 and json.loads(saved.stdout)["model_store"] == doc["model_store"]
    assert hostile not in saved.stdout


def _no_disk_for(monkeypatch, reader, roots: tuple[str, ...]) -> list[str]:
    """Make any filesystem look at a path under ROOTS fail the test; return the log."""
    seen: list[str] = []

    def guard(name, real):
        def wrapped(target, *args, **kwargs):
            text = os.fsdecode(target) if isinstance(target, (str, bytes, os.PathLike)) else ""
            if any(
                text == root
                or text.startswith(root + "/")
                or root.startswith(text + "/")
                and text not in ("", "/")
                for root in roots
            ):
                seen.append(f"{name}({text})")
                raise AssertionError(f"the reader called {name} on {text!r}")
            return real(target, *args, **kwargs)

        return wrapped

    for mod, name in (
        (reader.os, "stat"),
        (reader.os, "lstat"),
        (reader.os, "open"),
        (reader.os, "listdir"),
        (reader.os, "scandir"),
        (reader.os, "access"),
        (reader.os.path, "exists"),
        (reader.os.path, "lexists"),
        (reader.os.path, "isdir"),
        (reader.os.path, "islink"),
        (reader.os.path, "realpath"),
    ):
        monkeypatch.setattr(mod, name, guard(name, getattr(mod, name)))
    return seen


@pytest.mark.parametrize("mode", ["apply", "saved"])
def test_the_reader_makes_no_filesystem_call_for_either_path(
    tmp_path: Path, monkeypatch, capsys, mode: str
) -> None:
    reader = _reader_module()
    existing = tmp_path / "probe-store"
    (existing / "flm").mkdir(parents=True)
    for store in (str(existing), "/srv/hal0-lexical-probe/models"):
        doc = tmp_path / "answers.json"
        doc.write_text(
            json.dumps({"version": 1, "model_store": {"path": store, "flm_path": f"{store}/flm"}}),
            encoding="utf-8",
        )
        manifest = _manifest(tmp_path)
        with monkeypatch.context() as patch:
            seen = _no_disk_for(patch, reader, (store,))
            argv = (
                ["--origin", "wizard", "--models-manifest", str(manifest), "--", str(doc)]
                if mode == "apply"
                else ["--models-manifest", str(manifest), "--emit-saved", str(doc)]
            )
            rc = reader.main(argv)
        out = capsys.readouterr()
        assert seen == [] and rc == 0, (seen, out.err)
        assert store in out.out


def test_the_reader_source_has_no_filesystem_probe_left() -> None:
    text = READER.read_text(encoding="utf-8")
    for gone in (
        "_canonical",
        "_safe_location",
        "_unsafe_parent",
        "_denied_by_identity",
        "_identity",
        "HAL0_ANSWERS_TEST_ROOT_UID",
        "os.lstat",
        "os.stat(",
        "os.path.exists",
        "os.path.lexists",
        "os.path.isdir",
    ):
        assert gone not in text, gone
    # the one realpath left locates the reader's own manifest file, never a document path
    assert text.count("realpath") == 1 and "os.path.realpath(__file__)" in text


@pytest.mark.parametrize(
    "bad", ["/mnt/café", "/mnt/١٢", "/mnt/ａ", "/mnt/a b", "/mnt/models\n", "/mnt/x y"]
)
def test_non_ascii_letters_and_a_trailing_newline_are_refused(tmp_path: Path, bad: str) -> None:
    for key in _STORE_KEYS:
        proc = run({"version": 1, "model_store": {key: bad}}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == "", (key, bad)
        assert f"model_store.{key}" in proc.stderr


@pytest.mark.parametrize("key", _STORE_KEYS)
def test_a_path_longer_than_4096_characters_is_refused(tmp_path: Path, key: str) -> None:
    value = "/var/lib/hal0/models/" + "a" * 4096
    proc = run({"version": 1, "model_store": {key: value}}, tmp_path)
    assert proc.returncode == 2 and "longer than 4096" in proc.stderr
    assert "a" * 100 not in proc.stderr


# ── model_store.flm_path: only a folder inside the model store ──────────────


@pytest.mark.parametrize(
    ("store", "flm", "ok"),
    [
        (None, "/var/lib/hal0/models/flm", True),
        (None, "/var/lib/hal0/models/npu/flm", True),
        (None, "/var/lib/hal0/models", False),  # the store itself is not inside it
        (None, "/var/lib/hal0/modelsx/flm", False),  # a look-alike, compared by component
        (None, "/var/lib/hal0/flm", False),
        (None, "/mnt/disk/flm", False),
        (None, "/home/alice/.config/systemd/user", False),
        ("/mnt/disk/models", "/mnt/disk/models/flm", True),
        ("/mnt/disk/models", "//mnt//disk/models/./flm/", True),
        ("/mnt/disk/models", "/mnt/disk/models", False),
        ("/mnt/disk/models", "/mnt/disk/modelsx/flm", False),
        ("/mnt/disk/models", "/mnt/disk/flm", False),
        (
            "/mnt/disk/models",
            "/var/lib/hal0/models/flm",
            False,
        ),  # the default store is not the store now
        ("/mnt/disk/models/", "/mnt/disk/models/flm", True),
    ],
)
def test_flm_path_must_be_inside_the_model_store(
    tmp_path: Path, store: str | None, flm: str, ok: bool
) -> None:
    section = {"flm_path": flm} if store is None else {"path": store, "flm_path": flm}
    proc = run({"version": 1, "model_store": section}, tmp_path)
    if ok:
        assert f"HAL0_FLM_MODELS_DIR={reader_normal(flm)}" in lines(proc)
    else:
        assert proc.returncode == 2 and proc.stdout == ""
        assert "model_store.flm_path" in proc.stderr and _OUTSIDE_STORE in proc.stderr
        assert (
            "set HAL0_FLM_MODELS_DIR in the environment when running the installer" in proc.stderr
        )


def reader_normal(path: str) -> str:
    return "/" + "/".join(p for p in path.split("/") if p not in ("", "."))


def test_a_refused_store_path_does_not_add_a_second_error_for_flm(tmp_path: Path) -> None:
    proc = run(
        {"version": 1, "model_store": {"path": "/etc/m", "flm_path": "/etc/m/flm"}}, tmp_path
    )
    assert proc.returncode == 2
    assert proc.stderr.count("error:") == 1 and "model_store.path" in proc.stderr


# ── the default store comes from the command line ───────────────────────────


def test_default_store_argument_moves_the_flm_rule_to_a_dev_prefix(tmp_path: Path) -> None:
    dev = tmp_path / "dev" / "var" / "lib" / "hal0"
    value = f"{dev}/models/flm"
    refused = _store(tmp_path, "flm_path", value)
    assert refused.returncode == 2 and _OUTSIDE_STORE in refused.stderr
    accepted = _store(tmp_path, "flm_path", value, "--default-store", f"{dev}/models")
    assert f"HAL0_FLM_MODELS_DIR={value}" in lines(accepted)
    saved = subprocess.run(
        [
            sys.executable,
            "-I",
            str(READER),
            "--default-store",
            f"{dev}/models",
            "--emit-saved",
            str(tmp_path / "answers.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert saved.returncode == 0, saved.stderr
    assert json.loads(saved.stdout)["model_store"] == {"flm_path": value}


def test_default_store_argument_is_the_only_exception_it_names(tmp_path: Path) -> None:
    # the literal /var/lib/hal0/models stops being special once another store is passed
    proc = _store(
        tmp_path,
        "path",
        "/var/lib/hal0/models/x",
        "--default-store",
        str(tmp_path / "dev" / "models"),
    )
    assert proc.returncode == 2 and "system location" in proc.stderr
    proc = _store(
        tmp_path,
        "path",
        f"{tmp_path}/dev/models/x",
        "--default-store",
        str(tmp_path / "dev" / "models"),
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "bad",
    [
        "relative/models",
        "",
        "/mnt/with\nnewline",
        "/mnt/a;b",
        "/mnt/../x",
        "/" + "a" * 5000,
        "/mnt/café",
        "/mnt",
    ],
)
def test_default_store_argument_is_validated_like_a_path(tmp_path: Path, bad: str) -> None:
    for mode in ("apply", "saved"):
        if mode == "apply":
            proc = _store(tmp_path, "path", "/mnt/disk/models", "--default-store", bad)
        else:
            (tmp_path / "answers.json").write_text('{"version": 1}', encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(READER),
                    "--default-store",
                    bad,
                    "--emit-saved",
                    str(tmp_path / "answers.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        assert proc.returncode == 2 and proc.stdout == "", (mode, bad)
        assert "default-store" in proc.stderr
        assert "newline" not in proc.stderr and "aaaa" not in proc.stderr


def test_a_document_cannot_name_the_default_store(tmp_path: Path) -> None:
    doc = {
        "version": 1,
        "model_store": {"path": "/mnt/disk/models", "default_store": "/etc"},
        "default_store": "/etc",
    }
    proc = run(doc, tmp_path)
    assert proc.returncode == 0 and "default_store" not in "".join(lines(proc))
    assert all("/etc" not in line for line in lines(proc))


# ── one read, one run: the saved form is the apply run's last line ──────────


def test_the_apply_run_ends_with_exactly_one_saved_line(tmp_path: Path) -> None:
    proc = run(FULL, tmp_path, manifest=_manifest(tmp_path))
    out = proc.stdout.splitlines()
    assert [x for x in out if x.startswith("#saved")] == [out[-1]]
    assert all(x.startswith("HAL0_") for x in out[:-1])
    payload = out[-1][len("#saved ") :]
    assert (
        payload and base64.b64encode(base64.b64decode(payload, validate=True)).decode() == payload
    )
    emitted = _emit(FULL, tmp_path, "--models-manifest", str(_manifest(tmp_path)))
    assert emitted.returncode == 0, emitted.stderr
    assert base64.b64decode(payload).decode("utf-8") == emitted.stdout  # the same saved form


def test_the_saved_line_drops_the_grant_the_token_and_unknown_keys(tmp_path: Path) -> None:
    doc = {
        "version": 1,
        "huggingface": {"token": "hf_secret"},
        "mystery": "hf_secret2",
        "apps": {"hermes": {"terminal_tool": True}},
    }
    proc = run(doc, tmp_path)
    assert "HAL0_HERMES_TERMINAL=1" in lines(proc)
    assert saved_of(proc) == {"version": 1, "apps": {"hermes": {}}}
    assert "hf_secret" not in base64.b64decode(proc.stdout.splitlines()[-1][7:]).decode()


def test_a_rejected_document_prints_no_saved_line(tmp_path: Path) -> None:
    proc = run({"version": 1, "network": {"port": 0}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""


# ── message volume is capped ────────────────────────────────────────────────


@pytest.mark.parametrize(("strict", "kind"), [(False, "warning"), (True, "error")])
@pytest.mark.parametrize("count", [20, 21, 500])
def test_messages_are_capped_at_twenty_per_kind(
    tmp_path: Path, strict: bool, kind: str, count: int
) -> None:
    doc = {"version": 1, "strict": strict, **{f"unknown{i:04d}": 1 for i in range(count)}}
    proc = run(doc, tmp_path)
    assert proc.returncode == (2 if strict else 0)
    err = proc.stderr.splitlines()
    shown = [x for x in err if x.startswith(f"{kind}: ") and "unknown" in x]
    assert len(shown) == min(count, 20)
    more = [x for x in err if x.startswith(f"{kind}: ... and ")]
    if count > 20:
        assert more == [f"{kind}: ... and {count - 20} more"]
    else:
        assert more == []
    assert len(err) <= 21 + (1 if strict else 0)


@pytest.mark.parametrize("strict", [False, True])
def test_a_flood_of_unknown_keys_is_read_quickly(tmp_path: Path, strict: bool) -> None:
    # each message is de-duplicated against the ones already kept; a list made
    # that quadratic (a 0.9 MB document took about 17 seconds)
    import time

    doc = {"version": 1, "strict": strict, **{f"unknown{i:05d}": 1 for i in range(50_000)}}
    start = time.monotonic()
    proc = run(doc, tmp_path)
    elapsed = time.monotonic() - start
    assert proc.returncode == (2 if strict else 0), proc.stderr
    kind = "error" if strict else "warning"
    assert f"{kind}: ... and {50_000 - 20} more" in proc.stderr.splitlines()
    assert elapsed < 3.0, f"{elapsed:.1f}s"


def test_repeated_messages_are_still_shown_once(tmp_path: Path) -> None:
    from importlib import util

    spec = util.spec_from_file_location("_answers_reader_dedupe", READER)
    assert spec is not None and spec.loader is not None
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    problems = mod._Problems()
    for _ in range(3):
        problems.warn("same")
        problems.error("same")
    problems.warn("other")
    assert problems.warnings == ["same", "other"] and problems.errors == ["same"]


# ── the wizard's vocabulary (seam contract 2026-11-28) ─────────────────────

WIZARD_META = {"saved_at": "2026-11-28T09:30:00Z", "saved_by": "1.9.0"}

WIZARD_RECOMMENDED = {
    "version": 1,
    "strict": True,
    "install": {"type": "recommended"},
    "access": {"admin_key": "generate"},
    "maintenance": {"weekly_benchmark": False},
    "models": {"starter": "auto", "brain": "auto", "memory": "shared"},
    "memory": {"enabled": True},
    "npu": {"opt_in": "auto"},
    "apps": {
        "openwebui": {"enabled": True},
        "hermes": {"enabled": True, "terminal_tool": False},
        "pi": {"enabled": True},
    },
    "gen": {"mode": "scaffold_and_download", "models": []},
    "model_store": {"path": "/var/lib/hal0/models"},
    "network": {"hostname": "hal0", "port": 8080, "allowed_origins": ["https://hal0.example.com"]},
    "huggingface": {"token_env": "HF_TOKEN"},
    "meta": WIZARD_META,
}

WIZARD_MINIMAL = {
    "version": 1,
    "strict": True,
    "install": {"type": "minimal"},
    "access": {"admin_key": "generate"},
    "maintenance": {"weekly_benchmark": False},
    "models": {"starter": "none", "brain": "none"},
    "memory": {"enabled": False},
    "npu": {"opt_in": False},
    "apps": {
        "openwebui": {"enabled": False},
        "hermes": {"enabled": False, "terminal_tool": False},
        "pi": {"enabled": False},
    },
    "gen": {"mode": "off", "models": []},
    "model_store": {"path": "/var/lib/hal0/models"},
    "network": {"hostname": "hal0", "port": 8080, "allowed_origins": []},
    "huggingface": {"token_env": "HF_TOKEN"},
    "meta": WIZARD_META,
}


def test_wizard_recommended_document_loads_strict(tmp_path: Path) -> None:
    proc = run(WIZARD_RECOMMENDED, tmp_path)
    got = lines(proc)
    assert proc.stderr == ""
    assert "HAL0_ADMIN_KEY_MODE=generate" in got and "HAL0_NPU_SETUP=auto" in got
    saved = saved_of(proc)
    assert saved["install"] == {"type": "recommended"} and saved["meta"] == WIZARD_META
    assert saved["models"]["memory"] == "shared" and "huggingface" not in saved
    assert saved["apps"]["hermes"] == {"enabled": True}


def test_wizard_minimal_document_loads_strict(tmp_path: Path) -> None:
    proc = run(WIZARD_MINIMAL, tmp_path)
    got = lines(proc)
    assert proc.stderr == ""
    assert "HAL0_ADMIN_KEY_MODE=generate" in got and "HAL0_NPU_SETUP=off" in got
    assert saved_of(proc)["install"] == {"type": "minimal"}


def test_old_words_rejected(tmp_path: Path) -> None:
    proc = run({"version": 1, "access": {"admin_key": "create"}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""
    proc = run({"version": 1, "strict": True, "npu": {"setup": "auto"}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == "" and "npu.setup" in proc.stderr
    proc = run({"version": 1, "npu": {"setup": "off"}}, tmp_path)  # non-strict: unknown, ignored
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"] and "npu.setup" in proc.stderr
    assert "npu" not in saved_of(proc) or "setup" not in saved_of(proc)["npu"]


@pytest.mark.parametrize(("value", "word"), [("auto", "auto"), (True, "on"), (False, "off")])
def test_npu_opt_in_values(tmp_path: Path, value: object, word: str) -> None:
    proc = run({"version": 1, "npu": {"opt_in": value}}, tmp_path)
    assert f"HAL0_NPU_SETUP={word}" in lines(proc)


@pytest.mark.parametrize("bad", ["off", "on", "Auto", "", 1, 0, ["auto"], {}])
def test_npu_opt_in_rejects(tmp_path: Path, bad: object) -> None:
    proc = run({"version": 1, "npu": {"opt_in": bad}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""


def test_models_memory(tmp_path: Path) -> None:
    proc = run({"version": 1, "models": {"memory": "shared"}}, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]
    for bad in ("dedicated", "", "Shared", True, 1, ["shared"]):
        proc = run({"version": 1, "models": {"memory": bad}}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == "", bad


def test_install_type(tmp_path: Path) -> None:
    for word in ("recommended", "minimal", "custom"):
        proc = run({"version": 1, "strict": True, "install": {"type": word}}, tmp_path)
        assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]
        assert saved_of(proc)["install"] == {"type": word}
    for bad in ("Recommended", "", "full", 1, None, ["minimal"], "minimal\n"):
        proc = run({"version": 1, "install": {"type": bad}}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == "", bad
    for bad in ("minimal", [], 3):
        proc = run({"version": 1, "install": bad}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == "", bad
    proc = run({"version": 1, "strict": True, "install": {"type": "minimal", "x": 1}}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""


def test_meta_values(tmp_path: Path) -> None:
    proc = run({"version": 1, "strict": True, "meta": WIZARD_META}, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"]
    for saved_by in ("unknown", "1.9.0-rc.1+g1a2b3c", "0" * 64):
        proc = run({"version": 1, "meta": {"saved_by": saved_by}}, tmp_path)
        assert lines(proc)
    bad_at = (
        "2026-11-28 09:30:00Z",
        "2026-11-28T09:30:00",
        "2026-11-28T09:30:00Z\n",
        "\u0662026-11-28T09:30:00Z",
        "",
        5,
        None,
    )
    for bad in bad_at:
        proc = run({"version": 1, "meta": {"saved_at": bad}}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == "", bad
    bad_by = ("", "-x", ".x", "a b", "a" * 65, "x\n", "a/b", "hf_secret;rm", 1, None)
    for bad in bad_by:
        proc = run({"version": 1, "meta": {"saved_by": bad}}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == "", bad
    proc = run({"version": 1, "meta": "x"}, tmp_path)
    assert proc.returncode == 2 and proc.stdout == ""


def test_huggingface_token_env(tmp_path: Path) -> None:
    proc = run({"version": 1, "huggingface": {"token_env": "HF_TOKEN"}}, tmp_path)
    assert lines(proc) == ["HAL0_NONINTERACTIVE=1"] and proc.stderr == ""
    for bad in ("MY_TOKEN", "hf_secret_value", "", "hf_token", 1, None):
        proc = run({"version": 1, "huggingface": {"token_env": bad}}, tmp_path)
        assert proc.returncode == 2 and proc.stdout == "", bad
        assert "hf_secret_value" not in proc.stderr
    proc = run({"version": 1, "huggingface": {"token_env": "HF_TOKEN", "other": 1}}, tmp_path)
    assert lines(proc) and proc.stderr.count("huggingface settings are not read") == 1
    assert "huggingface" not in saved_of(proc)
