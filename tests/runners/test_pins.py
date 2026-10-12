"""The runner pin table (``manifest.json`` ``runners``) and its derivations."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import hal0.runners.pins as pins_mod
from hal0.runners import RUNNER_IMAGES
from hal0.runners.pins import (
    CUDA_RELEASE_LAG_WARN,
    RunnerPin,
    RunnerPinsError,
    export_payload,
    llama_release,
    load_runner_pins,
    parse_runner_pins,
    release_gate_problems,
    release_gate_warnings,
    vulkan_capable_refs,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_D = "sha256:" + "b" * 64


def _row(
    key: str, tag: str | None = None, digest: str | None = None, lanes: list[str] | None = None
) -> dict:
    image = "ghcr.io/ggml-org/llama.cpp" if key == "cuda" else f"ghcr.io/hal0ai/hal0-runner-{key}"
    return {"image": image, "tag": tag, "digest": digest, "lanes_gated": lanes or []}


def _release_ready(**overrides: dict) -> dict[str, RunnerPin]:
    rows = {key: _row(key, "b11552-r1a2b3c4", _D) for key in RUNNER_IMAGES}
    rows["cuda"] = _row("cuda", "server-cuda-b11541", _D)
    rows.update(overrides)
    return parse_runner_pins({"runners": rows})


# ── RunnerPin ─────────────────────────────────────────────────────────────────


def test_ref_shapes() -> None:
    repo = "ghcr.io/hal0ai/hal0-runner-cpu"
    assert RunnerPin("cpu", repo, "t", _D).ref == f"{repo}:t@{_D}"
    assert RunnerPin("cpu", repo, "t").ref == f"{repo}:t"


def test_an_unpublished_pin_has_no_ref() -> None:
    """The bare repo would pull ``latest``: an unpublished row must refuse."""
    with pytest.raises(RunnerPinsError, match="HAL0_RUNNER_IMAGE_CPU"):
        _ = RunnerPin("cpu", "ghcr.io/hal0ai/hal0-runner-cpu").ref


def test_an_unpublished_runner_resolves_only_through_an_env_override(monkeypatch) -> None:
    from hal0.runners import resolve_runner_image

    table = dict(load_runner_pins())
    table["cpu"] = RunnerPin("cpu", "ghcr.io/hal0ai/hal0-runner-cpu")
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    monkeypatch.delenv("HAL0_RUNNER_IMAGE_CPU", raising=False)
    with pytest.raises(RunnerPinsError):
        resolve_runner_image(RUNNER_IMAGES["cpu"])
    monkeypatch.setenv("HAL0_RUNNER_IMAGE_CPU", "docker.io/me/cpu:1")
    assert resolve_runner_image(RUNNER_IMAGES["cpu"]) == "docker.io/me/cpu:1"


def test_spellings_name_the_same_bytes() -> None:
    repo = "ghcr.io/hal0ai/hal0-runner-cpu"
    pin = RunnerPin("cpu", repo, "t", _D)
    assert pin.spellings() == {f"{repo}:t@{_D}", f"{repo}:t", f"{repo}@{_D}"}
    assert pin.matches(f"{repo}@{_D}")
    assert not pin.matches(repo)
    assert RunnerPin("cpu", repo).spellings() == frozenset()


def test_published_means_tagged() -> None:
    assert RunnerPin("cpu", "r", "t").published
    assert not RunnerPin("cpu", "r").published


# ── parsing ───────────────────────────────────────────────────────────────────


def test_shipped_manifest_parses_with_one_row_per_runner() -> None:
    pins = load_runner_pins(_REPO_ROOT / "manifest.json")
    assert set(pins) == set(RUNNER_IMAGES)


@pytest.mark.parametrize(
    ("row", "match"),
    [
        ({"tag": None}, "image is required"),
        ({"image": "ghcr.io/x/y:tag"}, "bare repo"),
        ({"image": "ghcr.io/x/y@sha256:" + "c" * 64}, "bare repo"),
        ({"image": "ghcr.io/x/y", "digest": "sha256:short", "tag": "t"}, "sha256"),
        ({"image": "ghcr.io/x/y", "digest": _D}, "no tag"),
        ({"image": "ghcr.io/x/y", "lanes_gated": "vulkan"}, "lanes_gated"),
        ({"image": "ghcr.io/x/y", "tag": ""}, "non-empty"),
    ],
)
def test_malformed_rows_are_refused(row: dict, match: str) -> None:
    with pytest.raises(RunnerPinsError, match=match):
        parse_runner_pins({"runners": {"cpu": row}})


def test_missing_table_is_refused() -> None:
    with pytest.raises(RunnerPinsError, match="no `runners`"):
        parse_runner_pins({"toolbox_images": {}})


def test_unreadable_manifest_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "manifest.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(RunnerPinsError):
        load_runner_pins(bad)


# ── derivations ───────────────────────────────────────────────────────────────


def test_vulkan_capable_refs_come_from_lanes_gated(monkeypatch) -> None:
    table = _release_ready(standard=_row("standard", "b1-r1", _D, ["rocm", "vulkan"]))
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    refs = vulkan_capable_refs()
    assert table["standard"].spellings() <= refs
    # cpu is Vulkan-gated in this table but cannot run Vulkan: never admitted.
    assert not (table["cpu"].spellings() & refs)


def test_no_vulkan_gate_no_vulkan_lane(monkeypatch) -> None:
    table = _release_ready(standard=_row("standard", "b1-r1", _D, ["rocm"]))
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    assert vulkan_capable_refs() == frozenset()


def test_cpu_runner_cannot_join_the_vulkan_set_even_if_gated(monkeypatch) -> None:
    table = _release_ready(cpu=_row("cpu", "b11552-r1a2b3c4", _D, ["vulkan"]))
    monkeypatch.setattr(pins_mod, "runner_pins", lambda: table)
    assert not (table["cpu"].spellings() & vulkan_capable_refs())


def test_export_lists_every_published_ref() -> None:
    table = _release_ready(qwen3tts=_row("qwen3tts"))
    payload = export_payload(table)
    assert payload["source"] == "manifest.json"
    assert payload["pins"] == sorted(p.ref for k, p in table.items() if k != "qwen3tts")


def test_export_file_matches_the_pin_table() -> None:
    """exports/runner-image-pins.json is regenerated from manifest.json
    (``python scripts/check_runner_pins.py --export``); the runner-images
    retention automation reads it as its never-delete allowlist."""
    shipped = json.loads((_REPO_ROOT / "exports" / "runner-image-pins.json").read_text())
    pins = load_runner_pins(_REPO_ROOT / "manifest.json")
    assert shipped == export_payload(pins)


# ── release gate ──────────────────────────────────────────────────────────────


def test_release_ready_table_passes() -> None:
    assert release_gate_problems(_release_ready()) == []


@pytest.mark.parametrize("key", ["standard", "cpu", "flm", "kokoro", "moonshine", "comfyui"])
def test_default_runner_without_a_digest_fails_the_gate(key: str) -> None:
    table = _release_ready(**{key: _row(key, "b11552-r1a2b3c4", None)})
    assert any(f"'{key}' is not digest-pinned" in p for p in release_gate_problems(table))


def test_default_runner_without_a_tag_fails_the_gate() -> None:
    table = _release_ready(flm=_row("flm"))
    problems = release_gate_problems(table)
    assert any("'flm' has no published tag" in p for p in problems)


@pytest.mark.parametrize("key", ["qwen3tts", "cuda"])
def test_optional_runners_may_stay_unpublished(key: str) -> None:
    assert release_gate_problems(_release_ready(**{key: _row(key)})) == []


def test_shipped_pin_table_is_release_ready() -> None:
    """The shipped table carries the hardware-gated pins: the release ritual
    must accept it, and every published pin is digest-pinned."""
    table = load_runner_pins(_REPO_ROOT / "manifest.json")
    assert release_gate_problems(table) == []
    assert all(pin.digest for pin in table.values() if pin.tag)


def test_standard_and_cpu_must_share_a_release() -> None:
    table = _release_ready(cpu=_row("cpu", "b11541-r1a2b3c4", _D))
    assert any("one llama.cpp release" in p for p in release_gate_problems(table))


def test_cuda_must_be_a_server_cuda_release_tag() -> None:
    table = _release_ready(cuda=_row("cuda", "server-cuda", _D))
    assert any("server-cuda-b<N>" in p for p in release_gate_problems(table))


def test_cuda_may_not_be_newer_than_standard() -> None:
    table = _release_ready(cuda=_row("cuda", "server-cuda-b11600", _D))
    assert any("newer than standard" in p for p in release_gate_problems(table))


def test_lagging_cuda_only_warns() -> None:
    lag = 11552 - CUDA_RELEASE_LAG_WARN - 1
    table = _release_ready(cuda=_row("cuda", f"server-cuda-b{lag}", _D))
    assert release_gate_problems(table) == []
    assert release_gate_warnings(table)


def test_registry_and_table_must_agree() -> None:
    rows = {key: _row(key, "b11552-r1a2b3c4", _D) for key in RUNNER_IMAGES}
    rows["cuda"] = _row("cuda", "server-cuda-b11541", _D)
    rows["strix"] = _row("strix", "b1", _D)
    del rows["kokoro"]
    problems = release_gate_problems(parse_runner_pins({"runners": rows}))
    assert any("'kokoro' has no row" in p for p in problems)
    assert any("runners.strix names no registered runner" in p for p in problems)


@pytest.mark.parametrize(
    ("tag", "release"),
    [("b11552-r1a2b3c4", 11552), ("server-cuda-b11541", 11541), ("v1", None), (None, None)],
)
def test_llama_release(tag: str | None, release: int | None) -> None:
    assert llama_release(tag) == release


def test_release_check_script_passes_on_the_shipped_pins() -> None:
    proc = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "check_runner_pins.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "release-ready" in proc.stdout
