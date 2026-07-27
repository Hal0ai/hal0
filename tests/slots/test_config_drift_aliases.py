"""Config-drift comparison is flag-alias aware.

``_argv_values`` used to compare raw argv tokens, so a running container
started with ``--batch-size 2048`` read as drifted against a rendered
``-b 2048`` (and vice versa) even though llama-server treats the two
spellings identically. Both sides are now canonicalized through
``hal0.slots.argv.FLAG_ALIASES`` before comparison.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.manager import _CONFIG_DRIFT_KEYS, SlotManager, _argv_values
from tests.slots.conftest import FakeContainerProvider


def test_argv_values_matches_long_spelling_for_short_key() -> None:
    """``-b``/``-ub`` keys pick up ``--batch-size``/``--ubatch-size`` tokens."""
    argv = ["--batch-size", "2048", "--ubatch-size", "512", "--ctx-size", "8192"]
    out = _argv_values(argv, _CONFIG_DRIFT_KEYS)
    assert out["-b"] == "2048"
    assert out["-ub"] == "512"
    assert out["--ctx-size"] == "8192"


def test_argv_values_matches_short_spelling_for_long_key() -> None:
    """A ``--ctx-size`` key matches the ``-c`` short alias (and inline =)."""
    assert _argv_values(["-c", "4096"], ("--ctx-size",)) == {"--ctx-size": "4096"}
    assert _argv_values(["--ctx-size=4096"], ("--ctx-size",)) == {"--ctx-size": "4096"}
    assert _argv_values(["--batch-size=64"], ("-b",)) == {"-b": "64"}


def test_argv_values_last_value_wins_across_spellings() -> None:
    """extra_args overriding the profile with the other spelling still wins."""
    argv = ["-b", "512", "--batch-size", "2048"]
    assert _argv_values(argv, ("-b",)) == {"-b": "2048"}


async def test_no_false_drift_between_alias_spellings(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """Running ``--batch-size`` vs rendered ``-b`` must NOT report drift."""
    container_stub.expected_argv_by_slot["chat"] = [
        "--model",
        "/mnt/ai-models/qwen.gguf",
        "--alias",
        "qwen3-4b-q4_k_m",
        "--ctx-size",
        "131072",
        "-b",
        "2048",
        "-ub",
        "512",
    ]
    container_stub.running_argv_by_slot["chat"] = [
        "--model",
        "/mnt/ai-models/qwen.gguf",
        "--alias",
        "qwen3-4b-q4_k_m",
        "-c",
        "131072",
        "--batch-size",
        "2048",
        "--ubatch-size",
        "512",
    ]

    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.status("chat", include_config_drift=True)

    assert snap.metadata.get("config_drift") == {"drifted": False, "diffs": []}


async def test_real_drift_still_detected_across_spellings(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A real value difference is still flagged even with mixed spellings."""
    container_stub.expected_argv_by_slot["chat"] = ["-b", "2048"]
    container_stub.running_argv_by_slot["chat"] = ["--batch-size", "512"]

    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.status("chat", include_config_drift=True)

    drift = snap.metadata.get("config_drift")
    assert drift is not None and drift["drifted"] is True
    assert {"key": "-b", "running": "512", "rendered": "2048"} in drift["diffs"]


async def test_ctx_size_change_surfaces_as_drift(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """O25 follow-up: a running container serving a STALE --ctx-size (e.g. 4096)
    while the config now renders 64000 must surface as drift (needs-restart),
    not read silently 'ready'. --ctx-size is a container-restart-required arg,
    so it is one of the compared _CONFIG_DRIFT_KEYS."""
    assert "--ctx-size" in _CONFIG_DRIFT_KEYS
    container_stub.expected_argv_by_slot["chat"] = [
        "--model",
        "/mnt/ai-models/qwen.gguf",
        "--ctx-size",
        "64000",
    ]
    container_stub.running_argv_by_slot["chat"] = [
        "--model",
        "/mnt/ai-models/qwen.gguf",
        "--ctx-size",
        "4096",
    ]

    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.status("chat", include_config_drift=True)

    drift = snap.metadata.get("config_drift")
    assert drift is not None and drift["drifted"] is True
    assert {"key": "--ctx-size", "running": "4096", "rendered": "64000"} in drift["diffs"]


async def test_no_false_drift_for_registry_id_model_and_alias(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1226: a slot created with a registry id must not permanently warn.

    The renderer resolves the id → on-disk path (``--model``) and slugifies it
    for the advertised ``--alias``; the drift preview may surface the raw id.
    Both sides run through the same resolution, so this is NOT drift.
    """
    model_id = "Qwopus3.5-4B-Coder-MTP-Q6_K"
    model_path = "/mnt/ai-models/qwopus/Qwopus3.5-4B-Coder-MTP-Q6_K.gguf"

    async def _fake_info(self: SlotManager, mid: str | None) -> dict[str, object]:
        return {"_model_key": model_id, "path": model_path}

    monkeypatch.setattr(SlotManager, "_resolve_model_info", _fake_info)

    # Running container: id already resolved to a path + slugified alias.
    container_stub.running_argv_by_slot["chat"] = [
        "--model",
        model_path,
        "--alias",
        "qwopus3-5-4b-coder-mtp-q6-k",
    ]
    # Rendered preview: raw registry id on both flags.
    container_stub.expected_argv_by_slot["chat"] = [
        "--model",
        model_id,
        "--alias",
        model_id,
    ]

    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.status("chat", include_config_drift=True)

    assert snap.metadata.get("config_drift") == {"drifted": False, "diffs": []}


async def test_real_model_drift_still_flagged_after_resolution(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolution must not mask a genuinely different running --model path."""
    model_id = "Qwopus3.5-4B-Coder-MTP-Q6_K"
    model_path = "/mnt/ai-models/qwopus/Qwopus3.5-4B-Coder-MTP-Q6_K.gguf"

    async def _fake_info(self: SlotManager, mid: str | None) -> dict[str, object]:
        return {"_model_key": model_id, "path": model_path}

    monkeypatch.setattr(SlotManager, "_resolve_model_info", _fake_info)

    # Running container is on a DIFFERENT file than the config resolves to.
    container_stub.running_argv_by_slot["chat"] = [
        "--model",
        "/mnt/ai-models/other/stale-model.gguf",
        "--alias",
        "qwopus3-5-4b-coder-mtp-q6-k",
    ]
    container_stub.expected_argv_by_slot["chat"] = ["--model", model_id, "--alias", model_id]

    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.status("chat", include_config_drift=True)

    drift = snap.metadata.get("config_drift")
    assert drift is not None and drift["drifted"] is True
    keys = {d["key"] for d in drift["diffs"]}
    assert "--model" in keys


async def test_no_false_drift_when_registry_key_is_the_slugified_id(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1226 (live CT105 shape): TOML keeps the catalog spelling, registry the slug.

    The slot TOML stores ``Qwopus3.5-4B-Coder-MTP-Q6_K`` while the registry key
    (and therefore the running container's ``--alias``) is the normalised
    ``qwopus3-5-4b-coder-mtp-q6-k``. The id→path substitution compared the two
    id spellings with ``==``, so it never fired and the operator saw a
    permanent, entirely bogus::

        WARN config drift: --model: running=/mnt/ai-models/....gguf
                                    rendered=Qwopus3.5-4B-Coder-MTP-Q6_K
    """
    toml_id = "Qwopus3.5-4B-Coder-MTP-Q6_K"
    registry_key = "qwopus3-5-4b-coder-mtp-q6-k"
    model_path = "/mnt/ai-models/qwopus/Qwopus3.5-4B-Coder-MTP-Q6_K.gguf"

    async def _fake_info(self: SlotManager, mid: str | None) -> dict[str, object]:
        return {"_model_key": registry_key, "path": model_path}

    monkeypatch.setattr(SlotManager, "_resolve_model_info", _fake_info)

    container_stub.running_argv_by_slot["chat"] = [
        "--model",
        model_path,
        "--alias",
        registry_key,
    ]
    # The rendered preview surfaced the raw catalog id on both flags.
    container_stub.expected_argv_by_slot["chat"] = ["--model", toml_id, "--alias", toml_id]

    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.status("chat", include_config_drift=True)

    assert snap.metadata.get("config_drift") == {"drifted": False, "diffs": []}


async def test_drift_resolves_the_servable_model_like_the_launch_path(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The drift check must resolve the model the way ``load()`` does (#1226).

    ``load()`` runs the configured id through ``_resolve_servable_model``
    (a catalog id that landed locally under a different id → the id that
    actually has a file), so the container is launched with the SERVABLE
    model's path. The drift check looked up the raw TOML id instead: the
    registry missed, the renderer fell back to emitting the bare id, and the
    comparison against the running container warned forever.
    """
    toml_id = "gemma-4-12b-it"
    servable_id = "gemma-4-12b-it-ud-q4-k-xl"
    model_path = "/mnt/ai-models/gemma/gemma-4-12b-it-UD-Q4_K_XL.gguf"
    resolved_for: list[str] = []

    def _fake_servable(self: SlotManager, model_id: str, cfg: object) -> str:
        resolved_for.append(model_id)
        return servable_id if model_id == toml_id else model_id

    async def _fake_info(self: SlotManager, mid: str | None) -> dict[str, object]:
        # Only the SERVABLE id has a file on disk — the raw catalog id misses,
        # exactly as the live registry did.
        if mid == servable_id:
            return {"_model_key": servable_id, "path": model_path}
        return {"_model_key": mid}

    monkeypatch.setattr(SlotManager, "_resolve_servable_model", _fake_servable)
    monkeypatch.setattr(SlotManager, "_resolve_model_info", _fake_info)

    (slot_root / "chat.toml").write_text(
        "\n".join(
            [
                'name = "chat"',
                "port = 8081",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "enabled = true",
                "[model]",
                f'default = "{toml_id}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    # Running container: launched from the servable model's real path.
    container_stub.running_argv_by_slot["chat"] = ["--model", model_path]
    # Rendered preview when the lookup misses: the bare id falls through.
    container_stub.expected_argv_by_slot["chat"] = ["--model", servable_id]

    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.status("chat", include_config_drift=True)

    assert toml_id in resolved_for, "drift must go through the servable-model resolution"
    assert snap.metadata.get("config_drift") == {"drifted": False, "diffs": []}
