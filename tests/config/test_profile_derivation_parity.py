"""Parity/regression lock for the device→profile derivations (finding PS-4).

The platform review found three parallel ``(capability, device) → profile``
derivations that drifted apart across #834's churn:

  1. :func:`hal0.install.profile_derive.derive_profile` — the install-flavoured
     resolver (gpu-rocm + dense chat/coder → plain ``chat``).
  2. :data:`hal0.config.schema.DEVICE_DEFAULT_PROFILES` — the canonical, plain
     device-class-representative base table (never MTP).
  3. The verbatim-twin ``_profile_for_fit`` blocks in ``capabilities/catalog.py``
     and ``capabilities/orchestrator.py`` (now both routed through the shared
     :func:`hal0.capabilities.profile_fit.profile_name_for_fit`).

The consolidation in PS-4 is a literal-dedup + shared-helper refactor with ZERO
behaviour change. These tests pin the CURRENT matrix so a future unification
cannot silently drift. Since the 1.0 workload-profile rename (spec-hw-slot-ownership
§10), all three paths resolve to workload-oriented canonical names (chat,
embedding, reranking, cpu-chat, kokoro, qwen3-tts, flm, comfyui).
"""

from __future__ import annotations

import pytest

from hal0.capabilities.catalog import _profile_for_fit as catalog_profile_for_fit
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.capabilities.profile_fit import profile_name_for_fit
from hal0.config.schema import DEVICE_DEFAULT_PROFILES
from hal0.install.profile_derive import derive_profile

# The exact CURRENT (capability, device) → derive_profile matrix. Any change
# here is a behaviour change and must be a deliberate, reviewed edit.
_CAPABILITIES = ("chat", "coder", "embed", "rerank", "utility", "tts", "agent", "image")
_DEVICES = ("gpu-rocm", "gpu-vulkan", "cpu", "npu")

# derive_profile (install path): 1.0 workload-oriented canonical names.
_DERIVE_MATRIX: dict[tuple[str, str], str] = {
    # gpu-rocm — chat/coder/utility/agent/image → chat; embed → embedding;
    # rerank → reranking; tts → chat (tts on GPU goes through fit path).
    ("chat", "gpu-rocm"): "chat",
    ("coder", "gpu-rocm"): "chat",
    ("embed", "gpu-rocm"): "embedding",
    ("rerank", "gpu-rocm"): "reranking",
    ("utility", "gpu-rocm"): "chat",
    ("tts", "gpu-rocm"): "chat",
    ("agent", "gpu-rocm"): "chat",
    ("image", "gpu-rocm"): "chat",
    # gpu-vulkan — same as gpu-rocm (profiles are device-agnostic now).
    ("chat", "gpu-vulkan"): "chat",
    ("coder", "gpu-vulkan"): "chat",
    ("embed", "gpu-vulkan"): "embedding",
    ("rerank", "gpu-vulkan"): "reranking",
    ("utility", "gpu-vulkan"): "chat",
    ("tts", "gpu-vulkan"): "chat",
    ("agent", "gpu-vulkan"): "chat",
    ("image", "gpu-vulkan"): "chat",
    # cpu — embed/rerank take their (device-agnostic) lanes; tts → kokoro;
    # everything else → cpu-chat.
    #
    # #1830 (DELIBERATE change, rc.5 finding): embed/rerank on cpu used to
    # derive ``cpu-chat``, a chat profile that emits no ``--embedding`` /
    # ``--reranking``. The gate dated from the retired per-backend
    # embed/vulkan-embed seeds; the 1.0 seeds are device-agnostic, so a CPU-only
    # box gets them too. While it stood, a CPU-only box's embed/rerank slot
    # reported ``state=ready`` and 501'd its own endpoint.
    ("chat", "cpu"): "cpu-chat",
    ("coder", "cpu"): "cpu-chat",
    ("embed", "cpu"): "embedding",
    ("rerank", "cpu"): "reranking",
    ("utility", "cpu"): "cpu-chat",
    ("tts", "cpu"): "kokoro",
    ("agent", "cpu"): "cpu-chat",
    ("image", "cpu"): "cpu-chat",  # derive_profile always returns a llama-server name on cpu
    # npu — always flm.
    ("chat", "npu"): "flm",
    ("coder", "npu"): "flm",
    ("embed", "npu"): "flm",
    ("rerank", "npu"): "flm",
    ("utility", "npu"): "flm",
    ("tts", "npu"): "flm",
    ("agent", "npu"): "flm",
    ("image", "npu"): "flm",
}


@pytest.mark.parametrize(("capability", "device"), _DERIVE_MATRIX.keys())
def test_derive_profile_matrix_is_pinned(capability: str, device: str) -> None:
    """derive_profile locks the exact current output for every cap x device."""
    assert derive_profile(capability, device) == _DERIVE_MATRIX[(capability, device)]


def test_derive_profile_rocm_dense_chat_coder_and_lanes() -> None:
    # Chat and coder resolve to the canonical chat workload profile;
    # embed/rerank take their dedicated lanes.
    assert derive_profile("chat", "gpu-rocm") == "chat"
    assert derive_profile("coder", "gpu-rocm") == "chat"
    assert derive_profile("embed", "gpu-rocm") == "embedding"
    assert derive_profile("rerank", "gpu-rocm") == "reranking"


def test_derive_profile_cpu_tts_vs_non_tts() -> None:
    # TTS on CPU resolves to kokoro; chat resolves to cpu-chat.
    assert derive_profile("tts", "cpu") == "kokoro"
    assert derive_profile("chat", "cpu") == "cpu-chat"


def test_device_default_profiles_table_is_pinned() -> None:
    """Guards the 1.0 canonical device→profile table (spec-hw-slot-ownership §10)."""
    assert DEVICE_DEFAULT_PROFILES == {
        "gpu-rocm": "chat",
        "gpu-vulkan": "chat",
        "gpu-cuda": "chat",
        "cpu": "cpu-chat",
        "npu": "flm",
    }


@pytest.mark.parametrize("capability", _CAPABILITIES)
@pytest.mark.parametrize("device", _DEVICES)
def test_profile_for_fit_twin_parity(capability: str, device: str, tmp_hal0_home: str) -> None:
    """catalog._profile_for_fit and orchestrator._profile_for_fit resolve to
    the SAME profile name for every cap x device.

    Proves the two blocks are currently identical AND prevents the extracted
    shared helper from regressing only one copy. ``self`` is unused by the
    orchestrator method, so it is invoked unbound with ``None``.
    """
    cat = catalog_profile_for_fit(capability, device)
    orch = CapabilityOrchestrator._profile_for_fit(None, capability, device)
    cat_name = cat.name if cat is not None else None
    orch_name = orch.name if orch is not None else None
    assert cat_name == orch_name


@pytest.mark.parametrize("capability", _CAPABILITIES)
@pytest.mark.parametrize("device", _DEVICES)
def test_profile_for_fit_matches_shared_helper(
    capability: str, device: str, tmp_hal0_home: str
) -> None:
    """The resolved catalog profile name equals the shared helper's name.

    When the shared helper returns a profile name for which no seed entry
    exists, both branches return None (catalog skips it with the
    ``profile_fit_skipped`` log; the fit path treats None as 'no match').
    """
    cat = catalog_profile_for_fit(capability, device)
    expected = profile_name_for_fit(capability, device)
    cat_name = cat.name if cat is not None else None
    # profile_name_for_fit may return a legacy name with no corresponding
    # seed (e.g. ``tts`` on npu): catalog drops those to None, so the
    # twin-parity check above holds; here both branches see None.
    if expected is not None:
        from hal0.profiles import ProfileCatalog

        catalog = ProfileCatalog()
        try:
            catalog.resolve(expected)
        except Exception:
            expected = None
    assert cat_name == expected


def test_fit_helper_is_non_mtp_on_rocm() -> None:
    """The picker/apply fit path resolves to canonical workload names for
    every capability on every device."""
    assert profile_name_for_fit("chat", "gpu-rocm") == "chat"
    assert profile_name_for_fit("coder", "gpu-rocm") == "chat"
    # embed/rerank resolve to their dedicated workload lanes.
    assert profile_name_for_fit("embed", "gpu-rocm") == "embedding"
    assert profile_name_for_fit("rerank", "gpu-rocm") == "reranking"
    assert profile_name_for_fit("chat", "gpu-vulkan") == "chat"


def test_base_profile_for_backend_is_non_mtp(tmp_hal0_home: str) -> None:
    """_base_profile_for_backend answers the backend→base question using the
    1.0 canonical workload names (all GPU backends point to 'chat')."""
    from hal0.config.loader import load_profiles_config
    from hal0.slots.config_write import _base_profile_for_backend

    catalog = load_profiles_config()
    assert _base_profile_for_backend(catalog, "rocm") == "chat"
    assert _base_profile_for_backend(catalog, "vulkan") == "chat"
    assert _base_profile_for_backend(catalog, "cuda") == "chat"


def test_reconcile_device_flip_stays_non_mtp(tmp_hal0_home: str) -> None:
    """Flipping a GPU slot's device keeps the profile — device-only change
    triggers _reconcile_device_profile, which preserves the slot's working
    profile (not a catalog workload name)."""
    from hal0.slots.config_write import _reconcile_device_profile

    # Slot was on the chat profile; operator flips the device only.
    # Profile stays chat (already coherent with GPU).
    cfg_dict: dict[str, object] = {"device": "gpu-rocm", "profile": "chat"}
    _reconcile_device_profile(cfg_dict, changed={"device"})
    assert cfg_dict["profile"] == "chat"

    # Flip to vulkan: device-only, profile unchanged (chat already works on vulkan).
    cfg_dict2: dict[str, object] = {"device": "gpu-vulkan", "profile": "chat"}
    _reconcile_device_profile(cfg_dict2, changed={"device"})
    assert cfg_dict2["profile"] == "chat"


def test_reconcile_device_profile_writes_into_nested_slot_table(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1685: _reconcile_device_profile must read/write device+profile
    through slot_scalar_table, not the dict root — on a nested-shape
    cfg_dict (scalars under [slot]) it used to always no-op (profile_name
    read from the root came back None) and, worse, a write would have
    landed on a root key _flatten_slot_toml discards."""
    from hal0.config.schema import ProfileConfig, ProfilesConfig
    from hal0.slots.config_write import _reconcile_device_profile

    # Deliberately NOT named "chat" -- _base_profile_for_backend prefers a
    # literal "chat" profile over any backend match, which would mask the
    # thing this test is pinning.
    catalog = ProfilesConfig(
        profile={
            "gpu-rocm-base": ProfileConfig(backend="rocm"),
            "gpu-vulkan-base": ProfileConfig(backend="vulkan"),
        }
    )
    monkeypatch.setattr("hal0.config.loader.load_profiles_config", lambda: catalog)

    # A conflicting-backend flip (device -> vulkan, profile stayed the
    # rocm-backed profile) must derive a coherent vulkan profile -- and land
    # it in [slot], not the root, on a nested-shape cfg_dict.
    cfg_dict: dict[str, object] = {"slot": {"device": "gpu-vulkan", "profile": "gpu-rocm-base"}}
    _reconcile_device_profile(cfg_dict, changed={"device"})
    assert cfg_dict["slot"]["profile"] == "gpu-vulkan-base"
    assert "profile" not in cfg_dict
