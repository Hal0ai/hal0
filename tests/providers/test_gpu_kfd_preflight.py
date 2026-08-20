"""#1888 — /dev/kfd is a hard requirement for AMD-GPU llama.cpp slots.

The release-pinned ROCmFPX runner is one HIP+Vulkan build. llama.cpp picks
ROCm when ``/dev/kfd`` is visible and SILENTLY falls back to that image's
Vulkan backend when ``ggml_rocm_init`` fails — and that Vulkan backend emits
invalid tokens for every model it serves, at full nominal speed, while HTTP
200, container health, ``hal0 doctor`` and the SSE ``done`` frame all read
green. A box without ``/dev/kfd`` therefore has no valid GPU lane at all, and
the only honest behaviour is to refuse the load and say why.

Regression shape: before this, ``load_sync`` happily started such a slot and
every text surface on the box was garbage for hours.
"""

from __future__ import annotations

import os

import pytest

from hal0.providers._gpu import (
    ENV_ALLOW_VULKAN_FALLBACK,
    GpuPreflightError,
    host_is_amd_gpu,
    kfd_present,
    require_kfd_for_gpu_slot,
    runtime_lane_for_provider,
)


class TestKfdPresent:
    def test_true_when_the_node_exists(self, tmp_path) -> None:
        node = tmp_path / "kfd"
        node.write_text("")
        assert kfd_present(str(node)) is True

    def test_false_when_it_does_not(self, tmp_path) -> None:
        assert kfd_present(str(tmp_path / "kfd")) is False

    def test_false_when_it_exists_but_is_not_accessible(self, tmp_path) -> None:
        """An LXC passthrough with a mis-mapped gid leaves the node visible but
        unopenable — HIP still fails and llama.cpp still lands on the invalid
        Vulkan lane, so existence alone must not count as a pass."""
        node = tmp_path / "kfd"
        node.write_text("")
        node.chmod(0o000)
        try:
            if os.access(str(node), os.R_OK):  # running as root — chmod is advisory
                pytest.skip("root bypasses file permissions")
            assert kfd_present(str(node)) is False
        finally:
            node.chmod(0o600)


class TestHostIsAmdGpu:
    def test_true_when_the_amdgpu_module_dir_exists(self, tmp_path) -> None:
        assert host_is_amd_gpu(str(tmp_path)) is True

    def test_false_otherwise(self, tmp_path) -> None:
        assert host_is_amd_gpu(str(tmp_path / "nope")) is False


class TestRequireKfdForGpuSlot:
    @pytest.mark.parametrize("device", ["gpu-rocm", "gpu-vulkan"])
    def test_amd_gpu_slot_without_kfd_is_refused(self, device, tmp_path) -> None:
        with pytest.raises(GpuPreflightError) as exc:
            require_kfd_for_gpu_slot(
                "agent", device=device, kfd_path=str(tmp_path / "kfd"), env={}, amd_host=True
            )
        msg = str(exc.value)
        # The operator must be able to act on this without reading the source.
        assert "agent" in msg
        assert "/kfd" in msg
        assert "1888" in msg

    @pytest.mark.parametrize("device", ["gpu-rocm", "gpu-vulkan"])
    def test_passes_when_kfd_is_present(self, device, tmp_path) -> None:
        node = tmp_path / "kfd"
        node.write_text("")
        require_kfd_for_gpu_slot("agent", device=device, kfd_path=str(node), env={}, amd_host=True)

    def test_gpu_vulkan_on_a_non_amd_host_is_not_gated(self, tmp_path) -> None:
        """An Intel iGPU / NVIDIA-without-CDI box has no /dev/kfd by design and
        is not the hardware #1888 was characterised on — demanding one there
        would strand every GPU slot on unaffected hardware."""
        require_kfd_for_gpu_slot(
            "utility",
            device="gpu-vulkan",
            kfd_path=str(tmp_path / "kfd"),
            env={},
            amd_host=False,
        )

    def test_gpu_rocm_is_gated_even_on_a_non_amd_host(self, tmp_path) -> None:
        """The device name IS the ROCm claim — a gpu-rocm slot with no compute
        node is broken regardless of what the host reports."""
        with pytest.raises(GpuPreflightError):
            require_kfd_for_gpu_slot(
                "agent",
                device="gpu-rocm",
                kfd_path=str(tmp_path / "kfd"),
                env={},
                amd_host=False,
            )

    @pytest.mark.parametrize("device", ["cpu", "npu", "gpu-cuda", "", "img"])
    def test_non_rocmfpx_devices_are_untouched(self, device, tmp_path) -> None:
        """CPU/NPU slots need no compute node, and gpu-cuda runs the upstream
        CUDA image via CDI — none of them can hit the Vulkan fallback."""
        require_kfd_for_gpu_slot(
            "x", device=device, kfd_path=str(tmp_path / "kfd"), env={}, amd_host=True
        )

    def test_explicit_opt_in_downgrades_to_a_warning(self, tmp_path) -> None:
        require_kfd_for_gpu_slot(
            "agent",
            device="gpu-rocm",
            kfd_path=str(tmp_path / "kfd"),
            env={ENV_ALLOW_VULKAN_FALLBACK: "1"},
            amd_host=True,
        )

    def test_a_non_truthy_opt_in_still_refuses(self, tmp_path) -> None:
        with pytest.raises(GpuPreflightError):
            require_kfd_for_gpu_slot(
                "agent",
                device="gpu-rocm",
                kfd_path=str(tmp_path / "kfd"),
                env={ENV_ALLOW_VULKAN_FALLBACK: "0"},
                amd_host=True,
            )


class TestLoadSyncGuard:
    """The guard must fire on the LOAD path, not on unit rendering — a
    preview/status render has to stay host-independent."""

    def test_load_sync_refuses_a_gpu_slot_with_no_kfd(self, monkeypatch) -> None:
        from hal0.providers import container as container_mod

        monkeypatch.setattr(container_mod, "kfd_present_used", False, raising=False)
        monkeypatch.setattr(container_mod, "require_kfd_for_gpu_slot", _raiser, raising=True)
        provider = container_mod.ContainerProvider()
        with pytest.raises(GpuPreflightError):
            provider.load_sync({"name": "agent", "device": "gpu-rocm", "port": 8081}, {})

    def test_load_sync_calls_the_guard_with_the_slots_device(self, monkeypatch) -> None:
        from hal0.providers import container as container_mod

        seen: dict[str, object] = {}

        def _spy(
            slot_name: str, *, device: str, runtime_lane: str = "llama", **_kw: object
        ) -> None:
            seen["slot"] = slot_name
            seen["device"] = device
            seen["runtime_lane"] = runtime_lane
            raise GpuPreflightError("stop here — the rest of load_sync writes units")

        monkeypatch.setattr(container_mod, "require_kfd_for_gpu_slot", _spy)
        provider = container_mod.ContainerProvider()
        with pytest.raises(GpuPreflightError):
            provider.load_sync({"name": "utility", "device": "gpu-vulkan", "port": 8082}, {})
        assert seen == {"slot": "utility", "device": "gpu-vulkan", "runtime_lane": "llama"}


class TestRuntimeLaneForProvider:
    """#1941 — the lane comes from the PROVIDER's declared image backend, not
    from the slot's device string.

    ``capabilities/catalog.py`` labels every non-llama GPU runtime
    ``gpu-vulkan``; that is the picker's GPU row, not a statement about the
    image. Two of those runtimes are ROCm builds and two are CPU ONNX images,
    so the device string cannot answer "does this need /dev/kfd".
    """

    def test_none_is_the_llama_lane(self) -> None:
        """``_spec_provider_for`` returns None for the default llama-server
        GPU provider — #1888's lane."""
        assert runtime_lane_for_provider(None) == "llama"

    @pytest.mark.parametrize(
        ("module", "cls_name"),
        [
            ("hal0.providers.comfyui", "ComfyUIProvider"),
            ("hal0.providers.qwen3tts", "Qwen3TTSProvider"),
        ],
    )
    def test_rocm_image_runtimes_declare_the_rocm_lane(self, module, cls_name) -> None:
        """Both forward ``resolve_gpu_device_paths()`` (which includes
        /dev/kfd) and are registered ``supported_backends=("rocm",)`` — they
        genuinely cannot initialise HIP without the compute node."""
        import importlib

        provider = getattr(importlib.import_module(module), cls_name)()
        assert provider.gpu_runtime_needs_rocm is True
        assert runtime_lane_for_provider(provider) == "rocm"

    @pytest.mark.parametrize(
        ("module", "cls_name"),
        [
            ("hal0.providers.kokoro", "KokoroProvider"),
            ("hal0.providers.moonshine", "MoonshineProvider"),
            ("hal0.providers.flm", "FLMProvider"),
        ],
    )
    def test_non_rocm_runtimes_declare_the_no_rocm_lane(self, module, cls_name) -> None:
        """CPU ONNX / NPU images: they forward no GPU device node at all, so
        /dev/kfd is irrelevant to them."""
        import importlib

        provider = getattr(importlib.import_module(module), cls_name)()
        assert provider.gpu_runtime_needs_rocm is False
        assert runtime_lane_for_provider(provider) == "no-rocm"

    def test_the_declaration_matches_the_runner_registry(self) -> None:
        """Cross-check the per-provider flag against the OTHER place that
        records the same fact (``RUNNER_IMAGES[...].supported_backends``), so
        the two cannot drift apart silently."""
        import importlib

        from hal0.runners import RUNNER_IMAGES

        for runner_key, module, cls_name in (
            ("comfyui", "hal0.providers.comfyui", "ComfyUIProvider"),
            ("qwen3tts", "hal0.providers.qwen3tts", "Qwen3TTSProvider"),
            ("kokoro", "hal0.providers.kokoro", "KokoroProvider"),
            ("moonshine", "hal0.providers.moonshine", "MoonshineProvider"),
            ("flm", "hal0.providers.flm", "FLMProvider"),
        ):
            runner = RUNNER_IMAGES[runner_key]
            registry_says_rocm = "rocm" in runner.supported_backends or runner.backend == "rocm"
            provider = getattr(importlib.import_module(module), cls_name)()
            assert provider.gpu_runtime_needs_rocm is registry_says_rocm, runner_key


class TestNonRocmRuntimesAreNotGated:
    """#1941 — a ``gpu-vulkan`` slot whose runtime never touches HIP must load
    on a kfd-less AMD box, exactly as it did before #1923.

    Kokoro TTS and Moonshine STT run CPU ONNX images and forward no GPU node,
    so ``/dev/kfd`` is irrelevant to them — but the pre-fix guard saw only
    ``device == "gpu-vulkan"`` and refused them with a llama.cpp error.
    """

    @staticmethod
    def _amd_box_without_kfd(monkeypatch) -> None:
        """An AMD host (amdgpu bound) that has no usable /dev/kfd — the exact
        shape of the box #1941 was reported from."""
        from hal0.providers import _gpu as gpu_mod

        monkeypatch.setattr(gpu_mod, "host_is_amd_gpu", lambda *_a, **_k: True)
        monkeypatch.setattr(gpu_mod, "kfd_present", lambda *_a, **_k: False)

    @staticmethod
    def _stub_unit_write(monkeypatch) -> list[str]:
        """Stop load_sync after the guard: record the unit write, do none."""
        from hal0.providers import container as container_mod

        written: list[str] = []
        monkeypatch.setattr(
            container_mod.ContainerProvider,
            "_render_quadlet_text",
            lambda self, slot_cfg, model_info: "[Container]\n",
        )
        monkeypatch.setattr(
            container_mod.ContainerProvider,
            "_write_and_start_unit",
            lambda self, token, unit_text: written.append(token),
        )
        return written

    @pytest.mark.parametrize(
        "slot_cfg",
        [
            pytest.param(
                {"name": "stt", "type": "transcription", "device": "gpu-vulkan", "port": 8092},
                id="moonshine-stt",
            ),
            pytest.param(
                {"name": "voice", "type": "tts", "device": "gpu-vulkan", "port": 8090},
                id="kokoro-tts",
            ),
        ],
    )
    def test_load_sync_loads_a_non_rocm_vulkan_slot(self, monkeypatch, slot_cfg) -> None:
        from hal0.providers import container as container_mod

        self._amd_box_without_kfd(monkeypatch)
        written = self._stub_unit_write(monkeypatch)

        container_mod.ContainerProvider().load_sync(dict(slot_cfg), {})

        assert written == [slot_cfg["name"]]

    def test_load_sync_still_refuses_the_llama_lane(self, monkeypatch) -> None:
        """The #1888 refusal must survive the scoping: a profile-less
        ``gpu-vulkan`` slot resolves to the default llama-server provider and
        still runs the poisoned ROCmFPX Vulkan lane."""
        from hal0.providers import container as container_mod

        self._amd_box_without_kfd(monkeypatch)
        self._stub_unit_write(monkeypatch)

        with pytest.raises(GpuPreflightError):
            container_mod.ContainerProvider().load_sync(
                {"name": "utility", "device": "gpu-vulkan", "port": 8082}, {}
            )

    @pytest.mark.parametrize(
        "slot_cfg",
        [
            pytest.param(
                {"name": "imagegen", "type": "image", "device": "gpu-vulkan", "port": 8188},
                id="comfyui-on-the-vulkan-label",
            ),
            pytest.param(
                {
                    "name": "voice",
                    "type": "tts",
                    "profile": "qwen3-tts",
                    "device": "gpu-vulkan",
                    "port": 8095,
                },
                id="qwen3tts-on-the-vulkan-label",
            ),
        ],
    )
    def test_load_sync_still_refuses_a_rocm_image_on_the_vulkan_label(
        self, monkeypatch, slot_cfg
    ) -> None:
        """The half the naive "non-llama ⇒ safe" scoping would have broken.

        ComfyUI's Strix Halo image is a PyTorch-ROCm build, and
        ``tts_profile_for_device`` maps ANY GPU device — ``gpu-vulkan``
        included — to the ROCm-only ``qwen3-tts`` profile. Both forward
        ``/dev/kfd`` in their specs, so both must keep the refusal even though
        neither is llama.cpp.
        """
        from hal0.providers import container as container_mod

        self._amd_box_without_kfd(monkeypatch)
        self._stub_unit_write(monkeypatch)

        with pytest.raises(GpuPreflightError):
            container_mod.ContainerProvider().load_sync(dict(slot_cfg), {})

    def test_a_non_llama_gpu_rocm_slot_is_still_gated(self, tmp_path) -> None:
        """Scoping relaxes ``gpu-vulkan`` only. ``gpu-rocm`` stays gated in
        every lane: the device name IS the ROCm claim, whatever the runtime
        does with it."""
        with pytest.raises(GpuPreflightError):
            require_kfd_for_gpu_slot(
                "voice",
                device="gpu-rocm",
                kfd_path=str(tmp_path / "kfd"),
                env={},
                amd_host=True,
                runtime_lane="no-rocm",
            )

    def test_the_non_llama_refusal_does_not_blame_the_vulkan_fallback(self, tmp_path) -> None:
        """#1888's silent-Vulkan-fallback story is llama.cpp's alone — quoting
        it at a ComfyUI operator sends them chasing the wrong defect."""
        with pytest.raises(GpuPreflightError) as exc:
            require_kfd_for_gpu_slot(
                "imagegen",
                device="gpu-vulkan",
                kfd_path=str(tmp_path / "kfd"),
                env={},
                amd_host=True,
                runtime_lane="rocm",
            )
        msg = str(exc.value)
        assert "1888" not in msg
        # Still actionable: what is missing and how to forward it.
        assert "/kfd" in msg
        assert "pct stop/start" in msg


def _raiser(*_a: object, **_kw: object) -> None:
    raise GpuPreflightError("no /dev/kfd")
