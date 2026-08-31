"""Install-time brain-model provisioning (v1.0, Stream A).

Covers the three things that can silently break the platform steward:

* the quant choice — a Q8/Q4 build carries custom GGML tensor type ids that
  stock llama.cpp REJECTS, so pulling one onto a CPU-only box means a gigabyte
  downloaded for a container that can never start;
* the pullability of whatever id gets chosen. (The old ``brain.toml`` pinned
  ``MiniCPM5-1B-Agentic-Tooluse`` — a real, anonymously-pullable model, but one
  the SHIPPED curated catalogue does not define, so a fresh box could not
  resolve it. Whatever the installer picks must be resolvable from code
  alone.); and
* the fail-soft contract — a failed pull must leave the slot MODEL-LESS and
  never abort the install.

The pull itself is stubbed: ``run_pull`` is the unit under test elsewhere
(``tests/registry/test_pull.py``) and no test should reach out to
huggingface.co.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hal0.config.schema import GPUInfo, HardwareInfo
from hal0.install.brain_model import (
    BRAIN_HF_REPO,
    BRAIN_MODEL_DEFAULT,
    BRAIN_MODEL_IDS,
    BRAIN_MODEL_PORTABLE,
    BRAIN_MODEL_ROCMFPX,
    BRAIN_MODEL_SMALL,
    BRAIN_SLOT_NAME,
    already_pulled,
    bind_brain_model,
    brain_model_for_hardware,
    check_binding,
    current_brain_binding,
    main,
    provision_brain_model,
    remediation_command,
    rocmfpx_capable,
)
from hal0.registry.curated import get_curated

# ── quant choice ────────────────────────────────────────────────────────────


def _hw(*, platform: str = "generic", gpus: list[GPUInfo] | None = None) -> HardwareInfo:
    return HardwareInfo(platform=platform, gpus=gpus or [])


def test_cpu_only_box_gets_the_default_lfm_build() -> None:
    """No GPU → still the LFM default: plain Q8_0 GGUF, no custom tensor
    types, so a stock llama.cpp image loads it fine. (Under the sft lineage
    this box HAD to divert to F16 — that split is now override-only.)
    """
    hw = _hw()
    assert rocmfpx_capable(hw) is False
    assert brain_model_for_hardware(hw) == BRAIN_MODEL_DEFAULT


def test_strix_halo_box_gets_the_default_lfm_build() -> None:
    hw = _hw(platform="strix-halo")
    assert rocmfpx_capable(hw) is True
    assert brain_model_for_hardware(hw) == BRAIN_MODEL_DEFAULT


def test_compute_capable_gpu_box_gets_the_default_lfm_build() -> None:
    hw = _hw(gpus=[GPUInfo(vendor="amd", compute_capable=True)])
    assert brain_model_for_hardware(hw) == BRAIN_MODEL_DEFAULT


def test_vulkan_only_gpu_box_gets_the_default_lfm_build() -> None:
    hw = _hw(gpus=[GPUInfo(vendor="amd", vulkan_capable=True)])
    assert brain_model_for_hardware(hw) == BRAIN_MODEL_DEFAULT


def test_override_selects_a_known_variant() -> None:
    hw = _hw(platform="strix-halo")
    assert brain_model_for_hardware(hw, override=BRAIN_MODEL_SMALL) == BRAIN_MODEL_SMALL
    # Whitespace from an env var is tolerated.
    assert brain_model_for_hardware(hw, override=f"  {BRAIN_MODEL_PORTABLE} ") == (
        BRAIN_MODEL_PORTABLE
    )


class TestHardwareDecisionMatrix:
    """The selection matrix from hal0#1790, updated for the LFM default:
    gpu-rocm / gpu-vulkan / cpu-only all land on ``BRAIN_MODEL_DEFAULT`` —
    plain Q8_0 GGUF loads on the FPX runner and stock llama.cpp alike, so
    hardware no longer forks the pick. ``rocmfpx_capable`` assertions stay:
    other callers (``install.agent_model``) still fork on it.
    """

    def test_gpu_rocm_box_selects_the_default(self) -> None:
        hw = _hw(gpus=[GPUInfo(vendor="amd", compute_capable=True, vulkan_capable=False)])
        assert rocmfpx_capable(hw) is True
        assert brain_model_for_hardware(hw) == BRAIN_MODEL_DEFAULT

    def test_gpu_vulkan_box_selects_the_default(self) -> None:
        hw = _hw(gpus=[GPUInfo(vendor="amd", compute_capable=False, vulkan_capable=True)])
        assert rocmfpx_capable(hw) is True
        assert brain_model_for_hardware(hw) == BRAIN_MODEL_DEFAULT

    def test_cpu_only_box_selects_the_default(self) -> None:
        hw = _hw(gpus=[])
        assert rocmfpx_capable(hw) is False
        assert brain_model_for_hardware(hw) == BRAIN_MODEL_DEFAULT

    def test_gpu_present_but_neither_compute_nor_vulkan_capable_selects_the_default(
        self,
    ) -> None:
        """hal0#1790's exact shape: an AMD GPU row IS present in ``hw.gpus``
        (sysfs saw it) but both capability flags are False — the GPU-less LXC
        case. Under the sft lineage the capability split decided F16 vs Q8
        here; with the LFM default the answer is the same id either way, and
        this test pins that the capability probe still returns False (the
        agent-model chooser depends on it).
        """
        hw = _hw(
            platform="lxc",
            gpus=[GPUInfo(vendor="amd", compute_capable=False, vulkan_capable=False)],
        )
        assert rocmfpx_capable(hw) is False
        assert brain_model_for_hardware(hw) == BRAIN_MODEL_DEFAULT


def test_unknown_override_falls_back_instead_of_404ing() -> None:
    """An off-catalogue HAL0_BRAIN_MODEL must not become a doomed pull.

    ``MiniCPM5-1B-Agentic-Tooluse`` is deliberately the example: it exists
    upstream and is anonymously pullable, but the shipped catalogue has no row
    for it, so ``get_curated`` returns None and the installer would have no
    coordinates. Falling back beats attempting it.
    """
    hw = _hw()
    assert brain_model_for_hardware(hw, override="MiniCPM5-1B-Agentic-Tooluse") == (
        BRAIN_MODEL_DEFAULT
    )


# ── pullability ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("model_id", BRAIN_MODEL_IDS)
def test_every_brain_variant_is_pullable_from_a_public_repo(model_id: str) -> None:
    """Each declared variant must resolve to real HF coordinates in a PUBLIC
    repo: the LFM default from LiquidAI's GGUF repo, the sft overrides from
    ``BRAIN_HF_REPO`` (the ``Hal0ai/hal0-brain-sft`` base repo is private and
    safetensors-only — no chat runner consumes safetensors)."""
    curated = get_curated(model_id)
    assert curated is not None, f"{model_id!r} missing from CURATED_MODELS"
    if model_id == BRAIN_MODEL_DEFAULT:
        assert curated.hf_repo == "LiquidAI/LFM2.5-2.6B-GGUF"
    else:
        assert curated.hf_repo == BRAIN_HF_REPO
    assert curated.hf_file.endswith(".gguf")
    assert curated.capability == "chat"


def test_every_variant_is_a_distinct_file() -> None:
    files = {get_curated(m).hf_file for m in BRAIN_MODEL_IDS}  # type: ignore[union-attr]
    assert len(files) == len(BRAIN_MODEL_IDS)


# ── provisioning ────────────────────────────────────────────────────────────


class _FakeRegistry:
    def __init__(self) -> None:
        self.ensured: list[str] = []

    def ensure(self, model_id: str) -> None:
        self.ensured.append(model_id)


class _FakeSlotManager:
    """Records the config writes ``run_pull_and_activate`` performs."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, dict[str, Any]]] = []

    async def update_config(self, slot: str, updates: dict[str, Any]) -> None:
        self.updates.append((slot, updates))


def _stub_run_pull(monkeypatch: pytest.MonkeyPatch, *, state: str, exc: Exception | None = None):
    """Replace ``registry.pull.run_pull`` with a no-network stub.

    ``run_pull_and_activate`` imports it lazily from the module, so patching the
    module attribute is what lands.
    """
    import hal0.registry.pull as pull_mod

    async def _fake(job, **kwargs):
        if exc is not None:
            raise exc
        job.state = state

    monkeypatch.setattr(pull_mod, "run_pull", _fake)


def test_successful_pull_binds_the_model_to_the_brain_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_run_pull(monkeypatch, state="completed")
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    landed = asyncio.run(
        provision_brain_model(hw=_hw(platform="strix-halo"), slot_manager=sm, registry=reg)
    )
    assert landed == BRAIN_MODEL_DEFAULT
    assert reg.ensured == [BRAIN_MODEL_DEFAULT]
    assert sm.updates == [(BRAIN_SLOT_NAME, {"model": {"default": BRAIN_MODEL_DEFAULT}})]


def test_failed_pull_leaves_the_slot_model_less_and_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ruling 7: warn, seed model-less, install still succeeds.

    ``run_pull_and_activate`` must NOT stamp ``[model].default`` — writing an id
    for bytes that never landed is the start-before-model race (#1108), and
    since model-presence IS activation (#1369) withholding it parks the slot.
    """
    _stub_run_pull(monkeypatch, state="failed")
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    with pytest.raises(RuntimeError):
        asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    assert sm.updates == [(BRAIN_SLOT_NAME, {"meta": {"pull_failed": True}})]
    assert all("model" not in upd for _, upd in sm.updates)


def test_raising_pull_also_leaves_the_slot_model_less(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_run_pull(monkeypatch, state="running", exc=OSError("network is unreachable"))
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    with pytest.raises(OSError, match="unreachable"):
        asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    assert sm.updates == [(BRAIN_SLOT_NAME, {"meta": {"pull_failed": True}})]


# ── the install.sh entry point never raises ─────────────────────────────────


def test_main_returns_nonzero_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """install.sh runs under ``set -euo pipefail`` behind a ``|| warn``.

    A traceback escaping ``main()`` would abort the whole install over an
    optional model — exactly what ruling 7 forbids. So EVERY failure must come
    back as exit code 1.
    """
    import hal0.install.brain_model as bm

    def _boom() -> None:
        raise RuntimeError("hardware probe exploded")

    monkeypatch.setattr(bm, "_load_hardware", _boom)
    assert main() == 1


def test_main_returns_zero_on_a_successful_pull(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.install.brain_model as bm

    monkeypatch.setattr(bm, "_load_hardware", lambda: _hw(platform="strix-halo"))
    monkeypatch.setattr(
        "hal0.cli.setup_command._build_offline_deps",
        lambda: (_FakeSlotManager(), _FakeRegistry()),
    )
    _stub_run_pull(monkeypatch, state="completed")
    assert main() == 0


# ── re-run idempotence: never re-download what is already on disk ───────────
#
# Neither run_pull nor run_pull_and_activate checks the destination before
# streaming, and a COMPLETED pull leaves no `.part` for the resume path to find
# — so without `already_pulled` every `install.sh` re-run re-downloads the
# whole model, contradicting the documented "re-running install.sh is safe"
# contract. These pin the guard AND its deliberate limits.


@pytest.fixture()
def store(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point the pull-destination resolver at a tmp store."""
    import hal0.config.store as store_mod

    monkeypatch.setattr(store_mod, "store_root", lambda: tmp_path)
    return tmp_path


def _place(store_root, model_id: str, *, meta: dict | None = None, body: bytes = b"weights"):
    """Write a model file (and its meta.json sidecar) where a pull would."""
    import json

    from hal0.registry.pull import _final_path_for_entry

    curated = get_curated(model_id)
    assert curated is not None
    dest = _final_path_for_entry(model_id, curated.hf_file, None, "chat")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    if meta is None:
        meta = {
            "curated_id": model_id,
            "hf_repo": curated.hf_repo,
            "hf_file": curated.hf_file,
            "sha256": None,
            "size_bytes": len(body),
            "quant": None,
            "capability": "chat",
        }
    if meta is not False:  # type: ignore[comparison-overlap]
        (dest.parent / "meta.json").write_text(json.dumps(meta))
    return dest


def test_nothing_on_disk_is_not_already_pulled(store) -> None:
    assert already_pulled(BRAIN_MODEL_PORTABLE) is None


def test_a_complete_prior_pull_is_recognised(store) -> None:
    dest = _place(store, BRAIN_MODEL_PORTABLE)
    assert already_pulled(BRAIN_MODEL_PORTABLE) == dest


def test_a_file_with_no_sidecar_is_not_trusted(store) -> None:
    """Presence alone proves nothing about provenance — re-pull."""
    _place(store, BRAIN_MODEL_PORTABLE, meta=False)  # type: ignore[arg-type]
    assert already_pulled(BRAIN_MODEL_PORTABLE) is None


def test_a_truncated_file_re_pulls(store) -> None:
    """The failure this guard must never create: activating a slot on a
    half-written file, which then crash-loops the container."""
    _place(
        store,
        BRAIN_MODEL_PORTABLE,
        meta={
            "curated_id": BRAIN_MODEL_PORTABLE,
            "hf_file": get_curated(BRAIN_MODEL_PORTABLE).hf_file,
            "size_bytes": 999_999,
        },
    )
    assert already_pulled(BRAIN_MODEL_PORTABLE) is None


def test_a_sidecar_from_a_different_model_re_pulls(store) -> None:
    _place(store, BRAIN_MODEL_PORTABLE, meta={"curated_id": "something-else", "hf_file": "x.gguf"})
    assert already_pulled(BRAIN_MODEL_PORTABLE) is None


def test_a_malformed_sidecar_re_pulls(store) -> None:
    from hal0.registry.pull import _final_path_for_entry

    curated = get_curated(BRAIN_MODEL_PORTABLE)
    assert curated is not None
    _place(store, BRAIN_MODEL_PORTABLE)
    dest = _final_path_for_entry(BRAIN_MODEL_PORTABLE, curated.hf_file, None, "chat")
    (dest.parent / "meta.json").write_text("{not json")
    assert already_pulled(BRAIN_MODEL_PORTABLE) is None


def test_an_unknown_id_is_never_already_pulled(store) -> None:
    assert already_pulled("no-such-curated-id") is None


def test_a_variant_on_disk_does_not_satisfy_a_sibling(store) -> None:
    """Documented limit: the store is id-addressed, not content-addressed.

    A box carrying the F16 build does NOT satisfy a Q8 pull — different id,
    different directory, genuinely different bytes. The same mechanic is why a
    pre-existing `hal0-brain-sft` row does not satisfy `hal0-brain-sft-f16`
    even though the artefacts are the same size.
    """
    _place(store, BRAIN_MODEL_PORTABLE)
    assert already_pulled(BRAIN_MODEL_ROCMFPX) is None


def test_provisioning_reuses_an_existing_pull_without_downloading(
    store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: bind the slot, touch the network zero times."""
    import hal0.registry.pull as pull_mod

    async def _explode(job, **kwargs):
        raise AssertionError("run_pull must not be called when the file is already on disk")

    monkeypatch.setattr(pull_mod, "run_pull", _explode)
    _place(store, BRAIN_MODEL_DEFAULT)
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    landed = asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    assert landed == BRAIN_MODEL_DEFAULT
    assert sm.updates == [(BRAIN_SLOT_NAME, {"model": {"default": BRAIN_MODEL_DEFAULT}})]


def test_provisioning_still_pulls_when_the_prior_file_is_untrustworthy(
    store, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_pull(monkeypatch, state="completed")
    _place(store, BRAIN_MODEL_DEFAULT, meta=False)  # type: ignore[arg-type]
    sm, reg = _FakeSlotManager(), _FakeRegistry()
    landed = asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    assert landed == BRAIN_MODEL_DEFAULT


# ── #2131: the 0.9.8 → 1.0.0 upgrade — model on disk, slot never bound ──────
#
# The documented upgrade path ended at "Verify FAILED: structured-output probe
# failed" on every stable-channel box: the 2.87 GB default landed and was
# registered, but `/etc/hal0/slots/brain.toml` kept a `[model]` table naming
# nothing, so the gateway had nothing to answer with. The seeding pass reported
# success throughout — `_activate_slot_model` wraps its write in
# `contextlib.suppress(Exception)`, so a write that never landed is
# indistinguishable from one that did.
#
# The semantics these pin: an existing-but-UNBOUND `[model]` table is the
# shipped seed state, not operator config, so the default is bound into it; a
# NON-EMPTY `[model].default` is an operator pick and is never touched; and a
# binding that genuinely cannot be made is REPORTED (exit 1 + the exact
# remediation command) instead of reported as success.


class _ConfiguredSlotManager:
    """A slot manager backed by one in-memory brain config.

    Unlike :class:`_FakeSlotManager` it can be READ, which is what the binding
    contract turns on: "" (an empty ``[model].default``) is the seed state,
    a non-empty one is an operator pick.
    """

    def __init__(self, bound: str = "", *, persist: bool = True) -> None:
        self.cfg: dict[str, Any] = {
            "name": BRAIN_SLOT_NAME,
            "type": "llm",
            "device": "gpu-vulkan",
            "port": 8089,
            "model": {"default": bound, "context_size": 65536},
        }
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self._persist = persist

    async def get_config(self, slot: str) -> dict[str, Any]:
        assert slot == BRAIN_SLOT_NAME
        return {**self.cfg, "model": dict(self.cfg["model"])}

    async def update_config(self, slot: str, updates: dict[str, Any]) -> None:
        self.updates.append((slot, updates))
        if not self._persist:  # a write that silently does not land
            return
        model = updates.get("model")
        if isinstance(model, dict):
            self.cfg["model"] = {**self.cfg["model"], **model}


class _WriteFailsSlotManager(_ConfiguredSlotManager):
    """Every config write raises — a read-only /etc, a lock we lost, a
    validation refusal on a config shape this release no longer accepts."""

    async def update_config(self, slot: str, updates: dict[str, Any]) -> None:
        self.updates.append((slot, updates))
        raise RuntimeError("failed to rewrite /etc/hal0/slots/brain.toml")


def test_an_unbound_model_table_is_seed_state_and_gets_the_default_bound(
    store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact #2131 shape: brain.toml exists, `[model]` names nothing, and
    the bytes are already on disk (the upgrade re-run / already-pulled path)."""
    _place(store, BRAIN_MODEL_DEFAULT)
    sm, reg = _ConfiguredSlotManager(bound=""), _FakeRegistry()
    landed = asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    assert landed == BRAIN_MODEL_DEFAULT
    assert sm.cfg["model"]["default"] == BRAIN_MODEL_DEFAULT
    # Binding is a merge, never a reset of the slot's tuning.
    assert sm.cfg["model"]["context_size"] == 65536


def test_an_unbound_model_table_gets_bound_on_the_pull_path_too(
    store, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_pull(monkeypatch, state="completed")
    sm, reg = _ConfiguredSlotManager(bound=""), _FakeRegistry()
    landed = asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    assert landed == BRAIN_MODEL_DEFAULT
    assert sm.cfg["model"]["default"] == BRAIN_MODEL_DEFAULT


def test_an_operator_set_default_is_never_overwritten(
    store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the rule. A non-empty `[model].default` is a pick —
    a re-run of install.sh must not revert it to the shipped default."""
    _place(store, BRAIN_MODEL_DEFAULT)
    sm, reg = _ConfiguredSlotManager(bound="my-own-brain"), _FakeRegistry()
    landed = asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    assert landed == BRAIN_MODEL_DEFAULT  # the pull still reports what it has
    assert sm.cfg["model"]["default"] == "my-own-brain"
    assert sm.updates == [], "an operator's pick must not be written over"


def test_an_operator_set_default_survives_the_pull_path(
    store, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_pull(monkeypatch, state="completed")
    sm, reg = _ConfiguredSlotManager(bound="my-own-brain"), _FakeRegistry()
    asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    assert sm.cfg["model"]["default"] == "my-own-brain"
    assert sm.updates == []


def test_binding_is_idempotent_across_re_runs(store, monkeypatch: pytest.MonkeyPatch) -> None:
    _place(store, BRAIN_MODEL_DEFAULT)
    sm, reg = _ConfiguredSlotManager(bound=""), _FakeRegistry()
    asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    first = len(sm.updates)
    asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))
    assert len(sm.updates) == first, "a converged slot must be re-read, not re-written"
    assert sm.cfg["model"]["default"] == BRAIN_MODEL_DEFAULT


def test_a_binding_write_that_fails_is_reported_not_swallowed(
    store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #2131 silence. Before this, the suppressed write left the slot
    model-less while the module printed "brain model ready" and exited 0."""
    _place(store, BRAIN_MODEL_DEFAULT)
    sm, reg = _WriteFailsSlotManager(bound=""), _FakeRegistry()
    with pytest.raises(RuntimeError, match="could not be bound"):
        asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))


def test_a_binding_write_that_does_not_land_is_reported_not_swallowed(
    store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that raises nothing but changes nothing is the same failure —
    "it did not raise" is not proof the slot is bound."""
    _place(store, BRAIN_MODEL_DEFAULT)
    sm, reg = _ConfiguredSlotManager(bound="", persist=False), _FakeRegistry()
    with pytest.raises(RuntimeError, match="could not be bound"):
        asyncio.run(provision_brain_model(hw=_hw(), slot_manager=sm, registry=reg))


def test_an_unbindable_slot_exits_nonzero_with_the_remediation(
    store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """install.sh's ``|| warn`` needs a non-zero exit to fire at all, and the
    operator needs the command that recovers the box (ruling 7: still not
    fatal — the install continues)."""
    import hal0.install.brain_model as bm

    _place(store, BRAIN_MODEL_DEFAULT)
    monkeypatch.setattr(bm, "_load_hardware", lambda: _hw())
    monkeypatch.setattr(
        "hal0.cli.setup_command._build_offline_deps",
        lambda: (_WriteFailsSlotManager(bound=""), _FakeRegistry()),
    )
    assert main([]) == 1
    err = capsys.readouterr().err
    assert f"hal0 slot edit {BRAIN_SLOT_NAME} --model {BRAIN_MODEL_DEFAULT}" in err
    assert f"hal0 slot load {BRAIN_SLOT_NAME}" in err


def test_main_reports_the_model_the_slot_actually_names(
    store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A transcript that says "bound to the 'brain' slot" while the slot names
    the operator's own model is exactly the misreport #2131 was made of."""
    import hal0.install.brain_model as bm

    _place(store, BRAIN_MODEL_DEFAULT)
    monkeypatch.setattr(bm, "_load_hardware", lambda: _hw())
    monkeypatch.setattr(
        "hal0.cli.setup_command._build_offline_deps",
        lambda: (_ConfiguredSlotManager(bound="my-own-brain"), _FakeRegistry()),
    )
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "keeps its existing model my-own-brain" in out


# ── the binding helpers, directly ───────────────────────────────────────────


def test_current_binding_distinguishes_unbound_from_unreadable() -> None:
    """`""` (readable, unbound → bindable) must never be confused with `None`
    (unreadable → we know nothing, so a write is trusted)."""
    assert asyncio.run(current_brain_binding(_ConfiguredSlotManager(bound=""))) == ""
    assert asyncio.run(current_brain_binding(_ConfiguredSlotManager(bound="x"))) == "x"
    assert asyncio.run(current_brain_binding(_FakeSlotManager())) is None


def test_bind_trusts_a_clean_write_on_an_unreadable_slot() -> None:
    """A slot manager with no read surface must not turn "cannot check" into
    "did not work" — that would fail every install for a missing accessor."""
    sm = _FakeSlotManager()
    assert asyncio.run(bind_brain_model(sm, BRAIN_MODEL_DEFAULT)) == BRAIN_MODEL_DEFAULT
    assert sm.updates == [(BRAIN_SLOT_NAME, {"model": {"default": BRAIN_MODEL_DEFAULT}})]


def test_bind_does_not_restamp_an_unreadable_slot_the_gate_already_stamped() -> None:
    """The pull path's activation gate writes first. When the slot cannot be
    read there is no evidence to act on, so re-writing would just be a second
    identical stamp — the readable case is where the repair earns its keep."""
    sm = _FakeSlotManager()
    bound = asyncio.run(bind_brain_model(sm, BRAIN_MODEL_DEFAULT, already_stamped=True))
    assert bound == BRAIN_MODEL_DEFAULT
    assert sm.updates == []


def test_remediation_command_names_the_id_that_was_pulled() -> None:
    assert remediation_command(BRAIN_MODEL_ROCMFPX).startswith(
        f"hal0 slot edit {BRAIN_SLOT_NAME} --model {BRAIN_MODEL_ROCMFPX}"
    )


# ── --check-binding: the hint install.sh prints beside a failed probe ────────


def _stub_check_binding_deps(monkeypatch: pytest.MonkeyPatch, slot_manager) -> None:
    import hal0.install.brain_model as bm

    monkeypatch.setattr(bm, "_load_hardware", lambda: _hw())
    monkeypatch.setattr(
        "hal0.cli.setup_command._build_offline_deps",
        lambda: (slot_manager, _FakeRegistry()),
    )


def test_check_binding_names_the_fix_when_the_model_is_on_disk_but_unbound(
    store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _place(store, BRAIN_MODEL_DEFAULT)
    _stub_check_binding_deps(monkeypatch, _ConfiguredSlotManager(bound=""))
    assert check_binding([]) == 0
    out = capsys.readouterr().out
    assert remediation_command(BRAIN_MODEL_DEFAULT) in out


def test_check_binding_is_silent_when_the_slot_is_bound(
    store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Empty stdout is the contract — install.sh splices the hint in with a
    plain `[[ -n ... ]]`, so a bound slot must add nothing to the transcript."""
    _place(store, BRAIN_MODEL_DEFAULT)
    _stub_check_binding_deps(monkeypatch, _ConfiguredSlotManager(bound=BRAIN_MODEL_DEFAULT))
    assert check_binding([]) == 0
    assert capsys.readouterr().out == ""


def test_check_binding_is_silent_when_no_model_is_on_disk(
    store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No bytes means the unbound slot is not the story — a failed pull has
    already printed its own warning."""
    _stub_check_binding_deps(monkeypatch, _ConfiguredSlotManager(bound=""))
    assert check_binding([]) == 0
    assert capsys.readouterr().out == ""


def test_check_binding_never_fails_the_installer(
    store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """It runs while install.sh is ALREADY reporting a failure, under
    `set -euo pipefail`. A diagnostic that aborts the install it is explaining
    is worse than no diagnostic."""
    import hal0.install.brain_model as bm

    def _boom() -> None:
        raise RuntimeError("hardware probe exploded")

    monkeypatch.setattr(bm, "_load_hardware", _boom)
    assert check_binding([]) == 0
    assert capsys.readouterr().out == ""


def test_module_entry_point_routes_the_check_binding_flag(
    store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """install.sh invokes it as `python -m hal0.install.brain_model
    --check-binding`, which lands in ``main`` — it must not run a pull."""
    import hal0.install.brain_model as bm
    import hal0.registry.pull as pull_mod

    async def _explode(job, **kwargs):
        raise AssertionError("--check-binding must never pull")

    monkeypatch.setattr(pull_mod, "run_pull", _explode)
    _place(store, BRAIN_MODEL_DEFAULT)
    _stub_check_binding_deps(monkeypatch, _ConfiguredSlotManager(bound=""))
    assert main([bm.CHECK_BINDING_FLAG]) == 0
    assert remediation_command(BRAIN_MODEL_DEFAULT) in capsys.readouterr().out


# ── end to end on a real slot TOML: the shape the box actually had ───────────


def test_the_upgrade_shape_binds_against_a_real_slot_manager(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fakes cannot prove the TOML on disk changed. This drives the REAL
    SlotManager over a real `/etc/hal0/slots/brain.toml` in the 0.9.8 shape
    the reporter found: `[model]` present, nothing bound."""
    import hal0.config.store as store_mod

    home = tmp_path / "home"
    slots_dir = home / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True)
    (slots_dir / "brain.toml").write_text(
        'name = "brain"\n'
        'type = "llm"\n'
        'device = "gpu-vulkan"\n'
        'runtime = "container"\n'
        'profile = "vulkan"\n'
        "port = 8089\n"
        "\n"
        "[model]\n"
        "context_size = 65536\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HAL0_HOME", str(home))
    monkeypatch.setattr(store_mod, "store_root", lambda: tmp_path / "store")
    _place(tmp_path / "store", BRAIN_MODEL_DEFAULT)  # bytes already on disk

    from hal0.cli.setup_command import _build_offline_deps

    slot_manager, registry = _build_offline_deps()
    landed = asyncio.run(
        provision_brain_model(hw=_hw(), slot_manager=slot_manager, registry=registry)
    )
    assert landed == BRAIN_MODEL_DEFAULT

    import tomllib

    raw = tomllib.loads((slots_dir / "brain.toml").read_text(encoding="utf-8"))
    assert raw["model"]["default"] == BRAIN_MODEL_DEFAULT
    assert raw["model"]["context_size"] == 65536, "binding must not reset the slot's tuning"
