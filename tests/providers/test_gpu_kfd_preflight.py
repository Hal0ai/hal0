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
    KFD_MISSING,
    KFD_NOT_OPENABLE,
    KFD_OK,
    GpuPreflightError,
    converge_kfd_device_group,
    host_is_amd_gpu,
    kfd_present,
    kfd_status,
    require_kfd_for_gpu_slot,
    resolve_kfd_target_gid,
    runtime_lane_for_provider,
)


class TestKfdPresent:
    def test_true_when_the_node_exists(self, tmp_path) -> None:
        node = tmp_path / "kfd"
        node.write_text("")
        assert kfd_present(str(node)) is True

    def test_false_when_it_does_not(self, tmp_path) -> None:
        assert kfd_present(str(tmp_path / "kfd")) is False

    def test_false_when_it_exists_but_this_process_cannot_open_it(self, tmp_path) -> None:
        """An LXC passthrough with a mis-mapped gid leaves the node visible but
        unopenable — HIP still fails and llama.cpp still lands on the invalid
        Vulkan lane, so existence alone must not count as a pass.

        ``for_uid=None`` asks about THIS process, which is the only identity a
        DAC probe can honestly answer for.
        """
        node = tmp_path / "kfd"
        node.write_text("")
        node.chmod(0o000)
        try:
            if os.access(str(node), os.R_OK):  # running as root — chmod is advisory
                pytest.skip("root bypasses file permissions")
            assert kfd_present(str(node), for_uid=None) is False
        finally:
            node.chmod(0o600)

    def test_unopenable_node_still_passes_for_the_root_slot_runner(self, tmp_path) -> None:
        """#1953: the identity that matters is the one that OPENS the device.

        hal0-api runs as ``hal0``; the slot containers run rootful as root. A
        plain LXC passthrough routinely leaves ``/dev/kfd`` ``root:root 0660``
        while the render node lands ``root:render 0660`` — ROCm works fine in
        the container, but a probe run as ``hal0`` used to report False and
        #1923's guard then refused every AMD GPU slot on a healthy box.
        """
        node = tmp_path / "kfd"
        node.write_text("")
        # 0o000, not 0o660: the node must be unopenable to the CURRENT process,
        # or this passes on main too and proves nothing (review nit). The dual
        # assert is the actual contract — same node, opposite verdicts,
        # decided solely by which identity is asked about.
        node.chmod(0o000)
        try:
            if os.access(str(node), os.R_OK):
                pytest.skip("this process bypasses file permissions")
            assert kfd_status(str(node), for_uid=None) == KFD_NOT_OPENABLE
            assert kfd_present(str(node)) is True  # default: SLOT_RUNNER_UID
            assert kfd_status(str(node), for_uid=0) == KFD_OK
        finally:
            node.chmod(0o600)

    def test_status_distinguishes_missing_from_unopenable(self, tmp_path) -> None:
        """The two shapes need OPPOSITE remedies, so they must not be one bool."""
        assert kfd_status(str(tmp_path / "nope"), for_uid=0) == KFD_MISSING
        node = tmp_path / "kfd"
        node.write_text("")
        node.chmod(0o000)
        try:
            if os.access(str(node), os.R_OK):
                pytest.skip("root bypasses file permissions")
            assert kfd_status(str(node), for_uid=None) == KFD_NOT_OPENABLE
        finally:
            node.chmod(0o600)


class TestKfdStatusIdentityRules:
    """#1953 — which identity the probe answers for, pinned explicitly.

    The first cut short-circuited ``uid == 0 -> KFD_OK`` on the theory that
    root always bypasses DAC. It does not: root bypasses only with
    CAP_DAC_OVERRIDE, and a hardened container drops it. CI runs in exactly
    such a container, so an unopenable node was reported usable — failing
    OPEN, into the poisoned Vulkan lane this guard exists to block.
    """

    def test_asking_about_this_process_consults_the_os_even_when_root(
        self, tmp_path, monkeypatch
    ) -> None:
        """No uid-0 short-circuit for the CURRENT process: ask os.access.

        Simulated rather than requiring a root test runner: pretend to be uid
        0 while os.access reports the denial a capability-dropped container
        would produce.
        """
        node = tmp_path / "kfd"
        node.write_text("")
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(os, "access", lambda *a, **k: False)
        assert kfd_status(str(node), for_uid=None) == KFD_NOT_OPENABLE
        # for_uid=0 is NOT the same question, even from a root caller: it names
        # the rootful slot container, whose CAP_DAC_OVERRIDE this process's
        # denial says nothing about. Asserting NOT_OPENABLE here contradicted
        # test_asking_about_a_different_root_identity_still_assumes_override
        # for identical input, and collapsed the parameter for root callers.
        assert kfd_status(str(node), for_uid=0) == KFD_OK

    def test_asking_about_a_different_root_identity_still_assumes_override(
        self, tmp_path, monkeypatch
    ) -> None:
        """The slot container's root is not this process — we cannot probe it,
        so the usual DAC override is assumed. That is the whole point of the
        runner-identity parameter."""
        node = tmp_path / "kfd"
        node.write_text("")
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        monkeypatch.setattr(os, "access", lambda *a, **k: False)
        assert kfd_status(str(node), for_uid=0) == KFD_OK


class TestHostIsAmdGpu:
    def test_true_when_the_amdgpu_module_dir_exists(self, tmp_path) -> None:
        assert host_is_amd_gpu(str(tmp_path)) is True

    def test_false_otherwise(self, tmp_path) -> None:
        assert host_is_amd_gpu(str(tmp_path / "nope")) is False


class TestRequireKfdForGpuSlot:
    def test_amd_gpu_slot_without_kfd_is_refused(self, tmp_path) -> None:
        with pytest.raises(GpuPreflightError) as exc:
            require_kfd_for_gpu_slot(
                "agent", device="gpu-rocm", kfd_path=str(tmp_path / "kfd"), env={}, amd_host=True
            )
        msg = str(exc.value)
        # The operator must be able to act on this without reading the source.
        assert "agent" in msg
        assert "/kfd" in msg
        assert "1888" in msg

    def test_an_amd_vulkan_llama_slot_is_refused_on_an_unvalidated_image(self, tmp_path) -> None:
        """#1948 Phase D moved this device's gate, it did not remove it.

        ``gpu-vulkan`` no longer needs ``/dev/kfd`` — Vulkan does not use the
        compute node — but it now needs a runner image whose Vulkan backend
        passed the §3-C matrix. The default ``image=None`` fails closed, so the
        pre-#1948 call shape still refuses; only the reason changed from "no
        compute node" to "unvalidated Vulkan image".

        The positive direction and the render-node half live in
        ``test_gpu_vulkan_lane_gate.py``.
        """
        with pytest.raises(GpuPreflightError) as exc:
            require_kfd_for_gpu_slot(
                "agent", device="gpu-vulkan", kfd_path=str(tmp_path / "kfd"), env={}, amd_host=True
            )
        msg = str(exc.value)
        assert "agent" in msg
        assert "1888" in msg
        assert "not validated for the Vulkan lane" in msg

    def test_passes_when_kfd_is_present(self, tmp_path) -> None:
        node = tmp_path / "kfd"
        node.write_text("")
        require_kfd_for_gpu_slot(
            "agent", device="gpu-rocm", kfd_path=str(node), env={}, amd_host=True
        )

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
        shape of the box #1941 was reported from.

        Patches ``kfd_status``, NOT ``kfd_present``: the guard calls the former
        (#1952 + #1953 composed). Patching ``kfd_present`` was a dead stub —
        these tests then passed only because the CI runner happens to lack
        /dev/kfd, and went RED on any box that has one (CT105, ct151, dev
        workstations), taking the #1888/#1941 regression cover with them.

        Note for the next person to widen this helper: only ONE caller still
        depends on the kfd patch (``…refuses_a_rocm_image_on_the_vulkan_label``,
        which reaches the kfd branch via the ``rocm`` lane). The two
        ``gpu-vulkan``/llama callers stopped consulting ``/dev/kfd`` at all in
        #1948 — they are gated on the render node and the image — so they would
        keep passing even if this patch went dead again, which is exactly how
        the last dead stub survived unnoticed.
        """
        from hal0.providers import _gpu as gpu_mod

        monkeypatch.setattr(gpu_mod, "host_is_amd_gpu", lambda *_a, **_k: True)
        monkeypatch.setattr(gpu_mod, "kfd_status", lambda *_a, **_k: gpu_mod.KFD_MISSING)

    @staticmethod
    def _amd_box_with_unopenable_kfd(monkeypatch) -> None:
        """The node IS forwarded but the runner identity cannot open it — the
        third status, which nothing exercised through ``load_sync`` before."""
        from hal0.providers import _gpu as gpu_mod

        monkeypatch.setattr(gpu_mod, "host_is_amd_gpu", lambda *_a, **_k: True)
        monkeypatch.setattr(gpu_mod, "kfd_status", lambda *_a, **_k: gpu_mod.KFD_NOT_OPENABLE)

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

    def test_load_sync_refuses_the_llama_lane_on_an_unopenable_node(self, monkeypatch) -> None:
        """KFD_NOT_OPENABLE through load_sync — the third status, previously
        unexercised on this path.

        A forwarded-but-unopenable node is just as poisoned as a missing one:
        HIP still fails to initialise and llama.cpp still lands on the invalid
        Vulkan lane. The refusal must fire, and its remedy must be the group
        fix rather than a re-forward (#1953).
        """
        from hal0.providers import container as container_mod

        self._amd_box_with_unopenable_kfd(monkeypatch)
        self._stub_unit_write(monkeypatch)

        with pytest.raises(GpuPreflightError) as err:
            container_mod.ContainerProvider().load_sync(
                {"name": "utility", "device": "gpu-vulkan", "port": 8082}, {}
            )
        msg = str(err.value)
        assert "IS forwarded" in msg
        assert "dev1:" not in msg  # never send them to re-forward a present node

    def test_a_non_rocm_runtime_is_unaffected_by_an_unopenable_node(self, monkeypatch) -> None:
        """The lane scoping (#1941) still wins over the status: Kokoro forwards
        no compute node at all, so its openability is irrelevant."""
        from hal0.providers import container as container_mod

        self._amd_box_with_unopenable_kfd(monkeypatch)
        written = self._stub_unit_write(monkeypatch)

        container_mod.ContainerProvider().load_sync(
            {"name": "voice", "type": "tts", "device": "gpu-vulkan", "port": 8090}, {}
        )
        assert written == ["voice"]

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
        ``gpu-vulkan`` slot resolves to the default llama-server provider, and
        on the default pin it still runs the poisoned ROCmFPX Vulkan lane.

        Post-#1948 the refusal comes from the image gate rather than the kfd
        one — the box in this fixture resolves the ade07ba-lineage default,
        which is not a validated Vulkan image. Pinning the slot to
        ``VULKAN_FIXED_IMAGE`` is what makes it load; see
        ``test_gpu_vulkan_lane_gate.py``.
        """
        from hal0.providers import container as container_mod

        self._amd_box_without_kfd(monkeypatch)
        self._stub_unit_write(monkeypatch)

        with pytest.raises(GpuPreflightError):
            container_mod.ContainerProvider().load_sync(
                {
                    "name": "utility",
                    "device": "gpu-vulkan",
                    "port": 8082,
                    # Pinned LITERALLY rather than left to the default pin: the
                    # default is a moving target and this assertion is about
                    # #1888's carrier specifically.
                    "image_pin": "ghcr.io/hal0ai/hal0-rocmfpx:ade07ba",
                },
                {},
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

    def test_the_no_rocm_gpu_rocm_refusal_describes_a_device_mismatch(self, tmp_path) -> None:
        """A no-rocm runtime (Kokoro/Moonshine) only reaches the raise path via
        a hand-set ``device="gpu-rocm"``, since the gpu-vulkan branch already
        exempts this lane. The image never resolves ROCm/HIP, so the refusal
        must not claim it does (Codex review on PR #1952) — the actual
        problem is the device declaration, not the runtime."""
        with pytest.raises(GpuPreflightError) as exc:
            require_kfd_for_gpu_slot(
                "voice",
                device="gpu-rocm",
                kfd_path=str(tmp_path / "kfd"),
                env={},
                amd_host=True,
                runtime_lane="no-rocm",
            )
        msg = str(exc.value)
        assert "1888" not in msg
        assert "resolves ROCm at launch" not in msg
        assert "cannot initialise HIP without it" not in msg
        assert "never touches ROCm/HIP" in msg
        assert "declared 'gpu-rocm'" in msg
        # Still actionable: what is missing and how to forward it.
        assert "/kfd" in msg
        assert "pct stop/start" in msg


class TestFallbackWarningIsLaneScoped:
    """PR #1952 review: the ``HAL0_ALLOW_VULKAN_FALLBACK`` warn path
    hardcoded ``detail="output will be invalid — see #1888"`` for every lane,
    including ``"rocm"`` — a ComfyUI/Qwen3-TTS operator has no Vulkan
    fallback to speak of and gets sent to the wrong defect. The warning must
    reuse the same lane-scoped explanation as the raise path."""

    def test_the_llama_lane_warning_still_names_1888(self, tmp_path) -> None:
        from structlog.testing import capture_logs

        with capture_logs() as logs:
            require_kfd_for_gpu_slot(
                "agent",
                device="gpu-rocm",
                kfd_path=str(tmp_path / "kfd"),
                env={ENV_ALLOW_VULKAN_FALLBACK: "1"},
                amd_host=True,
                runtime_lane="llama",
            )
        warnings = [e for e in logs if e.get("log_level") == "warning"]
        assert len(warnings) == 1
        assert "1888" in warnings[0]["detail"]

    def test_the_rocm_lane_warning_does_not_blame_the_vulkan_fallback(self, tmp_path) -> None:
        from structlog.testing import capture_logs

        with capture_logs() as logs:
            require_kfd_for_gpu_slot(
                "imagegen",
                device="gpu-vulkan",
                kfd_path=str(tmp_path / "kfd"),
                env={ENV_ALLOW_VULKAN_FALLBACK: "1"},
                amd_host=True,
                runtime_lane="rocm",
            )
        warnings = [e for e in logs if e.get("log_level") == "warning"]
        assert len(warnings) == 1
        detail = warnings[0]["detail"]
        assert "1888" not in detail
        assert "output will be invalid" not in detail
        assert "resolves ROCm at launch" in detail

    def test_the_no_rocm_lane_warning_describes_the_device_mismatch(self, tmp_path) -> None:
        from structlog.testing import capture_logs

        with capture_logs() as logs:
            require_kfd_for_gpu_slot(
                "voice",
                device="gpu-rocm",
                kfd_path=str(tmp_path / "kfd"),
                env={ENV_ALLOW_VULKAN_FALLBACK: "1"},
                amd_host=True,
                runtime_lane="no-rocm",
            )
        warnings = [e for e in logs if e.get("log_level") == "warning"]
        assert len(warnings) == 1
        detail = warnings[0]["detail"]
        assert "1888" not in detail
        assert "output will be invalid" not in detail
        assert "never touches ROCm/HIP" in detail


def _raiser(*_a: object, **_kw: object) -> None:
    raise GpuPreflightError("no /dev/kfd")


class TestResolveKfdTargetGid:
    """#1953: the target gid must come from the DEVICE NODE, never a group name.

    The kernel gates on the integer. On a halo143-class box ``renderD128`` is
    owned by gid 993 whose ``/etc/group`` name is ``clock``, while ``render``
    resolves to a different, useless gid — so ``grp.getgrnam("render")``
    produces a number that grants nothing. Baking in 993 (or any constant) is
    the same bug wearing a different hat.
    """

    def test_follows_the_render_node_gid(self, tmp_path, monkeypatch) -> None:
        node = tmp_path / "renderD128"
        node.write_text("")
        os.chown(node, -1, os.getgid())
        monkeypatch.setattr(
            "hal0.providers._gpu.resolve_gpu_device_paths", lambda *a, **k: [str(node)]
        )
        monkeypatch.setattr(
            "hal0.providers._gpu._device_node_for_group",
            lambda name, paths: str(node) if name == "render" else None,
        )
        assert resolve_kfd_target_gid() == os.stat(node).st_gid

    def test_returns_none_rather_than_guessing_a_constant(self, monkeypatch) -> None:
        """No render node → no opinion. Guessing 993 is how a wrong gid gets baked in."""
        monkeypatch.setattr("hal0.providers._gpu.resolve_gpu_device_paths", lambda *a, **k: [])
        monkeypatch.setattr("hal0.providers._gpu._device_node_for_group", lambda name, paths: None)
        assert resolve_kfd_target_gid() is None


class TestConvergeKfdDeviceGroup:
    def test_noop_when_already_aligned(self, tmp_path, monkeypatch) -> None:
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        kfd.chmod(0o660)
        monkeypatch.setattr(
            "hal0.providers._gpu.resolve_kfd_target_gid",
            lambda *a, **k: os.stat(kfd).st_gid,
        )
        status, _ = converge_kfd_device_group(str(kfd))
        assert status == "noop"

    def test_skips_when_no_render_node_to_learn_from(self, tmp_path, monkeypatch) -> None:
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        monkeypatch.setattr("hal0.providers._gpu.resolve_kfd_target_gid", lambda *a, **k: None)
        status, detail = converge_kfd_device_group(str(kfd))
        assert status == "skipped"
        assert "render node" in detail

    def test_skips_when_the_node_is_absent(self, tmp_path) -> None:
        status, _ = converge_kfd_device_group(str(tmp_path / "nope"))
        assert status == "skipped"

    def test_dry_run_reports_without_writing(self, tmp_path, monkeypatch) -> None:
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        kfd.chmod(0o600)
        before = os.stat(kfd).st_mode
        monkeypatch.setattr("hal0.providers._gpu.resolve_kfd_target_gid", lambda *a, **k: 4242)
        status, _ = converge_kfd_device_group(str(kfd), dry_run=True)
        assert status == "changed"
        assert os.stat(kfd).st_mode == before

    def test_failure_is_reported_not_raised(self, tmp_path, monkeypatch) -> None:
        """Unprivileged LXC: chgrp is EPERM. Surface the host remedy, never abort."""
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        monkeypatch.setattr("hal0.providers._gpu.resolve_kfd_target_gid", lambda *a, **k: 4242)

        def _boom(*a, **k):
            raise PermissionError("Operation not permitted")

        monkeypatch.setattr(os, "chown", _boom)
        status, detail = converge_kfd_device_group(str(kfd))
        assert status == "failed"
        assert "gid=4242" in detail


class TestGuardRemedyText:
    def test_unopenable_does_not_tell_you_to_re_forward_the_device(
        self, tmp_path, monkeypatch
    ) -> None:
        """#1953's headline harm: the old text sent operators to reboot production
        for a device that was already forwarded."""
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        monkeypatch.setattr("hal0.providers._gpu.kfd_status", lambda *a, **k: KFD_NOT_OPENABLE)
        monkeypatch.setattr("hal0.providers._gpu.resolve_kfd_target_gid", lambda *a, **k: 993)
        with pytest.raises(GpuPreflightError) as err:
            require_kfd_for_gpu_slot("brain", device="gpu-rocm", kfd_path=str(kfd))
        msg = str(err.value)
        assert "IS forwarded" in msg
        assert "chgrp 993" in msg
        assert "pct stop/start" in msg  # only as the unprivileged-LXC sub-case
        assert "dev1:" not in msg  # the WRONG remedy must not appear

    def test_missing_still_tells_you_to_forward_it(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("hal0.providers._gpu.kfd_status", lambda *a, **k: KFD_MISSING)
        with pytest.raises(GpuPreflightError) as err:
            require_kfd_for_gpu_slot("brain", device="gpu-rocm", kfd_path=str(tmp_path / "nope"))
        assert "dev1:" in str(err.value)


class TestConvergeAgainstRealPermissionDenial:
    """#1953 gap 2 — exercise a GENUINE chgrp refusal, not a monkeypatched one.

    The unprivileged-LXC path (where ``/dev/kfd``'s ownership is host-mapped and
    ``chown`` returns EPERM) has NOT been validated on a real unprivileged
    container — see the regression register entry
    ``kfd-gid-unprivileged-lxc-unverified``. This gets as close as a unit test
    honestly can: a real chown against a gid the running user is not a member
    of, which the kernel refuses for the same reason.

    It proves the failure is reported rather than raised, and that the message
    carries an actionable gid. It does NOT prove the idmap semantics of a real
    unprivileged LXC, and must not be read as if it did.
    """

    def test_a_real_eperm_is_reported_with_an_actionable_remedy(
        self, tmp_path, monkeypatch
    ) -> None:
        if os.geteuid() == 0:
            pytest.skip("root can chown to any gid — no denial to observe")
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        # A gid this user is certainly not a member of.
        foreign_gid = 61997
        assert foreign_gid not in os.getgroups()
        monkeypatch.setattr(
            "hal0.providers._gpu.resolve_kfd_target_gid", lambda *a, **k: foreign_gid
        )
        status, detail = converge_kfd_device_group(str(kfd))
        assert status == "failed"
        # The operator must be able to act on this without reading the source.
        assert f"gid={foreign_gid}" in detail
        assert "unprivileged" in detail.lower() or "host" in detail.lower()

    def test_the_node_is_left_untouched_when_the_chgrp_is_refused(
        self, tmp_path, monkeypatch
    ) -> None:
        """A refused converge must not half-apply (e.g. chmod without chgrp)."""
        if os.geteuid() == 0:
            pytest.skip("root can chown to any gid — no denial to observe")
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        kfd.chmod(0o600)
        before = os.stat(kfd)
        monkeypatch.setattr("hal0.providers._gpu.resolve_kfd_target_gid", lambda *a, **k: 61997)
        converge_kfd_device_group(str(kfd))
        after = os.stat(kfd)
        assert after.st_gid == before.st_gid
        assert after.st_mode == before.st_mode


class TestResolveImageRuntimeUidUsesTheRootStore:
    """#1953 R2 — the uid must come from ROOT's image store, never hal0's own.

    hal0-api runs as the unprivileged ``hal0`` user with no subuid ranges, so a
    bare ``podman image inspect`` reads hal0's ROOTLESS store — a different
    store, which never contains a slot image. Reading it would make this check
    a silent no-op in production, which is #1889 with extra steps.
    """

    def test_it_routes_through_the_seam(self, monkeypatch) -> None:
        from hal0.providers import _gpu as gpu_mod

        seen: list[str] = []
        monkeypatch.setattr(
            "hal0.providers.podman_introspect.image_user",
            lambda ref, **k: seen.append(ref) or "hal0",
        )
        import pwd

        monkeypatch.setattr(pwd, "getpwnam", lambda n: type("P", (), {"pw_uid": 1234})())
        assert gpu_mod.resolve_image_runtime_uid("ghcr.io/x/y:1") == 1234
        assert seen == ["ghcr.io/x/y:1"]

    def test_it_never_shells_bare_podman(self, monkeypatch) -> None:
        """The regression guard: a bare subprocess call is the #1889 trap."""
        import subprocess

        from hal0.providers import _gpu as gpu_mod

        monkeypatch.setattr("hal0.providers.podman_introspect.image_user", lambda *a, **k: "")

        def _boom(*a, **k):  # pragma: no cover - only runs if the bug returns
            raise AssertionError(f"bare podman call: {a!r}")

        monkeypatch.setattr(subprocess, "run", _boom)
        assert gpu_mod.resolve_image_runtime_uid("ghcr.io/x/y:1") == gpu_mod.SLOT_RUNNER_UID

    def test_an_unanswerable_seam_falls_back_to_root_not_rootless(self, monkeypatch) -> None:
        """``None`` means the seam did not answer. Deliberately NO rootless
        fallback: that store's answer is about a different object."""
        from hal0.providers import _gpu as gpu_mod

        monkeypatch.setattr("hal0.providers.podman_introspect.image_user", lambda *a, **k: None)
        assert gpu_mod.resolve_image_runtime_uid("ghcr.io/x/y:1") == gpu_mod.SLOT_RUNNER_UID

    @pytest.mark.parametrize("declared", ["", "root", "0"])
    def test_root_equivalents_resolve_to_the_slot_runner_uid(self, monkeypatch, declared) -> None:
        from hal0.providers import _gpu as gpu_mod

        monkeypatch.setattr("hal0.providers.podman_introspect.image_user", lambda *a, **k: declared)
        assert gpu_mod.resolve_image_runtime_uid("ghcr.io/x/y:1") == gpu_mod.SLOT_RUNNER_UID

    def test_a_numeric_user_is_honoured(self, monkeypatch) -> None:
        from hal0.providers import _gpu as gpu_mod

        monkeypatch.setattr(
            "hal0.providers.podman_introspect.image_user", lambda *a, **k: "1000:1000"
        )
        assert gpu_mod.resolve_image_runtime_uid("ghcr.io/x/y:1") == 1000


class TestTheUidProbeIsLazy:
    """#1953 N2 — resolving the runner uid is a sudo round-trip plus two podman
    calls. An eager argument expression charged EVERY slot for a probe only the
    gated ones need."""

    def _never(self):
        def _boom() -> int:  # pragma: no cover - only runs if laziness breaks
            raise AssertionError("uid probe ran for an ungated slot")

        return _boom

    @pytest.mark.parametrize("device", ["cpu", "npu", "gpu-cuda", ""])
    def test_ungated_devices_never_resolve_the_uid(self, device, tmp_path) -> None:
        require_kfd_for_gpu_slot(
            "x",
            device=device,
            kfd_path=str(tmp_path / "kfd"),
            env={},
            amd_host=True,
            runner_uid=self._never(),
        )

    def test_a_non_rocm_lane_never_resolves_the_uid(self, tmp_path) -> None:
        """Kokoro/Moonshine forward no compute node, so the identity that would
        open it is irrelevant (#1941 scoping runs before the probe)."""
        require_kfd_for_gpu_slot(
            "voice",
            device="gpu-vulkan",
            runtime_lane="no-rocm",
            kfd_path=str(tmp_path / "kfd"),
            env={},
            amd_host=True,
            runner_uid=self._never(),
        )

    def test_a_gated_lane_does_resolve_the_uid(self, tmp_path) -> None:
        """...and the lazy form must still actually be consulted when it counts."""
        node = tmp_path / "kfd"
        node.write_text("")
        calls: list[int] = []

        def _probe() -> int:
            calls.append(1)
            return 0

        require_kfd_for_gpu_slot(
            "brain",
            device="gpu-rocm",
            kfd_path=str(node),
            env={},
            amd_host=True,
            runner_uid=_probe,
        )
        assert calls == [1]

    def test_a_plain_int_still_works(self, tmp_path) -> None:
        node = tmp_path / "kfd"
        node.write_text("")
        require_kfd_for_gpu_slot(
            "brain", device="gpu-rocm", kfd_path=str(node), env={}, amd_host=True, runner_uid=0
        )
