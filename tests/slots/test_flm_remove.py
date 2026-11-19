"""Removing a downloaded FLM model: the refuse rules and the remove itself.

The seam call (``hal0-flmctl run-remove``) is mocked at
:func:`hal0.slots.flm_remove._run_remove`; the catalogue, install-dir lookup
and slot configs are stubbed, so nothing here needs podman or an NPU.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest

from hal0.errors import Conflict, NotFound, UnprocessableEntity
from hal0.providers import flm_cli
from hal0.registry import pull as pull_mod
from hal0.slots import flm_remove


def _anchor(model: str = "qwen3:0.6b", **npu: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": "npu",
        "device": "npu",
        "type": "llm",
        "model": {"default": model},
    }
    if npu:
        cfg["npu"] = npu
    return cfg


def _shadow(slot_type: str, name: str, model: str) -> dict[str, Any]:
    return {"name": name, "device": "npu", "type": slot_type, "model": {"default": model}}


# ── flm_tags_in_use ───────────────────────────────────────────────────────


def test_chat_model_of_the_anchor_is_in_use() -> None:
    used = flm_remove.flm_tags_in_use([_anchor("llama3.2:1b")])
    assert used == {"llama3.2:1b": [{"role": "chat", "slot": "npu"}]}


def test_embed_asr_and_decision_roles_follow_the_npu_table() -> None:
    used = flm_remove.flm_tags_in_use(
        [_anchor("qwen3:0.6b", embed=True, asr=True, decision="d3:lite")]
    )
    assert used["embed-gemma:300m"] == [{"role": "embed", "slot": "npu"}]
    assert used["whisper-v3:turbo"] == [{"role": "asr", "slot": "npu"}]
    assert used["d3:lite"] == [{"role": "decision", "slot": "npu"}]


def test_roles_switched_off_do_not_hold_their_model() -> None:
    used = flm_remove.flm_tags_in_use([_anchor("qwen3:0.6b", embed=False, asr=False)])
    assert set(used) == {"qwen3:0.6b"}


def test_shadow_placeholders_never_count() -> None:
    """The trio's shadows always carry a placeholder model; only the anchor's table decides."""
    used = flm_remove.flm_tags_in_use(
        [
            _anchor("qwen3:0.6b"),
            _shadow("transcription", "npu-stt", "whisper-v3:turbo"),
            _shadow("embedding", "npu-embed", "embed-gemma:300m"),
        ]
    )
    assert set(used) == {"qwen3:0.6b"}


def test_no_activated_anchor_means_nothing_in_use() -> None:
    """A model-less anchor serves nothing, so its [npu] flags hold no model either."""
    assert flm_remove.flm_tags_in_use([_anchor("", embed=True, asr=True)]) == {}


def test_xdna_slot_is_not_an_flm_role() -> None:
    cfg = _anchor("qwen3:0.6b")
    cfg["binary"] = "xdna"
    assert flm_remove.flm_tags_in_use([cfg]) == {}


def test_live_flm_slot_serving_another_tag_holds_it() -> None:
    used = flm_remove.flm_tags_in_use(
        [_anchor("qwen3:0.6b")],
        live=[("npu", "llama3.2:1b"), ("gpu-chat", "gemma4:e2b")],
    )
    assert used["llama3.2:1b"] == [{"role": "chat", "slot": "npu"}]
    assert "gemma4:e2b" not in used  # not an FLM slot


def test_legacy_flm_id_maps_back_to_its_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.providers.flm as flm_mod

    monkeypatch.setattr(
        flm_mod,
        "flm_id_to_tag",
        lambda mid: "gemma4-it:e2b" if mid == "gemma4-it-e2b-FLM" else None,
    )
    used = flm_remove.flm_tags_in_use([_anchor("gemma4-it-e2b-FLM")])
    assert set(used) == {"gemma4-it:e2b"}


# ── check_removable / remove_flm_model ────────────────────────────────────


class _Registry:
    def __init__(self, ids: set[str], foreign: set[str] | None = None) -> None:
        self.ids = set(ids)
        self.foreign = set(foreign or ())

    def has(self, model_id: str) -> bool:
        return model_id in self.ids

    def get(self, model_id: str) -> Any:
        if model_id not in self.ids:
            return None
        from hal0.registry.model import Model

        if model_id in self.foreign:
            return Model(id=model_id, path="/m/x.gguf", backends=["llamacpp"])
        return Model(
            id=model_id, path=f"/m/flm/{model_id}", backends=["npu"], metadata={"runtime": "flm"}
        )

    def remove(self, model_id: str) -> bool:
        if model_id in self.ids:
            self.ids.discard(model_id)
            return True
        return False


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.SimpleNamespace:
    """Stub every outside fact the remove reads; returns the knobs."""
    store = tmp_path / "store"
    model_dir = store / "Qwen3-0.6B-NPU2"
    model_dir.mkdir(parents=True)
    (model_dir / "model.q4nx").write_bytes(b"x" * 1000)
    (model_dir / "config.json").write_bytes(b"{}")

    state = types.SimpleNamespace(
        configs=[_anchor("llama3.2:1b")],
        catalog=[
            {"tag": "qwen3:0.6b", "installed": True, "size_bytes": 900},
            {"tag": "llama3.2:1b", "installed": True, "size_bytes": 2000},
            {"tag": "gemma3:1b", "installed": False, "size_bytes": 1500},
        ],
        rc=0,
        output="[FLM]  Removing model: qwen3:0.6b\n",
        delete=True,
        calls=[],
        resets=0,
        model_dir=model_dir,
        store=store,
    )

    monkeypatch.setattr(flm_cli, "_slot_configs", lambda: state.configs)

    import hal0.providers.flm as flm_mod

    monkeypatch.setattr(flm_mod, "flm_catalog", lambda: state.catalog)

    def _reset() -> None:
        state.resets += 1

    monkeypatch.setattr(flm_mod, "reset_flm_catalog_cache", _reset)
    monkeypatch.setattr(
        flm_remove, "_install_dir", lambda tag, store_dir: str(model_dir) if tag else None
    )

    def _invocation(tag: str) -> tuple[flm_cli.FlmInvocation, str]:
        return (
            flm_cli.FlmInvocation(
                "run", ["sudo", "-n", flm_cli.SEAM_BIN, "run-remove", "img", str(store), tag]
            ),
            str(store),
        )

    monkeypatch.setattr(flm_cli, "flm_remove_invocation", _invocation)

    async def _run(argv: list[str], tag: str) -> tuple[int, str]:
        state.calls.append(argv)
        if state.delete:
            for f in model_dir.iterdir():
                f.unlink()
            model_dir.rmdir()
        return state.rc, state.output

    monkeypatch.setattr(flm_remove, "_run_remove", _run)

    import hal0.registry.pull_jobs as pull_jobs_mod

    monkeypatch.setattr(pull_jobs_mod, "load_persisted", lambda tag, registry=None: None)
    monkeypatch.setattr(pull_mod, "pull_job_file", lambda tag: tmp_path / f"{tag}.json")
    pull_mod._DELETING.clear()
    yield state
    pull_mod._DELETING.clear()


async def test_remove_runs_flm_remove_and_drops_the_row(env) -> None:
    registry = _Registry({"qwen3:0.6b", "llama3.2:1b"})
    done = types.SimpleNamespace(state="completed")
    jobs: dict[str, Any] = {"qwen3:0.6b": done}

    result = await flm_remove.remove_flm_model("qwen3:0.6b", registry=registry, jobs=jobs)

    assert env.calls == [
        ["sudo", "-n", flm_cli.SEAM_BIN, "run-remove", "img", str(env.store), "qwen3:0.6b"]
    ]
    assert result == {
        "tag": "qwen3:0.6b",
        "removed": True,
        "path": str(env.model_dir),
        "bytes_freed": 1002,
        "registry_removed": True,
        "warnings": [],
    }
    assert registry.ids == {"llama3.2:1b"}
    assert "qwen3:0.6b" not in jobs  # a finished pull job no longer reads as downloaded
    assert env.resets == 1  # catalogue cache reset so `installed` flips
    assert "qwen3:0.6b" not in pull_mod._DELETING


async def test_flm_warnings_are_passed_back(env) -> None:
    env.output = (
        "[WARNING]  Installed model nemotron3.5-bolt-dflash:e4b requires qwen3:0.6b "
        "and will not run until it is pulled again.\n[FLM]  Successfully removed 2 files\n"
    )
    result = await flm_remove.remove_flm_model("qwen3:0.6b", registry=None, jobs=None)
    assert result["warnings"] == [
        "Installed model nemotron3.5-bolt-dflash:e4b requires qwen3:0.6b "
        "and will not run until it is pulled again."
    ]


async def test_chat_model_in_use_is_refused_before_anything_runs(env) -> None:
    with pytest.raises(Conflict) as exc:
        await flm_remove.remove_flm_model("llama3.2:1b", registry=None, jobs=None)
    assert exc.value.code == "flm.model_in_use"
    assert exc.value.details["in_use_by"] == [{"role": "chat", "slot": "npu"}]
    assert env.calls == []


@pytest.mark.parametrize(
    ("npu", "tag", "role"),
    [
        ({"embed": True}, "embed-gemma:300m", "embed"),
        ({"asr": True}, "whisper-v3:turbo", "asr"),
    ],
)
async def test_embed_and_asr_models_in_use_are_refused(env, npu, tag, role) -> None:
    env.configs = [_anchor("llama3.2:1b", **npu)]
    env.catalog.append({"tag": tag, "installed": True})
    with pytest.raises(Conflict) as exc:
        await flm_remove.remove_flm_model(tag, registry=None, jobs=None)
    assert exc.value.code == "flm.model_in_use"
    assert exc.value.details["in_use_by"] == [{"role": role, "slot": "npu"}]
    assert env.calls == []


async def test_model_a_live_slot_serves_is_refused(env) -> None:
    with pytest.raises(Conflict) as exc:
        await flm_remove.remove_flm_model(
            "qwen3:0.6b", registry=None, jobs=None, live=[("npu", "qwen3:0.6b")]
        )
    assert exc.value.code == "flm.model_in_use"
    assert env.calls == []


@pytest.mark.parametrize("state", ["queued", "running"])
async def test_running_pull_is_refused(env, state: str) -> None:
    jobs = {"qwen3:0.6b": types.SimpleNamespace(state=state)}
    with pytest.raises(Conflict) as exc:
        await flm_remove.remove_flm_model("qwen3:0.6b", registry=None, jobs=jobs)
    assert exc.value.code == "flm.pull_running"
    assert exc.value.details["pull_state"] == state
    assert env.calls == []


async def test_pull_running_in_another_process_is_refused(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hal0.registry.pull_jobs as pull_jobs_mod

    monkeypatch.setattr(
        pull_jobs_mod, "load_persisted", lambda tag, registry=None: {"state": "running"}
    )
    with pytest.raises(Conflict) as exc:
        await flm_remove.remove_flm_model("qwen3:0.6b", registry=None, jobs={})
    assert exc.value.code == "flm.pull_running"
    assert env.calls == []


async def test_concurrent_remove_of_the_same_tag_is_refused(env) -> None:
    pull_mod.mark_deleting("qwen3:0.6b")
    with pytest.raises(Conflict) as exc:
        await flm_remove.remove_flm_model("qwen3:0.6b", registry=None, jobs=None)
    assert exc.value.code == "model.deleting"
    assert env.calls == []


async def test_pull_cannot_start_while_the_remove_runs(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def _run(argv: list[str], tag: str) -> tuple[int, str]:
        with pytest.raises(Conflict) as exc:
            pull_mod.ensure_not_deleting(tag)
        seen.append(exc.value.code)
        for f in env.model_dir.iterdir():
            f.unlink()
        env.model_dir.rmdir()
        return 0, ""

    monkeypatch.setattr(flm_remove, "_run_remove", _run)
    await flm_remove.remove_flm_model("qwen3:0.6b", registry=None, jobs=None)
    assert seen == ["model.deleting"]


async def test_not_installed_and_unknown_tags_are_refused(env) -> None:
    with pytest.raises(Conflict) as exc:
        await flm_remove.remove_flm_model("gemma3:1b", registry=None, jobs=None)
    assert exc.value.code == "flm.model_not_installed"
    with pytest.raises(NotFound) as nf:
        await flm_remove.remove_flm_model("nope:1b", registry=None, jobs=None)
    assert nf.value.code == "flm.model_not_found"
    assert env.calls == []


async def test_ghost_registry_row_is_cleared_without_flm_remove(env) -> None:
    """Files removed outside hal0 leave a row only this route can clear (#3810)."""
    registry = _Registry({"gemma3:1b"})
    result = await flm_remove.remove_flm_model("gemma3:1b", registry=registry, jobs=None)
    assert env.calls == []  # nothing on disk for flm to remove
    assert result["removed"] is True
    assert result["registry_removed"] is True
    assert result["bytes_freed"] == 0
    assert registry.ids == set()
    assert env.resets >= 1


async def test_non_flm_row_with_an_flm_id_is_not_dropped(env) -> None:
    """A GGUF row sharing an FLM tag is not a ghost: still 409, row kept."""
    registry = _Registry({"gemma3:1b"}, foreign={"gemma3:1b"})
    with pytest.raises(Conflict) as exc:
        await flm_remove.remove_flm_model("gemma3:1b", registry=registry, jobs=None)
    assert exc.value.code == "flm.model_not_installed"
    assert registry.ids == {"gemma3:1b"}


async def test_ghost_row_refuses_a_pull_that_started_during_the_catalogue_lookup(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The jobs recheck runs before the ghost shortcut, so a racing pull wins."""
    registry = _Registry({"gemma3:1b"})
    running = types.SimpleNamespace(state="running")
    jobs: dict[str, Any] = {}
    real = flm_remove._catalog_entry

    def _entry_then_pull(tag: str) -> Any:
        jobs[tag] = running  # the pull lands while the catalogue is probed
        return real(tag)

    monkeypatch.setattr(flm_remove, "_catalog_entry", _entry_then_pull)
    with pytest.raises(Conflict) as exc:
        await flm_remove.remove_flm_model("gemma3:1b", registry=registry, jobs=jobs)
    assert exc.value.code == "flm.pull_running"
    assert registry.ids == {"gemma3:1b"}


@pytest.mark.parametrize("tag", ["qwen3", "-rf:x", "a:b;id", ""])
async def test_bad_tag_is_422(env, tag: str) -> None:
    with pytest.raises(UnprocessableEntity) as exc:
        await flm_remove.remove_flm_model(tag, registry=None, jobs=None)
    assert exc.value.code == "flm.invalid_tag"
    assert env.calls == []


async def test_exit_zero_but_files_still_there_fails(env) -> None:
    """FLM 1.0.12 exits 0 when its remove fails, so the directory decides."""
    env.delete = False
    registry = _Registry({"qwen3:0.6b"})
    with pytest.raises(flm_remove.FlmRemoveFailed) as exc:
        await flm_remove.remove_flm_model("qwen3:0.6b", registry=registry, jobs=None)
    assert exc.value.code == "flm.remove_failed"
    assert exc.value.status == 502
    assert "still on disk" in exc.value.message
    assert registry.ids == {"qwen3:0.6b"}  # row kept: the files are still there
    assert "qwen3:0.6b" not in pull_mod._DELETING
    assert env.resets == 1


async def test_flm_error_line_fails_with_its_message(env) -> None:
    env.output = "[ERROR]  Failed to remove model directory: /x\n"
    with pytest.raises(flm_remove.FlmRemoveFailed) as exc:
        await flm_remove.remove_flm_model("qwen3:0.6b", registry=None, jobs=None)
    assert "Failed to remove model directory" in exc.value.message


async def test_missing_runner_image_fails_clearly(env) -> None:
    env.rc = flm_cli.RC_IMAGE_MISSING
    env.delete = False
    with pytest.raises(flm_remove.FlmRemoveFailed) as exc:
        await flm_remove.remove_flm_model("qwen3:0.6b", registry=None, jobs=None)
    assert "runner image" in exc.value.message


def test_remove_invocation_uses_the_run_remove_verb() -> None:
    target = flm_cli.FlmTarget(image="ghcr.io/x/flm:1", store="/srv/flm", token=None)
    inv = flm_cli.remove_invocation(target, "qwen3:0.6b", launcher=["sudo", "-n", "seam"])
    assert inv.argv == [
        "sudo",
        "-n",
        "seam",
        "run-remove",
        "ghcr.io/x/flm:1",
        "/srv/flm",
        "qwen3:0.6b",
    ]
    with pytest.raises(flm_cli.FLMRunnerUnavailableError):
        flm_cli.remove_invocation(target, "qwen3:0.6b", launcher=None)
    with pytest.raises(flm_cli.FLMRunnerUnavailableError):
        flm_cli.remove_invocation(target, "bad tag", launcher=["seam"])
