"""#2993: ``flm list`` / ``flm pull`` run inside the flm runner image.

Covers :mod:`hal0.providers.flm_cli`: which slot, image and store the commands
target, how the privileged route is chosen, the exec → one-shot → host order
for ``flm list``, and that ``flm pull`` never falls back to the host binary.
The wrapper's own argv is pinned in ``tests/installer/test_flmctl_wrapper.py``.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.providers import flm as flm_mod
from hal0.providers import flm_cli
from hal0.providers.flm_cli import FlmTarget

LAUNCHER = ["sudo", "-n", flm_cli.SEAM_BIN]
TARGET = FlmTarget(image="ghcr.io/hal0ai/hal0-runner-flm:1.0.10", store="/srv/flm", token="8")


def _parse(text: str) -> Any:
    return flm_mod._parse_flm_list(text)


class _Runs:
    """A ``subprocess.run`` stand-in answering per invocation mode."""

    def __init__(self, answers: dict[str, tuple[int, bytes] | Exception]) -> None:
        self.answers = answers
        self.argvs: list[list[str]] = []

    def __call__(self, argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        self.argvs.append(argv)
        # The host binary, else the seam verb: sudo -n <seam> <verb>.
        key = "host" if argv[0] == flm_mod._HOST_FLM_BIN else argv[3]
        answer = self.answers[key]
        if isinstance(answer, Exception):
            raise answer
        rc, out = answer
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr=b"")


_OK = b'[FLM]  Using custom model list path: x\n{"models": [{"model": "qwen3:0.6b"}]}'


# ── target resolution ────────────────────────────────────────────────────────


def test_target_follows_the_anchor_slot(tmp_path: Path) -> None:
    """Image pin, store override and id token all come from the FLM anchor."""
    anchor = {
        "id": 8,
        "name": "flm",
        "device": "npu",
        "type": "llm",
        "image_pin": "localhost/hal0-runner-flm:dev",
        "_paths": {"flm_models": str(tmp_path)},
        "model": {"default": "gemma4-it:e4b"},
    }
    target = flm_cli.resolve_target(anchor)
    assert target == FlmTarget(
        image="localhost/hal0-runner-flm:dev", store=str(tmp_path), token="8"
    )


def test_target_store_is_the_serving_slot_mount_source(tmp_path: Path) -> None:
    """The pull store is exactly what container_spec bind-mounts for the slot."""
    anchor = {
        "name": "flm",
        "device": "npu",
        "type": "llm",
        "_paths": {"flm_models": str(tmp_path)},
    }
    target = flm_cli.resolve_target(anchor)
    plan = flm_mod.FLMProvider().container_spec(anchor, {})
    assert [m.source for m in plan.mounts] == [target.store]
    assert target.image == plan.image
    assert target.token == "flm"


def test_target_without_a_flm_slot_uses_the_runner_pin(monkeypatch) -> None:
    monkeypatch.setattr(flm_cli, "_anchor_cfg", lambda: None)
    target = flm_cli.resolve_target()
    assert target.token is None
    assert target.image == flm_mod.FLMProvider().image_ref({})
    assert target.store == flm_mod._host_flm_models_dir()


def test_anchor_is_read_from_slot_tomls(tmp_path: Path, monkeypatch) -> None:
    """The anchor is the npu/llm slot, not its served_by shadows (id-keyed box)."""
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "6.toml").write_text(
        'name = "flm-embed"\ntype = "embedding"\ndevice = "npu"\nid = 6\n'
        'served_by = "flm"\n[model]\ndefault = "embed-gemma:300m"\n'
    )
    (slots / "8.toml").write_text(
        'name = "flm"\ntype = "llm"\ndevice = "npu"\nid = 8\n[model]\ndefault = "gemma4-it:e4b"\n'
    )
    (slots / "broken.toml").write_text("not = [valid")
    monkeypatch.setattr("hal0.config.paths.slots_config_dir", lambda: slots)
    anchor = flm_cli._anchor_cfg()
    assert anchor is not None and anchor["name"] == "flm"
    assert flm_cli.resolve_target(anchor).token == "8"


# ── privilege route ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("service", "euid", "present", "expected"),
    [
        (True, 996, True, LAUNCHER),
        (False, 0, True, [flm_cli.SEAM_BIN]),
        (False, 1000, True, None),
        (True, 996, False, None),
        (False, 0, False, None),
    ],
)
def test_launcher(service: bool, euid: int, present: bool, expected) -> None:
    got = flm_cli._launcher(
        is_hal0_user=lambda: service, euid=lambda: euid, seam_present=lambda: present
    )
    assert got == expected


# ── flm list ─────────────────────────────────────────────────────────────────


def test_list_order_is_exec_then_run_then_host() -> None:
    invs = flm_cli.list_invocations(TARGET, launcher=LAUNCHER)
    assert [i.mode for i in invs] == ["exec", "run", "host"]
    assert invs[0].argv == [*LAUNCHER, "exec-list", "8"]
    assert invs[1].argv == [*LAUNCHER, "run-list", TARGET.image, TARGET.store]
    assert invs[2].argv == [flm_mod._HOST_FLM_BIN, "list", "-j"]


def test_list_without_a_slot_skips_exec() -> None:
    target = FlmTarget(image=TARGET.image, store=TARGET.store, token=None)
    assert [i.mode for i in flm_cli.list_invocations(target, launcher=LAUNCHER)] == ["run", "host"]


def test_list_without_a_route_is_host_only() -> None:
    assert [i.mode for i in flm_cli.list_invocations(TARGET, launcher=None)] == ["host"]


def test_list_prefers_the_running_slot() -> None:
    runs = _Runs({"exec-list": (0, _OK)})
    out = flm_cli.flm_list_output(parse=_parse, target=TARGET, launcher=LAUNCHER, run=runs)
    assert out == [{"model": "qwen3:0.6b"}]
    assert runs.argvs == [[*LAUNCHER, "exec-list", "8"]]


def test_list_falls_back_to_one_shot_when_slot_is_down() -> None:
    runs = _Runs({"exec-list": (flm_cli.RC_NOT_RUNNING, b""), "run-list": (0, _OK)})
    out = flm_cli.flm_list_output(parse=_parse, target=TARGET, launcher=LAUNCHER, run=runs)
    assert out == [{"model": "qwen3:0.6b"}]
    assert [a[3] for a in runs.argvs] == ["exec-list", "run-list"]


def test_unparseable_container_answer_tries_the_next_route() -> None:
    runs = _Runs({"exec-list": (0, b"garbage"), "run-list": (0, _OK)})
    out = flm_cli.flm_list_output(parse=_parse, target=TARGET, launcher=LAUNCHER, run=runs)
    assert out == [{"model": "qwen3:0.6b"}]


def test_host_fallback_is_logged(caplog) -> None:
    runs = _Runs(
        {
            "exec-list": (flm_cli.RC_NOT_RUNNING, b""),
            "run-list": (flm_cli.RC_IMAGE_MISSING, b""),
            "host": (0, _OK),
        }
    )
    with caplog.at_level(logging.WARNING, logger="hal0.providers.flm_cli"):
        out = flm_cli.flm_list_output(parse=_parse, target=TARGET, launcher=LAUNCHER, run=runs)
    assert out == [{"model": "qwen3:0.6b"}]
    assert any(r.message == "flm.cli_host_fallback" for r in caplog.records)


def test_no_route_answers_is_none() -> None:
    runs = _Runs(
        {
            "exec-list": subprocess.TimeoutExpired("x", 30),
            "run-list": (66, b""),
            "host": FileNotFoundError(),
        }
    )
    assert flm_cli.flm_list_output(parse=_parse, target=TARGET, launcher=LAUNCHER, run=runs) is None


def test_unresolvable_target_still_tries_the_host(monkeypatch) -> None:
    def _boom(*_a: Any) -> FlmTarget:
        raise RuntimeError("bad slot toml")

    monkeypatch.setattr(flm_cli, "resolve_target", _boom)
    runs = _Runs({"host": (0, _OK)})
    out = flm_cli.flm_list_output(parse=_parse, launcher=LAUNCHER, run=runs)
    assert out == [{"model": "qwen3:0.6b"}]


# ── flm pull ─────────────────────────────────────────────────────────────────


def test_pull_is_a_one_shot_even_while_the_slot_runs() -> None:
    """Never exec: a killed ``podman exec`` client leaves ``flm pull`` running."""
    inv = flm_cli.pull_invocation(TARGET, "llama3.2:1b", launcher=LAUNCHER)
    assert inv.mode == "run"
    assert inv.argv == [*LAUNCHER, "run-pull", TARGET.image, TARGET.store, "llama3.2:1b"]


def test_pull_without_a_route_refuses() -> None:
    with pytest.raises(flm_cli.FLMRunnerUnavailableError) as exc:
        flm_cli.pull_invocation(TARGET, "llama3.2:1b", launcher=None)
    assert exc.value.code == "model.flm_runner_unavailable"


@pytest.mark.parametrize("tag", ["", "qwen3", "--rm:x", "a b:1"])
def test_pull_rejects_a_non_tag_before_sudo(tag: str) -> None:
    with pytest.raises(flm_cli.FLMRunnerUnavailableError):
        flm_cli.pull_invocation(TARGET, tag, launcher=LAUNCHER)


def test_flm_pull_invocation_returns_the_target_store(monkeypatch) -> None:
    monkeypatch.setattr(flm_cli, "_launcher", lambda: LAUNCHER)
    inv, store = flm_cli.flm_pull_invocation("qwen3:0.6b", target=TARGET)
    assert store == TARGET.store
    assert inv.argv[-1] == "qwen3:0.6b"


def test_stop_pull_needs_a_route_and_a_valid_tag() -> None:
    launcher = ["sudo", "-n", flm_cli.SEAM_BIN]
    inv = flm_cli.stop_pull_invocation("qwen3:0.6b", launcher=launcher)
    assert inv is not None and inv.argv == [*launcher, "stop-pull", "qwen3:0.6b"]
    assert flm_cli.stop_pull_invocation("qwen3:0.6b", launcher=None) is None
    assert flm_cli.stop_pull_invocation("-rm:x", launcher=launcher) is None
