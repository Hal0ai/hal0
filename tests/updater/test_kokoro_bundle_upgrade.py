"""Upgrade path: `hal0 update` fetches the kokoro-v1 bundle once (#2739, ADR-0030).

From 1.4.0 the kokoro tts slot launches only from the pulled bundle in
``<store>/tts/kokoro-v1/``. A box upgraded from an earlier release holds the
old mislabelled ``model.gguf`` (or nothing), so the post-activation migrations
must pull the bundle, or report the remedy when they cannot.

Same chain as ``tests/cli/test_update_unassigned_e2e.py`` (#2733), on a
synthetic upgrade fixture: run_post_activation_migrations (the kokoro pass is
real, and so is run_pull, with HF served by an httpx MockTransport)
-> PostActivationResult -> the post-swap child script's JSON envelope
-> _run_post_activation_migrations_after_swap -> the convergence dict
-> `hal0 update` output.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import tomli_w

from hal0.config import paths
from hal0.providers.kokoro import check_kokoro_bundle
from hal0.updater.updater import PostActivationResult, run_post_activation_migrations
from tests.cli.test_update_unassigned_e2e import (
    _convergence_as_commit_builds_it,
    _flat,
    _migration_envelope_through_child_script,
    _run_update,
)

_ONNX = b"kokoro-onnx-bytes" * 64
_VOICES = b"kokoro-voices" * 32


@pytest.fixture
def store(tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("HAL0_MODEL_STORE", str(root))
    return root


@pytest.fixture
def upgraded_tts_box(store: Path) -> Path:
    """A pre-1.4 box: the shipped tts slot on kokoro, and the old pull's layout."""
    old = store / "tts" / "kokoro-v1"
    old.mkdir(parents=True)
    (old / "model.gguf").write_bytes(_ONNX)  # the ONNX bytes under the wrong name
    (old / "meta.json").write_text(
        json.dumps({"curated_id": "kokoro-v1", "hf_file": "kokoro-v1.0.onnx"})
    )
    slot = paths.slots_config_dir() / "tts.toml"
    slot.parent.mkdir(parents=True, exist_ok=True)
    slot.write_text(
        tomli_w.dumps(
            {
                "name": "tts",
                "type": "tts",
                "device": "cpu",
                "runtime": "container",
                "profile": "kokoro",
                "port": 8085,
            }
        ),
        encoding="utf-8",
    )
    return old


@pytest.fixture
def _only_kokoro_pass_is_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every post-activation pass except the kokoro bundle pass."""
    from hal0.config.migrations.profile_centric_apply import ConversionReport

    monkeypatch.setattr(
        "hal0.config.migrations.runner_system.migrate_runner_system",
        lambda **_kw: SimpleNamespace(backup=None, unassigned=[]),
    )
    monkeypatch.setattr(
        "hal0.config.migrations.profile_centric_apply.migrate_profile_centric",
        lambda **_kw: ConversionReport(dry_run=False),
    )
    monkeypatch.setattr(
        "hal0.updater.updater._maybe_run_config_migrations", lambda *_a, **_kw: (1, 2)
    )
    monkeypatch.setattr(
        "hal0.registry.toml_retirement.retire_registry_toml", lambda **_kw: {"outcome": "absent"}
    )
    monkeypatch.setattr("hal0.updater.updater.relabel_stale_vulkan_slots", lambda **_kw: 0)
    monkeypatch.setattr("hal0.updater.updater.repair_hermes_mcp_client", lambda **_kw: False)
    monkeypatch.setattr("hal0.components.runner.converge_components", lambda **_kw: {})
    monkeypatch.setattr("hal0.updater.updater.rerender_slot_units", lambda **_kw: None)


def _serve_hf(monkeypatch: pytest.MonkeyPatch, *, online: bool) -> list[str]:
    """Route the real pull's HTTP through a MockTransport; ``online=False`` refuses."""
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if not online:
            raise httpx.ConnectError("network unreachable", request=req)
        if req.url.path.endswith("/kokoro-v1.0.onnx"):
            body = _ONNX
        elif req.url.path.endswith("/voices-v1.0.bin"):
            body = _VOICES
        else:
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    real_client = httpx.AsyncClient

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("hal0.registry.pull.httpx.AsyncClient", _client)
    return seen


def test_upgrade_pulls_the_bundle_and_replaces_the_old_layout(
    monkeypatch: pytest.MonkeyPatch, upgraded_tts_box: Path, _only_kokoro_pass_is_real: None
) -> None:
    seen = _serve_hf(monkeypatch, online=True)

    result = run_post_activation_migrations()

    assert isinstance(result, PostActivationResult)
    assert [(b["model"], b["outcome"]) for b in result.model_bundles] == [("kokoro-v1", "pulled")]
    bundle = upgraded_tts_box
    assert (bundle / "kokoro-v1.0.onnx").read_bytes() == _ONNX
    assert (bundle / "voices-v1.0.bin").read_bytes() == _VOICES
    assert not (bundle / "model.gguf").exists(), "the mislabelled copy must be cleaned up"
    check_kokoro_bundle(bundle)
    # Only the curated HF coordinates, never a GitHub release URL.
    assert all("/mikkoph/kokoro-onnx/resolve/" in p for p in seen)
    assert result.conversion_blockers == []


def test_second_run_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, upgraded_tts_box: Path, _only_kokoro_pass_is_real: None
) -> None:
    _serve_hf(monkeypatch, online=True)
    run_post_activation_migrations()
    seen = _serve_hf(monkeypatch, online=True)

    result = run_post_activation_migrations()

    assert result.model_bundles == []
    assert seen == []


def test_offline_upgrade_still_succeeds_and_names_the_remedy(
    monkeypatch: pytest.MonkeyPatch, upgraded_tts_box: Path, _only_kokoro_pass_is_real: None
) -> None:
    _serve_hf(monkeypatch, online=False)

    result = run_post_activation_migrations()

    (entry,) = result.model_bundles
    assert entry["outcome"] == "failed"
    assert entry["remedy"] == "hal0 model pull kokoro-v1"
    assert result.conversion_blockers == []
    assert (upgraded_tts_box / "model.gguf").exists(), "a failed pull removes nothing"


def test_box_without_kokoro_in_use_pulls_nothing(
    monkeypatch: pytest.MonkeyPatch, store: Path, _only_kokoro_pass_is_real: None
) -> None:
    seen = _serve_hf(monkeypatch, online=True)
    result = run_post_activation_migrations()
    assert result.model_bundles == []
    assert seen == []


def test_capability_selection_alone_triggers_the_pull(
    monkeypatch: pytest.MonkeyPatch, store: Path, _only_kokoro_pass_is_real: None
) -> None:
    from hal0.capabilities.config import capabilities_toml_path

    path = capabilities_toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        tomli_w.dumps(
            {
                "schema_version": 2,
                "selections": {
                    "voice": {
                        "tts": {
                            "device": "cpu",
                            "provider": "kokoro",
                            "model": "kokoro-v1",
                            "enabled": True,
                        }
                    }
                },
            }
        )
    )
    _serve_hf(monkeypatch, online=True)

    result = run_post_activation_migrations()

    assert [b["outcome"] for b in result.model_bundles] == ["pulled"]
    check_kokoro_bundle(store / "tts" / "kokoro-v1")


def test_boot_safety_net_never_downloads(
    monkeypatch: pytest.MonkeyPatch, upgraded_tts_box: Path, _only_kokoro_pass_is_real: None
) -> None:
    seen = _serve_hf(monkeypatch, online=True)

    result = run_post_activation_migrations(fetch_model_bundles=False)

    assert [b["outcome"] for b in result.model_bundles] == ["missing"]
    assert seen == []


def test_pulled_bundle_is_reported_in_hal0_update_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    upgraded_tts_box: Path,
    _only_kokoro_pass_is_real: None,
) -> None:
    _serve_hf(monkeypatch, online=True)
    migration = _migration_envelope_through_child_script(monkeypatch, capsys)
    assert [b["outcome"] for b in migration["model_bundles"]] == ["pulled"]

    convergence = _convergence_as_commit_builds_it(migration)
    convergence["post_activation_migrations"]["model_bundles"] = migration["model_bundles"]
    result = _run_update(monkeypatch, convergence, [])

    out = _flat(result.output)
    assert "pulled the kokoro-v1 bundle into" in out
    assert result.exit_code == 0, result.output


def test_offline_failure_and_remedy_reach_hal0_update_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    upgraded_tts_box: Path,
    _only_kokoro_pass_is_real: None,
) -> None:
    _serve_hf(monkeypatch, online=False)
    migration = _migration_envelope_through_child_script(monkeypatch, capsys)

    convergence = _convergence_as_commit_builds_it(migration)
    convergence["post_activation_migrations"]["model_bundles"] = migration["model_bundles"]
    result = _run_update(monkeypatch, convergence, [])

    out = _flat(result.output)
    assert "kokoro-v1 bundle could not be pulled" in out
    assert "hal0 model pull kokoro-v1" in out
    # A failed fetch is reported, not a blocker: the update still succeeds.
    assert result.exit_code == 0, result.output
