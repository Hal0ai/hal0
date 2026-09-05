"""Downloaded gate for capability apply (#2026).

rc-validate register key ``capability-apply-skips-model-pull``: a
capability apply naming a catalog row whose weights are NOT on disk
(``downloaded: false``) used to go straight to ``SlotManager.load`` —
llama-server was spawned with the bare model id as ``--model``, died
with "failed to open GGUF file", the apply blocked for the full
~180 s crash-loop dwell, and then answered HTTP 200 ``"warming"``.

These tests pin the fixed contract:

  - enabled selection + undownloaded catalog row → typed 409
    ``capability.model_not_downloaded`` naming the
    ``POST /api/models/{id}/pull`` remediation, raised BEFORE anything
    is persisted or any lifecycle call fires;
  - unknown model name → 404 ``capability.unknown_model`` (pre-existing,
    pinned here against regressions), never a slot launch;
  - downloaded row → the happy path is unchanged (slot loads);
  - disabling / staging-while-disabled never trips the gate — you can
    always turn a broken selection off, and picking a model before
    enabling is legal (the gate fires on the enable).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.errors import Conflict, NotFound
from tests.capabilities.test_orchestrator_reconciliation import FakeSlotManager


@pytest.fixture(autouse=True)
def _no_spawn_context_refresh(monkeypatch):
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)


def _catalog_rows(*, downloaded: bool, pullable: bool = True) -> list[dict[str, Any]]:
    """One embed model advertised on gpu-vulkan, shaped like models_for_capability."""
    return [
        {
            "id": "bge-base-en-v1.5-q4_k_m",
            "capabilities": ["embed"],
            "size_gb": 0.1,
            "backends": [
                {
                    "id": "gpu-vulkan",
                    "provider": "llama-server",
                    "downloaded": downloaded,
                    "pullable": pullable,
                }
            ],
        }
    ]


@pytest.fixture
def home(tmp_hal0_home: str) -> Path:
    """Minimal on-disk state: an embed slot TOML, no capabilities.toml."""
    home = Path(tmp_hal0_home)
    slots_dir = home / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "embed.toml").write_text(
        "\n".join(
            [
                'name = "embed"',
                "port = 8082",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "[model]",
                'default = "bge-base-en-v1.5-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return home


def _patch_catalog(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(
        "hal0.capabilities.orchestrator.models_for_capability",
        lambda capability, registry=None: rows,
    )


def _apply_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "enabled": True,
        "backend": "gpu-vulkan",
        "provider": "llama-server",
        "model": "bge-base-en-v1.5-q4_k_m",
    }
    body.update(overrides)
    return body


async def test_enabled_apply_of_undownloaded_row_is_409_and_spawns_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_catalog(monkeypatch, _catalog_rows(downloaded=False, pullable=True))
    fake = FakeSlotManager()
    orch = CapabilityOrchestrator(slot_manager=fake)

    with pytest.raises(Conflict) as exc:
        await orch.apply("embed", "embed", _apply_body())

    assert exc.value.status == 409
    assert exc.value.code == "capability.model_not_downloaded"
    details = exc.value.details
    assert details["model"] == "bge-base-en-v1.5-q4_k_m"
    assert details["downloaded"] is False
    assert details["pullable"] is True
    # The remediation names the pull job endpoint the operator must hit.
    assert details["pull_endpoint"] == "/api/models/bge-base-en-v1.5-q4_k_m/pull"
    assert "/api/models/bge-base-en-v1.5-q4_k_m/pull" in str(exc.value)

    # No lifecycle call fired — most importantly no load/swap/create that
    # would spawn llama-server with the bare model id.
    lifecycle = [c for c in fake.calls if c[0] in {"load", "swap", "create", "unload"}]
    assert lifecycle == [], f"unexpected lifecycle calls: {fake.calls}"

    # Nothing was persisted: the gate rejects before the store commit, so
    # capabilities.toml never materialises and the slot TOML is untouched.
    assert not (home / "etc" / "hal0" / "capabilities.toml").exists()
    with open(home / "etc" / "hal0" / "slots" / "embed.toml", "rb") as f:
        on_disk = tomllib.load(f)
    assert on_disk.get("backend") == "vulkan"


async def test_unpullable_undownloaded_row_409_without_pull_endpoint(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_catalog(monkeypatch, _catalog_rows(downloaded=False, pullable=False))
    fake = FakeSlotManager()
    orch = CapabilityOrchestrator(slot_manager=fake)

    with pytest.raises(Conflict) as exc:
        await orch.apply("embed", "embed", _apply_body())

    assert exc.value.code == "capability.model_not_downloaded"
    assert exc.value.details["pullable"] is False
    assert "pull_endpoint" not in exc.value.details
    assert [c for c in fake.calls if c[0] == "load"] == []


async def test_unknown_model_is_404_and_spawns_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_catalog(monkeypatch, _catalog_rows(downloaded=True))
    fake = FakeSlotManager()
    orch = CapabilityOrchestrator(slot_manager=fake)

    with pytest.raises(NotFound) as exc:
        await orch.apply("embed", "embed", _apply_body(model="no-such-model"))

    assert exc.value.status == 404
    assert exc.value.code == "capability.unknown_model"
    assert [c for c in fake.calls if c[0] in {"load", "swap", "create"}] == []


async def test_downloaded_row_happy_path_unchanged(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_catalog(monkeypatch, _catalog_rows(downloaded=True))
    fake = FakeSlotManager()
    orch = CapabilityOrchestrator(slot_manager=fake)

    result = await orch.apply("embed", "embed", _apply_body())

    loads = [c for c in fake.calls if c[0] == "load"]
    assert loads == [("load", "embed", {"model_id": "bge-base-en-v1.5-q4_k_m"})]
    assert result["enabled"] is True
    assert result["model"] == "bge-base-en-v1.5-q4_k_m"


async def test_disable_never_trips_the_downloaded_gate(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turning a selection off must succeed even when its weights vanished."""
    _patch_catalog(monkeypatch, _catalog_rows(downloaded=False))
    caps_path = home / "etc" / "hal0" / "capabilities.toml"
    caps_path.write_text(
        "\n".join(
            [
                "[selections.embed.embed]",
                'backend = "gpu-vulkan"',
                'provider = "llama-server"',
                'model = "bge-base-en-v1.5-q4_k_m"',
                "enabled = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    fake = FakeSlotManager()
    orch = CapabilityOrchestrator(slot_manager=fake)

    result = await orch.apply("embed", "embed", {"enabled": False})

    assert result["enabled"] is False
    assert [c for c in fake.calls if c[0] == "unload"], "expected an unload"


async def test_staging_model_while_disabled_is_allowed(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Picking an undownloaded model with enabled=false persists (no spawn)."""
    _patch_catalog(monkeypatch, _catalog_rows(downloaded=False))
    fake = FakeSlotManager()
    orch = CapabilityOrchestrator(slot_manager=fake)

    result = await orch.apply("embed", "embed", _apply_body(enabled=False))

    assert result["enabled"] is False
    assert result["model"] == "bge-base-en-v1.5-q4_k_m"
    assert [c for c in fake.calls if c[0] in {"load", "swap"}] == []


def test_npu_backend_is_exempt_from_the_downloaded_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NPU-trio selections toggle the FLM anchor; they never spawn a bare-id
    llama-server, and FLM weights pull via ``flm pull``, not the registry."""
    monkeypatch.setattr(
        "hal0.capabilities.orchestrator.models_for_capability",
        lambda capability, registry=None: [
            {
                "id": "flm-embedder",
                "capabilities": ["embed"],
                "backends": [{"id": "npu", "downloaded": False, "pullable": True}],
            }
        ],
    )
    orch = CapabilityOrchestrator(slot_manager=FakeSlotManager())  # type: ignore[arg-type]
    # Must not raise despite downloaded=False.
    orch._validate_model_in_catalog(
        "embed", "embed", "flm-embedder", "npu", require_downloaded=True
    )


def test_registry_only_model_with_missing_weights_is_409(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The permissive registry fallback still gates on weights-on-disk."""

    class FakeEntry:
        path = str(tmp_path / "gone.gguf")  # never created
        hf_repo = "org/repo"
        hf_filename = "gone.gguf"

    class FakeRegistry:
        def has(self, model_id: str) -> bool:
            return model_id == "user-model"

        def get(self, model_id: str) -> FakeEntry:
            return FakeEntry()

    monkeypatch.setattr(
        "hal0.capabilities.orchestrator.models_for_capability",
        lambda capability, registry=None: [],
    )
    orch = CapabilityOrchestrator(
        slot_manager=FakeSlotManager(),  # type: ignore[arg-type]
        registry=FakeRegistry(),  # type: ignore[arg-type]
    )

    with pytest.raises(Conflict) as exc:
        orch._validate_model_in_catalog(
            "embed", "embed", "user-model", "gpu-vulkan", require_downloaded=True
        )
    assert exc.value.code == "capability.model_not_downloaded"
    assert exc.value.details["pullable"] is True

    # And with the weights actually present the same selection passes.
    present = tmp_path / "here.gguf"
    present.write_bytes(b"gguf")

    class FakeEntryPresent(FakeEntry):
        path = str(present)

    class FakeRegistryPresent(FakeRegistry):
        def get(self, model_id: str) -> FakeEntry:
            return FakeEntryPresent()

    orch2 = CapabilityOrchestrator(
        slot_manager=FakeSlotManager(),  # type: ignore[arg-type]
        registry=FakeRegistryPresent(),  # type: ignore[arg-type]
    )
    orch2._validate_model_in_catalog(
        "embed", "embed", "user-model", "gpu-vulkan", require_downloaded=True
    )
