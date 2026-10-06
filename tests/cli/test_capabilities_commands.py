"""Tests for ``hal0 capabilities`` — list/set (live API) + migrate (footgun fix).

``migrate`` used to default to a live write of ``/etc/hal0/capabilities.toml``
(``--dry-run`` was opt-in). That inverted the safety contract every sibling
repair command uses (``hal0 migrate model-layout`` is dry-run by default,
``--apply`` opts into the write). These tests pin the new default: no flags =
preview only, ``--apply`` = write.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.capabilities.config import CapabilityConfig, CapabilitySelection
from hal0.cli import capabilities_commands as cc

runner = CliRunner()


@pytest.fixture
def stub_config(monkeypatch: pytest.MonkeyPatch):
    """Stub load/save so migrate never touches a real capabilities.toml."""
    saved: dict[str, Any] = {"cfg": None}

    def _install(selections: dict[str, dict[str, CapabilitySelection]]) -> CapabilityConfig:
        cfg = CapabilityConfig(selections=selections)
        monkeypatch.setattr(cc, "load_capabilities_config", lambda: cfg)

        def _save(c: CapabilityConfig) -> None:
            saved["cfg"] = c

        monkeypatch.setattr(cc, "save_capabilities_config", _save)
        monkeypatch.setattr(cc, "file_lock", lambda *_a, **_k: _NullLock())
        return cfg

    _install.saved = saved  # type: ignore[attr-defined]
    return _install


class _NullLock:
    def __enter__(self) -> _NullLock:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _illegal_selection() -> dict[str, dict[str, CapabilitySelection]]:
    # A model no longer in the catalog — models_for_capability() stubbed to
    # return nothing, so this always classifies as "unknown_model". "embed" is
    # a legal child of the "embed" slot (see _CHILD_TO_CAPABILITY).
    return {"embed": {"embed": CapabilitySelection(backend="cpu", provider="", model="ghost")}}


def test_migrate_default_is_dry_run_and_does_not_write(
    stub_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_config(_illegal_selection())
    monkeypatch.setattr(cc, "models_for_capability", lambda *_a, **_k: [])

    result = runner.invoke(cc.app, ["migrate"])

    assert result.exit_code == 0, result.output
    assert "--dry-run" in result.output
    assert stub_config.saved["cfg"] is None  # never written


def test_migrate_apply_writes(stub_config, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_config(_illegal_selection())
    monkeypatch.setattr(cc, "models_for_capability", lambda *_a, **_k: [])

    result = runner.invoke(cc.app, ["migrate", "--apply"])

    assert result.exit_code == 0, result.output
    assert "migrated" in result.output
    assert stub_config.saved["cfg"] is not None


def test_migrate_deprecated_dry_run_flag_is_still_a_noop_preview(
    stub_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old ``--dry-run`` flag is hidden but harmless — still previews."""
    stub_config(_illegal_selection())
    monkeypatch.setattr(cc, "models_for_capability", lambda *_a, **_k: [])

    result = runner.invoke(cc.app, ["migrate", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert stub_config.saved["cfg"] is None


def test_migrate_nothing_to_do(stub_config, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_config({})
    result = runner.invoke(cc.app, ["migrate"])
    assert result.exit_code == 0, result.output
    assert "nothing to migrate" in result.output


# ── list / set — thin clients over the live API ─────────────────────────────


def test_list_renders_selections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "_api_unreachable", lambda _u: False)
    monkeypatch.setattr(
        cc,
        "api_get",
        lambda path, **_k: {
            "selections": {
                "embed": {
                    "default": {
                        "backend": "cpu",
                        "provider": "",
                        "model": "bge-small",
                        "enabled": True,
                    }
                }
            }
        },
    )
    result = runner.invoke(cc.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "bge-small" in result.output


def test_list_renders_status_column(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1905: the API payload carries a per-selection ``status`` field
    (e.g. "failed" after a botched apply) that the table used to drop
    entirely, leaving a failed apply looking identical to a healthy one."""
    monkeypatch.setattr(cc, "_api_unreachable", lambda _u: False)
    monkeypatch.setattr(
        cc,
        "api_get",
        lambda path, **_k: {
            "selections": {
                "embed": {
                    "default": {
                        "backend": "cpu",
                        "provider": "",
                        "model": "bge-small",
                        "enabled": True,
                        "status": "failed",
                    }
                }
            }
        },
    )
    result = runner.invoke(cc.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "Status" in result.output
    assert "failed" in result.output


def test_list_empty_selections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "_api_unreachable", lambda _u: False)
    monkeypatch.setattr(cc, "api_get", lambda path, **_k: {"selections": {}})
    result = runner.invoke(cc.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "no capability selections" in result.output


def test_set_unknown_slot_dies() -> None:
    result = runner.invoke(cc.app, ["set", "not-a-slot", "default", "--model", "x"])
    assert result.exit_code != 0
    assert "unknown capability slot" in result.output


def test_set_requires_at_least_one_field(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(cc.app, ["set", "embed", "embed"])
    assert result.exit_code != 0
    assert "nothing to set" in result.output


def test_set_posts_only_provided_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc, "_api_unreachable", lambda _u: False)
    captured: dict[str, Any] = {}

    def fake_post(path: str, *, json: Any = None, **_k: Any) -> dict:
        captured["path"] = path
        captured["json"] = json
        return {"ok": True, "selection": json}

    monkeypatch.setattr(cc, "api_post", fake_post)
    result = runner.invoke(cc.app, ["set", "embed", "embed", "--model", "bge-small"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/capabilities/embed/embed"
    assert captured["json"] == {"model": "bge-small"}


# ── #1832: `capability set` blocks on the slot state machine ────────────────
#
# ``POST /api/capabilities/{slot}/{child}`` is not a config write that returns
# immediately. ``routes/capabilities.py`` awaits ``CapabilityOrchestrator.apply``,
# which awaits ``SlotManager.load`` (off→on), ``unload`` (on→off) or ``swap``
# (model/backend change while on) *inline*, holding the response open until the
# slot converges. On ``api_post``'s 10s default that is #1832 verbatim: the CLI
# raises ReadTimeout and exits non-zero while the capability change succeeds.
#
# As in ``tests/cli/test_slot_commands.py``, the floor is computed from the
# SERVER modules, never from the client constant under test — comparing the
# client budget against the constant it was derived from would be a tautology.

#: See ``tests/cli/test_slot_commands.py`` — ``preload_evict.admit`` runs one
#: sequential ``host.unload`` per evicted candidate inside the load path; two
#: is the modest case the client budget must at minimum cover.
_MIN_EVICTION_UNLOADS = 2


def _server_worst_case_s(*, loads: int = 1, unloads: int = 1) -> float:
    from hal0.providers.container import _HEALTH_TIMEOUT_S
    from hal0.slots.manager import SlotManager

    terminate = float(SlotManager._terminate_timeout_s)
    load_phase = loads * (float(_HEALTH_TIMEOUT_S) + _MIN_EVICTION_UNLOADS * terminate)
    return load_phase + unloads * terminate


def _capture_set(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(cc, "_api_unreachable", lambda _u: False)
    captured: dict[str, Any] = {}

    def fake_post(path: str, *, json: Any = None, **kw: Any) -> dict:
        captured["path"] = path
        captured["json"] = json
        captured["kwargs"] = kw
        return {"ok": True, "selection": json}

    monkeypatch.setattr(cc, "api_post", fake_post)
    return captured


@pytest.mark.parametrize(
    "argv",
    [
        # off→on: the orchestrator ensures the slot exists then awaits load.
        ["set", "embed", "embed", "--enabled"],
        # on→off: the orchestrator awaits unload.
        ["set", "embed", "embed", "--disabled"],
        # model change while on: the orchestrator awaits swap (unload-then-load).
        ["set", "embed", "embed", "--model", "bge-small"],
    ],
)
def test_capability_set_passes_the_lifecycle_timeout(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    captured = _capture_set(monkeypatch)
    result = runner.invoke(cc.app, argv)
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/capabilities/embed/embed"

    # One request cannot know which branch the server will take, so the single
    # budget it sends has to clear the worst one: swap, i.e. unload-then-load.
    floor = _server_worst_case_s(loads=1, unloads=1)
    kwargs = captured["kwargs"]
    assert "timeout" in kwargs, "capability set passed no explicit timeout kwarg"
    assert kwargs["timeout"] >= floor, (
        f"timeout={kwargs['timeout']} is under the server's {floor}s worst case "
        f"(the orchestrator's swap branch)"
    )


# ── #1974: migrate and the FLM-image probe ──────────────────────────────────


def _probe_recorder(
    monkeypatch: pytest.MonkeyPatch, *, npu: bool, answer: bool | None
) -> list[str]:
    """Real catalog path; record every FLM-image probe instead of shelling podman."""
    import types

    import hal0.providers.container as container_mod
    import hal0.providers.flm as flm_mod
    from hal0.capabilities import catalog

    calls: list[str] = []

    class _Provider:
        def image_present(self, image: str) -> bool | None:
            calls.append(image)
            return answer

    monkeypatch.setattr(container_mod, "container_provider", lambda: _Provider())
    monkeypatch.setattr(
        catalog,
        "load_hardware_info",
        lambda: types.SimpleNamespace(npu=types.SimpleNamespace(present=npu), gpus=[]),
    )
    monkeypatch.setattr(
        flm_mod,
        "flm_served_models",
        lambda: [
            {
                "tag": "embed-gemma:300m",
                "capabilities": ["embed"],
                "installed": True,
                "size_bytes": 300_000_000,
                "footprint_gb": 0.6,
                "family": "embed-gemma",
            }
        ],
    )
    monkeypatch.setattr(catalog, "_flm_last_definitive", None)
    catalog.reset_flm_image_present_cache()
    return calls


def _settle_and_reset() -> None:
    from hal0.capabilities import catalog

    if catalog._flm_probe_thread is not None:
        catalog._flm_probe_thread.join(timeout=5)
    catalog.reset_flm_image_present_cache()


def test_migrate_on_a_non_npu_host_never_probes_the_flm_image(
    stub_config, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
) -> None:
    """The catalog consults the FLM-image probe only behind NPU presence, and
    ``migrate`` adds no probe of its own, so CPU/GPU-only hosts never probe."""
    calls = _probe_recorder(monkeypatch, npu=False, answer=True)
    stub_config(_illegal_selection())

    result = runner.invoke(cc.app, ["migrate"])
    _settle_and_reset()

    assert result.exit_code == 0, result.output
    assert calls == []


@pytest.mark.parametrize("answer", [False, None, True])
def test_migrate_verdict_does_not_depend_on_the_flm_image_probe(
    stub_config, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str, answer: bool | None
) -> None:
    """Why ``migrate`` needs no settled FLM-image answer: NPU catalog rows come
    from ``flm list`` (``flm_served_models``), never from the image probe, so an
    absent or unanswerable probe leaves an NPU selection legal."""
    from hal0.capabilities import catalog

    _probe_recorder(monkeypatch, npu=True, answer=answer)
    catalog.prime_flm_image_probe(timeout=5)  # land this probe answer first
    stub_config(_npu_selection())

    result = runner.invoke(cc.app, ["migrate", "--apply"])
    verdict = cc._classify_pair("embed", "embed-gemma:300m", "npu", None)
    _settle_and_reset()

    assert result.exit_code == 0, result.output
    assert stub_config.saved["cfg"] is None, "an NPU selection was rewritten"
    assert verdict == ("ok", ["npu"])


def _npu_selection() -> dict[str, dict[str, CapabilitySelection]]:
    return {
        "embed": {
            "embed": CapabilitySelection(device="npu", provider="flm", model="embed-gemma:300m")
        }
    }
