"""Tests for hal0.slots.capacity: GPU-capability VRAM/RAM gating (#1839)
and the systemd-unit cgroup fallback.

Two related regressions, both filed as #1839:

1. ``build_per_slot`` used to book a GPU-*declared* slot's memory as
   ``vram_mb`` purely from the configured ``device`` token, never
   consulting whether a usable GPU actually exists. The shipped static
   seeds hardcode ``device = "gpu-vulkan"`` regardless of hardware, so a
   GPU-less (``HAL0_ALLOW_CPU_ONLY=1``) install showed phantom VRAM usage.
   ``gpu_capable`` (resolved from the hardware probe's
   ``vulkan_capable``/``compute_capable`` signal when not passed
   explicitly) now gates the split.

2. ``_container_cgroup_mem_bytes`` shelled ``podman inspect`` from the API
   process, which runs unprivileged (``User=hal0``) against ROOTFUL slot
   containers — permission denied, silently returns 0 on every standard
   install. It now falls back to the slot's own systemd unit's
   ``MemoryCurrent`` property (``systemctl show``, no privilege needed).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hal0.slots.capacity import (
    _host_has_capable_gpu,
    _systemd_unit_mem_bytes,
    build_per_slot,
    gtt_fit_warning,
)

# ── helpers ──────────────────────────────────────────────────────────────


def _make_slot(name, state="ready", model_id="mymodel", backend="vulkan", slot_id=None):
    slot = MagicMock()
    slot.name = name
    slot.state = state
    slot.model_id = model_id
    slot.backend = backend
    slot.slot_id = slot_id
    slot.metadata = {"provider": "llama-server", "backend": backend}
    return slot


def _hw_info(vulkan_capable=False, compute_capable=False, no_gpus=False):
    """Fake object shaped like hal0.config.schema.HardwareInfo."""
    info = MagicMock()
    if no_gpus:
        info.gpus = []
    else:
        gpu = MagicMock()
        gpu.vulkan_capable = vulkan_capable
        gpu.compute_capable = compute_capable
        info.gpus = [gpu]
    return info


# ── _host_has_capable_gpu ────────────────────────────────────────────────


class TestHostHasCapableGpu:
    """Patches at the freshness-resolver seam (#1862): ``_host_has_capable_gpu``
    now reads through :func:`hal0.hardware.freshness.resolve_fresh_hardware_info`
    rather than the raw cache, so that is the boundary these tests fake."""

    def test_true_when_vulkan_capable_gpu_present(self):
        with patch(
            "hal0.hardware.freshness.resolve_fresh_hardware_info",
            return_value=(_hw_info(vulkan_capable=True), None),
        ):
            assert _host_has_capable_gpu() is True

    def test_true_when_compute_capable_gpu_present(self):
        with patch(
            "hal0.hardware.freshness.resolve_fresh_hardware_info",
            return_value=(_hw_info(compute_capable=True), None),
        ):
            assert _host_has_capable_gpu() is True

    def test_false_when_no_gpus(self):
        with patch(
            "hal0.hardware.freshness.resolve_fresh_hardware_info",
            return_value=(_hw_info(no_gpus=True), None),
        ):
            assert _host_has_capable_gpu() is False

    def test_false_when_gpu_present_but_not_capable(self):
        """A GPU sysfs node with no capable render node (#1839's box) — the
        exact CPU-only-LXC repro. Not vulkan- or compute-capable."""
        with patch(
            "hal0.hardware.freshness.resolve_fresh_hardware_info",
            return_value=(_hw_info(vulkan_capable=False, compute_capable=False), None),
        ):
            assert _host_has_capable_gpu() is False

    def test_false_on_probe_error(self):
        """Never raises — a broken/missing hardware.json degrades to False,
        the conservative choice (books memory as RAM, not phantom VRAM)."""
        with patch(
            "hal0.hardware.freshness.resolve_fresh_hardware_info",
            side_effect=RuntimeError("boom"),
        ):
            assert _host_has_capable_gpu() is False


# ── build_per_slot: GPU-capability gating (defect 1) ─────────────────────


class TestBuildPerSlotGpuCapabilityGating:
    @pytest.mark.asyncio
    async def test_gpu_declared_slot_books_ram_on_gpu_less_box(self):
        """#1839 repro: a slot configured with a GPU backend token
        (device=gpu-vulkan -> backend='vulkan') on a box with NO usable
        GPU must book its resident footprint under ram_mb, not vram_mb —
        the pre-fix behaviour showed phantom VRAM usage on a CPU-only box.
        """
        slot = _make_slot("brain", backend="vulkan")

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=2 * 1024 * 1024 * 1024,  # 2 GiB
        ):
            result = await build_per_slot([slot], gpu_capable=False)

        row = result["brain"]
        assert row["vram_mb"] == 0.0
        assert row["ram_mb"] == row["mem_mb"] > 0.0

    @pytest.mark.asyncio
    async def test_gpu_declared_slot_books_vram_on_gpu_box(self):
        """Sibling check: the same GPU-declared slot on a box that DOES
        have a usable GPU keeps booking under vram_mb — #1839's fix must
        not regress the working (GPU box) case."""
        slot = _make_slot("brain", backend="vulkan")

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=2 * 1024 * 1024 * 1024,
        ):
            result = await build_per_slot([slot], gpu_capable=True)

        row = result["brain"]
        assert row["ram_mb"] == 0.0
        assert row["vram_mb"] == row["mem_mb"] > 0.0

    @pytest.mark.asyncio
    async def test_cpu_backend_books_ram_even_when_gpu_capable(self):
        """A slot explicitly configured 'cpu' still books to ram_mb even
        on a box that DOES have a capable GPU — the per-slot 'cpu' choice
        wins over host capability (#1796 behaviour preserved)."""
        slot = _make_slot("utility", backend="cpu")

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=1 * 1024 * 1024 * 1024,
        ):
            result = await build_per_slot([slot], gpu_capable=True)

        row = result["utility"]
        assert row["vram_mb"] == 0.0
        assert row["ram_mb"] == row["mem_mb"] > 0.0

    @pytest.mark.asyncio
    async def test_gpu_capable_none_resolves_via_host_probe(self):
        """When the caller doesn't pass gpu_capable, build_per_slot
        resolves it itself via _host_has_capable_gpu() rather than
        defaulting to True (which would silently reintroduce #1839)."""
        slot = _make_slot("brain", backend="vulkan")

        with (
            patch(
                "hal0.slots.capacity._container_cgroup_mem_bytes",
                new_callable=AsyncMock,
                return_value=2 * 1024 * 1024 * 1024,
            ),
            patch("hal0.slots.capacity._host_has_capable_gpu", return_value=False),
        ):
            result = await build_per_slot([slot])

        row = result["brain"]
        assert row["vram_mb"] == 0.0
        assert row["ram_mb"] > 0.0

    @pytest.mark.asyncio
    async def test_npu_fallthrough_keeps_vram_attribution_without_gpu(self):
        """PR review (#1839): an FLM slot that falls through the NPU catalog
        branch (miss / zero footprint_gb) must keep its historical UMA-style
        vram_mb attribution even on a box with gpu_capable=False (NPU-only
        host) — the classic-GPU gate must not apply to NPU slots."""
        slot = _make_slot("npu-chat", backend="flm")
        slot.metadata = {"provider": "flm", "backend": "flm"}

        model_mock = MagicMock()
        model_mock.size_bytes = 4 * 1024 * 1024 * 1024
        model_mock.model_dump = lambda: {}
        registry = MagicMock()
        registry.get = MagicMock(return_value=model_mock)

        # footprint_gb missing/zero -> falls through past the FLM `continue`.
        flm_catalog = {"mymodel": {"footprint_gb": 0.0, "size_bytes": 0}}

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await build_per_slot(
                [slot], registry=registry, flm_catalog=flm_catalog, gpu_capable=False
            )

        row = result["npu-chat"]
        assert row["ram_mb"] == 0.0
        assert row["vram_mb"] == row["mem_mb"] > 0.0


# ── build_per_slot: slots that are still starting (#3056) ─────────────────


class TestBuildPerSlotIncludeStarting:
    """A slot that is ``starting`` holds no weights yet but is about to: the
    memory budget needs its footprint to leave room for it. Opt-in, so every
    other caller keeps the resident-only map."""

    @staticmethod
    def _registry(size_gib: int = 4) -> MagicMock:
        model = MagicMock()
        model.size_bytes = size_gib * 1024 * 1024 * 1024
        model.model_dump = lambda: {}
        registry = MagicMock()
        registry.get = MagicMock(return_value=model)
        return registry

    @pytest.mark.asyncio
    async def test_build_per_slot_includes_starting_when_asked(self):
        slot = _make_slot("brain", state="starting", backend="vulkan")

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=0,
        ):
            default = await build_per_slot([slot], registry=self._registry(), gpu_capable=True)
            asked = await build_per_slot(
                [slot], registry=self._registry(), gpu_capable=True, include_starting=True
            )

        assert default == {}  # unchanged for every existing caller
        row = asked["brain"]
        assert row["state"] == "starting"
        assert row["model_id"] == "mymodel"
        assert row["mem_mb"] >= 4096.0  # at least the model file
        assert row["vram_mb"] == row["mem_mb"]
        assert row["ram_mb"] == 0.0

    @pytest.mark.asyncio
    async def test_starting_row_uses_the_file_size_estimate_not_the_cgroup(self):
        """A container that is still loading reports a cgroup far below what it
        is about to hold; the estimate is the footprint, and the probe is not run."""
        slot = _make_slot("brain", state="starting", backend="vulkan")
        probe = AsyncMock(return_value=100 * 1024 * 1024 * 1024)

        with patch("hal0.slots.capacity._container_cgroup_mem_bytes", probe):
            result = await build_per_slot(
                [slot], registry=self._registry(), gpu_capable=True, include_starting=True
            )

        probe.assert_not_called()
        assert result["brain"]["mem_mb"] < 100 * 1024

    @pytest.mark.asyncio
    async def test_resident_rows_are_unchanged_when_starting_is_included(self):
        ready = _make_slot("brain", state="ready", backend="vulkan")
        starting = _make_slot("coder", state="starting", backend="vulkan")

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=7 * 1024 * 1024 * 1024,
        ):
            result = await build_per_slot(
                [ready, starting],
                registry=self._registry(),
                gpu_capable=True,
                include_starting=True,
            )

        assert result["brain"]["state"] == "ready"
        assert result["brain"]["mem_mb"] == 7 * 1024.0  # cgroup still wins for a resident slot
        assert result["coder"]["state"] == "starting"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["offline", "pulling", "unloading", "error"])
    async def test_other_non_resident_states_stay_out(self, state):
        slot = _make_slot("brain", state=state, backend="vulkan")

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await build_per_slot(
                [slot], registry=self._registry(), gpu_capable=True, include_starting=True
            )

        assert result == {}


# ── build_per_slot: artefact-token cgroup probe (id-keying review) ───────


class TestBuildPerSlotArtefactToken:
    @pytest.mark.asyncio
    async def test_probes_by_slot_id_when_id_keyed(self, tmp_path):
        """On a genuinely id-keyed box (``hal0 slot migrate-id-keying`` has
        run, so ``<id>.toml`` is the config on disk) the container/unit is
        named off the durable slot_id — the cgroup probe must use the id."""
        slot = _make_slot("brain", backend="vulkan", slot_id=42)
        (tmp_path / "42.toml").write_text("name = 'brain'\n", encoding="utf-8")

        with (
            patch("hal0.config.paths.slots_config_dir", return_value=tmp_path),
            patch(
                "hal0.slots.capacity._container_cgroup_mem_bytes",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_probe,
        ):
            await build_per_slot([slot], gpu_capable=True)

        mock_probe.assert_awaited_once_with("42")

    @pytest.mark.asyncio
    async def test_probes_by_name_when_slot_id_set_but_artefacts_name_keyed(self, tmp_path):
        """THE standard install (#1839 repro box, and every un-migrated box).

        ``SlotManager.fold_identity()`` runs on every boot and stamps a
        ``slot_id`` on every slot WITHOUT renaming a single on-disk artefact
        — the destructive rename only happens under ``hal0 slot
        migrate-id-keying``. So ``slot_id`` being set says nothing about how
        the container/unit is named: here the config is still ``brain.toml``,
        the unit is ``hal0-slot@brain.service`` and the container is
        ``hal0-slot-brain``. Probing by id would miss both, collapse the
        cgroup read to 0, and silently under-report resident memory.
        """
        slot = _make_slot("brain", backend="vulkan", slot_id=1)
        (tmp_path / "brain.toml").write_text("name = 'brain'\n", encoding="utf-8")

        with (
            patch("hal0.config.paths.slots_config_dir", return_value=tmp_path),
            patch(
                "hal0.slots.capacity._container_cgroup_mem_bytes",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_probe,
        ):
            await build_per_slot([slot], gpu_capable=True)

        mock_probe.assert_awaited_once_with("brain")

    @pytest.mark.asyncio
    async def test_probes_by_name_when_not_id_keyed(self, tmp_path):
        """Pre-fold slots have no slot_id at all — falls back to the display
        name, unchanged from before this review fix."""
        slot = _make_slot("brain", backend="vulkan", slot_id=None)

        with (
            patch("hal0.config.paths.slots_config_dir", return_value=tmp_path),
            patch(
                "hal0.slots.capacity._container_cgroup_mem_bytes",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_probe,
        ):
            await build_per_slot([slot], gpu_capable=True)

        mock_probe.assert_awaited_once_with("brain")

    @pytest.mark.asyncio
    async def test_id_keyed_probe_reports_real_cgroup_memory(self, tmp_path):
        """End-to-end consequence of the token choice: a name-keyed box whose
        unit reports 3 GiB must render 3072 MB, not the file-size estimate.

        Drives the real :func:`_container_cgroup_mem_bytes` with only the
        systemd property faked, so a wrong token shows up as a wrong number
        rather than only a wrong mock argument.
        """
        slot = _make_slot("brain", backend="vulkan", slot_id=7)
        (tmp_path / "brain.toml").write_text("name = 'brain'\n", encoding="utf-8")

        async def fake_props(unit, *props):
            if unit == "hal0-slot@brain.service":
                return {"MemoryCurrent": str(3 * 1024 * 1024 * 1024)}
            return {}

        with (
            patch("hal0.config.paths.slots_config_dir", return_value=tmp_path),
            patch(
                "hal0.slots.capacity._runtime_inspect_mem_bytes",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch("hal0.slots.capacity.systemd_props", new=fake_props),
        ):
            result = await build_per_slot([slot], gpu_capable=True)

        assert result["brain"]["mem_mb"] == 3072.0


# ── _systemd_unit_mem_bytes / _container_cgroup_mem_bytes fallback (defect 2) ──


class TestSystemdUnitMemFallback:
    @pytest.mark.asyncio
    async def test_reads_memory_current_from_unit(self):
        with patch(
            "hal0.slots.capacity.systemd_props",
            new_callable=AsyncMock,
            return_value={"MemoryCurrent": "2147483648"},
        ) as mock_props:
            result = await _systemd_unit_mem_bytes("brain")

        assert result == 2147483648
        # Queried the slot's own systemd unit, not a container name.
        mock_props.assert_awaited_once_with("hal0-slot@brain.service", "MemoryCurrent")

    @pytest.mark.asyncio
    async def test_returns_zero_when_unit_not_loaded(self):
        with patch("hal0.slots.capacity.systemd_props", new_callable=AsyncMock, return_value={}):
            result = await _systemd_unit_mem_bytes("brain")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_accounting_disabled(self):
        """systemd reports '[not set]' when MemoryAccounting is off."""
        with patch(
            "hal0.slots.capacity.systemd_props",
            new_callable=AsyncMock,
            return_value={"MemoryCurrent": "[not set]"},
        ):
            result = await _systemd_unit_mem_bytes("brain")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_for_uint64_max_sentinel(self):
        """Some systemd versions render an unset MemoryCurrent as UINT64_MAX
        rather than '[not set]'. It parses cleanly, and capacity takes
        max(cgroup_mb, estimate_mb) — so without a guard the slot renders
        ~16 EiB resident instead of its real footprint."""
        with patch(
            "hal0.slots.capacity.systemd_props",
            new_callable=AsyncMock,
            return_value={"MemoryCurrent": "18446744073709551615"},
        ):
            result = await _systemd_unit_mem_bytes("brain")
        assert result == 0

    @pytest.mark.asyncio
    async def test_container_cgroup_mem_bytes_uses_fallback_when_runtime_probe_fails(self):
        """#1839: the end-to-end probe must not silently collapse to 0 just
        because podman inspect failed (permission denied against a rootful
        container) — it must fall back to the unit's own MemoryCurrent."""
        from hal0.slots.capacity import _container_cgroup_mem_bytes

        with (
            patch(
                "hal0.slots.capacity._runtime_inspect_mem_bytes",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "hal0.slots.capacity._systemd_unit_mem_bytes",
                new_callable=AsyncMock,
                return_value=3133931520,  # ~2987.85 MiB, matches the issue's repro
            ),
        ):
            result = await _container_cgroup_mem_bytes("brain")

        assert result == 3133931520

    @pytest.mark.asyncio
    async def test_container_cgroup_mem_bytes_prefers_runtime_probe_when_it_succeeds(self):
        """The higher-fidelity runtime-inspect probe wins when it actually
        works (e.g. hal0-api itself running as root, or a rootless slot)."""
        from hal0.slots.capacity import _container_cgroup_mem_bytes

        with (
            patch(
                "hal0.slots.capacity._runtime_inspect_mem_bytes",
                new_callable=AsyncMock,
                return_value=999,
            ),
            patch(
                "hal0.slots.capacity._systemd_unit_mem_bytes",
                new_callable=AsyncMock,
                return_value=123456,
            ) as mock_fallback,
        ):
            result = await _container_cgroup_mem_bytes("brain")

        assert result == 999
        mock_fallback.assert_not_called()


# ── gtt_fit_warning — WARN-only host-truth GTT feasibility (pure) ───────────
#
# Field finding (LXC on a UMA Proxmox host): a 47.3 GiB weight load failed
# with cudaMalloc OOM while the container's meminfo showed 65 GiB
# "available" — GTT allocations draw from the HOST's free RAM and the
# cgroup-shaped meminfo is a lie for GPU purposes. The helper compares the
# incoming weights against the amdgpu sysfs free-GTT counters instead.


class TestGttFitWarning:
    def test_warns_when_weights_exceed_free_gtt(self) -> None:
        """The ct105 shape: 47.3 GiB weights vs ~18 GiB actually free."""
        msg = gtt_fit_warning(47.3 * 1024, gtt_free_mb=18.0 * 1024, gtt_total_mb=96.0 * 1024)
        assert msg is not None
        assert "47.3 GiB" in msg
        assert "18.0 GiB" in msg
        assert "96.0 GiB" in msg

    def test_silent_when_it_fits(self) -> None:
        assert gtt_fit_warning(8192.0, gtt_free_mb=20480.0, gtt_total_mb=98304.0) is None

    def test_silent_at_exact_boundary(self) -> None:
        """needed == free is not "clearly exceeds" — no warning."""
        assert gtt_fit_warning(20480.0, gtt_free_mb=20480.0, gtt_total_mb=98304.0) is None

    def test_silent_when_counters_absent(self) -> None:
        """Non-AMD / no-GPU host (None counters) → fail-soft silence."""
        assert gtt_fit_warning(47.3 * 1024, gtt_free_mb=None, gtt_total_mb=None) is None
        assert gtt_fit_warning(47.3 * 1024, gtt_free_mb=None, gtt_total_mb=98304.0) is None
        assert gtt_fit_warning(47.3 * 1024, gtt_free_mb=18432.0, gtt_total_mb=None) is None

    def test_silent_on_unknown_or_zero_size(self) -> None:
        """Unknown model size never warns (mirrors preload_evict's fail-open)."""
        assert gtt_fit_warning(0.0, gtt_free_mb=1024.0, gtt_total_mb=98304.0) is None
        assert gtt_fit_warning(-5.0, gtt_free_mb=1024.0, gtt_total_mb=98304.0) is None

    def test_warns_even_when_free_is_zero(self) -> None:
        """A fully-consumed pool (free 0) still warns rather than dividing oddly."""
        msg = gtt_fit_warning(1024.0, gtt_free_mb=0.0, gtt_total_mb=98304.0)
        assert msg is not None
        assert "0.0 GiB" in msg


# ── gtt_feasibility_verdict — advisory verdict over host GTT truth (pure) ──
#
# Provides verdict states ("fits", "tight", "exceeds", "exceeds_total", "unknown")
# for the dashboard form and pre-load capacity estimation paths. Never blocks.


class TestGttFeasibilityVerdict:
    def test_gtt_feasibility_verdict_states(self) -> None:
        from hal0.slots.capacity import gtt_feasibility_verdict as v

        assert v(1000.0, gtt_free_mb=None, gtt_total_mb=98304.0)["verdict"] == "unknown"
        assert v(0.0, gtt_free_mb=50000.0, gtt_total_mb=98304.0)["verdict"] == "unknown"
        assert v(40000.0, gtt_free_mb=69632.0, gtt_total_mb=98304.0)["verdict"] == "fits"
        assert (
            v(64000.0, gtt_free_mb=69632.0, gtt_total_mb=98304.0)["verdict"] == "tight"
        )  # > 0.9 * free
        assert v(75000.0, gtt_free_mb=69632.0, gtt_total_mb=98304.0)["verdict"] == "exceeds"
        assert v(131072.0, gtt_free_mb=69632.0, gtt_total_mb=98304.0)["verdict"] == "exceeds_total"

    def test_gtt_feasibility_verdict_echoes_inputs(self) -> None:
        from hal0.slots.capacity import gtt_feasibility_verdict as v

        out = v(40000.0, gtt_free_mb=69632.0, gtt_total_mb=98304.0)
        assert out == {
            "verdict": "fits",
            "needed_mb": 40000.0,
            "gtt_free_mb": 69632.0,
            "gtt_total_mb": 98304.0,
        }


# ── KV estimate follows the slot profile's window (ADR-0029) ─────────────


def _write_profile(home: str, name: str, ctx_size: int) -> None:
    from pathlib import Path

    path = Path(home) / "etc" / "hal0" / "profiles.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'[profile.{name}]\nflags = ""\nctx_size = {ctx_size}\n', encoding="utf-8")


def _write_slot_toml(home: str, name: str, *, profile: str | None) -> None:
    from pathlib import Path

    slots = Path(home) / "etc" / "hal0" / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    lines = [f'name = "{name}"', "port = 8081", 'backend = "vulkan"', 'provider = "llama-server"']
    if profile:
        lines.append(f'profile = "{profile}"')
    lines += ["[model]", 'default = "mymodel"', ""]
    (slots / f"{name}.toml").write_text("\n".join(lines), encoding="utf-8")


class _BigWindowRegistry:
    """One model: 1000 MiB of weights, a 131072-token native window."""

    class _Row:
        size_bytes = 1000 * 1024 * 1024

        def model_dump(self):
            return {"id": "mymodel", "metadata": {"context_length": 131072}}

    def get(self, model_id):
        return self._Row()


class TestKvEstimateFollowsTheSlotProfileWindow:
    @pytest.mark.asyncio
    async def test_profile_ctx_size_shrinks_the_kv_share(self, tmp_hal0_home):
        """A 4096-token profile over a 131072-token model books ~2 MiB of KV,
        not the 32768-token native-capped ~16 MiB."""
        _write_profile(tmp_hal0_home, "tiny-ctx", 4096)
        _write_slot_toml(tmp_hal0_home, "brain", profile="tiny-ctx")
        slot = _make_slot("brain", model_id="mymodel")

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await build_per_slot([slot], registry=_BigWindowRegistry(), gpu_capable=True)

        assert result["brain"]["mem_mb"] == pytest.approx(1000.0 + 4096 / 1000 * 0.5, abs=0.1)

    @pytest.mark.asyncio
    async def test_no_profile_ctx_size_uses_the_native_window_capped(self, tmp_hal0_home):
        """The ``chat`` seed sets no ``ctx_size``: the window is the model's
        native one dense-capped at 32768."""
        _write_slot_toml(tmp_hal0_home, "brain", profile=None)
        slot = _make_slot("brain", model_id="mymodel")

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await build_per_slot([slot], registry=_BigWindowRegistry(), gpu_capable=True)

        assert result["brain"]["mem_mb"] == pytest.approx(1000.0 + 32768 / 1000 * 0.5, abs=0.1)

    @pytest.mark.asyncio
    async def test_slot_without_a_readable_toml_falls_back_to_the_model_window(self, tmp_hal0_home):
        """No slot file: no profile window, so the estimate sizes the model's
        own ``metadata.context_length`` exactly as before."""
        slot = _make_slot("ghost", model_id="mymodel")

        with patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await build_per_slot([slot], registry=_BigWindowRegistry(), gpu_capable=True)

        assert result["ghost"]["mem_mb"] == pytest.approx(1000.0 + 131072 / 1000 * 0.5, abs=0.1)

    def test_ctx_tokens_for_prefers_an_explicit_window_over_the_model_metadata(self):
        from hal0.slots.capacity import _ctx_tokens_for

        meta = {"metadata": {"context_length": 131072}, "defaults": {"context_size": 8192}}
        assert _ctx_tokens_for({**meta, "ctx_tokens": 4096}) == 4096
        assert _ctx_tokens_for(meta) == 131072  # a stray defaults key is never read
